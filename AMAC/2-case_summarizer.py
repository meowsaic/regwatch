"""
AMAC纪律处分案例结构化提取器 (case_summarizer.py)
功能：读取 case_fetcher 产出的案例原文JSON，使用文本LLM提取结构化信息，
     保存为摘要JSON，支持增量处理和失败重试。
"""

import os
import json
import time
import logging
import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict, field
from concurrent.futures import ThreadPoolExecutor, as_completed

PROVIDER = "glm"

PROVIDER_CONFIG = {
    "glm": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_default": "",
        "vision_model": "glm-4.6v-flash",
        "text_model": "glm-4.7-flash",
    },
    "mimo": {
        "api_key_env": "MIMO_API_KEY",
        "api_key_default": "",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "vision_model": "mimo-v2.5",
        "text_model": "mimo-v2.5-pro",
    },
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_default": "",
        "base_url": "https://api.deepseek.com",
        "text_model": "deepseek-v4-flash",
    },
    "zen": {
        "api_key_env": "OPENCODE_ZEN_API_KEY",
        "api_key_default": "",
        "base_url": "https://opencode.ai/zen/v1",
        "vision_model": "mimo-v2.5-free",
        "text_model": "x-preview-f-free",
        "text_models": {
            "x-preview-f-free": "Ox Alpha Free",
            "hy3-free": "Hy3 Free",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
        },
    },
}

DELAY_BETWEEN_API_CALLS = 1.5
MAX_RETRIES = 3

EXTRACT_PROMPT = """你是一名专业的基金监管法规分析师。请从以下纪律处分决定书中提取结构化信息，严格按JSON格式返回。

需要提取的字段：
- punished_entity: 受处分机构或人员的全称
- entity_type: "机构" 或 "个人"
- violation_type: 违规类型，从以下选项中选择最匹配的（如有多个违规类型，用顿号分隔）：

  【募集行为类】
  · 违规募集：向不合格投资者募集、承诺保本保收益、适当性管理不到位、违规外包销售、宣传推介不实

  【登记备案类】
  · 未按规定备案：基金产品未备案、未及时更新备案信息、重大事项未报告
  · 登记信息失实：虚假填报登记备案信息、高管人员信息不实、未及时提交重大事项变更

  【投资运作类】
  · 违规投资运作：超范围投资、资金池运作、违规加杠杆、违反合同约定投资、开展通道业务
  · 挪用基金财产：将基金财产用于非约定用途、侵占基金财产
  · 违规关联交易：未披露关联交易、利益输送、关联交易价格不公允
  · 未按规定托管：未设托管机构、未选合格托管机构
  · 未按规定估值：估值方法不当、估值不及时

  【管理人义务类】
  · 非专业化运营：兼营与私募基金管理无关的业务（如民间借贷、担保、保理、小额贷款等）、兼营存在利益冲突的业务
  · 未尽勤勉尽责义务：未谨慎勤勉履行管理人职责、尽调不充分、未核实投资者与投资标的关联关系、疏于管理基金财产

  【内部治理类】
  · 内控缺失：内控制度不健全或形同虚设、合规风控体系无效、岗位设置混乱、文件资料保管不善
  · 人员与场所违规：办公场所不独立或不合规、人员配置不足、高管任职不符合要求、合规风控人员兼任冲突职务
  · 未持续符合登记条件：管理人不再具备登记条件（人员、场所、资本金等基本条件缺失）、管理人失联

  【信息披露与自律类】
  · 信息披露违规：未按约定披露基金信息、虚假披露、选择性披露、监管信息报送失准
  · 未配合自律管理：拒不配合协会检查、提供虚假材料、逾期不整改

  · 其他：以上类型均不匹配时使用

分类要点：
1. "非专业化运营"专指管理人兼营与私募基金无关或存在利益冲突的其他业务（如民间借贷、担保、保理等），不用于人员不足或场所不独立的情况
2. "未尽勤勉尽责义务"专指管理人未谨慎勤勉履行管理职责（如尽调不充分、疏于管理），不用于内控制度缺失
3. "人员与场所违规"用于办公场所不独立、人员不足、高管任职违规等具体条件不符合要求的情况
4. "未持续符合登记条件"用于管理人因基本条件缺失不再符合登记要求（如失联、资本金不足等综合情况）
5. "内控缺失"仅用于内控制度本身不健全或形同虚设的情况
6. "登记信息失实"用于虚假填报或未及时变更登记信息的情况，与"未按规定备案"区分

- punishment: 处分措施的具体内容，如"公开谴责""暂停受理备案""撤销管理人登记"等
- punishment_date: 处罚日期，格式YYYY-MM-DD，如无法确定则填空字符串
- involved_fund: 涉及的基金或产品名称，多个用顿号分隔，如无则填空字符串
- violation_summary: 违规事实摘要，100-200字，客观概括
- legal_basis: 处罚依据的法规条款，如《私募投资基金监督管理暂行办法》第XX条，多条用顿号分隔

请只返回JSON，不要有任何其他文字。如果某个字段无法从文中提取，填空字符串。

---
案例原文：

{text}"""


# ──────────────────────────── 日志 ────────────────────────────

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("case_summarizer")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


logger = setup_logging()


# ──────────────────────────── 数据模型 ────────────────────────────

@dataclass
class CaseSummary:
    case_id: str
    source_url: str
    category: str
    title: str
    date: str
    punished_entity: str = ""
    entity_type: str = ""
    org_type: str = ""
    violation_type: str = ""
    punishment: str = ""
    punishment_date: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    extract_success: bool = False
    error: str = ""
    extract_time: str = ""


_client_cache = {}


def get_client(provider=None):
    if provider is None:
        provider = PROVIDER
    if provider in _client_cache:
        return _client_cache[provider]

    config = PROVIDER_CONFIG[provider]
    api_key = os.environ.get(config["api_key_env"], config.get("api_key_default", ""))
    if not api_key:
        api_key = input(f"未设置环境变量 {config['api_key_env']}，请输入 API Key: ").strip()
        if api_key:
            os.environ[config["api_key_env"]] = api_key
        else:
            raise ValueError(f"未提供 {config['api_key_env']}，无法初始化客户端")
    client = None

    if provider == "mimo":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"MiMo客户端已初始化，模型: {config['text_model']}")
    elif provider == "deepseek":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"DeepSeek客户端已初始化，模型: {config['text_model']}")
    elif provider == "zen":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"OpenCode Zen客户端已初始化，模型: {config['text_model']}")
    else:
        try:
            from zai import ZhipuAiClient
            client = ZhipuAiClient(api_key=api_key)
            logger.info(f"智谱AI客户端已初始化 (zai.ZhipuAiClient)")
        except ImportError:
            pass
        if client is None:
            try:
                from zhipuai import ZhipuAI
                client = ZhipuAI(api_key=api_key)
                logger.info(f"智谱AI客户端已初始化 (zhipuai.ZhipuAI)")
            except ImportError:
                pass
        if client is None:
            raise ImportError("无法导入智谱AI客户端库，请安装: pip install zai-sdk 或 pip install zhipuai")

    _client_cache[provider] = client
    return client


def llm_chat(messages, model=None, max_tokens=8000, temperature=0.1,
             thinking=None, provider=None, stream=False):
    if provider is None:
        provider = PROVIDER
    if model is None:
        model = PROVIDER_CONFIG[provider]["text_model"]

    client = get_client(provider)

    if provider == "mimo":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": stream,
        }
    elif provider == "deepseek":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            # "thinking": {"type": "enabled"},
            # "reasoning_effort": "high",
        }
    elif provider == "zen":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
    else:
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if thinking:
            kwargs["thinking"] = thinking

    return client.chat.completions.create(**kwargs)


# ──────────────────────────── 摘要索引 ────────────────────────────

class SummaryIndex:
    """
    持久化的摘要处理索引，记录哪些案例已完成结构化提取。
    文件结构: {output_dir}/_summary_index.json
    {
        "last_updated": "2026-05-25T14:30:00",
        "cases": {
            "P020260109619175763031": {
                "status": "done",
                "summary_file": "P020260109619175763031_summary.json",
                "extract_time": "2026-05-25T14:30:00"
            },
            "20200109_21962": {
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
        info = self.data["cases"].get(case_id, {})
        return info.get("status") == "done"

    def mark_done(self, case_id: str, summary_file: str):
        with self._lock:
            self.data["cases"][case_id] = {
                "status": "done",
                "summary_file": summary_file,
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

    def get_stats(self, total_count: int) -> Dict[str, int]:
        done = sum(1 for info in self.data["cases"].values() if info.get("status") == "done")
        failed = sum(1 for info in self.data["cases"].values() if info.get("status") == "failed")
        return {"total": total_count, "done": done, "pending": total_count - done - failed, "failed": failed}


# ──────────────────────────── 结构化提取 ────────────────────────────

def extract_structured_info(raw_text: str) -> Optional[Dict]:
    """调用文本LLM从原文提取结构化信息，返回解析后的字典或None"""
    if not raw_text or len(raw_text) < 50:
        logger.warning("原文过短，跳过提取")
        return None

    truncated = raw_text[:8000] if len(raw_text) > 8000 else raw_text
    prompt = EXTRACT_PROMPT.format(text=truncated)

    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"  [提取] 请求模型... (尝试 {attempt + 1}/{MAX_RETRIES})")

            response = llm_chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=8000,
                temperature=0.1,
                thinking={"type": "disabled"} if PROVIDER == "glm" else None,
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
                logger.info(f"  [提取] 成功，字段数: {len(result)}")
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


def _parse_json_from_response(content: str) -> Optional[Dict]:
    """从模型响应中解析JSON，兼容markdown代码块包裹的情况"""
    text = content.strip()

    if text.startswith("```"):
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_block:
                    break
                in_block = True
                continue
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)

    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    import re
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None


# ──────────────────────────── 单案例处理 ────────────────────────────

def process_single_case(
    case_data: Dict,
    output_dir: Path,
    index: SummaryIndex,
) -> Optional[CaseSummary]:
    """处理单个案例的结构化提取"""

    case_id = case_data.get("case_id", "")
    summary_path = output_dir / f"{case_id}_summary.json"

    if index.is_done(case_id) and summary_path.exists():
        logger.info(f"  [跳过] 已完成提取: {case_id}")
        with open(summary_path, "r", encoding="utf-8") as f:
            return CaseSummary(**json.load(f))

    if summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as f:
            existing = CaseSummary(**json.load(f))
        if existing.extract_success:
            logger.info(f"  [跳过] 已完成提取: {case_id}")
            return existing
        else:
            logger.info(f"  [重试] 删除之前失败的摘要: {case_id} ({existing.error})")
            summary_path.unlink()

    raw_text = case_data.get("raw_text", "")

    extracted = extract_structured_info(raw_text)

    summary = CaseSummary(
        case_id=case_id,
        source_url=case_data.get("source_url", ""),
        category=case_data.get("category", ""),
        title=case_data.get("title", ""),
        date=case_data.get("date", ""),
        punished_entity=case_data.get("punished_entity", ""),
        org_type=case_data.get("org_type", ""),
        extract_time=datetime.now().isoformat(),
    )

    if extracted is not None:
        llm_entity = extracted.get("punished_entity", "")
        if llm_entity:
            summary.punished_entity = llm_entity
        summary.entity_type = extracted.get("entity_type", "")
        summary.violation_type = extracted.get("violation_type", "")
        summary.punishment = extracted.get("punishment", "")
        summary.punishment_date = extracted.get("punishment_date", "")
        summary.involved_fund = extracted.get("involved_fund", "")
        summary.violation_summary = extracted.get("violation_summary", "")
        summary.legal_basis = extracted.get("legal_basis", "")
        summary.extract_success = True

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, ensure_ascii=False, indent=2)

        index.mark_done(case_id, summary_path.name)
        logger.info(f"  [✓] {case_id} | {summary.punished_entity} | {summary.violation_type}")
    else:
        summary.extract_success = False
        summary.error = "模型未能返回有效结构化JSON"

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(asdict(summary), f, ensure_ascii=False, indent=2)

        index.mark_failed(case_id, summary.error)
        logger.warning(f"  [✗] {case_id} | 提取失败")

    return summary


# ──────────────────────────── 扫描与批量处理 ────────────────────────────

def scan_case_files(cases_dir: Path) -> List[Dict]:
    """扫描cases目录下所有ocr_success的案例JSON，返回按日期排序的列表"""
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
            if data.get("ocr_success", False) and data.get("raw_text", ""):
                cases.append(data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"读取案例文件失败 {json_path.name}: {e}")

    cases.sort(key=lambda x: x.get("date", ""), reverse=False)
    logger.info(f"扫描到 {len(cases)} 个有效案例 (ocr_success=True)")
    return cases


def run_batch(
    cases_dir: Path,
    output_dir: Path,
    retry_failed: bool = True,
) -> Tuple[int, int]:
    """批量处理所有案例的结构化提取"""
    cases = scan_case_files(cases_dir)
    if not cases:
        logger.info("未找到可处理的案例")
        return 0, 0

    output_dir.mkdir(parents=True, exist_ok=True)
    index = SummaryIndex(output_dir)

    all_ids = [c["case_id"] for c in cases]
    pending_ids = index.get_pending_cases(all_ids)
    failed_ids = index.get_failed_cases() if retry_failed else []

    to_process_ids = set(pending_ids)
    if retry_failed:
        to_process_ids.update(failed_ids)

    to_process = [c for c in cases if c["case_id"] in to_process_ids]

    stats = index.get_stats(len(cases))
    logger.info(f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"待处理 {stats['pending']}, 失败 {stats['failed']}")
    logger.info(f"本次需处理: {len(pending_ids)} pending + {len(failed_ids)} failed = {len(to_process)} 个")

    if not to_process:
        logger.info("无待处理案例")
        return stats["done"], 0

    success = 0
    fail = 0

    for i, case_data in enumerate(to_process, 1):
        case_id = case_data.get("case_id", "unknown")
        title = case_data.get("title", "")
        logger.info(f"[{i}/{len(to_process)}] {case_id} | {title}")

        try:
            summary = process_single_case(case_data, output_dir, index)
            if summary and summary.extract_success:
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
    logger.info(f"本次完成: 成功 {success}, 失败 {fail}")
    logger.info(f"索引总计: {final_stats['done']} done / {final_stats['total']} total")
    return success, fail


def _process_case_worker(case_data: Dict, output_dir: Path, index: SummaryIndex,
                         counter: Dict, total: int) -> Optional[CaseSummary]:
    """并发工作线程：处理单个案例，更新共享计数器"""
    case_id = case_data.get("case_id", "unknown")
    title = case_data.get("title", "")

    with counter["lock"]:
        seq = counter["done"] + counter["fail"] + 1
        logger.info(f"[{seq}/{total}] 开始处理: {case_id} | {title}")

    try:
        summary = process_single_case(case_data, output_dir, index)
        with counter["lock"]:
            if summary and summary.extract_success:
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
    cases_dir: Path,
    output_dir: Path,
    max_workers: int = 5,
    retry_failed: bool = True,
) -> Tuple[int, int]:
    """并发批量处理所有案例的结构化提取（滑动窗口模式）

    使用 ThreadPoolExecutor 实现：始终保持 max_workers 个任务在跑，
    某个任务完成后立即从队列中取下一个任务启动，无需等待整批完成。
    """
    cases = scan_case_files(cases_dir)
    if not cases:
        logger.info("未找到可处理的案例")
        return 0, 0

    output_dir.mkdir(parents=True, exist_ok=True)
    index = SummaryIndex(output_dir)

    all_ids = [c["case_id"] for c in cases]
    pending_ids = index.get_pending_cases(all_ids)
    failed_ids = index.get_failed_cases() if retry_failed else []

    to_process_ids = set(pending_ids)
    if retry_failed:
        to_process_ids.update(failed_ids)

    to_process = [c for c in cases if c["case_id"] in to_process_ids]

    stats = index.get_stats(len(cases))
    logger.info(f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"待处理 {stats['pending']}, 失败 {stats['failed']}")
    logger.info(f"本次需处理: {len(pending_ids)} pending + {len(failed_ids)} failed = {len(to_process)} 个")
    logger.info(f"并发数: {max_workers} (滑动窗口模式)")

    if not to_process:
        logger.info("无待处理案例")
        return stats["done"], 0

    counter = {"done": 0, "fail": 0, "lock": threading.Lock()}
    total = len(to_process)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for case_data in to_process:
            future = executor.submit(
                _process_case_worker, case_data, output_dir, index, counter, total,
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
    final_stats = index.get_stats(len(cases))
    logger.info(f"本次完成: 成功 {success}, 失败 {fail}")
    logger.info(f"索引总计: {final_stats['done']} done / {final_stats['total']} total")
    return success, fail


def _zen_pick_text_model():
    """选择 OpenCode Zen 文本模型（Ox Alpha Free / Hy3 Free / DeepSeek V4 Flash）"""
    tm_cfg = PROVIDER_CONFIG["zen"]
    options = list(tm_cfg["text_models"].items())
    print("\n请选择OpenCode Zen文本模型:")
    for i, (mid, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    choice = input(f"请输入选择 (1-{len(options)}, 默认1): ").strip() or "1"
    try:
        idx = max(0, min(len(options) - 1, int(choice) - 1))
    except ValueError:
        idx = 0
    tm_cfg["text_model"] = options[idx][0]
    logger.info(f"已选择文本模型: {options[idx][1]} ({options[idx][0]})")


# ──────────────────────────── 主函数 ────────────────────────────

def main():
    global PROVIDER

    print("AMAC纪律处分案例结构化提取器 v1.2")
    print("=" * 60)

    print("\n请选择模型提供者:")
    print("1. 智谱AI (GLM)")
    print("2. 小米 (MiMo)")
    print("3. DeepSeek (v4Flash)")
    print("4. OpenCode Zen (免费模型)")
    provider_choice = input("请输入选择 (1-4, 默认1): ").strip() or "1"
    if provider_choice == "2":
        PROVIDER = "mimo"
        logger.info("已选择MiMo模型提供者")
    elif provider_choice == "3":
        PROVIDER = "deepseek"
        logger.info("已选择DeepSeek模型提供者")
    elif provider_choice == "4":
        PROVIDER = "zen"
        logger.info("已选择OpenCode Zen模型提供者")
        _zen_pick_text_model()
    else:
        logger.info("已选择智谱AI模型提供者")

    max_workers = 1
    if PROVIDER == "mimo":
        workers_input = input("请输入并发数 (1-50, 默认5): ").strip()
        try:
            max_workers = max(1, min(50, int(workers_input))) if workers_input else 5
        except ValueError:
            max_workers = 5
        logger.info(f"MiMo并发数: {max_workers}")
    elif PROVIDER == "deepseek":
        workers_input = input("请输入并发数 (1-50, 默认5): ").strip()
        try:
            max_workers = max(1, min(50, int(workers_input))) if workers_input else 5
        except ValueError:
            max_workers = 5
        logger.info(f"DeepSeek并发数: {max_workers}")
    elif PROVIDER == "zen":
        workers_input = input("请输入并发数 (1-50, 默认5): ").strip()
        try:
            max_workers = max(1, min(50, int(workers_input))) if workers_input else 5
        except ValueError:
            max_workers = 5
        logger.info(f"OpenCode Zen并发数: {max_workers}")

    start_time = time.time()

    script_dir = Path(__file__).parent.resolve()
    cases_dir = script_dir / "cases"
    output_dir = script_dir / "summaries"

    print("\n请选择运行模式:")
    print("1. 批量提取所有未处理的案例")
    print("2. 提取单个案例 (输入case_id)")
    print("3. 仅重试之前失败的案例")

    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"

    try:
        if choice == "1":
            if PROVIDER in ("mimo", "zen") and max_workers > 1:
                success, fail = run_batch_concurrent(
                    cases_dir, output_dir, max_workers=max_workers, retry_failed=True,
                )
            else:
                success, fail = run_batch(cases_dir, output_dir, retry_failed=True)

        elif choice == "2":
            case_id = input("请输入case_id: ").strip()
            if not case_id:
                print("未输入case_id，退出。")
                return

            found = None
            for json_path in cases_dir.rglob(f"{case_id}.json"):
                if json_path.name.startswith("_"):
                    continue
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        found = json.load(f)
                    break
                except (json.JSONDecodeError, IOError):
                    continue

            if not found:
                print(f"未找到案例: {case_id}")
                return

            if not found.get("ocr_success", False):
                print(f"案例 {case_id} 原文提取失败，无法进行结构化提取")
                return

            output_dir.mkdir(parents=True, exist_ok=True)
            index = SummaryIndex(output_dir)
            summary = process_single_case(found, output_dir, index)
            if summary and summary.extract_success:
                print(f"\n摘要已保存至: {output_dir / f'{case_id}_summary.json'}")
            else:
                print("\n结构化提取失败。")
            return

        elif choice == "3":
            if PROVIDER in ("mimo", "zen") and max_workers > 1:
                success, fail = run_batch_concurrent(
                    cases_dir, output_dir, max_workers=max_workers, retry_failed=True,
                )
            else:
                success, fail = run_batch(cases_dir, output_dir, retry_failed=True)

        else:
            print("无效选择，退出。")
            return

        elapsed = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"全部完成: 成功 {success}, 失败 {fail}")
        print(f"输出目录: {output_dir}")
        print(f"耗时: {elapsed:.1f}秒")

    except KeyboardInterrupt:
        logger.info("\n用户中断，正在退出... (进度已保存到索引)")
    except Exception as e:
        logger.error(f"程序异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
