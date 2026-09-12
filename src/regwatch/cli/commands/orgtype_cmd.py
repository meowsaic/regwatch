"""``org-type`` —— 机构登记类型回填。"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .. import resolve_services
from ..common import console, print_kv

app = typer.Typer(help="机构登记类型回填", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def org_type(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只统计不写盘")] = False,
    limit: Annotated[int, typer.Option("--limit", help="最多处理多少条，0=全部")] = 0,
    interactive: Annotated[bool, typer.Option("--interactive", help="交互式人工补全")] = False,
) -> None:
    """为 AMAC 机构类案例补齐机构登记类型。"""
    services = resolve_services()
    if interactive:
        filled, skipped = services.org_type.interactive_fill()
        print_kv("交互式补全", {"已补全": filled, "已跳过": skipped})
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task_id: list[Any] = [None]

        def hook(done: int, total: int, label: str = "") -> None:
            if task_id[0] is None and total:
                task_id[0] = progress.add_task("回填中", total=total)
            if task_id[0] is not None:
                progress.update(task_id[0], completed=done, description=label or "回填中")

        result = services.org_type.backfill(dry_run=dry_run, limit=limit or None, on_progress=hook)

    print_kv("机构类型回填结果", result.to_dict())
