"""``report`` —— 报告生成。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ...domain import Dataset
from .. import resolve_services
from ..common import console, fail, print_kv

app = typer.Typer(help="报告生成", invoke_without_command=True)


@app.callback(invoke_without_command=True)
def report(
    ctx: typer.Context,
    dataset: Annotated[str, typer.Option("--dataset", help="amac / csrc")] = "amac",
    start: Annotated[str, typer.Option("--start", help="起始日期，不填=全量")] = "",
    end: Annotated[str, typer.Option("--end", help="结束日期，不填=全量")] = "",
    use_llm: Annotated[bool, typer.Option("--llm/--no-llm", help="是否用模型撰写合规建议")] = False,
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="输出目录，默认 <reports_dir>/<dataset>")
    ] = None,
    save: Annotated[bool, typer.Option("--save/--no-save", help="是否落盘")] = True,
) -> None:
    """按数据集与日期范围生成分析报告。"""
    target = Dataset.parse(dataset)
    if target is None:
        fail(f"数据集应为 amac / csrc，收到：{dataset!r}")
        raise typer.Exit(1)

    services = resolve_services()
    output = services.reporting.build_report(
        target, start_date=start, end_date=end, use_llm=use_llm
    )

    paths: dict[str, str] = {}
    if save:
        directory = out_dir or (services.settings.reports_dir / target.value)
        paths = services.reporting.save(output, directory)

    print_kv(
        "报告生成结果",
        {
            "标题": output.title,
            "周期": output.period_label,
            "案例数": output.row_count,
            **{f"产物[{kind}]": path for kind, path in paths.items()},
        },
    )
    if not save:
        console.print(output.markdown)
