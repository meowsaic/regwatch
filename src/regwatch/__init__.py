"""regwatch —— AMAC 与证监会基金相关案例的采集、摘要与统计分析工具包。

分层结构（依赖方向自上而下，越往下越稳定）::

    交付层   cli/ 、 web/          只做参数解析与呈现
    用例层   services/             业务编排，依赖注入
    领域层   domain/               枚举、数据模型、分类体系（无 IO）
    数据层   db/                   SQLite 连接 + 仓储

模块导航：

- :mod:`regwatch.domain`     领域模型与枚举
- :mod:`regwatch.settings`   配置（单一 SQLite 库路径、模型条目、任务绑定）
- :mod:`regwatch.db`         SQLite 连接、迁移与四个仓储
- :mod:`regwatch.llm`        OpenAI 兼容模型客户端
- :mod:`regwatch.sources`    采集子包（AMAC / CSRC）
- :mod:`regwatch.services`   用例层（摘要 / 统计 / 报告 / 机构类型 / 任务编排）
- :mod:`regwatch.cli`        命令行入口
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .domain import (
    CaseQuery,
    CaseRecord,
    CaseRow,
    CaseStatus,
    CaseType,
    Category,
    Dataset,
    EntityType,
    JobKind,
    JobStatus,
    ModelProfile,
    SourceType,
    SummaryRecord,
    TaskRecord,
    split_multi_value,
)
from .llm import (
    ChatResult,
    LLMClient,
    LLMClientFactory,
    LLMError,
    parse_json_response,
)
from .logging_setup import bind_task, configure_logging, get_logger
from .services import (
    Services,
    build_services,
    get_services,
    reset_services,
)
from .settings import ConfigError, Settings, get_settings, reset_settings

try:  # 正常安装时由包元数据提供版本号
    __version__ = version("regwatch")
except PackageNotFoundError:  # pragma: no cover - 源码直跑时回退
    __version__ = "0.0.0"

__all__ = [
    "CaseQuery",
    "CaseRecord",
    "CaseRow",
    "CaseStatus",
    "CaseType",
    "Category",
    "ChatResult",
    "ConfigError",
    "Dataset",
    "EntityType",
    "JobKind",
    "JobStatus",
    "LLMClient",
    "LLMClientFactory",
    "LLMError",
    "ModelProfile",
    "Services",
    "Settings",
    "SourceType",
    "SummaryRecord",
    "TaskRecord",
    "__version__",
    "bind_task",
    "build_services",
    "configure_logging",
    "get_logger",
    "get_services",
    "get_settings",
    "parse_json_response",
    "reset_services",
    "reset_settings",
    "split_multi_value",
]
