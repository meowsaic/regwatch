"""采集子包：AMAC（中基协）与 CSRC（证监会）两套案例来源。

共享设施：

- :mod:`regwatch.sources.http`      可注入的 HTTP 客户端与共享会话
- :mod:`regwatch.sources.common`    URL / 日期 / 文件名工具
- :mod:`regwatch.sources.htmlparse` 页面下载、正文提取与附件链接发现
- :mod:`regwatch.sources.docparse`  PDF / Office 附件正文解析
- :mod:`regwatch.sources.progress`  进度上报

采集器：

- :mod:`regwatch.sources.amac`         AMAC 纪律处分案例抓取
- :mod:`regwatch.sources.amac_monthly` AMAC 公告 PDF 月度归档
- :mod:`regwatch.sources.csrc`         CSRC 行政处罚 / 监管措施抓取
- :mod:`regwatch.sources.bureaus`      各证监局来源配置
"""

from __future__ import annotations

__all__ = ["amac", "amac_monthly", "bureaus", "csrc", "docparse"]
