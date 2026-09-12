"""Streamlit Community Cloud 部署引导（本地开发用不到）。

云端与本地有三处差异，这个模块把前两处兜住，且**全部是可选增强**：
任何一步失败都只记日志并降级，不影响应用启动。

1. **没有 ``config.json``**：该文件含密钥、不随仓库走，于是把应用 Secrets
   （``.streamlit/secrets.toml``）里的标量键桥接成 ``REGWATCH_*`` 环境变量，
   后续由 :mod:`regwatch.settings` 正常读取；
2. **没有 ``data/regwatch.db``**：容器文件系统是临时的，把几十 MB 的库提交进
   仓库也不合适；在 Secrets 里给一个 ``database_url``，启动时按需下载一次；
3. **入口脚本在包外**：由 ``deploy/streamlit_app.py`` 自己把 ``src/`` 加进
   ``sys.path``，本模块不参与。

Secrets 写法（Streamlit 应用设置 → Secrets）::

    api_key_deepseek = "sk-xxxx"        # → REGWATCH_API_KEY_DEEPSEEK
    regwatch_database = "data/regwatch.db"   # 可选：任意 REGWATCH_ 前缀键都透传成环境变量
    database_url = "https://example.com/regwatch.db"
    database_token = "ghp_xxx"          # 可选：私有仓库的 Release 资产需要

注意：容器内的写入（抓取 / 摘要 / 报告）在实例重启后会丢失，长期数据请放回
``database_url`` 指向的位置。
"""

from __future__ import annotations

import os
import re
import shutil
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

__all__ = [
    "API_KEY_PREFIX",
    "DATABASE_TOKEN_KEY",
    "DATABASE_URL_KEY",
    "ENV_PREFIX",
    "apply_env_secrets",
    "ensure_database",
    "prepare_runtime",
    "resolve_database_path",
]

logger = get_logger("web.cloud")

#: 与 :mod:`regwatch.settings` 约定的环境变量前缀
ENV_PREFIX = "REGWATCH_"

#: ``api_key_<模型ID>`` → ``REGWATCH_API_KEY_<模型ID大写>``
API_KEY_PREFIX = "api_key_"

DATABASE_URL_KEY = "database_url"
DATABASE_TOKEN_KEY = "database_token"

#: 下载数据库的超时时间（秒）与流式拷贝块大小
DOWNLOAD_TIMEOUT = 180.0
CHUNK_SIZE = 1 << 20

#: 可注入的下载实现，签名与 :func:`_download` 一致（便于测试替换）
Downloader = Callable[..., None]


# ──────────────────────────── secrets 读取 ────────────────────────────


def _load_secrets() -> Mapping[str, Any]:
    """读取 ``st.secrets``；没有配置 Secrets 时返回空字典。"""
    try:
        import streamlit as st

        return dict(st.secrets)
    except Exception as exc:  # 无 secrets 文件属正常情况
        logger.debug("未读取到 Streamlit Secrets：%s", exc)
        return {}


def _scalar(value: Any) -> str:
    """取标量值；嵌套表 / 列表 / 布尔值一律忽略。"""
    if value is None or isinstance(value, (bool, dict, list, tuple)):
        return ""
    text = str(value).strip()
    return text


def apply_env_secrets(secrets: Mapping[str, Any]) -> tuple[str, ...]:
    """把 Secrets 中的标量键桥接成环境变量，返回写入的变量名。

    - ``api_key_<id>`` → ``REGWATCH_API_KEY_<ID>``（与本地环境变量用法一致）；
    - ``regwatch_*`` → 原样大写，如 ``regwatch_database`` → ``REGWATCH_DATABASE``；
    - 其余键忽略；已在环境中存在的变量**不覆盖**（真实环境变量优先）。

    只返回变量名不返回值，避免密钥进入日志。
    """
    applied: list[str] = []
    for raw_key, raw_value in secrets.items():
        name = str(raw_key)
        text = _scalar(raw_value)
        if not text:
            continue
        if name.lower().startswith(ENV_PREFIX.lower()):
            env_name = name.upper()
        elif name.startswith(API_KEY_PREFIX):
            suffix = re.sub(r"[^A-Za-z0-9]", "_", name[len(API_KEY_PREFIX) :]).upper()
            env_name = f"{ENV_PREFIX}API_KEY_{suffix}"
        else:
            continue
        if os.environ.get(env_name):
            logger.debug("环境变量 %s 已存在，忽略同名 Secrets", env_name)
            continue
        os.environ[env_name] = text
        applied.append(env_name)
    if applied:
        logger.info("已从 Secrets 注入环境变量：%s", "、".join(applied))
    return tuple(applied)


# ──────────────────────────── 数据库准备 ────────────────────────────


def resolve_database_path() -> Path:
    """当前配置指向的库文件（云端走 Secrets 透传的 ``REGWATCH_DATABASE``）。"""
    from ..settings import get_settings

    return get_settings().database


#: SQLite 文件头；用来识破 Git LFS 指针文件、半截下载与 HTML 报错页
SQLITE_MAGIC = b"SQLite format 3\x00"

#: 一个合法的 SQLite 库至少有一页
MIN_DATABASE_BYTES = 512


def _database_ready(path: Path) -> bool:
    """文件存在且确实是 SQLite 库（LFS 指针只有约 130 字节，不算可用）。"""
    try:
        if not path.exists() or path.stat().st_size < MIN_DATABASE_BYTES:
            return False
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def _download(url: str, target: Path, *, token: str | None = None) -> None:
    """流式下载到临时文件后原子替换，避免半截文件被当成可用数据库。"""
    request = urllib.request.Request(url, headers={"User-Agent": "regwatch-cloud"})
    if token:
        # 私有仓库的 Release 资产要走 GitHub API，需要鉴权 + octet-stream
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/octet-stream")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.download")
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
            open(tmp, "wb") as handle,
        ):
            shutil.copyfileobj(response, handle, CHUNK_SIZE)
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def ensure_database(
    secrets: Mapping[str, Any],
    *,
    target: Path | None = None,
    download: Downloader | None = None,
) -> Path | None:
    """Secrets 里给了 ``database_url`` 且本地库缺失时下载一次。

    Returns:
        实际下载到的库路径；未配置或库已存在时返回 ``None``。
    """
    url = _scalar(secrets.get(DATABASE_URL_KEY))
    if not url:
        return None
    path = Path(target) if target is not None else resolve_database_path()
    if _database_ready(path):
        logger.info("数据库已存在，跳过下载：%s", path)
        return None
    fetcher = download or _download
    token = _scalar(secrets.get(DATABASE_TOKEN_KEY)) or None
    logger.info("开始下载云端数据库：%s → %s", url, path)
    fetcher(url, path, token=token)
    logger.info("云端数据库就绪：%s（%.1f MB）", path, path.stat().st_size / (1 << 20))
    return path


# ──────────────────────────── 入口 ────────────────────────────


def _warn(message: str) -> None:
    """在页面上提示；没有 Streamlit 运行时（裸跑 / 测试）时只记日志。"""
    logger.warning("%s", message)
    try:
        import streamlit as st

        st.warning(message)
    except Exception:
        logger.debug("Streamlit 运行时不可用，仅记录日志")


def prepare_runtime(secrets: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """云端入口调用的一次性准备，返回诊断信息（便于日志与测试）。

    传入 ``secrets`` 时按该映射处理（测试用），否则读取 ``st.secrets``。
    """
    resolved = dict(secrets) if secrets is not None else dict(_load_secrets())
    report: dict[str, Any] = {"env": (), "database": None, "error": "", "warning": ""}
    if not resolved:
        return report

    report["env"] = apply_env_secrets(resolved)

    target = resolve_database_path()
    url = _scalar(resolved.get(DATABASE_URL_KEY))
    if _database_ready(target):
        return report
    if not url:
        if target.exists():
            # 典型场景：仓库里的库文件是 Git LFS 指针，云端却没拉到实体
            report["warning"] = (
                f"{target.name} 不是有效的 SQLite 库，"
                "若是 Git LFS 跟踪的文件请确认已拉取实体（git lfs pull）"
            )
            _warn(f"数据库文件不可用：{report['warning']}")
        return report

    import streamlit as st

    try:
        with st.spinner("首次启动：正在准备云端数据库…"):
            report["database"] = ensure_database(resolved, target=target)
    except Exception as exc:  # 下载失败也要能启动：空库照样能跑，只是没数据
        report["error"] = str(exc)
        _warn(f"云端数据库下载失败，当前可能没有数据：{exc}")
    return report
