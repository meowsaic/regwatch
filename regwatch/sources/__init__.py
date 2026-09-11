"""采集子包：AMAC（中基协）与 CSRC（证监会）两套案例来源。

模块导航：

- :mod:`regwatch.sources.amac`         AMAC 纪律处分案例抓取
- :mod:`regwatch.sources.amac_monthly` AMAC 公告 PDF 月度归档
- :mod:`regwatch.sources.csrc`         CSRC 行政处罚 / 监管措施抓取
- :mod:`regwatch.sources.csrc_bureaus` 37 个来源（证监局）配置
"""

from __future__ import annotations

__all__ = ["amac", "amac_monthly", "csrc", "csrc_bureaus"]
