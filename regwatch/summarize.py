"""统一结构化摘要提取。

对 AMAC 与 CSRC 两套案例原文调用大模型，提取违规类型、处罚措施、涉及基金、
法律依据等结构化字段，并写入各自的摘要目录：

- AMAC：``AMAC/summaries/{case_id}_summary.json``（扁平）
- CSRC：``CSRC/summaries/{Bureau}/{case_type}/{case_id}_summary.json``

要点：

1. **两阶段处理**：先只扫描文件路径与索引状态（不读正文），再逐条按需载入，
   避免把上 GB 的 ``raw_text`` 一次性读进内存。
2. **CSRC 基金相关性精判**：模型判定 ``fund_related=false`` 时不生成摘要文件，
   仅把案例标记为 ``skipped`` 并记录理由，``_summary_index.json`` 是唯一事实来源。
3. **断点续传**：已 ``done`` / ``skipped`` 的案例不再重复调用模型，失败可重试。
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from .config import Config, get_config
from .llm import LLMClient, LLMError, get_llm, parse_json_response
from .logutil import get_logger
from .prompts import AMAC_EXTRACT_PROMPT, CSRC_EXTRACT_PROMPT
from .storage import (
    DATASET_AMAC,
    DATASET_CSRC,
    DATASETS,
    AmacSummary,
    CsrcSummary,
    SummaryIndex,
    amac_cases_dir,
    amac_summaries_dir,
    csrc_cases_dir,
    csrc_summaries_dir,
    read_json,
    write_json,
)

__all__ = [
    "INPUT_TRUNCATE",
    "SummarizeResult",
    "CaseRef",
    "scan_candidates",
    "extract_structured",
    "summarize",
    "summarize_one",
]

logger = get_logger("summarize")

#: 送入模型的正文截断长度（CSRC 行政处罚决定书更长）
INPUT_TRUNCATE: Dict[str, int] = {DATASET_AMAC: 8000, DATASET_CSRC: 12000}

MAX_RETRIES = 3
MIN_INPUT_LEN = 50
MIN_SUMMARY_INPUT_LEN = 100
DEFAULT_WORKERS = 5
MAX_REPORTED_ERRORS = 50

_FINISHED_STATUSES = ("done", "skipped")


@dataclass
class CaseRef:
    """待处理案例的轻量引用（不包含正文）。"""

    dataset: str
    case_id: str
    path: Path
    bureau: str = ""
    case_type: str = ""
    category: str = ""

    @property
    def summary_relative_path(self) -> str:
        """摘要文件相对于摘要根目录的路径（写入索引用）。"""
        if self.dataset == DATASET_CSRC and self.bureau and self.case_type:
            return f"{self.bureau}/{self.case_type}/{self.case_id}_summary.json"
        return f"{self.case_id}_summary.json"


@dataclass
class SummarizeResult:
    """一次摘要提取任务的汇总结果。"""

    dataset: str = ""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.success + self.skipped + self.failed

    @property
    def progress(self) -> float:
        return (self.handled / self.total) if self.total else 0.0

    def record_error(self, case_id: str, error: str) -> None:
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append({"case_id": case_id, "error": error})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "total": self.total,
            "success": self.success,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
        }


# ──────────────────────────── 候选扫描 ────────────────────────────


def _dataset_roots(dataset: str, config: Optional[Config]) -> Tuple[Path, Path]:
    if dataset == DATASET_AMAC:
        return amac_cases_dir(config), amac_summaries_dir(config)
    if dataset == DATASET_CSRC:
        return csrc_cases_dir(config), csrc_summaries_dir(config)
    raise ValueError(f"未知数据集：{dataset}（可选 {'、'.join(DATASETS)}）")


def scan_candidates(
    dataset: str,
    config: Optional[Config] = None,
    retry_failed: bool = True,
    limit: Optional[int] = None,
) -> List[CaseRef]:
    """扫描待处理案例，只读文件路径与索引状态，不载入正文。

    Args:
        dataset: ``amac`` 或 ``csrc``。
        config: 配置对象，默认使用全局单例。
        retry_failed: 是否把上次失败的案例重新纳入。
        limit: 最多返回多少条（用于抽样试跑）。

    Returns:
        按路径排序的 :class:`CaseRef` 列表。
    """
    cfg = config or get_config()
    cases_root, summaries_root = _dataset_roots(dataset, cfg)
    if not cases_root.exists():
        logger.warning("案例目录不存在：%s", cases_root)
        return []

    index = SummaryIndex(summaries_root)
    refs: List[CaseRef] = []
    for path in sorted(cases_root.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        case_id = path.stem
        status = index.status_of(case_id)
        if status in _FINISHED_STATUSES:
            continue
        if status == "failed" and not retry_failed:
            continue

        ref = CaseRef(dataset=dataset, case_id=case_id, path=path)
        try:
            relative = path.relative_to(cases_root).parts
        except ValueError:
            relative = (path.name,)
        if dataset == DATASET_CSRC and len(relative) >= 3:
            ref.bureau, ref.case_type = relative[0], relative[1]
        elif dataset == DATASET_AMAC and len(relative) >= 2:
            ref.category = "scfjg" if relative[0] == "institution" else "scfry"
        refs.append(ref)

        if limit and len(refs) >= limit:
            break
    return refs


# ──────────────────────────── 单案例提取 ────────────────────────────


def _build_messages(dataset: str, raw_text: str, case_type: str) -> List[Dict[str, str]]:
    truncate = INPUT_TRUNCATE.get(dataset, 8000)
    text = raw_text[:truncate] if len(raw_text) > truncate else raw_text
    if dataset == DATASET_CSRC:
        prompt = CSRC_EXTRACT_PROMPT.format(text=text, case_type=case_type or "measure")
    else:
        prompt = AMAC_EXTRACT_PROMPT.format(text=text)
    return [{"role": "user", "content": prompt}]


def extract_structured(
    raw_text: str,
    dataset: str,
    case_type: str = "",
    client: Optional[LLMClient] = None,
    config: Optional[Config] = None,
) -> Optional[Dict[str, Any]]:
    """调用大模型提取结构化字段，失败返回 ``None``。

    最多重试 ``MAX_RETRIES`` 次；模型返回内容为空时自动回退到推理内容。
    """
    if not raw_text or len(raw_text) <= MIN_INPUT_LEN:
        logger.warning("原文过短（%d 字），跳过提取", len(raw_text or ""))
        return None

    llm = client or get_llm(task="summarize", config=config)
    messages = _build_messages(dataset, raw_text, case_type)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("  [提取] 请求模型... (%d/%d)", attempt, MAX_RETRIES)
            result = llm.chat(messages, max_tokens=8000, temperature=0.1)
            payload = parse_json_response(result.best_text())
            if payload is not None:
                return payload
            logger.warning("  [提取] 第 %d 次未能从响应中解析出 JSON", attempt)
        except LLMError as exc:
            logger.warning("  [提取] 第 %d 次调用失败: %s", attempt, exc)
        except Exception as exc:  # noqa: BLE001 - 保证单条失败不影响整批
            logger.warning("  [提取] 第 %d 次异常: %s", attempt, exc)

        if attempt < MAX_RETRIES:
            wait = 3 * attempt
            logger.info("  [提取] %d 秒后重试...", wait)
            time.sleep(wait)

    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """把模型返回的 true/false 兼容成 Python 布尔值；无法判断时返回 ``None``。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _summary_path(ref: CaseRef, summaries_root: Path) -> Path:
    if ref.dataset == DATASET_CSRC and ref.bureau and ref.case_type:
        return summaries_root / ref.bureau / ref.case_type / f"{ref.case_id}_summary.json"
    return summaries_root / f"{ref.case_id}_summary.json"


def summarize_one(
    ref: CaseRef,
    config: Optional[Config] = None,
    index: Optional[SummaryIndex] = None,
    client: Optional[LLMClient] = None,
) -> str:
    """处理单条案例，返回 ``"done"`` / ``"skipped"`` / ``"failed"``。"""
    cfg = config or get_config()
    _, summaries_root = _dataset_roots(ref.dataset, cfg)
    summaries_root.mkdir(parents=True, exist_ok=True)
    idx = index or SummaryIndex(summaries_root)

    data = read_json(ref.path)
    if not data:
        idx.mark_failed(ref.case_id, "案例文件无法读取")
        return "failed"

    raw_text = str(data.get("raw_text") or "")
    if len(raw_text) <= MIN_INPUT_LEN:
        idx.mark_failed(ref.case_id, "原文为空或过短，无法结构化提取")
        return "failed"

    case_type = ref.case_type or str(data.get("case_type", ""))
    extracted = extract_structured(raw_text, ref.dataset, case_type, client=client, config=cfg)
    if extracted is None:
        idx.mark_failed(ref.case_id, "模型未能返回有效结构化 JSON")
        logger.warning("  [✗] %s | 提取失败", ref.case_id)
        return "failed"

    # CSRC：先做基金相关性精判，非基金相关不生成摘要文件
    if ref.dataset == DATASET_CSRC:
        fund_related = _coerce_bool(extracted.get("fund_related"))
        if fund_related is False:
            reason = str(extracted.get("fund_relation_reason", "") or "").strip() or "模型判定与基金业务无关"
            idx.mark_skipped(ref.case_id, reason)
            logger.info("  [跳过] %s | 非基金相关：%s", ref.case_id, reason)
            return "skipped"

    profile = (client or get_llm(task="summarize", config=cfg)).profile
    llm_model = profile.model

    if ref.dataset == DATASET_AMAC:
        summary: Any = AmacSummary(
            case_id=ref.case_id,
            source_url=str(data.get("source_url", "")),
            category=str(data.get("category", "")) or ref.category,
            title=str(data.get("title", "")),
            date=str(data.get("date", "")),
            punished_entity=str(extracted.get("punished_entity", ""))
            or str(data.get("punished_entity", "")),
            entity_type=str(extracted.get("entity_type", "")),
            org_type=str(data.get("org_type", "")),
            violation_type=str(extracted.get("violation_type", "")),
            punishment=str(extracted.get("punishment", "")),
            punishment_date=str(extracted.get("punishment_date", "")),
            involved_fund=str(extracted.get("involved_fund", "")),
            violation_summary=str(extracted.get("violation_summary", "")),
            legal_basis=str(extracted.get("legal_basis", "")),
            extract_success=True,
            extract_time=_now(),
        )
    else:
        summary = CsrcSummary(
            case_id=ref.case_id,
            source_url=str(data.get("source_url", "")),
            case_type=case_type,
            bureau=str(data.get("bureau", "")) or ref.bureau,
            title=str(data.get("title", "")),
            date=str(data.get("date", "")),
            document_number=str(data.get("document_number", "")),
            punished_entities=str(data.get("punished_entities", "")),
            is_fund_related=bool(data.get("is_fund_related", False)),
            fund_evidence=str(data.get("fund_evidence", "")),
            pdf_url=str(data.get("pdf_url", "")),
            entity_type=str(extracted.get("entity_type", "")),
            violation_type=str(extracted.get("violation_type", "")),
            punishment=str(extracted.get("punishment", "")),
            involved_fund=str(extracted.get("involved_fund", "")),
            violation_summary=str(extracted.get("violation_summary", "")),
            legal_basis=str(extracted.get("legal_basis", "")),
            penalty_amount=str(extracted.get("penalty_amount", "")),
            market_ban=str(extracted.get("market_ban", "")),
            extract_success=True,
            extract_time=_now(),
            llm_provider=profile.id,
            llm_model=llm_model,
        )

    write_json(_summary_path(ref, summaries_root), asdict(summary))
    idx.mark_done(ref.case_id, ref.summary_relative_path)
    logger.info("  [✓] %s | %s | %s", ref.case_id, summary.entity_type, summary.violation_type)
    return "done"


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


# ──────────────────────────── 批量编排 ────────────────────────────


def summarize(
    dataset: str,
    config: Optional[Config] = None,
    workers: Optional[int] = None,
    retry_failed: bool = True,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> SummarizeResult:
    """批量提取结构化摘要。

    Args:
        dataset: ``amac`` 或 ``csrc``。
        config: 配置对象，默认使用全局单例。
        workers: 并发线程数；``1`` 表示串行。默认取配置中的 ``summarize`` 值。
        retry_failed: 是否重试上次失败的案例。
        limit: 最多处理多少条（用于抽样试跑）。
        on_progress: 进度回调 ``(已处理, 总数, 案例ID)``。

    Returns:
        :class:`SummarizeResult`。
    """
    cfg = config or get_config()
    workers = max(1, int(workers if workers is not None else cfg.concurrency("summarize", DEFAULT_WORKERS)))

    refs = scan_candidates(dataset, cfg, retry_failed=retry_failed, limit=limit)
    result = SummarizeResult(dataset=dataset, total=len(refs))

    _, summaries_root = _dataset_roots(dataset, cfg)
    summaries_root.mkdir(parents=True, exist_ok=True)
    index = SummaryIndex(summaries_root)
    stats = index.get_stats()
    logger.info(
        "摘要索引状态：已完成 %d，已跳过 %d，失败 %d | 本次待处理 %d 条（并发 %d）",
        stats["done"], stats["skipped"], stats["failed"], result.total, workers,
    )
    if not refs:
        logger.info("没有待处理的案例")
        return result

    client = get_llm(task="summarize", config=cfg)
    counter_lock = threading.Lock()
    completed = 0

    def _worker(ref: CaseRef) -> Tuple[CaseRef, str, str]:
        status = "failed"
        error = ""
        try:
            status = summarize_one(ref, config=cfg, index=index, client=client)
        except Exception as exc:  # noqa: BLE001 - 单条异常不中断整批
            error = f"{type(exc).__name__}: {exc}"
            logger.error("  处理异常 %s: %s", ref.case_id, error)
            logger.debug(traceback.format_exc())
            try:
                index.mark_failed(ref.case_id, error)
            except Exception:  # pragma: no cover - 索引写入失败无需二次报错
                pass
        return ref, status, error

    def _record(ref: CaseRef, status: str, error: str) -> None:
        nonlocal completed
        with counter_lock:
            completed += 1
            if status == "done":
                result.success += 1
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
                result.record_error(ref.case_id, error or "提取失败")
            if on_progress:
                on_progress(completed, result.total, ref.case_id)

    started = time.time()
    if workers <= 1:
        for ref in refs:
            ref_obj, status, error = _worker(ref)
            _record(ref_obj, status, error)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="summarize") as executor:
            futures = {executor.submit(_worker, ref): ref for ref in refs}
            for future in as_completed(futures):
                try:
                    ref_obj, status, error = future.result()
                except Exception as exc:  # noqa: BLE001 - 兜底
                    ref_obj, status, error = futures[future], "failed", str(exc)
                _record(ref_obj, status, error)

    elapsed = time.time() - started
    logger.info(
        "摘要提取完成：成功 %d，跳过（非基金相关）%d，失败 %d，耗时 %.1f 秒",
        result.success, result.skipped, result.failed, elapsed,
    )
    return result
