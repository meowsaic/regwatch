"""日志与任务日志路由。

统一 ``regwatch`` 命名空间的日志配置，并提供「按任务路由」的内存缓冲：
后台任务运行在独立线程，线程启动时 :func:`bind_task` 绑定任务 ID，
:class:`TaskLogRouter` 便把该线程产生的日志按任务分别缓冲，
网页端可实时读取某任务的日志而不与其它任务串台。

与旧版 ``logutil`` 的差异：

- 日志行可注册 **sink**（用于把任务日志落库），原先只能内存缓冲；
- 移除了零调用的 ``timestamp()`` / ``TaskLogRouter.clear()``；
- :func:`configure_logging` 可重复调用以切换级别与日志文件。
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

from .clock import now_iso
from .domain import TaskLogLine

__all__ = [
    "LOG_FORMAT",
    "TaskLogRouter",
    "bind_task",
    "configure_logging",
    "current_task_id",
    "get_logger",
    "get_task_log_router",
    "reset_logging",
]

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"
FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOGGER_NAME = "regwatch"
_configured = False
_config_lock = threading.Lock()

_local = threading.local()
_router: TaskLogRouter | None = None


# ──────────────────────────── 任务绑定 ────────────────────────────


def bind_task(task_id: str | None) -> None:
    """把当前线程绑定到指定任务 ID（传 ``None`` 解绑）。"""
    _local.task_id = task_id


def current_task_id() -> str | None:
    """返回当前线程绑定的任务 ID。"""
    return getattr(_local, "task_id", None)


# ──────────────────────────── 任务日志路由 ────────────────────────────


class TaskLogRouter(logging.Handler):
    """按线程绑定的任务 ID 将日志分发到各自的内存缓冲，并可转发给 sink。"""

    def __init__(self, capacity: int = 3000) -> None:
        super().__init__()
        self.capacity = capacity
        self._buffers: dict[str, deque[TaskLogLine]] = {}
        self._seq: dict[str, int] = {}
        self._sinks: list[Callable[[TaskLogLine], None]] = []
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

    # ── sink 注册 ──

    def add_sink(self, sink: Callable[[TaskLogLine], None]) -> None:
        """注册一个日志落库回调（线程安全）。"""
        with self._lock:
            self._sinks.append(sink)

    # ── 缓冲管理 ──

    def open_buffer(self, task_id: str) -> None:
        """为任务开启日志缓冲（重复调用保留已有内容）。"""
        with self._lock:
            if task_id not in self._buffers:
                self._buffers[task_id] = deque(maxlen=self.capacity)
                self._seq[task_id] = 0

    def close_buffer(self, task_id: str) -> None:
        """释放任务日志缓冲（任务结束后调用）。"""
        with self._lock:
            self._buffers.pop(task_id, None)
            self._seq.pop(task_id, None)

    # ── 读取 ──

    def lines(self, task_id: str, start: int = 0) -> list[TaskLogLine]:
        """返回该任务自序号 ``start`` 起的日志行。"""
        with self._lock:
            buffer = self._buffers.get(task_id)
            snapshot: Iterable[TaskLogLine] = list(buffer) if buffer is not None else []
        return [line for line in snapshot if line.seq >= start]

    def text(self, task_id: str, start: int = 0) -> str:
        return "\n".join(line.format() for line in self.lines(task_id, start))

    def count(self, task_id: str) -> int:
        with self._lock:
            buffer = self._buffers.get(task_id)
            return len(buffer) if buffer is not None else 0

    # ── Handler 接口 ──

    def emit(self, record: logging.LogRecord) -> None:
        task_id = current_task_id()
        if not task_id:
            return
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 格式化失败不应影响主流程
            return

        with self._lock:
            buffer = self._buffers.get(task_id)
            if buffer is None:
                return
            seq = self._seq.get(task_id, 0)
            self._seq[task_id] = seq + 1
            sinks = tuple(self._sinks)

        line = TaskLogLine(
            task_id=task_id,
            seq=seq,
            level=record.levelname,
            message=message,
            created_at=now_iso(),
        )
        with self._lock:
            buffer = self._buffers.get(task_id)
            if buffer is not None:
                buffer.append(line)

        for sink in sinks:
            try:
                sink(line)
            except Exception:  # pragma: no cover - sink 失败不应中断业务
                continue


def get_task_log_router() -> TaskLogRouter:
    """返回全局唯一的任务日志路由器（随日志系统一同初始化）。"""
    global _router
    configure_logging()
    assert _router is not None
    return _router


# ──────────────────────────── 初始化 ────────────────────────────


def _ensure_utf8_stdout() -> None:
    """在 Windows 控制台上把标准输出切到 UTF-8，避免中文日志乱码。"""
    stream = sys.stdout
    if not hasattr(stream, "reconfigure"):
        return
    try:
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding in ("utf8", "utf8mb3", "utf8mb4"):
            return
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - 环境相关
        return


def get_logger(name: str = "") -> logging.Logger:
    """获取 ``regwatch`` 命名空间下的日志器。"""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def configure_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
    force: bool = False,
) -> logging.Logger:
    """初始化 ``regwatch`` 日志系统（幂等）。

    只配置 ``regwatch`` 命名空间，不触碰 root logger，
    避免与 Streamlit 等宿主进程的日志配置互相干扰。
    """
    global _configured, _router

    logger = logging.getLogger(_LOGGER_NAME)
    with _config_lock:
        if _configured and not force:
            logger.setLevel(level)
            return logger

        _ensure_utf8_stdout()

        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover
                pass

        logger.setLevel(level)
        logger.propagate = False

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(console)

        router = _router if _router is not None else TaskLogRouter()
        router.setLevel(level)
        logger.addHandler(router)
        _router = router

        if log_file is not None:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(FILE_FORMAT, FILE_DATE_FORMAT))
            logger.addHandler(file_handler)

        _configured = True
        return logger


def reset_logging() -> None:
    """销毁日志系统状态（测试专用）。"""
    global _configured, _router
    with _config_lock:
        logger = logging.getLogger(_LOGGER_NAME)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover
                pass
        _configured = False
        _router = None
