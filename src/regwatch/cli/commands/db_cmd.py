"""``db`` —— 数据库的初始化、体检与数据质量修复。"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from .. import resolve_services
from ..common import console, print_kv

app = typer.Typer(help="数据库维护", no_args_is_help=True)


@app.command("init")
def init() -> None:
    """建库并升级到最新结构（已存在则只做升级）。"""
    services = resolve_services()
    services.store.meta.revision()
    print_kv(
        "数据库就绪", {"路径": str(services.store.path), "数据版本": services.store.revision()}
    )


@app.command("rebuild-violations")
def rebuild_violations() -> None:
    """按当前分类体系重建违规类型关联表（不调用模型）。

    分类体系升级（合并同义类型、补充别名）后执行一次，
    即可让筛选与统计口径统一；``summaries.violation_type`` 原文不受影响。
    """
    services = resolve_services()
    count = services.store.summaries.rebuild_violations()
    services.store.touch()
    print_kv(
        "违规类型关联表重建完成",
        {"摘要条数": count, "数据版本": services.store.revision()},
    )


@app.command("repair")
def repair(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只统计不写库")] = False,
    short_body_max: Annotated[
        int, typer.Option("--short-body-max", help="短正文阈值（字符）")
    ] = 200,
) -> None:
    """确定性数据质量修复（不调用模型）。

    回填 AMAC 标题当事人、CSRC 文书号、正文落款处分日期；
    重建违规类型关联表；列出过短正文。
    """
    services = resolve_services()
    if services.data_repair is None:
        console.print("[red]data_repair 服务未装配[/red]")
        raise typer.Exit(1)
    report = services.data_repair.repair(dry_run=dry_run, short_body_max=short_body_max)
    payload = report.as_dict()
    samples = payload.pop("samples", {})
    short = payload.pop("short_bodies", [])
    console.print(json.dumps(payload, ensure_ascii=False, indent=2))
    if samples:
        console.print("\n[bold]样例[/bold]")
        for key, items in samples.items():
            console.print(f"  {key}:")
            for item in items:
                console.print(f"    - {item}")
    if short:
        console.print(f"\n[bold]短正文[/bold]（前 20 / 共 {len(short)}）")
        for item in short[:20]:
            console.print(f"  {item['dataset']}:{item['case_id']} length={item['length']}")
    if dry_run:
        console.print("[yellow]dry-run：未写入数据库[/yellow]")


@app.command("stats")
def stats() -> None:
    """打印库内规模与状态分布。"""
    services = resolve_services()
    totals = services.store.cases.totals()
    console.print(json.dumps(totals, ensure_ascii=False, indent=2))
    print_kv(
        "库内概览",
        {
            "数据库": str(services.store.path),
            "案例总数": totals.get("all", 0),
            "正文条数": totals.get("bodies", 0),
            "摘要条数": services.store.summaries.count(),
            "数据版本": services.store.revision(),
        },
    )
