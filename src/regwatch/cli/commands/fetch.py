"""``fetch`` —— 案例抓取。

进度以富文本进度条呈现，实际抓取逻辑全部委托 :mod:`regwatch.sources`。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import typer
from rich.progress import Progress

from ...domain import Dataset
from ...sources import amac, csrc
from ...sources.common import FETCH_OVERLAP_DAYS, parse_iso_date, split_csv
from ...sources.csrc.constants import DEFAULT_START_DATE
from .. import resolve_services
from ..common import console, fail, print_kv, progress_bar

app = typer.Typer(help="案例抓取", no_args_is_help=True)

#: AMAC 全量重扫起点：官网现存最早的机构处分案例发布于 2015-01-20
AMAC_FULL_START = date(2015, 1, 1)


def _parse_date(value: str | None, field_name: str) -> date | None:
    """解析 ``YYYY-MM-DD``；格式非法时按命令行错误退出。"""
    try:
        return parse_iso_date(value, field_name)
    except ValueError as exc:
        fail(str(exc))


def _hook(progress: Progress):
    task_id: list[Any] = [None]

    def on_progress(done: int, total: int, label: str = "") -> None:
        if task_id[0] is None and total:
            task_id[0] = progress.add_task("抓取中", total=total)
        if task_id[0] is not None:
            progress.update(task_id[0], completed=done, description=label or "抓取中")

    return on_progress


@app.command("amac")
def fetch_amac(
    start: Annotated[
        str,
        typer.Option(
            "--start",
            help=f"起始日期 YYYY-MM-DD，不填=上次覆盖日期前 {FETCH_OVERLAP_DAYS} 天（空库=上季度初）",
        ),
    ] = "",
    end: Annotated[str, typer.Option("--end", help="结束日期 YYYY-MM-DD，不填=今天")] = "",
    categories: Annotated[
        str, typer.Option("--categories", help="all / Institution / Personnel，逗号分隔")
    ] = "all",
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help=f"全量重扫（补历史缺口）：从 {AMAC_FULL_START} 起，已入库案例自动跳过",
        ),
    ] = False,
) -> None:
    """抓取中基协纪律处分案例。"""
    services = resolve_services()
    selected = None if categories.strip().lower() in ("", "all") else split_csv(categories)
    with progress_bar() as progress:
        result = amac.fetch(
            start_date=AMAC_FULL_START if full else _parse_date(start, "起始日期"),
            end_date=_parse_date(end, "结束日期"),
            categories=selected,
            store=services.store,
            llm=services.llm,
            org_type=services.org_type,
            on_progress=_hook(progress),
        )
    print_kv("AMAC 抓取结果", result.to_dict())


@app.command("csrc")
def fetch_csrc(
    start: Annotated[
        str,
        typer.Option(
            "--start",
            help=f"起始日期，不填=上次覆盖日期前 {FETCH_OVERLAP_DAYS} 天（空库=2022-01-01）",
        ),
    ] = "",
    end: Annotated[str, typer.Option("--end", help="结束日期，不填=今天")] = "",
    bureaus: Annotated[
        str, typer.Option("--bureaus", help="来源局英文标识，逗号分隔；all=全部")
    ] = "all",
    types: Annotated[
        str, typer.Option("--types", help="all / penalty / measure，逗号分隔")
    ] = "all",
    concurrency: Annotated[int, typer.Option("--concurrency", help="并发线程数")] = 0,
    full: Annotated[
        bool,
        typer.Option(
            "--full",
            help=f"全量重扫（补历史缺口）：等价于 --start {DEFAULT_START_DATE}，已入库案例自动跳过",
        ),
    ] = False,
) -> None:
    """抓取证监会处罚与监管措施。"""
    services = resolve_services()
    with progress_bar() as progress:
        result = csrc.fetch(
            start_date=DEFAULT_START_DATE if full else _parse_date(start, "起始日期"),
            end_date=_parse_date(end, "结束日期"),
            bureaus=None if bureaus.strip().lower() in ("", "all") else split_csv(bureaus),
            case_types=None if types.strip().lower() in ("", "all") else split_csv(types),
            store=services.store,
            concurrency=concurrency or None,
            on_progress=_hook(progress),
        )
    print_kv("CSRC 抓取结果", result.to_dict())


@app.command("url")
def fetch_url(
    url: Annotated[str, typer.Option("--url", help="案例详情页 URL")],
    dataset: Annotated[str, typer.Option("--dataset", help="amac / csrc")] = "amac",
    bureau: Annotated[str, typer.Option("--bureau", help="CSRC 来源局，如 HQ")] = "HQ",
    case_type: Annotated[str, typer.Option("--case-type", help="CSRC 案例类型")] = "penalty",
    category: Annotated[str, typer.Option("--category", help="AMAC 分类")] = "Institution",
) -> None:
    """抓取单条案例链接。"""
    services = resolve_services()
    target = Dataset.parse(dataset)
    record: Any = None
    if target is Dataset.AMAC:
        record = amac.fetch_single(
            url, category, store=services.store, llm=services.llm, org_type=services.org_type
        )
    elif target is Dataset.CSRC:
        record = csrc.fetch_single(url, bureau, case_type, store=services.store)
    else:
        fail(f"数据集应为 amac / csrc，收到：{dataset!r}")
        raise typer.Exit(1)

    if record is None:
        fail("未能抓取到案例内容")
    console.print(f"[green]✓ 已入库[/green] {record.case_id} | {record.title}")
