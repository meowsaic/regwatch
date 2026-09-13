"""视图集合。

- :mod:`regwatch.web.views.overview`    总览看板
- :mod:`regwatch.web.views.cases`       案例浏览与检索
- :mod:`regwatch.web.views.statistics`  统计分析
- :mod:`regwatch.web.views.qa`          智能问答与专题报告
- :mod:`regwatch.web.views.jobs`        任务中心
- :mod:`regwatch.web.views.settings`    模型与配置

页面模块由 :func:`regwatch.web.shell.build_pages` 按
:data:`regwatch.web.nav.NAV_ITEMS` 动态装载，此处不预先导入任何视图。
"""
