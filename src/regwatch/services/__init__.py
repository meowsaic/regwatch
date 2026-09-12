"""用例层：围绕领域模型组织的业务服务。

每个服务都通过构造函数接收依赖（仓储、模型客户端、HTTP 客户端），
组合只在 :mod:`regwatch.services.container` 里发生一次。
"""

from __future__ import annotations

from .analyze import AnalysisResult, AnalysisService, analyze
from .container import Services, build_services, get_services, reset_services
from .jobs import (
    JOB_SPECS,
    JobCancelled,
    JobContext,
    JobError,
    JobManager,
    JobRunner,
    JobSpec,
    ParamSpec,
    describe_spec,
)
from .org_type import BackfillResult, OrgTypeService
from .reporting import ReportOutput, ReportService, save_report_files
from .summarize import SummarizationService, SummarizeResult

__all__ = [
    "JOB_SPECS",
    "AnalysisResult",
    "AnalysisService",
    "BackfillResult",
    "JobCancelled",
    "JobContext",
    "JobError",
    "JobManager",
    "JobRunner",
    "JobSpec",
    "OrgTypeService",
    "ParamSpec",
    "ReportOutput",
    "ReportService",
    "Services",
    "SummarizationService",
    "SummarizeResult",
    "analyze",
    "build_services",
    "describe_spec",
    "get_services",
    "reset_services",
    "save_report_files",
]
