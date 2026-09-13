"""公共辅助：进度条、结果呈现与错误处理。"""

from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

__all__ = ["console", "fail", "print_json", "print_kv", "print_table", "progress_bar"]

console = Console()


def progress_bar() -> Progress:
    """统一的命令行进度条（任务结束后自动清除，避免污染结果输出）。"""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


def print_kv(title: str, data: dict[str, Any]) -> None:
    """按键值表打印一次任务的汇总结果。"""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    table.add_column("字段")
    table.add_column("值")
    for key, value in data.items():
        table.add_row(str(key), str(value))
    console.print(table)


def print_table(
    title: str,
    columns: list[str],
    rows: list[list[Any]],
    *,
    limit: int = 0,
) -> None:
    """打印表格；``limit > 0`` 时只显示前若干行并提示省略。"""
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for column in columns:
        table.add_column(column)
    shown = rows[:limit] if limit > 0 else rows
    for row in shown:
        table.add_row(*[str(cell) for cell in row])
    console.print(table)
    if limit and len(rows) > limit:
        console.print(f"[dim]……共 {len(rows)} 行，仅显示前 {limit} 行[/dim]")


def print_json(data: Any) -> None:
    """以 JSON 形式输出（便于脚本消费）。"""
    import json

    console.print_json(json.dumps(data, ensure_ascii=False, default=str))


def fail(message: str, code: int = 1) -> NoReturn:
    """打印错误并以给定退出码结束。"""
    console.print(f"[bold red]✗ {message}[/bold red]")
    raise typer.Exit(code)
