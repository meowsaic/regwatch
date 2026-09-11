"""regwatch 统一命令行。

::

    regwatch fetch amac     抓取中基协纪律处分案例
    regwatch fetch csrc     抓取证监会处罚与监管措施
    regwatch fetch monthly  下载 AMAC 月度公告 PDF
    regwatch fetch url      抓取单个案例 URL
    regwatch summarize      批量结构化摘要提取
    regwatch report         生成季度 / 自定义区间报告
    regwatch org-type       机构登记类型回填
    regwatch config         配置管理（show / test / add-model / set-model）
    regwatch web            启动 Streamlit 网页界面
    regwatch tui            启动 Textual 终端界面

所有子命令均复用 :mod:`regwatch.jobs` 中定义的任务实现，
与网页端行为完全一致；缺省参数取自 ``config.json``。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .config import TASK_KINDS, TASK_LABELS, ConfigError, get_config
from .jobs import JobError
from .llm import test_profile
from .logutil import configure_logging
from .storage import DATASETS

__all__ = ["app", "main"]

app = typer.Typer(
    name="regwatch",
    help="AMAC 与证监会基金相关案例的采集、结构化摘要与报告工具",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

fetch_app = typer.Typer(help="抓取案例与公告", no_args_is_help=True)
config_app = typer.Typer(help="配置管理", no_args_is_help=True)
app.add_typer(fetch_app, name="fetch", help="抓取案例与公告")
app.add_typer(config_app, name="config", help="配置管理")

console = Console()


def _parse_date(value: str | None, label: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        console.print(f"[red]错误：{label}格式应为 YYYY-MM-DD，收到 {value!r}[/red]")
        raise typer.Exit(code=2) from exc


def _print_counts(title: str, result: dict) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("指标", style="cyan")
    table.add_column("数值", justify="right")
    for key in ("total", "success", "skipped", "failed"):
        if key in result:
            table.add_row(key, str(result[key]))
    console.print(table)
    detail = result.get("detail") or {}
    if detail:
        detail_table = Table(title="分类明细")
        detail_table.add_column("来源")
        detail_table.add_column("总数", justify="right")
        detail_table.add_column("已完成", justify="right")
        detail_table.add_column("待处理", justify="right")
        detail_table.add_column("失败", justify="right")
        for name, stats in detail.items():
            detail_table.add_row(
                name,
                str(stats.get("total", 0)),
                str(stats.get("done", 0)),
                str(stats.get("pending", 0)),
                str(stats.get("failed", 0)),
            )
        console.print(detail_table)


def _handle_error(exc: Exception) -> None:
    console.print(f"[red]错误：{exc}[/red]")
    raise typer.Exit(code=2)


# ──────────────────────────── fetch ────────────────────────────


@fetch_app.command("amac")
def fetch_amac(
    start: str | None = typer.Option(None, "--start", help="起始日期 YYYY-MM-DD，留空=上一季度"),
    end: str | None = typer.Option(None, "--end", help="结束日期 YYYY-MM-DD"),
    categories: str = typer.Option("all", "--categories", help="all / Institution / Personnel"),
) -> None:
    """抓取中基协纪律处分案例。"""
    configure_logging()
    from .jobs import run_task

    try:
        result = run_task(
            "fetch_amac",
            {"start_date": start or "", "end_date": end or "", "categories": categories},
        )
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return
    _print_counts("AMAC 抓取结果", result)


@fetch_app.command("csrc")
def fetch_csrc(
    start: str | None = typer.Option(None, "--start", help="起始日期 YYYY-MM-DD，留空=2022-01-01"),
    end: str | None = typer.Option(None, "--end", help="结束日期 YYYY-MM-DD，留空=今天"),
    bureaus: str = typer.Option("all", "--bureaus", help="逗号分隔的局英文标识，all=全部 37 个"),
    case_types: str = typer.Option("all", "--types", help="all / penalty / measure"),
    concurrency: int = typer.Option(0, "--concurrency", help="详情页并发数，0=使用配置默认值"),
) -> None:
    """抓取证监会行政处罚与监管措施。"""
    configure_logging()
    from .jobs import run_task

    try:
        result = run_task(
            "fetch_csrc",
            {
                "start_date": start or "",
                "end_date": end or "",
                "bureaus": bureaus,
                "case_types": case_types,
                "concurrency": concurrency,
            },
        )
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return
    _print_counts("CSRC 抓取结果", result)


@fetch_app.command("monthly")
def fetch_monthly() -> None:
    """下载上一个自然月的 AMAC 纪律处分公告 PDF。"""
    configure_logging()
    from .jobs import run_task

    try:
        result = run_task("fetch_monthly", {})
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return
    console.print(
        f"[green]下载成功 {result.get('success', 0)} 个，失败 {result.get('failed', 0)} 个[/green]"
    )
    for name, path in (result.get("dirs") or {}).items():
        console.print(f"  {name}: {path}")


@fetch_app.command("url")
def fetch_url(
    url: str = typer.Option(..., "--url", help="案例详情页或 PDF 链接"),
    dataset: str = typer.Option("amac", "--dataset", help="amac / csrc"),
    bureau: str = typer.Option("HQ", "--bureau", help="仅 CSRC：来源局英文标识"),
    category: str = typer.Option(
        "Institution", "--category", help="仅 AMAC：Institution / Personnel"
    ),
    case_type: str = typer.Option("penalty", "--case-type", help="仅 CSRC：penalty / measure"),
) -> None:
    """抓取单个案例 URL 并落库。"""
    configure_logging()
    dataset = dataset.strip().lower()
    if dataset not in DATASETS:
        _handle_error(ValueError(f"数据集应为 {'、'.join(DATASETS)}，收到 {dataset!r}"))
        return

    try:
        case: Any = None
        if dataset == "amac":
            from .sources import amac

            case = amac.fetch_single(url, category=category)
            label = f"amac / {category}"
        else:
            from .sources import csrc

            case = csrc.fetch_single(url, bureau=bureau, case_type=case_type)
            label = f"csrc / {bureau} / {case_type}"
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return

    if case is None or not getattr(case, "raw_text", ""):
        console.print(f"[red]案例抓取失败：{url}[/red]")
        raise typer.Exit(code=1)
    case_id = getattr(case, "case_id", "")
    console.print(f"[green]已保存 {case_id}[/green]（{label}）")
    console.print(f"  正文长度：{len(getattr(case, 'raw_text', ''))} 字符")


# ──────────────────────────── summarize ────────────────────────────


@app.command()
def summarize(
    dataset: str = typer.Option("all", "--dataset", help="all / amac / csrc"),
    workers: int = typer.Option(0, "--workers", help="并发线程数，0=使用配置默认值"),
    retry_failed: bool = typer.Option(True, "--retry/--no-retry", help="是否重试上次失败的案例"),
    limit: int = typer.Option(0, "--limit", help="最多处理多少条，0=全部"),
) -> None:
    """批量提取结构化摘要。"""
    configure_logging()
    from .jobs import run_task

    try:
        result = run_task(
            "summarize",
            {"dataset": dataset, "workers": workers, "retry_failed": retry_failed, "limit": limit},
        )
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return
    _print_counts("摘要提取结果", result)
    for error in (result.get("errors") or [])[:10]:
        console.print(f"  [red]{error.get('case_id')}[/red]: {error.get('error')}")


# ──────────────────────────── report ────────────────────────────


@app.command()
def report(
    dataset: str = typer.Option("amac", "--dataset", help="amac / csrc"),
    start: str | None = typer.Option(None, "--start", help="起始日期 YYYY-MM-DD，留空=全量"),
    end: str | None = typer.Option(None, "--end", help="结束日期 YYYY-MM-DD，留空=全量"),
    llm: bool = typer.Option(False, "--llm/--no-llm", help="是否调用模型撰写合规建议"),
    out: Path | None = typer.Option(None, "--out", help="输出目录，默认为数据集 reports 目录"),
    name: str | None = typer.Option(None, "--name", help="输出文件名（不含扩展名）"),
) -> None:
    """生成 Markdown / HTML / JSON 三格式分析报告。"""
    configure_logging()
    from . import report as report_mod

    dataset = dataset.strip().lower()
    if dataset not in DATASETS:
        _handle_error(ValueError(f"数据集应为 {'、'.join(DATASETS)}，收到 {dataset!r}"))
        return

    try:
        output = report_mod.build_report(
            dataset,
            start_date=start or "",
            end_date=end or "",
            use_llm=llm,
        )
        directory = (
            Path(out)
            if out
            else get_config().data_root("csrc_reports" if dataset == "csrc" else "amac_reports")
        )
        stem = name or f"{dataset}_报告_{datetime.now().strftime('%Y%m%d_%H%M')}"
        paths = report_mod.save_report_files(output, directory, stem=stem)
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return

    console.print(f"[green]报告已生成：{output.row_count} 条案例[/green]")
    for kind, path in paths.items():
        console.print(f"  {kind}: {path}")


# ──────────────────────────── org-type ────────────────────────────


@app.command("org-type")
def org_type_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅统计，不写盘"),
    limit: int = typer.Option(0, "--limit", help="最多处理多少条，0=全部"),
    interactive: bool = typer.Option(False, "--interactive", help="进入交互式人工补全"),
) -> None:
    """为历史 AMAC 机构类案例补齐机构登记类型。"""
    configure_logging()
    from .org_type import backfill_org_types, interactive_fill

    try:
        if interactive:
            filled, skipped = interactive_fill()
            console.print(f"[green]交互式补全完成：已补全 {filled}，跳过 {skipped}[/green]")
            return
        result = backfill_org_types(dry_run=dry_run, limit=limit or None)
    except (JobError, ConfigError) as exc:
        _handle_error(exc)
        return

    table = Table(title="机构类型回填结果" + ("（dry-run）" if dry_run else ""))
    for key in ("total", "already_have", "org_type_filled", "entity_filled", "unresolved"):
        table.add_row(key, str(result.to_dict()[key]))
    console.print(table)
    if result.manual_list_path:
        console.print(f"需人工补全清单：{result.manual_list_path}")


# ──────────────────────────── config ────────────────────────────


@config_app.command("show")
def config_show() -> None:
    """查看当前配置（密钥脱敏）。"""
    cfg = get_config()

    table = Table(title="模型配置")
    for column in ("标识", "名称", "接口地址", "文本模型", "视觉模型", "密钥"):
        table.add_column(column, justify="left")
    for profile in cfg.models():
        table.add_row(
            profile.id,
            profile.display_name,
            profile.base_url,
            profile.model,
            profile.vision_model or "（回落文本模型）",
            profile.masked_key,
        )
    console.print(table)
    if not cfg.models():
        console.print(
            "[yellow]尚未配置任何模型，请使用 `regwatch config add-model` 或网页端「模型配置」页添加[/yellow]"
        )

    tasks = Table(title="任务 → 模型绑定")
    tasks.add_column("任务")
    tasks.add_column("绑定模型")
    for kind in TASK_KINDS:
        bound = cfg.task_model_id(kind)
        tasks.add_row(
            f"{kind}（{TASK_LABELS[kind]}）", bound or "[yellow]（未绑定，使用第一个模型）[/yellow]"
        )
    console.print(tasks)

    roots = Table(title="数据目录")
    roots.add_column("键")
    roots.add_column("路径")
    for key, path in cfg.data_roots().items():
        exists = "[green]✓[/green]" if path.exists() else "[red]✗[/red]"
        roots.add_row(key, f"{path} {exists}")
    console.print(roots)

    console.print(f"配置文件：{cfg.path}（{'存在' if cfg.exists_on_disk() else '尚未生成'}）")
    console.print(
        f"并发：fetch={cfg.concurrency('fetch', 8)}, summarize={cfg.concurrency('summarize', 5)}"
    )


@config_app.command("test")
def config_test(
    model_id: str | None = typer.Argument(None, help="要测试的模型 ID；留空测试全部"),
) -> None:
    """测试模型连通性（最小请求，不回显密钥）。"""
    cfg = get_config()
    profiles = cfg.models()
    if model_id:
        profile = cfg.get_model(model_id)
        if profile is None:
            _handle_error(ValueError(f"未找到模型配置：{model_id}"))
            return
        profiles = [profile]
    if not profiles:
        _handle_error(ValueError("尚未配置任何模型"))
        return

    for profile in profiles:
        ok, message = test_profile(profile)
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"{mark} {profile.display_name}（{profile.id}）：{message}")


@config_app.command("add-model")
def config_add_model(
    model_id: str = typer.Option(..., "--id", help="配置唯一标识，如 deepseek"),
    base_url: str = typer.Option(..., "--base-url", help="OpenAI 兼容接口地址"),
    api_key: str = typer.Option("", "--api-key", help="API Key（留空则使用环境变量）"),
    model: str = typer.Option(..., "--model", help="文本模型名，如 deepseek-chat"),
    label: str = typer.Option("", "--label", help="展示名称"),
    vision_model: str = typer.Option("", "--vision-model", help="视觉模型名（可选）"),
    token_param: str = typer.Option(
        "max_tokens", "--token-param", help="max_tokens / max_completion_tokens"
    ),
) -> None:
    """新增或覆盖一条模型配置。"""
    from .datamodels import ModelProfile

    cfg = get_config()
    profile = ModelProfile(
        id=model_id,
        label=label,
        base_url=base_url,
        api_key=api_key,
        model=model,
        vision_model=vision_model,
        token_param=token_param,
    )
    missing = profile.missing_fields()
    if missing:
        _handle_error(ValueError(f"缺少必填项：{'、'.join(missing)}"))
        return
    try:
        cfg.upsert_model(profile)
    except ConfigError as exc:
        _handle_error(exc)
        return
    console.print(f"[green]已保存模型配置：{profile.id}[/green]")
    ok, message = test_profile(cfg.get_model(profile.id))  # type: ignore[arg-type]
    mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"{mark} 连通性测试：{message}")


@config_app.command("set-model")
def config_set_model(
    task: str = typer.Option(..., "--task", help=f"任务类型：{'、'.join(TASK_KINDS)}"),
    model_id: str = typer.Option(..., "--model", help="模型配置 ID"),
) -> None:
    """把某个任务绑定到指定模型。"""
    cfg = get_config()
    try:
        cfg.set_task_model(task, model_id)
    except ConfigError as exc:
        _handle_error(exc)
        return
    console.print(f"[green]已绑定：{task} → {model_id}[/green]")


# ──────────────────────────── tui ────────────────────────────


@app.command()
def tui() -> None:
    """启动 Textual 终端界面（菜单选任务，无需记子命令）。"""
    try:
        from .tui import main as tui_main
    except ImportError:
        console.print('[red]未安装 textual，请执行：uv sync 或 pip install "textual>=0.80"[/red]')
        raise typer.Exit(code=2) from None
    tui_main()


# ──────────────────────────── web ────────────────────────────


@app.command()
def web(
    port: int = typer.Option(8501, "--port", help="网页端口"),
    host: str = typer.Option("localhost", "--host", help="监听地址"),
) -> None:
    """启动 Streamlit 网页界面。"""
    from regwatch import web as web_pkg

    app_path = Path(web_pkg.__file__).resolve().parent / "app.py"
    if not app_path.exists():
        console.print(f"[red]未找到网页入口：{app_path}[/red]")
        raise typer.Exit(code=2)

    try:
        import streamlit.web.cli as stcli
    except ImportError:
        console.print(
            "[red]未安装 streamlit，请执行：uv sync --extra web 或 pip install streamlit plotly[/red]"
        )
        raise typer.Exit(code=2) from None

    console.print(f"[green]启动网页界面：http://{host}:{port}[/green]")
    sys.argv = [
        "streamlit",
        "run",
        str(app_path),
        f"--server.port={port}",
        f"--server.address={host}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    stcli.main()


def main() -> None:  # pragma: no cover
    """命令行入口。"""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
