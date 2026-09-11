"""后台任务编排。

为网页端与命令行提供同一套任务执行通道：

- 任务在**独立线程**中执行，不阻塞 Streamlit 主循环；
- 通过 :func:`regwatch.logutil.bind_task` 把线程日志路由到各自缓冲，网页端可实时读取；
- 进度经 ``on_progress`` 回调回写任务记录，并作为**协作式取消**的检查点；
- 同类型任务不允许并发启动，避免两个抓取任务同时写同一份索引。

任务状态机：``pending → running → success | failed | cancelled``。
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .datamodels import TaskRecord, TaskStatus
from .logutil import bind_task, configure_logging, get_logger, get_task_log_router
from .storage import DATASET_AMAC, DATASET_CSRC, DATASETS

__all__ = [
    "JOB_KINDS",
    "JOB_LABELS",
    "JobCancelled",
    "JobError",
    "JobManager",
    "get_job_manager",
    "reset_job_manager",
    "run_task",
]

logger = get_logger("jobs")

#: 全部任务类型及其中文名
JOB_KINDS: tuple[str, ...] = (
    "fetch_amac",
    "fetch_csrc",
    "fetch_monthly",
    "summarize",
    "report",
    "org_type",
)

JOB_LABELS: dict[str, str] = {
    "fetch_amac": "AMAC 案例抓取",
    "fetch_csrc": "CSRC 案例抓取",
    "fetch_monthly": "AMAC 月度公告下载",
    "summarize": "结构化摘要提取",
    "report": "报告生成",
    "org_type": "机构类型回填",
}

#: 每类任务在网页端需要的参数说明（label, key, default, hint）
JOB_PARAMS: dict[str, tuple[tuple[str, str, Any, str], ...]] = {
    "fetch_amac": (
        ("起始日期", "start_date", "", "YYYY-MM-DD，留空=上一季度"),
        ("结束日期", "end_date", "", "YYYY-MM-DD，留空=上一季度"),
        ("抓取分类", "categories", "all", "all / Institution / Personnel"),
    ),
    "fetch_csrc": (
        ("起始日期", "start_date", "", "YYYY-MM-DD，留空=2022-01-01"),
        ("结束日期", "end_date", "", "YYYY-MM-DD，留空=今天"),
        ("来源局", "bureaus", "all", "逗号分隔英文标识，all=全部 37 个"),
        ("案例类型", "case_types", "all", "all / penalty / measure"),
    ),
    "fetch_monthly": (),
    "summarize": (
        ("数据集", "dataset", "all", "all / amac / csrc"),
        ("并发数", "workers", 0, "0=使用配置默认值"),
        ("重试失败", "retry_failed", True, "是否重试上次失败的案例"),
    ),
    "report": (
        ("数据集", "dataset", "amac", "amac / csrc"),
        ("起始日期", "start_date", "", "YYYY-MM-DD，留空=全量"),
        ("结束日期", "end_date", "", "YYYY-MM-DD，留空=全量"),
        ("模型建议", "use_llm", False, "是否调用模型撰写合规建议"),
    ),
    "org_type": (
        ("仅试算", "dry_run", False, "只统计不写盘"),
        ("抽样上限", "limit", 0, "0=全部处理"),
    ),
}


class JobError(RuntimeError):
    """任务参数不合法或执行失败。"""


class JobCancelled(Exception):
    """任务被用户取消（内部信号，用于打断执行线程）。"""

    def __init__(self, message: str = "任务已被取消") -> None:
        super().__init__(message)
        self.message = message


ProgressHook = Callable[[int, int, str], None]


# ──────────────────────────── 参数解析 ────────────────────────────


def _parse_date(value: str | None, field_name: str) -> date | None:
    from datetime import datetime as _dt

    text = (value or "").strip()
    if not text:
        return None
    try:
        return _dt.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise JobError(f"{field_name} 日期格式应为 YYYY-MM-DD，收到：{text!r}") from exc


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


# ──────────────────────────── 任务实现 ────────────────────────────


def run_task(
    kind: str,
    params: dict[str, Any],
    on_progress: ProgressHook | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """按任务类型分发执行，返回可 JSON 序列化的结果。

    Args:
        kind: :data:`JOB_KINDS` 之一。
        params: 任务参数（见 :data:`JOB_PARAMS`）。
        on_progress: 进度回调 ``(已处理, 总数, 说明)``。
        config: 可选的 :class:`~regwatch.config.Config`。

    Raises:
        JobError: 参数不合法。
        JobCancelled: 任务被取消。
    """
    params = dict(params or {})
    if kind not in JOB_KINDS:
        raise JobError(f"未知任务类型：{kind}（可选 {'、'.join(JOB_KINDS)}）")

    if kind == "fetch_amac":
        from .sources import amac

        categories = params.get("categories") or "all"
        selected = None if categories in ("", "all") else _split_csv(categories)
        amac_result = amac.fetch(
            start_date=_parse_date(params.get("start_date"), "起始日期"),
            end_date=_parse_date(params.get("end_date"), "结束日期"),
            categories=selected,
            config=config,
            on_progress=on_progress,
        )
        return amac_result.to_dict()

    if kind == "fetch_csrc":
        from .sources import csrc

        bureaus_param = params.get("bureaus") or "all"
        types_param = params.get("case_types") or "all"
        csrc_result = csrc.fetch(
            start_date=_parse_date(params.get("start_date"), "起始日期"),
            end_date=_parse_date(params.get("end_date"), "结束日期"),
            bureaus=None if bureaus_param in ("", "all") else _split_csv(bureaus_param),
            case_types=None if types_param in ("", "all") else _split_csv(types_param),
            concurrency=int(params.get("concurrency") or 0) or None,
            config=config,
            on_progress=on_progress,
        )
        return csrc_result.to_dict()

    if kind == "fetch_monthly":
        from .sources import amac_monthly

        summary = amac_monthly.download_last_month(config=config)
        return summary

    if kind == "summarize":
        from . import summarize

        dataset = (params.get("dataset") or "all").strip().lower()
        datasets = [item for item in DATASETS if dataset in ("", "all", item)]
        if not datasets:
            raise JobError(f"数据集应为 all / amac / csrc，收到：{dataset!r}")
        workers = int(params.get("workers") or 0) or None
        retry_failed = bool(params.get("retry_failed", True))

        merged: dict[str, Any] = {
            "datasets": datasets,
            "total": 0,
            "success": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        for item in datasets:
            sum_result = summarize.summarize(
                item,
                config=config,
                workers=workers,
                retry_failed=retry_failed,
                limit=int(params.get("limit") or 0) or None,
                on_progress=on_progress,
            )
            merged["total"] += sum_result.total
            merged["success"] += sum_result.success
            merged["skipped"] += sum_result.skipped
            merged["failed"] += sum_result.failed
            merged["errors"].extend(sum_result.errors)
        return merged

    if kind == "report":
        from . import report as report_mod

        dataset = (params.get("dataset") or DATASET_AMAC).strip().lower()
        if dataset not in DATASETS:
            raise JobError(f"数据集应为 amac / csrc，收到：{dataset!r}")

        output = report_mod.build_report(
            dataset,
            config=config,
            start_date=(params.get("start_date") or "").strip(),
            end_date=(params.get("end_date") or "").strip(),
            use_llm=bool(params.get("use_llm", False)),
        )
        from .config import get_config as _get_config

        cfg = config or _get_config()
        directory = cfg.data_root("csrc_reports" if dataset == DATASET_CSRC else "amac_reports")
        stem = f"{dataset}_报告_{datetime.now().strftime('%Y%m%d_%H%M')}"
        paths = report_mod.save_report_files(output, directory, stem=stem)
        report_payload = output.to_dict()
        report_payload["paths"] = paths
        return report_payload

    if kind == "org_type":
        from .org_type import backfill_org_types

        limit = int(params.get("limit") or 0) or None
        org_result = backfill_org_types(
            dry_run=bool(params.get("dry_run", False)),
            config=config,
            limit=limit,
            on_progress=on_progress,
        )
        return org_result.to_dict()

    raise JobError(f"任务类型未实现：{kind}")  # pragma: no cover


# ──────────────────────────── 任务管理器 ────────────────────────────


@dataclass
class _JobHandle:
    record: TaskRecord
    thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


class JobManager:
    """线程安全的后台任务管理器（进程内单例）。"""

    def __init__(self, capacity: int = 200) -> None:
        self.capacity = capacity
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobHandle] = {}
        self._order: list[str] = []

    # ── 查询 ──

    def get(self, job_id: str) -> TaskRecord | None:
        with self._lock:
            handle = self._jobs.get(job_id)
            return handle.record if handle else None

    def jobs(self, limit: int = 50) -> list[TaskRecord]:
        """返回最近的任务记录（新的在前）。"""
        with self._lock:
            ordered = list(reversed(self._order))
        records: list[TaskRecord] = []
        for job_id in ordered[:limit]:
            record = self.get(job_id)
            if record is not None:
                records.append(record)
        return records

    def is_kind_running(self, kind: str) -> bool:
        with self._lock:
            for handle in self._jobs.values():
                if handle.record.kind == kind and handle.record.status is TaskStatus.RUNNING:
                    return True
        return False

    def running_count(self) -> int:
        with self._lock:
            return sum(
                1 for handle in self._jobs.values() if handle.record.status is TaskStatus.RUNNING
            )

    def log_lines(self, job_id: str, start: int = 0) -> list[str]:
        return get_task_log_router().lines(job_id, start)

    def log_text(self, job_id: str, start: int = 0) -> str:
        return get_task_log_router().text(job_id, start)

    # ── 提交 ──

    def submit(
        self,
        kind: str,
        params: dict[str, Any] | None = None,
        title: str = "",
        config: Any = None,
    ) -> str:
        """提交一个后台任务，返回任务 ID。

        Raises:
            JobError: 任务类型未知，或同类型任务仍在执行。
        """
        if kind not in JOB_KINDS:
            raise JobError(f"未知任务类型：{kind}")
        if self.is_kind_running(kind):
            raise JobError(f"「{JOB_LABELS[kind]}」任务正在执行中，请等待完成后再提交")

        job_id = uuid.uuid4().hex[:12]
        record = TaskRecord(
            id=job_id,
            kind=kind,
            title=title or JOB_LABELS[kind],
            status=TaskStatus.PENDING,
            created_at=datetime.now().isoformat(timespec="seconds"),
            params=dict(params or {}),
        )
        handle = _JobHandle(record=record)

        router = get_task_log_router()
        router.open_buffer(job_id)

        with self._lock:
            self._jobs[job_id] = handle
            self._order.append(job_id)
            self._prune_locked()

        thread = threading.Thread(
            target=self._execute,
            args=(handle, config),
            name=f"regwatch-job-{job_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        logger.info("任务已提交 [%s] %s：%s", job_id, JOB_LABELS[kind], record.params)
        return job_id

    def cancel(self, job_id: str) -> bool:
        """请求取消任务；仅 ``pending`` / ``running`` 状态可取消。"""
        with self._lock:
            handle = self._jobs.get(job_id)
        if handle is None:
            return False
        handle.cancel_event.set()
        if handle.record.status is TaskStatus.PENDING:
            handle.record.status = TaskStatus.CANCELLED
            handle.record.finished_at = datetime.now().isoformat(timespec="seconds")
        logger.info("已请求取消任务：%s", job_id)
        return True

    def wait(
        self, job_id: str, timeout: float | None = None, poll: float = 0.2
    ) -> TaskRecord | None:
        """阻塞等待任务结束，超时返回当前记录。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            record = self.get(job_id)
            if record is None or record.status.is_finished:
                return record
            if deadline is not None and time.monotonic() >= deadline:
                return record
            time.sleep(poll)

    # ── 内部 ──

    def _progress_hook(self, handle: _JobHandle) -> ProgressHook:
        record = handle.record

        def hook(done: int, total: int, label: str = "") -> None:
            if handle.cancel_event.is_set():
                raise JobCancelled()
            record.processed = int(done)
            record.total = max(record.total, int(total))
            if label:
                record.message = label

        return hook

    def _execute(self, handle: _JobHandle, config: Any) -> None:
        record = handle.record
        get_task_log_router()
        bind_task(record.id)
        record.status = TaskStatus.RUNNING
        record.started_at = datetime.now().isoformat(timespec="seconds")
        logger.info("任务开始：%s", record.title)

        try:
            result = run_task(
                record.kind,
                record.params,
                on_progress=self._progress_hook(handle),
                config=config,
            )
            record.result = result if isinstance(result, dict) else {"value": result}
            record.status = TaskStatus.SUCCESS
            record.message = self._summarize(record, result)
            logger.info("任务完成：%s", record.message)
        except JobCancelled:
            record.status = TaskStatus.CANCELLED
            record.message = "任务已取消"
            logger.warning("任务被取消：%s", record.title)
        except Exception as exc:
            record.status = TaskStatus.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            logger.error("任务失败：%s | %s", record.title, record.error)
            logger.debug(traceback.format_exc())
        finally:
            record.finished_at = datetime.now().isoformat(timespec="seconds")
            bind_task(None)

    @staticmethod
    def _summarize(record: TaskRecord, result: Any) -> str:
        if not isinstance(result, dict):
            return "完成"
        if record.kind == "report":
            paths = result.get("paths") or {}
            return f"已生成报告：{'、'.join(Path(p).name for p in paths.values()) or '无产物'}"
        if record.kind == "fetch_monthly":
            return f"下载成功 {result.get('success', 0)} 个，失败 {result.get('failed', 0)} 个"
        if record.kind == "org_type":
            return (
                f"共 {result.get('total', 0)} 条：补全 {result.get('org_type_filled', 0)}，"
                f"未解析 {result.get('unresolved', 0)}"
            )
        return (
            f"共 {result.get('total', 0)} 项：成功 {result.get('success', 0)}，"
            f"跳过 {result.get('skipped', 0)}，失败 {result.get('failed', 0)}"
        )

    def _prune_locked(self) -> None:
        """超出容量时清理最旧的已完成任务。"""
        while len(self._order) > self.capacity:
            for job_id in self._order:
                handle = self._jobs.get(job_id)
                if handle is not None and handle.record.status.is_finished:
                    self._order.remove(job_id)
                    self._jobs.pop(job_id, None)
                    get_task_log_router().close_buffer(job_id)
                    break
            else:
                break


_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_job_manager() -> JobManager:
    """返回全局任务管理器单例。"""
    global _manager
    configure_logging()
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = JobManager()
    return _manager


def reset_job_manager() -> JobManager:
    """重建全局任务管理器（测试用）。"""
    global _manager
    with _manager_lock:
        _manager = JobManager()
    return _manager
