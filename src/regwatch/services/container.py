"""组合根：把配置、数据层、模型客户端与各个用例装配起来。

这是**唯一**负责「谁依赖谁」的地方：

- 用例层（``services/*``）只通过构造函数接收依赖，自己不创建任何全局对象；
- 交付层（CLI / Web）只从 :func:`get_services` 拿一个已装配好的实例；
- 测试可以直接 :func:`build_services` 并传入内存库与假模型客户端。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import DataStore
from ..logging_setup import configure_logging
from ..settings import Settings, get_settings
from ..sources.http import HttpClient, RequestsHttpClient
from .analyze import AnalysisService
from .data_repair import DataRepairService
from .jobs import JobManager, JobRunner
from .llm_bridge import build_llm_factory
from .org_type import OrgTypeService
from .qa import QaService
from .reporting import ReportService
from .summarize import SummarizationService

__all__ = ["Services", "build_services", "get_services", "reset_services"]


@dataclass(slots=True)
class Services:
    """一次装配得到的全部门面。"""

    settings: Settings
    store: DataStore
    http: HttpClient
    llm: Any
    analysis: AnalysisService
    reporting: ReportService
    summarization: SummarizationService
    org_type: OrgTypeService
    qa: QaService | None = None
    jobs: JobManager | None = None
    data_repair: DataRepairService | None = None

    # ── 便捷属性 ──

    @property
    def cases(self) -> Any:
        return self.store.cases

    @property
    def summaries(self) -> Any:
        return self.store.summaries

    @property
    def tasks(self) -> Any:
        return self.store.tasks

    @property
    def meta(self) -> Any:
        return self.store.meta

    def touch(self) -> int:
        """数据变更后自增版本号（供网页缓存失效）。"""
        return self.store.touch()

    def refresh_models(self) -> None:
        """配置中的模型条目变更后重建客户端缓存。"""
        self.llm.refresh(self.settings.models, self.settings.tasks)

    def close(self) -> None:
        self.store.close()


def build_services(
    settings: Settings | None = None,
    *,
    database: Path | str | None = None,
    http: HttpClient | None = None,
) -> Services:
    """按配置装配全部服务。"""
    configure_logging()
    resolved = settings or get_settings()
    store = DataStore.open(database or resolved.database)
    client = http or RequestsHttpClient()

    llm = build_llm_factory(resolved)

    def touch() -> None:
        """数据变更后自增版本号（丢弃返回值以匹配 ``Callable[[], None]``）。"""
        store.touch()

    analysis = AnalysisService(store.cases, store.summaries)
    reporting = ReportService(analysis, llm)
    summarization = SummarizationService(
        store.cases,
        store.summaries,
        llm,
        default_concurrency=resolved.concurrency_for("summarize", 5),
        on_data_changed=touch,
    )
    org_type = OrgTypeService(store.cases, store.meta, llm, http=client, on_data_changed=touch)
    qa = QaService(llm, analysis)
    data_repair = DataRepairService(store)

    services = Services(
        settings=resolved,
        store=store,
        http=client,
        llm=llm,
        analysis=analysis,
        reporting=reporting,
        summarization=summarization,
        org_type=org_type,
        qa=qa,
        data_repair=data_repair,
    )
    # JobRunner 需要回指服务门面，因此任务管理器在构造后回填
    services.jobs = JobManager(store.tasks, JobRunner(services))
    return services


_services: Services | None = None
_lock = threading.Lock()


def get_services(
    settings: Settings | None = None,
    *,
    database: Path | str | None = None,
    force: bool = False,
) -> Services:
    """返回进程级服务实例（供 CLI / Web 入口使用）。"""
    global _services
    if _services is None or force or settings is not None or database is not None:
        with _lock:
            if _services is None or force or settings is not None or database is not None:
                _services = build_services(settings, database=database)
    return _services


def reset_services(
    settings: Settings | None = None, *, database: Path | str | None = None
) -> Services:
    """重建进程级服务实例（配置变更或测试时使用）。"""
    global _services
    with _lock:
        if _services is not None:
            try:
                _services.close()
            except Exception:  # pragma: no cover - 关闭失败不阻塞重建
                pass
        _services = build_services(settings, database=database)
        return _services
