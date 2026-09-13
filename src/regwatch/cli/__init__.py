"""命令行入口。

子命令按职责拆分到 :mod:`regwatch.cli.commands` 下，
每个模块只负责「参数解析 + 结果呈现」，业务逻辑一律委托用例层。
"""

from __future__ import annotations

from pathlib import Path

from ..services import Services, get_services

__all__ = ["resolve_services"]

_DatabaseOverride: Path | None = None


def set_database(path: Path | str | None) -> None:
    """设置本次进程使用的库文件（由根命令的 ``--database`` 注入）。"""
    global _DatabaseOverride
    _DatabaseOverride = Path(path) if path else None


def resolve_services() -> Services:
    """按当前数据库覆盖设置返回服务门面（未覆盖时走进程级单例）。"""
    return get_services(database=_DatabaseOverride)
