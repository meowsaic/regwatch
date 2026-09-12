"""结构化摘要提取服务。

对 AMAC 与 CSRC 两套案例原文调用大模型，提取违规类型、处罚措施、涉及基金、
法律依据等结构化字段并写入库。

要点：

1. **按需载入正文**：先只查索引状态（SQL），再逐条读入 ``raw_text``，
   避免把上 GB 的正文一次性读进内存。
2. **CSRC 基金相关性精判**：模型判定 ``fund_related=false`` 时不写摘要，
   只把案例标记为 ``skipped`` 并记录理由。
3. **断点续传**：``done`` / ``skipped`` 不再重复调用模型，``failed`` 可重试。

依赖全部通过构造函数注入，测试时替换 :class:`~regwatch.llm.LLMClientFactory`
即可离线验证整条流程。
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from ..clock import now_iso
from ..domain import (
    CaseRecord,
    CaseStatus,
    Dataset,
    SummaryRecord,
)
from ..llm import LLMClient, LLMClientFactory, parse_json_response
from ..logging_setup import get_logger
from ..prompts import AMAC_EXTRACT_PROMPT, CSRC_EXTRACT_PROMPT

logger = get_logger("summarize")

__all__ = [
    "DEFAULT_WORKERS",
    "INPUT_TRUNCATE",
    "SummarizationService",
    "SummarizeResult",
    "extract_structured",
]

#: 送入模型的正文截断长度（CSRC 行政处罚决定书更长）
INPUT_TRUNCATE: dict[str, int] = {Dataset.AMAC.value: 8000, Dataset.CSRC.value: 12000}

DEFAULT_WORKERS = 5
MAX_RETRIES = 3
MIN_INPUT_LEN = 50
MAX_REPORTED_ERRORS = 50
RETRY_BACKOFF_SECONDS = 3


@dataclass
class SummarizeResult:
    """一次摘要提取任务的汇总结果。"""

    dataset: str = ""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.success + self.skipped + self.failed

    @property
    def progress(self) -> float:
        return (self.handled / self.total) if self.total else 0.0

    def record_error(self, case_id: str, error: str) -> None:
        if len(self.errors) < MAX_REPORTED_ERRORS:
            self.errors.append({"case_id": case_id, "error": error})

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "total": self.total,
            "success": self.success,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
        }


def _coerce_bool(value: Any) -> bool | None:
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


def build_messages(case: CaseRecord) -> list[dict[str, str]]:
    """按数据集选择提示词并截断正文。"""
    truncate = INPUT_TRUNCATE.get(case.dataset.value, 8000)
    text = case.raw_text[:truncate]
    if case.dataset is Dataset.CSRC:
        prompt = CSRC_EXTRACT_PROMPT.format(text=text, case_type=case.case_type.value or "measure")
    else:
        prompt = AMAC_EXTRACT_PROMPT.format(text=text)
    return [{"role": "user", "content": prompt}]


def extract_structured(
    case: CaseRecord,
    client: LLMClient,
    *,
    max_retries: int = MAX_RETRIES,
) -> tuple[dict[str, Any] | None, str]:
    """调用模型提取结构化字段。

    Returns:
        ``(解析结果, 最后一次错误说明)``；成功时错误说明为空串。
    """
    if not case.raw_text or len(case.raw_text) <= MIN_INPUT_LEN:
        message = f"原文过短（{len(case.raw_text or '')} 字），无法提取"
        logger.warning(message)
        return None, message

    messages = build_messages(case)
    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("  [提取] 请求模型... (%d/%d)", attempt, max_retries)
            result = client.chat(messages, max_tokens=8000, temperature=0.1)
            payload = parse_json_response(result.best_text())
            if payload is not None:
                return payload, ""
            last_error = "模型响应中未包含可解析的 JSON 对象"
            logger.warning("  [提取] 第 %d 次未能从响应中解析出 JSON", attempt)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("  [提取] 第 %d 次调用失败: %s", attempt, exc)

        if attempt < max_retries:
            logger.info("  [提取] %d 秒后重试...", RETRY_BACKOFF_SECONDS * attempt)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return None, last_error


class SummarizationService:
    """结构化摘要提取用例。"""

    def __init__(
        self,
        cases: Any,
        summaries: Any,
        llm: LLMClientFactory,
        *,
        default_concurrency: int = DEFAULT_WORKERS,
        max_retries: int = MAX_RETRIES,
        on_data_changed: Callable[[], None] | None = None,
    ) -> None:
        self._cases = cases
        self._summaries = summaries
        self._llm = llm
        self._default_concurrency = max(1, int(default_concurrency))
        self._max_retries = max(1, int(max_retries))
        self._on_data_changed = on_data_changed

    # ── 候选扫描 ──

    def candidates(
        self,
        dataset: Dataset | str,
        *,
        retry_failed: bool = True,
        limit: int | None = None,
    ) -> list[CaseRecord]:
        """列出待处理案例（只读索引，不载入正文）。"""
        target = Dataset.parse(dataset)
        if target is None:
            raise ValueError(f"未知数据集：{dataset}")
        statuses: Sequence[CaseStatus] = (
            (CaseStatus.PENDING, CaseStatus.FAILED) if retry_failed else (CaseStatus.PENDING,)
        )
        result: list[CaseRecord] = []
        for case in self._cases.iter_cases(target, statuses):
            result.append(case)
            if limit and len(result) >= limit:
                break
        return result

    def iter_candidates(
        self,
        dataset: Dataset | str,
        *,
        retry_failed: bool = True,
        limit: int | None = None,
    ) -> Iterator[CaseRecord]:
        yield from self.candidates(dataset, retry_failed=retry_failed, limit=limit)

    # ── 单条处理 ──

    def summarize_one(self, case: CaseRecord, client: LLMClient | None = None) -> CaseStatus:
        """处理单条案例，返回最终状态。"""
        llm = client or self._llm.client(task="summarize")

        if len(case.raw_text or "") <= MIN_INPUT_LEN:
            self._cases.set_status(
                case.dataset, case.case_id, CaseStatus.FAILED, "原文为空或过短，无法结构化提取"
            )
            return CaseStatus.FAILED

        extracted, last_error = extract_structured(case, llm, max_retries=self._max_retries)
        if extracted is None:
            message = last_error or "模型未能返回有效结构化 JSON"
            self._cases.set_status(case.dataset, case.case_id, CaseStatus.FAILED, message)
            logger.warning("  [✗] %s | 提取失败：%s", case.case_id, message)
            return CaseStatus.FAILED

        # CSRC：先做基金相关性精判，非基金相关不写摘要
        if case.dataset is Dataset.CSRC:
            fund_related = _coerce_bool(extracted.get("fund_related"))
            if fund_related is False:
                reason = (
                    str(extracted.get("fund_relation_reason") or "").strip()
                    or "模型判定与基金业务无关"
                )
                self._cases.set_status(case.dataset, case.case_id, CaseStatus.SKIPPED, reason)
                logger.info("  [跳过] %s | 非基金相关：%s", case.case_id, reason)
                return CaseStatus.SKIPPED

        summary = SummaryRecord(
            dataset=case.dataset,
            case_id=case.case_id,
            entity_type=str(extracted.get("entity_type") or ""),
            violation_type=str(extracted.get("violation_type") or ""),
            punishment=str(extracted.get("punishment") or ""),
            punishment_date=str(extracted.get("punishment_date") or ""),
            involved_fund=str(extracted.get("involved_fund") or ""),
            violation_summary=str(extracted.get("violation_summary") or ""),
            legal_basis=str(extracted.get("legal_basis") or ""),
            penalty_amount=str(extracted.get("penalty_amount") or ""),
            market_ban=str(extracted.get("market_ban") or ""),
            extract_success=True,
            extract_time=now_iso(),
            llm_provider=llm.profile.id,
            llm_model=llm.profile.model,
        )
        if case.dataset is Dataset.AMAC:
            summary.punished_entity = str(extracted.get("punished_entity") or "") or (
                case.punished_entity
            )

        self._summaries.upsert(summary, status=CaseStatus.DONE)
        logger.info("  [✓] %s | %s | %s", case.case_id, summary.entity_type, summary.violation_type)
        return CaseStatus.DONE

    # ── 批量编排 ──

    def summarize(
        self,
        dataset: Dataset | str,
        *,
        workers: int | None = None,
        retry_failed: bool = True,
        limit: int | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> SummarizeResult:
        """批量提取结构化摘要。"""
        target = Dataset.parse(dataset)
        if target is None:
            raise ValueError(f"未知数据集：{dataset}")

        concurrency = max(1, int(workers or self._default_concurrency))
        cases = self.candidates(target, retry_failed=retry_failed, limit=limit)
        result = SummarizeResult(dataset=target.value, total=len(cases))
        if not cases:
            logger.info("%s 没有待处理的案例", target.label)
            return result

        client = self._llm.client(task="summarize")
        logger.info("%s 待处理 %d 条（并发 %d）", target.label, result.total, concurrency)

        lock = threading.Lock()
        completed = 0

        def worker(case: CaseRecord) -> tuple[str, CaseStatus, str]:
            try:
                return case.case_id, self.summarize_one(case, client), ""
            except Exception as exc:
                logger.error("  处理异常 %s: %s", case.case_id, exc)
                logger.debug(traceback.format_exc())
                self._cases.set_status(case.dataset, case.case_id, CaseStatus.FAILED, str(exc))
                return case.case_id, CaseStatus.FAILED, f"{type(exc).__name__}: {exc}"

        def record(case_id: str, status: CaseStatus, error: str) -> None:
            nonlocal completed
            with lock:
                completed += 1
                if status is CaseStatus.DONE:
                    result.success += 1
                elif status is CaseStatus.SKIPPED:
                    result.skipped += 1
                else:
                    result.failed += 1
                    result.record_error(case_id, error or "提取失败")
                if on_progress:
                    on_progress(completed, result.total, case_id)

        started = time.time()
        if concurrency <= 1:
            for case in cases:
                record(*worker(case))
        else:
            with ThreadPoolExecutor(
                max_workers=concurrency, thread_name_prefix="summarize"
            ) as pool:
                futures = {pool.submit(worker, case): case for case in cases}
                for future in as_completed(futures):
                    try:
                        record(*future.result())
                    except Exception as exc:  # pragma: no cover - worker 已兜底
                        record(futures[future].case_id, CaseStatus.FAILED, str(exc))

        self._touch()
        logger.info(
            "摘要提取完成：成功 %d，跳过（非基金相关）%d，失败 %d，耗时 %.1f 秒",
            result.success,
            result.skipped,
            result.failed,
            time.time() - started,
        )
        return result

    def _touch(self) -> None:
        """通知数据已变更（供上层缓存失效）。"""
        if self._on_data_changed is not None:
            self._on_data_changed()
