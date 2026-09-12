"""采集进度上报。

旧版 ``csrc.py`` 用全局 ``_last_progress_width`` 直写 ``sys.stdout`` 做行内刷新，
与 ``on_progress`` 回调并行存在两套进度通道；这里统一成一个对象：

- :meth:`Progress.update` 既写日志也回调上层（网页端进度条 / CLI 进度）；
- 模块级 :func:`progress` 只保留一个等价的 INFO 日志入口，
  供不便传参的内部函数使用，不再维护任何全局终端状态。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["Progress", "ProgressHook", "progress"]

ProgressHook = Callable[[int, int, str], None]

logger = logging.getLogger("regwatch.sources")


def progress(msg: str) -> None:
    """输出一行进度信息（INFO 级日志）。

    仅为兼容不便传递 :class:`Progress` 实例的内部函数；
    新代码应显式使用 :class:`Progress`。
    """
    logger.info(msg)


@dataclass(slots=True)
class Progress:
    """一次抓取任务的进度上报器。"""

    callback: ProgressHook | None = None
    total: int = 0
    done: int = 0

    def start(self, total: int, message: str = "") -> None:
        """设置总数并上报起点。"""
        self.total = max(0, int(total))
        self.done = 0
        self.update(0, message=message)

    def update(self, done: int, total: int | None = None, message: str = "") -> None:
        """更新进度并回调上层。"""
        if total is not None:
            self.total = max(self.total, int(total))
        self.done = int(done)
        if self.callback is not None:
            self.callback(self.done, self.total, message)

    def advance(self, step: int = 1, message: str = "") -> None:
        """完成一项并上报。"""
        self.update(self.done + step, message=message)

    def info(self, message: str, *args: Any) -> None:
        """只记日志、不改变进度的提示。"""
        logger.info(message, *args)
