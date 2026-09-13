"""后台任务编排。

职责边界：

- :class:`JobSpec` —— 任务参数的规格定义（网页端任务表单由它生成）；
  CLI 子命令的参数声明在 ``cli/commands/*``，两侧共用
  :mod:`regwatch.sources.common` 的日期 / 列表解析；
- :class:`JobRunner` —— 按任务类型分发执行；
- :class:`JobManager` —— 只管线程、取消与进度，状态与日志全部落库，
  对外返回**不可变快照**，避免调用方拿到可变的共享对象。

任务状态机：``pending → running → success | failed | cancelled``。
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..clock import now_iso
from ..domain import JobKind, JobStatus, TaskRecord
from ..logging_setup import bind_task, get_logger, get_task_log_router
from ..sources.common import FETCH_OVERLAP_DAYS, parse_iso_date, split_csv

logger = get_logger("jobs")

__all__ = [
    "JOB_SPECS",
    "JobCancelled",
    "JobContext",
    "JobError",
    "JobManager",
    "JobRunner",
    "JobSpec",
    "ParamSpec",
    "describe_spec",
]

ProgressHook = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class JobError(RuntimeError):
    """任务参数不合法或执行失败。"""


class JobCancelled(Exception):
    """任务被用户取消（内部信号，用于打断执行线程）。"""

    def __init__(self, message: str = "任务已被取消") -> None:
        super().__init__(message)
        self.message = message


# ──────────────────────────── 参数规格 ────────────────────────────


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """一个任务参数的描述（供网页表单与 CLI 参数解析共用）。"""

    name: str
    label: str
    kind: str = "str"  # str | int | bool | date | enum
    default: Any = ""
    hint: str = ""
    choices: tuple[str, ...] = ()

    def coerce(self, value: Any) -> Any:
        """把用户输入转成目标类型。"""
        if value is None or value == "":
            return self.default
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
        if self.kind == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                return self.default
        if self.kind == "date":
            from datetime import date, datetime

            if isinstance(value, datetime):
                return value.date().isoformat()
            if isinstance(value, date):
                return value.isoformat()
            text = str(value).strip()
            if not text:
                return self.default
            return text[:10]
        return str(value).strip()


@dataclass(frozen=True, slots=True)
class JobSpec:
    """一类任务的完整描述。"""

    kind: JobKind
    fields: tuple[ParamSpec, ...] = ()

    @property
    def label(self) -> str:
        return self.kind.label

    def normalize(self, params: dict[str, Any] | None) -> dict[str, Any]:
        """按字段规格清洗参数：只保留已知字段并做类型转换。"""
        raw = dict(params or {})
        return {spec.name: spec.coerce(raw.get(spec.name)) for spec in self.fields}


JOB_SPECS: dict[JobKind, JobSpec] = {
    JobKind.FETCH_AMAC: JobSpec(
        JobKind.FETCH_AMAC,
        (
            ParamSpec(
                "start_date",
                "起始日期",
                kind="date",
                hint=f"不填=上次覆盖日期前 {FETCH_OVERLAP_DAYS} 天（空库=上季度初）",
            ),
            ParamSpec("end_date", "结束日期", kind="date", hint="不填=今天"),
            ParamSpec(
                "categories", "抓取分类", default="all", hint="all / Institution / Personnel"
            ),
        ),
    ),
    JobKind.FETCH_CSRC: JobSpec(
        JobKind.FETCH_CSRC,
        (
            ParamSpec(
                "start_date",
                "起始日期",
                kind="date",
                hint=f"不填=上次覆盖日期前 {FETCH_OVERLAP_DAYS} 天（空库=2022-01-01）",
            ),
            ParamSpec("end_date", "结束日期", kind="date", hint="不填=今天"),
            ParamSpec("bureaus", "来源局", default="all", hint="逗号分隔英文标识，all=全部"),
            ParamSpec("case_types", "案例类型", default="all", hint="all / penalty / measure"),
            ParamSpec("concurrency", "并发数", kind="int", default=0, hint="0=使用配置默认值"),
        ),
    ),
    JobKind.SUMMARIZE: JobSpec(
        JobKind.SUMMARIZE,
        (
            ParamSpec("dataset", "数据集", default="all", hint="all / amac / csrc"),
            ParamSpec("workers", "并发数", kind="int", default=0, hint="0=使用配置默认值"),
            ParamSpec("retry_failed", "重试失败", kind="bool", default=True),
            ParamSpec("limit", "抽样上限", kind="int", default=0, hint="0=全部未提取的案例"),
        ),
    ),
    JobKind.REPORT: JobSpec(
        JobKind.REPORT,
        (
            ParamSpec("dataset", "数据集", default="amac", hint="amac / csrc"),
            ParamSpec("start_date", "起始日期", kind="date", hint="不填=全量"),
            ParamSpec("end_date", "结束日期", kind="date", hint="不填=全量"),
            ParamSpec("use_llm", "模型建议", kind="bool", default=False),
        ),
    ),
    JobKind.ORG_TYPE: JobSpec(
        JobKind.ORG_TYPE,
        (
            ParamSpec("dry_run", "仅试算", kind="bool", default=False, hint="只统计不写盘"),
            ParamSpec("limit", "抽样上限", kind="int", default=0, hint="0=全部未填写的案例"),
        ),
    ),
}


def describe_spec(kind: JobKind | str) -> JobSpec:
    """取任务规格；未知类型抛 :class:`JobError`。"""
    target = JobKind.parse(kind)
    if target is None or target not in JOB_SPECS:
        raise JobError(f"未知任务类型：{kind}（可选 {'、'.join(k.value for k in JOB_SPECS)}）")
    return JOB_SPECS[target]


# ──────────────────────────── 执行上下文 ────────────────────────────


@dataclass(frozen=True, slots=True)
class JobContext:
    """传给每个 runner 的执行上下文。"""

    services: Any
    progress: ProgressHook
    is_cancelled: CancelCheck

    def check_cancel(self) -> None:
        if self.is_cancelled():
            raise JobCancelled()


# ──────────────────────────── 任务分发 ────────────────────────────


def _parse_date(value: str | None, field_name: str) -> Any:
    """把任务参数里的日期串转成 ``date``；格式非法时抛 :class:`JobError`。"""
    try:
        return parse_iso_date(value, field_name)
    except ValueError as exc:
        raise JobError(str(exc)) from exc


class JobRunner:
    """按任务类型分发执行。"""

    def __init__(self, services: Any) -> None:
        self._services = services

    def run(
        self,
        kind: JobKind | str,
        params: dict[str, Any],
        progress: ProgressHook,
        is_cancelled: CancelCheck,
    ) -> dict[str, Any]:
        target = JobKind.parse(kind)
        if target is None:
            raise JobError(f"未知任务类型：{kind}")
        spec = describe_spec(target)
        context = JobContext(services=self._services, progress=progress, is_cancelled=is_cancelled)
        return _DISPATCH[target](context, spec.normalize(params))


# ──────────────────────────── 各任务实现 ────────────────────────────


def _as_payload(result: Any) -> dict[str, Any]:
    """把采集器的结果对象归一为可序列化的字典。"""
    if isinstance(result, dict):
        return result
    to_dict = getattr(result, "to_dict", None)
    return to_dict() if callable(to_dict) else {"value": result}


def _run_fetch_amac(context: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    from ..sources import amac

    categories = params.get("categories") or "all"
    result = amac.fetch(
        start_date=_parse_date(params.get("start_date"), "起始日期"),
        end_date=_parse_date(params.get("end_date"), "结束日期"),
        categories=None if categories in ("", "all") else split_csv(categories),
        store=context.services.store,
        llm=context.services.llm,
        org_type=context.services.org_type,
        on_progress=context.progress,
    )
    return _as_payload(result)


def _run_fetch_csrc(context: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    from ..sources import csrc

    bureaus = params.get("bureaus") or "all"
    case_types = params.get("case_types") or "all"
    result = csrc.fetch(
        start_date=_parse_date(params.get("start_date"), "起始日期"),
        end_date=_parse_date(params.get("end_date"), "结束日期"),
        bureaus=None if bureaus in ("", "all") else split_csv(bureaus),
        case_types=None if case_types in ("", "all") else split_csv(case_types),
        concurrency=int(params.get("concurrency") or 0) or None,
        store=context.services.store,
        on_progress=context.progress,
    )
    return _as_payload(result)


def _run_summarize(context: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    from ..domain import Dataset

    dataset = (params.get("dataset") or "all").strip().lower()
    datasets = [item for item in Dataset.all() if dataset in ("", "all", item.value)]
    if not datasets:
        raise JobError(f"数据集应为 all / amac / csrc，收到：{dataset!r}")

    service = context.services.summarization
    merged: dict[str, Any] = {
        "datasets": [item.value for item in datasets],
        "total": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }
    for item in datasets:
        result = service.summarize(
            item,
            workers=int(params.get("workers") or 0) or None,
            retry_failed=bool(params.get("retry_failed", True)),
            limit=int(params.get("limit") or 0) or None,
            on_progress=context.progress,
        )
        merged["total"] += result.total
        merged["success"] += result.success
        merged["skipped"] += result.skipped
        merged["failed"] += result.failed
        merged["errors"].extend(result.errors)
    return merged


def _run_report(context: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    from ..domain import Dataset

    dataset = Dataset.parse((params.get("dataset") or Dataset.AMAC.value).strip().lower())
    if dataset is None:
        raise JobError(f"数据集应为 amac / csrc，收到：{params.get('dataset')!r}")

    output = context.services.reporting.build_report(
        dataset,
        start_date=(params.get("start_date") or "").strip(),
        end_date=(params.get("end_date") or "").strip(),
        use_llm=bool(params.get("use_llm", False)),
    )
    directory = context.services.settings.reports_dir / dataset.value
    stem = f"{dataset.value}_报告_{now_iso().replace(':', '').replace('-', '')[:15]}"
    paths = context.services.reporting.save(output, directory, stem=stem)
    payload = output.to_dict()
    payload["paths"] = paths
    return payload


def _run_org_type(context: JobContext, params: dict[str, Any]) -> dict[str, Any]:
    return context.services.org_type.backfill(
        dry_run=bool(params.get("dry_run", False)),
        limit=int(params.get("limit") or 0) or None,
        on_progress=context.progress,
    ).to_dict()


_DISPATCH: dict[JobKind, Callable[[JobContext, dict[str, Any]], dict[str, Any]]] = {
    JobKind.FETCH_AMAC: _run_fetch_amac,
    JobKind.FETCH_CSRC: _run_fetch_csrc,
    JobKind.SUMMARIZE: _run_summarize,
    JobKind.REPORT: _run_report,
    JobKind.ORG_TYPE: _run_org_type,
}


# ──────────────────────────── 任务管理器 ────────────────────────────


@dataclass
class _Handle:
    task_id: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class JobManager:
    """线程安全的后台任务管理器（状态与日志落库）。"""

    def __init__(
        self,
        tasks: Any,
        runner: JobRunner,
        *,
        capacity: int = 100,
        log_router: Any = None,
    ) -> None:
        self._tasks = tasks
        self._runner = runner
        self.capacity = capacity
        self._handles: dict[str, _Handle] = {}
        self._lock = threading.RLock()
        router = log_router if log_router is not None else get_task_log_router()
        router.add_sink(self._persist_log)
        self._recover_orphans()

    def _recover_orphans(self) -> None:
        """启动恢复：把库里遗留的未结束任务标记为失败。

        任务线程只存在于本进程内存；进程重启后库里 ``pending`` / ``running``
        的记录必然已无对应线程，不回收会永远显示「执行中」且无法取消。
        """
        try:
            stale = self._tasks.running_ids()
        except Exception:  # pragma: no cover - 库不可用时跳过恢复
            return
        for task_id in stale:
            self._tasks.finish(task_id, JobStatus.FAILED, message="进程重启，任务已中断")
        if stale:
            logger.warning("启动恢复：%d 条遗留任务标记为失败", len(stale))

    # ── 日志 ──

    def _persist_log(self, line: Any) -> None:
        try:
            # 多个 JobManager 可能共用同一个日志路由器，只落库属于自己的任务
            if self._tasks.get(line.task_id) is None:
                return
            self._tasks.append_log(line)
        except Exception:  # pragma: no cover - 落库失败不应中断日志链路
            logger.debug("任务日志落库失败：%s", line)

    def logs(self, job_id: str, start: int = 0) -> list[str]:
        """优先读内存缓冲（实时），缺失时回落到库。"""
        router = get_task_log_router()
        in_memory = router.lines(job_id, start)
        if in_memory:
            return [line.format() for line in in_memory]
        return [line.format() for line in self._tasks.logs(job_id, start)]

    def log_text(self, job_id: str, start: int = 0) -> str:
        return "\n".join(self.logs(job_id, start))

    # ── 查询 ──

    def get(self, job_id: str) -> TaskRecord | None:
        """返回任务的**快照**（每次从库读取，调用方改动不会影响内部状态）。"""
        return self._tasks.get(job_id)

    def jobs(self, limit: int = 50) -> list[TaskRecord]:
        return self._tasks.list(limit=limit)

    def is_kind_running(self, kind: JobKind | str) -> bool:
        value = kind.value if isinstance(kind, JobKind) else str(kind)
        return any(
            record.status in (JobStatus.PENDING, JobStatus.RUNNING) and record.kind.value == value
            for record in self._tasks.list(limit=self.capacity)
        )

    def running_count(self) -> int:
        return sum(
            1
            for record in self._tasks.list(limit=self.capacity)
            if record.status in (JobStatus.PENDING, JobStatus.RUNNING)
        )

    # ── 提交与取消 ──

    def submit(
        self,
        kind: JobKind | str,
        params: dict[str, Any] | None = None,
        *,
        title: str = "",
    ) -> str:
        """提交后台任务，返回任务 ID。"""
        spec = describe_spec(kind)
        job_id = uuid.uuid4().hex[:12]
        if self.is_kind_running(spec.kind):
            raise JobError(f"「{spec.label}」任务正在执行中，请等待完成后再提交")

        record = TaskRecord(
            id=job_id,
            kind=spec.kind,
            title=title or spec.label,
            status=JobStatus.PENDING,
            created_at=now_iso(),
            params=spec.normalize(params),
        )
        self._tasks.save(record)
        self._tasks.prune(keep=self.capacity)

        handle = _Handle(task_id=job_id)
        get_task_log_router().open_buffer(job_id)
        with self._lock:
            self._handles[job_id] = handle

        thread = threading.Thread(
            target=self._execute,
            args=(record, handle),
            name=f"regwatch-job-{job_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        logger.info("任务已提交 [%s] %s：%s", job_id, spec.label, record.params)
        return job_id

    def cancel(self, job_id: str) -> bool:
        """请求取消任务；仅未结束的任务可取消。

        没有本进程执行线程（如跨进程遗留的记录）时，仅把库中状态落为取消。
        """
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is not None:
            handle.cancel_event.set()
        record = self._tasks.get(job_id)
        if record is not None and not record.status.is_finished:
            self._tasks.finish(job_id, JobStatus.CANCELLED, message="任务已取消")
            logger.info("已请求取消任务：%s", job_id)
            return True
        return handle is not None

    def wait(
        self, job_id: str, timeout: float | None = None, poll: float = 0.2
    ) -> TaskRecord | None:
        """阻塞等待任务结束。"""
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            record = self.get(job_id)
            if record is None or record.status.is_finished:
                return record
            if deadline is not None and time.monotonic() >= deadline:
                return record
            time.sleep(poll)

    # ── 内部 ──

    def _progress_hook(self, handle: _Handle) -> ProgressHook:
        def hook(done: int, total: int, label: str = "") -> None:
            if handle.cancel_event.is_set():
                raise JobCancelled()
            self._tasks.update_progress(
                handle.task_id, processed=int(done), total=int(total), message=label
            )

        return hook

    def _execute(self, record: TaskRecord, handle: _Handle) -> None:
        bind_task(record.id)
        self._tasks.save(record.replace(status=JobStatus.RUNNING, started_at=now_iso()))
        logger.info("任务开始：%s", record.title)

        try:
            result = self._runner.run(
                record.kind,
                record.params,
                self._progress_hook(handle),
                handle.cancel_event.is_set,
            )
            payload = result if isinstance(result, dict) else {"value": result}
            self._tasks.finish(
                record.id,
                JobStatus.SUCCESS,
                message=_summarize_result(record, payload),
                result=payload,
            )
            logger.info("任务完成：%s", record.title)
        except JobCancelled:
            self._tasks.finish(record.id, JobStatus.CANCELLED, message="任务已取消")
            logger.warning("任务被取消：%s", record.title)
        except Exception as exc:
            self._tasks.finish(
                record.id,
                JobStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                message=f"任务失败：{exc}",
            )
            logger.error("任务失败：%s | %s", record.title, exc)
            logger.debug(traceback.format_exc())
        finally:
            bind_task(None)
            with self._lock:
                self._handles.pop(record.id, None)
            get_task_log_router().close_buffer(record.id)


def _summarize_result(record: TaskRecord, result: dict[str, Any]) -> str:
    if record.kind is JobKind.REPORT:
        paths = result.get("paths") or {}
        from pathlib import Path

        names = "、".join(Path(path).name for path in paths.values())
        return f"已生成报告：{names or '无产物'}"
    if record.kind is JobKind.ORG_TYPE:
        return (
            f"共 {result.get('total', 0)} 条：补全 {result.get('org_type_filled', 0)}，"
            f"未解析 {result.get('unresolved', 0)}"
        )
    return (
        f"共 {result.get('total', 0)} 项：成功 {result.get('success', 0)}，"
        f"跳过 {result.get('skipped', 0)}，失败 {result.get('failed', 0)}"
    )
