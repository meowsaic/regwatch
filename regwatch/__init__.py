"""regwatch —— AMAC 与证监会基金相关案例的采集、摘要与统计分析工具包。

模块导航：

- :mod:`regwatch.config`     配置管理（数据路径、模型条目、任务映射）
- :mod:`regwatch.llm`        统一 OpenAI 兼容模型客户端
- :mod:`regwatch.datamodels` 数据模型（案例、摘要、任务）
- :mod:`regwatch.logutil`    日志与任务日志缓冲
- :mod:`regwatch.storage`    数据访问层
- :mod:`regwatch.sources`    采集子包（AMAC / CSRC）
- :mod:`regwatch.summarize`  结构化摘要提取
- :mod:`regwatch.analyze`    统计聚合
- :mod:`regwatch.report`     报告渲染
"""

from __future__ import annotations

from .config import Config, ConfigError, get_config, reset_config
from .datamodels import ModelProfile, TaskRecord, TaskStatus
from .llm import LLMClient, LLMError, get_llm, parse_json_response, reset_clients
from .logutil import configure_logging, get_logger

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "Config",
    "ConfigError",
    "get_config",
    "reset_config",
    "ModelProfile",
    "TaskRecord",
    "TaskStatus",
    "LLMClient",
    "LLMError",
    "get_llm",
    "reset_clients",
    "parse_json_response",
    "configure_logging",
    "get_logger",
]
