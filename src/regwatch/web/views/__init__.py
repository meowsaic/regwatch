"""视图集合。

- :mod:`regwatch.web.views.overview`    总览看板
- :mod:`regwatch.web.views.cases`       案例浏览与检索
- :mod:`regwatch.web.views.statistics`  统计分析
- :mod:`regwatch.web.views.qa`          智能问答与专题报告
- :mod:`regwatch.web.views.jobs`        任务中心
- :mod:`regwatch.web.views.settings`    模型与配置
"""

from __future__ import annotations

import types

from . import cases, jobs, overview, qa, statistics
from .settings import render as _render_settings

__all__ = ["cases", "jobs", "overview", "qa", "settings_view", "statistics"]

# 视图模块名为 settings，与配置读取函数同名易混淆，这里以命名空间形式导出
settings_view = types.SimpleNamespace(render=_render_settings)
