"""``regwatch`` 命令行入口。

子命令分组：

``fetch``（amac / csrc / url）、``summarize``、``report``、
``org-type``、``config``（show / test / add-model / remove-model / set-model / set-database）、
``db``（init / rebuild-violations / repair / stats）、``web``、``jobs``。

业务一律委托 :mod:`regwatch.services`，本模块只做参数解析与结果呈现。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

from .. import __version__
from . import resolve_services, set_database
from .commands import (
    config_app,
    db_app,
    fetch_app,
    org_type_app,
    report_app,
    summarize_app,
)
from .common import console, print_kv, print_table

app = typer.Typer(
    help="regwatch —— 基金监管案例采集、结构化摘要与统计分析",
    no_args_is_help=True,
    invoke_without_command=True,
)

app.add_typer(fetch_app, name="fetch")
app.add_typer(summarize_app, name="summarize")
app.add_typer(report_app, name="report")
app.add_typer(org_type_app, name="org-type")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", "-V", help="打印版本号并退出")] = False,
    database: Annotated[
        str | None, typer.Option("--database", help="覆盖 config.json 中的库文件路径")
    ] = None,
) -> None:
    """regwatch 根命令：``--database`` 可临时指定 SQLite 库文件。"""
    if version:
        console.print(__version__)
        raise typer.Exit(0)
    set_database(database)


@app.command("web")
def web(
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8501,
    address: Annotated[str, typer.Option("--address", help="监听地址")] = "localhost",
) -> None:
    """启动 Streamlit 网页看板。"""
    from ..web import app_path

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path()),
        f"--server.port={port}",
        f"--server.address={address}",
    ]
    raise typer.Exit(subprocess.call(command))


@app.command("jobs")
def jobs(
    limit: Annotated[int, typer.Option("--limit", help="显示条数")] = 20,
) -> None:
    """列出最近的后台任务。"""
    services = resolve_services()
    manager = services.jobs
    if manager is None:  # pragma: no cover - build_services 总会回填
        console.print("[red]任务管理器尚未初始化[/red]")
        raise typer.Exit(1)
    records = manager.jobs(limit=limit)
    print_table(
        "后台任务",
        ["ID", "类型", "标题", "状态", "进度", "创建时间"],
        [
            [
                record.id,
                record.kind.label,
                record.title,
                record.status.label,
                f"{record.progress_percent}%",
                record.created_at,
            ]
            for record in records
        ],
    )
    if not records:
        console.print("[dim]暂无任务记录[/dim]")


@app.command("info")
def info() -> None:
    """打印当前环境的配置与数据规模。"""
    services = resolve_services()
    totals = services.store.cases.totals()
    print_kv(
        "regwatch 环境",
        {
            "版本": __version__,
            "数据库": str(services.store.path),
            "报告目录": str(services.settings.reports_dir),
            "案例总数": totals.get("all", 0),
            "摘要条数": services.store.summaries.count(),
            "模型条目": len(services.settings.models),
        },
    )


if __name__ == "__main__":  # pragma: no cover
    app()
