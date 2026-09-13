"""``config`` —— 配置查看与模型管理。"""

from __future__ import annotations

from typing import Annotated

import typer

from ...settings import TASK_KINDS, TASK_LABELS, ConfigError, store
from ..common import console, fail, print_kv, print_table

app = typer.Typer(help="配置与模型管理", no_args_is_help=True)


@app.command("show")
def show() -> None:
    """展示当前生效的配置（密钥自动脱敏）。"""
    settings = store().settings
    print_kv(
        "当前配置",
        {
            "配置文件": str(settings.source_path),
            "数据库": str(settings.database),
            "报告目录": str(settings.reports_dir),
            "并发（抓取）": settings.concurrency_for("fetch", 8),
            "并发（摘要）": settings.concurrency_for("summarize", 5),
        },
    )

    if not settings.models:
        console.print("[yellow]尚未配置任何模型[/yellow]")
        return

    print_table(
        "模型条目",
        ["ID", "名称", "接口地址", "文本模型", "视觉模型", "密钥"],
        [
            [
                profile.id,
                profile.display_name,
                profile.base_url,
                profile.model,
                profile.resolved_vision_model(),
                profile.masked_key,
            ]
            for profile in settings.models
        ],
    )
    print_table(
        "任务绑定",
        ["任务", "说明", "模型"],
        [
            [kind, TASK_LABELS.get(kind, kind), settings.task_model(kind) or "（未绑定）"]
            for kind in TASK_KINDS
        ],
    )


@app.command("test")
def test() -> None:
    """测试全部模型条目的连通性。"""
    from ...llm import LLMClientFactory

    current = store().settings
    entries = LLMClientFactory.from_settings(current).test_all()
    print_table(
        "连通性测试",
        ["ID", "名称", "结果", "说明"],
        [
            [profile.id, profile.display_name, "✓" if ok else "✗", message]
            for profile, ok, message in entries
        ],
    )
    raise typer.Exit(0 if all(ok for _profile, ok, _message in entries) else 1)


@app.command("add-model")
def add_model(
    model_id: Annotated[str, typer.Option("--id", help="配置标识，如 deepseek")],
    base_url: Annotated[str, typer.Option("--base-url", help="接口根地址")],
    model: Annotated[str, typer.Option("--model", help="文本模型名")],
    api_key: Annotated[str, typer.Option("--api-key", help="密钥；留空则用环境变量")] = "",
    label: Annotated[str, typer.Option("--label", help="展示名称")] = "",
    vision_model: Annotated[str, typer.Option("--vision-model", help="视觉模型名")] = "",
    token_param: Annotated[
        str, typer.Option("--token-param", help="max_tokens 参数名")
    ] = "max_tokens",
) -> None:
    """新增或更新一条模型配置（同名条目的未填写字段会被保留）。"""
    try:
        profile = store().save_model_from_fields(
            model_id=model_id,
            base_url=base_url,
            model=model,
            api_key=api_key,
            label=label,
            vision_model=vision_model,
            token_param=token_param,
        )
    except ConfigError as exc:
        fail(str(exc))
    console.print(f"[green]✓ 已保存模型配置[/green] {profile.id}")


@app.command("remove-model")
def remove_model(model_id: Annotated[str, typer.Argument(help="配置标识")]) -> None:
    """删除一条模型配置。"""
    store().delete_model(model_id)
    console.print(f"[green]✓ 已删除模型配置[/green] {model_id}")


@app.command("set-model")
def set_model(
    task: Annotated[str, typer.Argument(help=f"任务类型，可选 {'/'.join(TASK_KINDS)}")],
    model_id: Annotated[str, typer.Argument(help="模型配置 ID，留空表示解绑")] = "",
) -> None:
    """把某类任务绑定到指定模型。"""
    try:
        store().set_task_model(task, model_id)
    except ConfigError as exc:
        fail(str(exc))
    console.print(f"[green]✓ 已绑定[/green] {task} → {model_id or '（未绑定）'}")


@app.command("set-database")
def set_database(path: Annotated[str, typer.Argument(help="SQLite 库文件路径")]) -> None:
    """修改数据文件路径（相对路径基于项目根）。"""
    settings = store().set_database(path)
    console.print(f"[green]✓ 数据库已切换[/green] {settings.database}")
