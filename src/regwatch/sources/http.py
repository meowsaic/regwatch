"""采集层共享的 HTTP 基础设施。

历史上 ``amac.py`` / ``csrc.py`` / ``csrc_bureaus.py`` / ``amac_monthly.py``
各自维护一份 Session、重试与超时常量，超时从 15s 到 60s 不等，退避策略也各不相同。
这里收敛为**一个可注入的客户端**：

- 统一默认 UA、超时与重试策略；
- 内置令牌桶限速，避免把对方站点打爆；
- 只暴露 ``get_text`` / ``post_json`` 两个方法，便于测试时整体替换。
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "HttpClient",
    "HttpError",
    "RateLimiter",
    "RequestsHttpClient",
]

DEFAULT_TIMEOUT = 30.0

#: 抓取 HTML 页面时的默认请求头
DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class HttpError(RuntimeError):
    """HTTP 请求失败。"""


class HttpClient(Protocol):
    """采集层对网络的最小抽象（依赖倒置，便于测试注入）。"""

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        encoding: str | None = None,
    ) -> str: ...

    def post_json(
        self,
        url: str,
        *,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any: ...


@dataclass(slots=True)
class RateLimiter:
    """简单的线程安全令牌桶限速器。"""

    min_interval: float = 0.0
    _last: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last
            delay = self.min_interval - elapsed
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class RequestsHttpClient:
    """基于 ``requests`` 的默认实现。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        backoff: float = 3.0,
        min_interval: float = 0.0,
        default_headers: Mapping[str, str] | None = None,
        session: Any = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff = backoff
        self.limiter = RateLimiter(min_interval)
        self.default_headers: dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            **(default_headers or {}),
        }
        self._session = session

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers.update(self.default_headers)
        return self._session

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        import requests

        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                response = self.session.request(
                    method, url, timeout=kwargs.pop("timeout", self.timeout), **kwargs
                )
                if response.status_code != 200:
                    raise HttpError(f"{method} {url} 返回 {response.status_code}")
                return response
            except (requests.RequestException, HttpError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.backoff * (attempt + 1))
        raise HttpError(f"{method} {url} 失败：{last_error}") from last_error

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        encoding: str | None = None,
    ) -> str:
        response = self._request("GET", url, headers=dict(headers or {}), timeout=timeout)
        if encoding:
            response.encoding = encoding
        return str(response.text)

    def post_json(
        self,
        url: str,
        *,
        payload: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = self._request(
            "POST", url, json=payload, headers=dict(headers or {}), timeout=timeout
        )
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(f"{url} 返回的不是合法 JSON：{exc}") from exc

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


def jitter() -> float:
    """返回一个 [0, 1) 的随机数，用于构造绕过缓存的查询参数。"""
    return random.random()


# ──────────────────────────── 共享会话 ────────────────────────────

_shared_session: Any = None
_shared_lock = threading.Lock()


def shared_session() -> Any:
    """返回进程内共享的 ``requests.Session``（统一超时、重试与连接池）。

    供历史翻页 / 搜索接口等需要底层响应对象（状态码、二进制体）的采集代码使用；
    新写的抓取逻辑应优先用 :class:`RequestsHttpClient`。
    """
    global _shared_session
    if _shared_session is None:
        with _shared_lock:
            if _shared_session is None:
                import requests
                from requests.adapters import HTTPAdapter, Retry

                session = requests.Session()
                session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
                retry = Retry(
                    total=3,
                    backoff_factor=1.0,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                _shared_session = session
    return _shared_session


def close_shared_session() -> None:
    """关闭共享会话（抓取任务结束时调用）。"""
    global _shared_session
    with _shared_lock:
        if _shared_session is not None:
            _shared_session.close()
            _shared_session = None
