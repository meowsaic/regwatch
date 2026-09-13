"""``summarize`` —— 结构化摘要提取。"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ...domain import Dataset
from .. import resolve_services
from ..common import console, fail, print_kv, progress_bar

app = typer.Typer(help="结构化摘要提取", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def summarize(
    ctx: typer.Context,
    dataset: Annotated[str, typer.Option("--dataset", help="all / amac / csrc")] = "all",
    workers: Annotated[int, typer.Option("--workers", help="并发数，0=取配置默认值")] = 0,
    limit: Annotated[int, typer.Option("--limit", help="最多处理多少条，0=不限")] = 0,
    retry_failed: Annotated[bool, typer.Option("--retry-failed/--no-retry-failed")] = True,
    redo: Annotated[
        bool,
        typer.Option(
            "--redo",
            help="连同已完成案例一起重跑（提示词 / 分类体系升级后回填用，会再次消耗模型额度）",
        ),
    ] = False,
) -> None:
    """对案例调用大模型提取结构化字段（默认处理全部未提取案例，含失败重试）。"""
    services = resolve_services()
    targets = [item for item in Dataset.all() if dataset.strip().lower() in ("", "all", item.value)]
    if not targets:
        fail(f"数据集应为 all / amac / csrc，收到：{dataset!r}")
        raise typer.Exit(1)

    total: dict[str, Any] = {"success": 0, "skipped": 0, "failed": 0, "errors": []}
    with progress_bar() as progress:
        task_ids: dict[str, Any] = {}

        def hook(done: int, total_count: int, label: str = "") -> None:
            key = str(total_count)
            if key not in task_ids and total_count:
                task_ids[key] = progress.add_task("提取中", total=total_count)
            if key in task_ids:
                progress.update(task_ids[key], completed=done, description=label or "提取中")

        for target in targets:
            result = services.summarization.summarize(
                target,
                workers=workers or None,
                retry_failed=retry_failed,
                include_done=redo,
                limit=limit or None,
                on_progress=hook,
            )
            total["success"] += result.success
            total["skipped"] += result.skipped
            total["failed"] += result.failed
            total["errors"].extend(result.errors)

    print_kv(
        "摘要提取结果",
        {
            "数据集": "、".join(item.label for item in targets),
            "成功": total["success"],
            "跳过（非基金相关）": total["skipped"],
            "失败": total["failed"],
        },
    )
    if total["errors"]:
        console.print("[yellow]失败明细（最多 10 条）[/yellow]")
        for item in total["errors"][:10]:
            console.print(f"  - {item['case_id']}: {item['error']}")
