"""日志工具。

统一日志格式，并提供「按任务路由」的内存缓冲处理器：

后台任务运行在独立线程中，线程启动时调用 :func:`bind_task` 绑定任务 ID，
:class:`TaskLogRouter` 便会把该线程产生的日志按任务分别缓冲，
网页端即可实时读取某个任务的日志而不与其它任务串台。
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

__all__ = [
    "LOG_FORMAT",
    "TaskLogRouter",
    "bind_task",
    "configure_logging",
    "current_task_id",
    "get_logger",
    "get_task_log_router",
]

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%H:%M:%S"

_LOGGER_NAME = "regwatch"
_configured = False
_config_lock = threading.Lock()

# 线程局部变量：当前线程正在执行的任务 ID
_local = threading.local()


def bind_task(task_id: str | None) -> None:
    """把当前线程绑定到指定任务 ID（传 ``None`` 解绑）。"""
    _local.task_id = task_id


def current_task_id() -> str | None:
    """返回当前线程绑定的任务 ID。"""
    return getattr(_local, "task_id", None)


class TaskLogRouter(logging.Handler):
    """按线程绑定的任务 ID 将日志分发到各自的内存缓冲。"""

    def __init__(self, capacity: int = 3000) -> None:
        super().__init__()
        self.capacity = capacity
        self._buffers: dict[str, deque[str]] = {}
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

    # ── 缓冲管理 ──
    def open_buffer(self, task_id: str) -> None:
        """为任务开启日志缓冲（重复调用会保留已有内容）。"""
        with self._lock:
            if task_id not in self._buffers:
                self._buffers[task_id] = deque(maxlen=self.capacity)

    def close_buffer(self, task_id: str) -> None:
        """释放任务日志缓冲（任务结束后调用）。"""
        with self._lock:
            self._buffers.pop(task_id, None)

    def clear(self, task_id: str) -> None:
        with self._lock:
            buffer = self._buffers.get(task_id)
            if buffer is not None:
                buffer.clear()

    # ── 读取 ──
    def lines(self, task_id: str, start: int = 0) -> list[str]:
        """返回该任务自 ``start`` 起的日志行。"""
        with self._lock:
            buffer = self._buffers.get(task_id)
            snapshot = list(buffer) if buffer is not None else []
        return snapshot[max(0, start) :]

    def count(self, task_id: str) -> int:
        with self._lock:
            buffer = self._buffers.get(task_id)
            return len(buffer) if buffer is not None else 0

    def text(self, task_id: str, start: int = 0) -> str:
        return "\n".join(self.lines(task_id, start))

    # ── Handler 接口 ──
    def emit(self, record: logging.LogRecord) -> None:
        task_id = current_task_id()
        if not task_id:
            return
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - 格式化失败不应影响主流程
            return
        with self._lock:
            buffer = self._buffers.get(task_id)
            if buffer is not None:
                buffer.append(line)


_router: TaskLogRouter | None = None


def get_task_log_router() -> TaskLogRouter:
    """返回全局唯一的任务日志路由器（随日志系统一同初始化）。"""
    global _router
    configure_logging()
    assert _router is not None
    return _router


def _ensure_utf8_stdout() -> None:
    """在 Windows 控制台上把标准输出切到 UTF-8，避免中文日志乱码。

    仅在流对象支持 ``reconfigure`` 且当前编码不是 UTF-8 时执行，
    失败静默忽略（例如流被宿主进程替换为不支持重配置的对象）。
    """
    stream = sys.stdout
    if not hasattr(stream, "reconfigure"):
        return
    try:
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding in ("utf8", "utf8mb3", "utf8mb4"):
            return
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - 环境相关，不影响功能
        pass


def get_logger(name: str = "") -> logging.Logger:
    """获取 ``regwatch`` 命名空间下的日志器。"""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME)


def configure_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
    force: bool = False,
) -> logging.Logger:
    """初始化 ``regwatch`` 日志系统（幂等）。

    只会配置 ``regwatch`` 命名空间，不触碰 root logger，避免与 Streamlit、
    Uvicorn 等宿主进程的日志配置互相干扰。

    Args:
        level: 日志级别。
        log_file: 可选的日志文件路径；传入后会追加一个文件处理器。
        force: 为 ``True`` 时重建处理器（用于切换日志文件）。
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
            except Exception:
                pass

        logger.setLevel(level)
        logger.propagate = False

        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(console)

        _router = TaskLogRouter()
        _router.setLevel(level)
        logger.addHandler(_router)

        if log_file is not None:
            log_file = Path(log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    "%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(file_handler)

        _configured = True
        return logger


def timestamp() -> str:
    """统一的时间戳字符串（秒级），用于任务记录。"""
    return datetime.now().isoformat(timespec="seconds")
