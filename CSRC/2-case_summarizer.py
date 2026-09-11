"""
CSRC 案例结构化提取器 (case_summarizer.py)
功能：读取 CSRC/cases/{Bureau}/{case_type}/*.json 中 raw_text 有效（长度 > 50）的案例，
     调用大模型提取结构化字段，输出到 CSRC/summaries/{Bureau}/{case_type}/{case_id}_summary.json，
     支持增量处理、失败重试与并发模式。

依赖：通过 importlib 加载基协 2-case_summarizer.py，复用其 get_client / llm_chat /
     _parse_json_from_response / PROVIDER_CONFIG，避免重新实现 LLM 调用逻辑。
"""

import os
import json
import time
import logging
import sys
import traceback
import threading
import importlib.util
import types as _types
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ──────────────────────────── 加载基协模块复用 LLM 函数 ────────────────────────────

# 基协模块文件名为 2-case_summarizer.py（以数字开头，无法用普通 import），
# 通过 importlib.util 加载。基协脚本已迁移至 AMAC/ 子目录。
_PARENT_DIR = str(Path(__file__).resolve().parent.parent)  # e:\Desktop\codes
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

# 兼容 Python 3.12：importlib.util.exec_module 加载本文件时，模块对象尚未注册到
# sys.modules，而 @dataclass 装饰器内部会调用 sys.modules.get(cls.__module__).__dict__，
# 未注册时会抛出 AttributeError。这里预先注册占位模块对象，确保 dataclass 装饰能正常工作。
if __name__ not in sys.modules:
    _placeholder = _types.ModuleType(__name__)
    _placeholder.__file__ = __file__
    sys.modules[__name__] = _placeholder

_SPEC = importlib.util.spec_from_file_location(
    "amac_summarizer", Path(_PARENT_DIR) / "AMAC" / "2-case_summarizer.py"
)
_AMAC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_AMAC)

_parse_json_from_response = _AMAC._parse_json_from_response
PROVIDER_CONFIG = _AMAC.PROVIDER_CONFIG

# 扩展 PROVIDER_CONFIG，添加 LongCat（OpenAI 兼容端点，文档见 https://api.longcat.chat）
PROVIDER_CONFIG["longcat"] = {
    "api_key_env": "LONGCAT_API_KEY",
    "api_key_default": "",
    "base_url": "https://api.longcat.chat/openai",
    "text_model": "LongCat-2.0",
}
PROVIDER_CONFIG["deepseek"] = {
    "api_key_env": "DEEPSEEK_API_KEY",
    "api_key_default": "",
    "base_url": "https://api.deepseek.com",
    "text_model": "deepseek-v4-flash",
}

# 保存基协原始函数，用于 glm/mimo 分支委托
_amac_get_client = _AMAC.get_client
_amac_llm_chat = _AMAC.llm_chat

_longcat_client_cache: Dict[str, object] = {}


def get_client(provider: Optional[str] = None):
    """获取 LLM 客户端。

    longcat 使用 OpenAI SDK + LongCat 兼容端点；其他 provider 委托基协实现。
    LongCat API Key 通过环境变量 LONGCAT_API_KEY 读取，未设置时抛出明确错误。
    """
    if provider is None:
        provider = PROVIDER
    if provider == "longcat":
        if provider in _longcat_client_cache:
            return _longcat_client_cache[provider]
        config = PROVIDER_CONFIG[provider]
        api_key = os.environ.get(config["api_key_env"], config.get("api_key_default", ""))
        if not api_key:
            raise ValueError(
                f"未设置环境变量 {config['api_key_env']}，请在运行前设置 LongCat API Key"
            )
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"LongCat客户端已初始化，模型: {config['text_model']}")
        _longcat_client_cache[provider] = client
        return client
    return _amac_get_client(provider)


def llm_chat(messages, model=None, max_tokens=8000, temperature=0.1,
             thinking=None, provider=None, stream=False):
    """调用 LLM。

    longcat 使用标准 OpenAI 格式（max_tokens/temperature，不含 thinking/top_p）；
    其他 provider 委托基协实现（mimo 用 max_completion_tokens+top_p，glm 可选 thinking）。
    """
    if provider is None:
        provider = PROVIDER
    if provider == "longcat":
        if model is None:
            model = PROVIDER_CONFIG[provider]["text_model"]
        client = get_client(provider)
        return client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
        )
    return _amac_llm_chat(messages, model, max_tokens, temperature, thinking, provider, stream)


# ──────────────────────────── 全局配置 ────────────────────────────

PROVIDER = "glm"

# 路径常量：cases 与 summaries 均在 CSRC 脚本目录下
CSRC_CASES_DIR = Path(__file__).resolve().parent / "cases"
CSRC_SUMMARIES_DIR = Path(__file__).resolve().parent / "summaries"

# LLM 调用相关
MAX_RETRIES = 3
DELAY_BETWEEN_API_CALLS = 1.5
INPUT_TRUNCATE = 12000  # CSRC 行政处罚决定书更长，截断长度从基协 8000 提升到 12000

# 并发相关
MAX_CONCURRENCY = 50
MIN_CONCURRENCY = 1
DEFAULT_CONCURRENCY = 5


# ──────────────────────────── EXTRACT_PROMPT ────────────────────────────

EXTRACT_PROMPT = """你是一名专业的证监会监管法规分析师。请从以下案例原文中提取结构化信息，严格按JSON格式返回。

案例类型标识：{case_type}
（penalty = 行政处罚决定书，通常含警告、罚款、市场禁入等，依据《证券法》等法律；
 measure = 行政监管措施决定书，通常含责令改正、警示函、监管谈话、暂停业务等，依据部门规章）

需要提取的字段：
- entity_type: 受处罚主体类型，枚举值 "机构" / "个人" / "机构+个人"
- violation_type: 违规类型，从以下 14 类中选择最匹配的（如有多个违规类型，用顿号分隔）：

  · 信息披露违规：虚假记载、重大遗漏、未按时披露定期报告、未披露重大事项
  · 操纵市场：操纵证券价格、操纵期货市场
  · 内幕交易：内幕交易、泄露内幕信息
  · 违规募集：向不合格投资者募集、承诺保本保收益、公开宣传推介私募、未做适当性管理
  · 未按规定备案：私募基金产品未备案、未及时更新备案信息
  · 登记信息失实：虚假填报登记备案信息、未及时变更重大事项
  · 违规投资运作：超范围投资、违反合同约定投资、开展通道业务、违规加杠杆
  · 挪用基金财产：挪用、侵占基金财产
  · 违规关联交易：未披露关联交易、利益输送、关联交易价格不公允
  · 未按规定托管：未设托管、未选合格托管
  · 内控缺失：内控制度不健全、合规风控体系无效、岗位设置混乱
  · 未勤勉尽责：未尽管理人职责、尽调不充分、疏于管理
  · 未配合监管：拒不配合检查、提供虚假材料、逾期不整改
  · 其他：以上类型均不匹配时使用

- punishment: 处罚/监管措施具体内容，如"警告并处以罚款150万元"、"责令改正"、"出具警示函"
- involved_fund: 涉及基金/产品名称，多个用顿号分隔，无则填空字符串
- violation_summary: 违规事实摘要，200字左右的客观概括
- legal_basis: 处罚依据的法规条款，如《证券法》第一百九十七条、《私募投资基金监督管理暂行办法》第三十三条，多条用顿号分隔
- penalty_amount: 罚款金额（单位：万元），仅行政处罚有，监管措施或无罚款填空字符串
- market_ban: 市场禁入信息，如"万连步：终身禁入"、"李计国：10年禁入"，无则填空字符串
- fund_related: 本案例是否真正与基金/基金管理人/基金业务直接相关（布尔值 true 或 false，必须输出）
  判断标准（严格，不可仅凭字面出现"基金"二字判定）：
  · 相关（true）：当事人是基金管理人、基金托管人、基金销售机构（针对基金销售业务）、基金份额持有人，或违法行为直接涉及公募基金、私募基金、证券投资基金的募集、投资、管理、销售、托管等环节
  · 不相关（false）：以下情形一律视为不相关——
    (1) 仅因引用《证券公司和证券投资基金管理公司合规管理办法》等同时适用于证券公司的通用法规，但实际违规业务与基金无关的；
    (2) 证券公司、证券公司营业部、期货公司因证券经纪、自营、投行、融资融券、期货经纪、投资者适当性管理（针对非基金产品）等非基金业务被罚的；
    (3) 上市公司、挂牌公司因信息披露、公司治理等被罚，与基金业务无关的
- fund_relation_reason: 基金相关性的判断理由（简短一句，说明当事人身份与违规业务性质，
  如"当事人为证券公司营业部，违规为金融产品销售适当性管理，非基金业务"）

请只返回JSON，不要有任何其他文字。如果某个字段无法从文中提取，填空字符串（fund_related 除外，必须给 true/false）。

---
案例原文：

{text}"""


# ──────────────────────────── 行内刷新日志 ────────────────────────────

# 常规进度信息采用行内刷新（\r 覆盖前一行），避免终端被大量常规日志刷屏。
# 仅异常（WARNING/ERROR）与最终结果信息走标准 logger 输出，自动换行到新行。
# 独立实现，不 import CSRC fetcher，避免触发 requests/bs4/bureaus 等重依赖初始化。
_progress_lock = threading.Lock()
_last_progress_width = 0  # 上一次 progress 输出的可见宽度，用于填充清空残留


def _visible_width(s: str) -> int:
    """估算字符串在终端的可见宽度（非 ASCII 字符按 2 列计）。"""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def progress(msg: str) -> None:
    """行内刷新输出常规进度信息（覆盖前一行，不换行）。

    用于"开始处理"、"LLM 调用中"、"提取成功"等常规进度。后续任意 logger
    输出会自动换行到新行，保证最终结果与异常信息可读。
    """
    global _last_progress_width
    with _progress_lock:
        sys.stdout.write("\r" + msg)
        # 用空格填充到上一次宽度，避免行尾残留字符
        pad = max(0, _last_progress_width - _visible_width(msg))
        if pad:
            sys.stdout.write(" " * pad)
        sys.stdout.flush()
        _last_progress_width = _visible_width(msg)


class _ProgressAwareStreamHandler(logging.StreamHandler):
    """输出日志前，若有未完成的 progress 行，先换行。"""

    def emit(self, record):
        global _last_progress_width
        with _progress_lock:
            if _last_progress_width > 0:
                sys.stdout.write("\n")
                _last_progress_width = 0
        super().emit(record)


def setup_logging() -> logging.Logger:
    """配置并返回模块日志器。"""
    log = logging.getLogger("csrc_case_summarizer")
    log.setLevel(logging.INFO)
    if log.handlers:
        return log
    handler = _ProgressAwareStreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return log


logger = setup_logging()


# ──────────────────────────── 数据模型 ────────────────────────────

@dataclass
class CaseSummary:
    """CSRC 案例结构化摘要。

    字段分三组：
    1. 继承字段（11个）：从 case JSON 直接复制，不调用 LLM
    2. LLM 提取字段（8个）：由大模型从 raw_text 提取
    3. 元数据字段（5个）：记录提取状态与所用模型
    """
    # 继承字段（必填，从 case_data 复制）
    case_id: str
    source_url: str
    case_type: str
    bureau: str
    title: str
    date: str
    document_number: str
    punished_entities: str
    is_fund_related: bool
    fund_evidence: str
    pdf_url: str
    # LLM 提取字段
    entity_type: str = ""
    violation_type: str = ""
    punishment: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    penalty_amount: str = ""
    market_ban: str = ""
    # 元数据字段
    extract_success: bool = False
    error: str = ""
    extract_time: str = ""
    llm_provider: str = ""
    llm_model: str = ""


# ──────────────────────────── 摘要索引 ────────────────────────────

class SummaryIndex:
    """持久化的摘要处理索引，记录哪些案例已完成结构化提取。

    索引文件：{summaries_root}/_summary_index.json
    结构：
    {
        "last_updated": "2026-07-09T14:30:00",
        "cases": {
            "20220111_c1763000": {
                "status": "done",
                "summary_file": "Anhui/measure/20220111_c1763000_summary.json",
                "extract_time": "2026-07-09T14:30:00"
            },
            "20220107_c1880815": {
                "status": "failed",
                "error": "模型返回非JSON",
                "extract_time": ""
            }
        }
    }
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.index_path = output_dir / "_summary_index.json"
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> Dict:
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"摘要索引文件损坏，将重建: {e}")
        return {"last_updated": "", "cases": {}}

    def save(self):
        with self._lock:
            self.data["last_updated"] = datetime.now().isoformat()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with open(self.index_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_done(self, case_id: str) -> bool:
        """是否已完成处理（done 或 skipped，均不重复处理）。"""
        info = self.data["cases"].get(case_id, {})
        return info.get("status") in ("done", "skipped")

    def mark_done(self, case_id: str, summary_file: str):
        with self._lock:
            self.data["cases"][case_id] = {
                "status": "done",
                "summary_file": summary_file,
                "extract_time": datetime.now().isoformat(),
            }
        self.save()

    def mark_skipped(self, case_id: str, reason: str):
        """标记为跳过（LLM 判定非基金相关，不生成 summary 文件）。"""
        with self._lock:
            self.data["cases"][case_id] = {
                "status": "skipped",
                "reason": reason,
                "extract_time": datetime.now().isoformat(),
            }
        self.save()

    def mark_failed(self, case_id: str, error: str):
        with self._lock:
            info = self.data["cases"].get(case_id, {})
            info["status"] = "failed"
            info["error"] = error
            info["extract_time"] = ""
            self.data["cases"][case_id] = info
        self.save()

    def get_pending_cases(self, all_case_ids: List[str]) -> List[str]:
        return [cid for cid in all_case_ids if not self.is_done(cid)]

    def get_failed_cases(self) -> List[str]:
        return [
            cid for cid, info in self.data["cases"].items()
            if info.get("status") == "failed"
        ]

    def get_skipped_cases(self) -> List[str]:
        return [
            cid for cid, info in self.data["cases"].items()
            if info.get("status") == "skipped"
        ]

    def get_stats(self, total_count: int) -> Dict[str, int]:
        done = sum(1 for info in self.data["cases"].values() if info.get("status") == "done")
        skipped = sum(1 for info in self.data["cases"].values() if info.get("status") == "skipped")
        failed = sum(1 for info in self.data["cases"].values() if info.get("status") == "failed")
        return {
            "total": total_count,
            "done": done,
            "skipped": skipped,
            "pending": total_count - done - skipped - failed,
            "failed": failed,
        }


# ──────────────────────────── 案例扫描 ────────────────────────────

def scan_case_files(cases_dir: Path) -> List[Dict]:
    """递归扫描 CSRC/cases/{Bureau}/{case_type}/*.json，过滤 raw_text 有效案例。

    过滤规则：raw_text 存在且 len(raw_text) > 50（与 fetcher 的"成功"判定一致）。
    返回按 date 升序排列的 list[dict]，每项含原始 JSON 内容并补充 _source_path 字段。
    """
    if not cases_dir.exists():
        logger.warning(f"案例目录不存在: {cases_dir}")
        return []

    cases = []
    for json_path in sorted(cases_dir.rglob("*.json")):
        if json_path.name.startswith("_"):
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_text = data.get("raw_text", "")
            if raw_text and len(raw_text) > 50:
                data["_source_path"] = str(json_path)
                cases.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"读取案例文件失败 {json_path.name}: {e}")

    cases.sort(key=lambda x: x.get("date", ""), reverse=False)
    logger.info(f"扫描到 {len(cases)} 个有效案例 (raw_text 长度 > 50)")
    return cases


# ──────────────────────────── 结构化提取 ────────────────────────────

def extract_structured_info(raw_text: str, case_type: str) -> Optional[Dict]:
    """调用文本 LLM 从原文提取结构化信息，返回解析后的字典或 None。

    截断到 INPUT_TRUNCATE 字，调用基协 llm_chat，重试 MAX_RETRIES 次，
    解析 JSON 失败时返回 None。
    """
    if not raw_text or len(raw_text) < 50:
        logger.warning("原文过短，跳过提取")
        return None

    truncated = raw_text[:INPUT_TRUNCATE] if len(raw_text) > INPUT_TRUNCATE else raw_text
    prompt = EXTRACT_PROMPT.format(text=truncated, case_type=case_type)

    for attempt in range(MAX_RETRIES):
        try:
            progress(f"  [提取] 请求模型... (尝试 {attempt + 1}/{MAX_RETRIES})")

            response = llm_chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=8000,
                temperature=0.1,
                thinking={"type": "disabled"} if PROVIDER == "glm" else None,
                provider=PROVIDER,
            )

            if not hasattr(response, "choices") or len(response.choices) == 0:
                logger.warning("  [提取] 响应结构异常")
                continue

            msg = response.choices[0].message
            content = msg.content

            if not content:
                rc = getattr(msg, "reasoning_content", None)
                if rc and len(rc) > 100:
                    logger.info(f"  [提取] content为空，尝试从reasoning_content提取JSON")
                    content = rc
                else:
                    logger.warning("  [提取] 模型返回空内容")
                    continue

            result = _parse_json_from_response(content)
            if result is not None:
                progress(f"  [提取] 成功，字段数: {len(result)}")
                return result
            else:
                logger.warning(f"  [提取] 第 {attempt + 1} 次未能解析出有效JSON")

        except Exception as e:
            logger.warning(f"  [提取] 第 {attempt + 1} 次失败: {e}")
            if attempt < MAX_RETRIES - 1:
                wait = 3 * (attempt + 1)
                logger.info(f"  [提取] {wait}秒后重试...")
                time.sleep(wait)

    return None


# ──────────────────────────── 单案例处理 ────────────────────────────

def process_single_case(
    case_data: Dict,
    cases_root: Path,
    summaries_root: Path,
    index: SummaryIndex,
) -> Optional[CaseSummary]:
    """处理单个案例的结构化提取。

    输出路径：{summaries_root}/{bureau}/{case_type}/{case_id}_summary.json
    - 已 done/skipped → 跳过
    - 上次 failed → 删除旧 summary 文件后重新调用 LLM
    - LLM 判定非基金相关 → 不写 summary 文件，index.mark_skipped，返回 None
    - 成功时合并 LLM 字段、写 summary、index.mark_done
    - 失败时写 summary（extract_success=False）、index.mark_failed
    """
    case_id = case_data.get("case_id", "")
    bureau = case_data.get("bureau", "")
    case_type = case_data.get("case_type", "")

    output_dir = summaries_root / bureau / case_type
    summary_path = output_dir / f"{case_id}_summary.json"

    # 已 done/skipped → 跳过（skipped 无 summary 文件，仅查 index）
    if index.is_done(case_id):
        if summary_path.exists():
            progress(f"  [跳过] 已完成提取: {case_id}")
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    return CaseSummary(**json.load(f))
            except Exception:
                summary_path.unlink(missing_ok=True)
        else:
            # skipped 状态：index 已标记非基金相关，直接跳过
            progress(f"  [跳过] 已判定非基金相关: {case_id}")
            return None

    # 上次 failed 或 summary 文件存在但未成功 → 删除旧 summary 文件后重试
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            if existing_data.get("extract_success"):
                # 已成功但索引未标记 done（异常情况），保守跳过并标记 done
                progress(f"  [跳过] 已完成提取: {case_id}")
                rel = summary_path.relative_to(summaries_root).as_posix()
                index.mark_done(case_id, rel)
                return CaseSummary(**existing_data)
            else:
                progress(f"  [重试] 删除失败摘要: {case_id} ({existing_data.get('error', '')})")
                summary_path.unlink()
        except Exception:
            summary_path.unlink(missing_ok=True)

    raw_text = case_data.get("raw_text", "")
    extracted = extract_structured_info(raw_text, case_type)

    # LLM 判定非基金相关 → 跳过，不写 summary 文件
    if extracted is not None:
        fund_related = extracted.get("fund_related")
        # 兼容字符串 "true"/"false" 与原生 bool
        if isinstance(fund_related, str):
            fund_related = fund_related.strip().lower() == "true"
        fund_relation_reason = extracted.get("fund_relation_reason", "")
        if fund_related is False:
            index.mark_skipped(case_id, fund_relation_reason)
            progress(f"  [跳过] 非基金相关: {case_id} | {fund_relation_reason}")
            return None

    # 构造 CaseSummary（继承字段从 case_data 复制）
    summary = CaseSummary(
        case_id=case_id,
        source_url=case_data.get("source_url", ""),
        case_type=case_type,
        bureau=bureau,
        title=case_data.get("title", ""),
        date=case_data.get("date", ""),
        document_number=case_data.get("document_number", ""),
        punished_entities=case_data.get("punished_entities", ""),
        is_fund_related=case_data.get("is_fund_related", False),
        fund_evidence=case_data.get("fund_evidence", ""),
        pdf_url=case_data.get("pdf_url", ""),
        extract_time=datetime.now().isoformat(),
        llm_provider=PROVIDER,
        llm_model=PROVIDER_CONFIG[PROVIDER]["text_model"],
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    if extracted is not None:
        # 合并 LLM 字段（覆盖空字符串默认值）
        summary.entity_type = extracted.get("entity_type", "")
        summary.violation_type = extracted.get("violation_type", "")
        summary.punishment = extracted.get("punishment", "")
        summary.involved_fund = extracted.get("involved_fund", "")
        summary.violation_summary = extracted.get("violation_summary", "")
        summary.legal_basis = extracted.get("legal_basis", "")
        summary.penalty_amount = extracted.get("penalty_amount", "")
        summary.market_ban = extracted.get("market_ban", "")
        summary.extract_success = True

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, ensure_ascii=False, indent=2)

        rel = summary_path.relative_to(summaries_root).as_posix()
        index.mark_done(case_id, rel)
        progress(f"  [✓] {case_id} | {summary.entity_type} | {summary.violation_type}")
    else:
        summary.extract_success = False
        summary.error = "模型未能返回有效结构化JSON"

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, ensure_ascii=False, indent=2)

        index.mark_failed(case_id, summary.error)
        logger.warning(f"  [✗] {case_id} | 提取失败")

    return summary


# ──────────────────────────── 批量处理 ────────────────────────────

def run_batch(
    cases_root: Path,
    summaries_root: Path,
    retry_failed: bool = True,
) -> Tuple[int, int]:
    """串行批量处理所有案例的结构化提取。

    遍历待处理列表，每条间隔 DELAY_BETWEEN_API_CALLS 秒。
    """
    cases = scan_case_files(cases_root)
    if not cases:
        logger.info("未找到可处理的案例")
        return 0, 0

    summaries_root.mkdir(parents=True, exist_ok=True)
    index = SummaryIndex(summaries_root)

    all_ids = [c["case_id"] for c in cases]
    pending_ids = index.get_pending_cases(all_ids)
    failed_ids = index.get_failed_cases() if retry_failed else []

    to_process_ids = set(pending_ids)
    if retry_failed:
        to_process_ids.update(failed_ids)

    to_process = [c for c in cases if c["case_id"] in to_process_ids]

    stats = index.get_stats(len(cases))
    logger.info(f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"已跳过 {stats['skipped']}, 待处理 {stats['pending']}, 失败 {stats['failed']}")
    logger.info(f"本次需处理: {len(pending_ids)} pending + {len(failed_ids)} failed = {len(to_process)} 个")

    if not to_process:
        logger.info("无待处理案例")
        return stats["done"], 0

    success = 0
    fail = 0
    skipped = 0

    for i, case_data in enumerate(to_process, 1):
        case_id = case_data.get("case_id", "unknown")
        title = case_data.get("title", "")
        progress(f"[{i}/{len(to_process)}] {case_id} | {title}")

        try:
            summary = process_single_case(case_data, cases_root, summaries_root, index)
            if summary is None:
                skipped += 1
            elif summary.extract_success:
                success += 1
            else:
                fail += 1
        except Exception as e:
            logger.error(f"  处理异常: {e}")
            traceback.print_exc()
            fail += 1
            index.mark_failed(case_id, str(e))

        if i < len(to_process):
            time.sleep(DELAY_BETWEEN_API_CALLS)

    final_stats = index.get_stats(len(cases))
    logger.info(f"本次完成: 成功 {success}, 跳过(非基金相关) {skipped}, 失败 {fail}")
    logger.info(f"索引总计: {final_stats['done']} done / {final_stats['skipped']} skipped / {final_stats['total']} total")
    return success, fail


def _process_case_worker(
    case_data: Dict,
    cases_root: Path,
    summaries_root: Path,
    index: SummaryIndex,
    counter: Dict,
    total: int,
) -> Optional[CaseSummary]:
    """并发工作线程：处理单个案例，更新共享计数器。"""
    case_id = case_data.get("case_id", "unknown")
    title = case_data.get("title", "")

    with counter["lock"]:
        seq = counter["done"] + counter["fail"] + counter["skipped"] + 1
        logger.info(f"[{seq}/{total}] 开始处理: {case_id} | {title}")

    try:
        summary = process_single_case(case_data, cases_root, summaries_root, index)
        with counter["lock"]:
            if summary is None:
                counter["skipped"] += 1
            elif summary.extract_success:
                counter["done"] += 1
            else:
                counter["fail"] += 1
        return summary
    except Exception as e:
        logger.error(f"  处理异常: {case_id} | {e}")
        traceback.print_exc()
        with counter["lock"]:
            counter["fail"] += 1
        index.mark_failed(case_id, str(e))
        return None


def run_batch_concurrent(
    cases_root: Path,
    summaries_root: Path,
    max_workers: int = DEFAULT_CONCURRENCY,
    retry_failed: bool = True,
) -> Tuple[int, int]:
    """并发批量处理所有案例的结构化提取（滑动窗口模式）。

    使用 ThreadPoolExecutor 实现：始终保持 max_workers 个任务在跑，
    某个任务完成后立即从队列中取下一个任务启动，无需等待整批完成。
    """
    cases = scan_case_files(cases_root)
    if not cases:
        logger.info("未找到可处理的案例")
        return 0, 0

    summaries_root.mkdir(parents=True, exist_ok=True)
    index = SummaryIndex(summaries_root)

    all_ids = [c["case_id"] for c in cases]
    pending_ids = index.get_pending_cases(all_ids)
    failed_ids = index.get_failed_cases() if retry_failed else []

    to_process_ids = set(pending_ids)
    if retry_failed:
        to_process_ids.update(failed_ids)

    to_process = [c for c in cases if c["case_id"] in to_process_ids]

    stats = index.get_stats(len(cases))
    logger.info(f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"已跳过 {stats['skipped']}, 待处理 {stats['pending']}, 失败 {stats['failed']}")
    logger.info(f"本次需处理: {len(pending_ids)} pending + {len(failed_ids)} failed = {len(to_process)} 个")
    logger.info(f"并发数: {max_workers} (滑动窗口模式)")

    if not to_process:
        logger.info("无待处理案例")
        return stats["done"], 0

    counter = {"done": 0, "fail": 0, "skipped": 0, "lock": threading.Lock()}
    total = len(to_process)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for case_data in to_process:
            future = executor.submit(
                _process_case_worker, case_data, cases_root, summaries_root, index, counter, total,
            )
            futures[future] = case_data.get("case_id", "unknown")

        for future in as_completed(futures):
            case_id = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"  任务异常: {case_id} | {e}")
                with counter["lock"]:
                    counter["fail"] += 1

    success = counter["done"]
    fail = counter["fail"]
    skipped = counter["skipped"]
    final_stats = index.get_stats(len(cases))
    logger.info(f"本次完成: 成功 {success}, 跳过(非基金相关) {skipped}, 失败 {fail}")
    logger.info(f"索引总计: {final_stats['done']} done / {final_stats['skipped']} skipped / {final_stats['total']} total")
    return success, fail


# ──────────────────────────── CLI 入口 ────────────────────────────

def main():
    global PROVIDER

    print("CSRC 案例结构化提取器 v1.0")
    print("=" * 60)

    # provider 选择
    print("\n请选择模型提供者:")
    print("1. 智谱AI (GLM, 串行模式)")
    print("2. 小米 (MiMo, 并发模式)")
    print("3. LongCat (并发模式, 需设置环境变量 LONGCAT_API_KEY)")
    print("4. DeepSeek (v4Flash, 并发模式)")
    print("5. OpenCode Zen (免费模型, 并发模式)")
    provider_choice = input("请输入选择 (1-5, 默认1): ").strip() or "1"
    if provider_choice == "2":
        PROVIDER = "mimo"
        logger.info("已选择MiMo模型提供者")
    elif provider_choice == "3":
        PROVIDER = "longcat"
        logger.info("已选择LongCat模型提供者")
    elif provider_choice == "4":
        PROVIDER = "deepseek"
        logger.info("已选择DeepSeek模型提供者")
    elif provider_choice == "5":
        PROVIDER = "zen"
        logger.info("已选择OpenCode Zen模型提供者")
        _AMAC._zen_pick_text_model()
    else:
        logger.info("已选择智谱AI模型提供者")

    # 并发数输入（MiMo、LongCat 与 DeepSeek 均支持并发）
    max_workers = 1
    if PROVIDER in ("mimo", "longcat", "deepseek", "zen"):
        workers_input = input(f"请输入并发数 ({MIN_CONCURRENCY}-{MAX_CONCURRENCY}, 默认{DEFAULT_CONCURRENCY}): ").strip()
        try:
            max_workers = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(workers_input))) if workers_input else DEFAULT_CONCURRENCY
        except ValueError:
            max_workers = DEFAULT_CONCURRENCY
        logger.info(f"{PROVIDER}并发数: {max_workers}")

    start_time = time.time()

    cases_root = CSRC_CASES_DIR
    summaries_root = CSRC_SUMMARIES_DIR

    # 模式选择
    print("\n请选择运行模式:")
    print("1. 批量提取所有未处理的案例 (含重试失败)")
    print("2. 提取单个案例 (输入case_id)")
    print("3. 仅重试之前失败的案例")

    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"

    try:
        if choice == "1":
            # 批量：pending + retry failed
            if PROVIDER in ("mimo", "longcat", "deepseek", "zen") and max_workers > 1:
                success, fail = run_batch_concurrent(
                    cases_root, summaries_root, max_workers=max_workers, retry_failed=True,
                )
            else:
                success, fail = run_batch(cases_root, summaries_root, retry_failed=True)

        elif choice == "2":
            # 单案例
            case_id = input("请输入case_id: ").strip()
            if not case_id:
                print("未输入case_id，退出。")
                return

            cases = scan_case_files(cases_root)
            found = next((c for c in cases if c.get("case_id") == case_id), None)
            if not found:
                print(f"未找到案例: {case_id}")
                return

            summaries_root.mkdir(parents=True, exist_ok=True)
            index = SummaryIndex(summaries_root)
            summary = process_single_case(found, cases_root, summaries_root, index)
            if summary is None:
                info = index.data["cases"].get(case_id, {})
                print(f"\n该案例经LLM判定为非基金相关，已跳过摘要生成。")
                print(f"理由: {info.get('reason', '')}")
            elif summary.extract_success:
                bureau = found.get("bureau", "")
                ct = found.get("case_type", "")
                print(f"\n摘要已保存至: {summaries_root / bureau / ct / f'{case_id}_summary.json'}")
            else:
                print("\n结构化提取失败。")
            return

        elif choice == "3":
            # 仅重试失败
            if PROVIDER in ("mimo", "longcat", "deepseek", "zen") and max_workers > 1:
                success, fail = run_batch_concurrent(
                    cases_root, summaries_root, max_workers=max_workers, retry_failed=True,
                )
            else:
                success, fail = run_batch(cases_root, summaries_root, retry_failed=True)

        else:
            print("无效选择，退出。")
            return

        elapsed = time.time() - start_time
        print(f"\n{'=' * 60}")
        print(f"全部完成: 成功 {success}, 失败 {fail}")
        print(f"输出目录: {summaries_root}")
        print(f"耗时: {elapsed:.1f}秒")

    except KeyboardInterrupt:
        logger.info("用户中断，进度已保存到索引")
    except Exception as e:
        logger.error(f"程序异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
