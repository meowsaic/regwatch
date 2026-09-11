"""regwatch 终端界面（Textual）。

``regwatch tui`` 启动。执行通道与网页端 / CLI 完全一致：
收集参数后交给 :class:`~regwatch.jobs.JobManager`，不在此处重写业务逻辑。

MVP 覆盖：AMAC/CSRC 抓取、月度公告下载、摘要提取、报告生成。
模型配置、案例浏览等仍使用 CLI 或 Streamlit 网页端。
"""

from __future__ import annotations

from typing import Any, ClassVar

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    RichLog,
    Select,
    Static,
)

from .jobs import JOB_LABELS, JOB_PARAMS, JobError, get_job_manager
from .logutil import configure_logging

__all__ = ["TUI_JOB_KINDS", "RegwatchApp", "fields_for", "main", "parse_field_value"]

#: MVP 任务清单（顺序即首页展示顺序）
TUI_JOB_KINDS: tuple[str, ...] = (
    "fetch_amac",
    "fetch_csrc",
    "fetch_monthly",
    "summarize",
    "report",
)

#: 相对网页 JOB_PARAMS 的 CLI 对等补充字段：(label, key, default, hint)
TUI_EXTRA_PARAMS: dict[str, tuple[tuple[str, str, Any, str], ...]] = {
    "fetch_csrc": (("并发数", "concurrency", 0, "0=使用配置默认值"),),
    "summarize": (("处理上限", "limit", 0, "0=全部处理"),),
}

#: 固定枚举字段 → 可选项
ENUM_OPTIONS: dict[str, tuple[str, ...]] = {
    "categories": ("all", "Institution", "Personnel"),
    "case_types": ("all", "penalty", "measure"),
    "dataset": ("all", "amac", "csrc"),
}

_HOME_CSS = """
Screen {
    background: $surface;
}
#home-title {
    text-style: bold;
    padding: 1 2 0 2;
}
#home-hint {
    color: $text-muted;
    padding: 0 2 1 2;
}
#model-warning {
    padding: 0 2 1 2;
}
#task-list {
    height: 1fr;
    border: round $primary;
    margin: 0 2;
    padding: 1 0;
}
#form-box {
    padding: 1 2;
}
#form-box Input, #form-box Select, #form-box Checkbox {
    margin: 0 0 1 0;
}
.field-label {
    color: $text-muted;
    margin: 1 0 0 0;
}
#form-actions, #run-actions {
    height: auto;
    padding: 1 2;
    align: left middle;
}
#form-actions Button, #run-actions Button {
    margin-right: 1;
}
#run-status {
    padding: 1 2 0 2;
    text-style: bold;
}
#run-progress {
    margin: 0 2;
}
#run-log {
    margin: 1 2;
    height: 1fr;
    border: round $primary;
}
"""


def fields_for(kind: str) -> tuple[tuple[str, str, Any, str], ...]:
    """返回某任务类型的表单字段定义。"""
    if kind not in JOB_PARAMS:
        raise ValueError(f"未知任务类型：{kind}")
    return JOB_PARAMS[kind] + TUI_EXTRA_PARAMS.get(kind, ())


def parse_field_value(key: str, default: Any, raw: str) -> Any:
    """把 Input 文本按默认值类型转换。"""
    text = (raw or "").strip()
    if isinstance(default, bool):
        return text.lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(default, int):
        if not text:
            return default
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{key} 应为整数，收到 {text!r}") from exc
    return text


def _options_for(kind: str, key: str) -> tuple[str, ...] | None:
    if key not in ENUM_OPTIONS:
        return None
    if kind == "report" and key == "dataset":
        return ("amac", "csrc")
    return ENUM_OPTIONS[key]


def _model_warning() -> str:
    """未配置任何模型时在首页给出提示；配置读取失败则不打扰。"""
    try:
        from .config import get_config

        if not get_config().models():
            return (
                "[yellow]尚未配置模型，请先执行 "
                "`regwatch config add-model` 或在网页端「模型与配置」填写。[/yellow]"
            )
    except Exception:
        return ""
    return ""


def _field_widget(kind: str, label: str, key: str, default: Any, hint: str):
    widget_id = f"f-{key}"
    options = _options_for(kind, key)
    if options is not None:
        value = str(default) if default not in (None, "") else options[0]
        if value not in options:
            value = options[0]
        return Select(
            options=[(opt, opt) for opt in options],
            value=value,
            allow_blank=False,
            id=widget_id,
        )
    if isinstance(default, bool):
        return Checkbox(label, value=bool(default), id=widget_id)
    placeholder = hint or ""
    initial = "" if default in (None, "") else str(default)
    return Input(value=initial, placeholder=placeholder, id=widget_id)


class HomeScreen(Screen):
    """首页：选择要执行的任务。"""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit_app", "退出"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("regwatch 终端界面", id="home-title")
        yield Static(
            "选择任务后填写参数即可运行。模型配置请使用 `regwatch config` 或网页端。",
            id="home-hint",
        )
        warning = _model_warning()
        if warning:
            yield Static(warning, id="model-warning", markup=True)
        yield ListView(
            *[ListItem(Label(JOB_LABELS[kind]), name=kind) for kind in TUI_JOB_KINDS],
            id="task-list",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        kind = event.item.name
        if kind in TUI_JOB_KINDS:
            self.app.push_screen(FormScreen(kind))

    def action_quit_app(self) -> None:
        self.app.exit()


class FormScreen(Screen):
    """任务参数表单。"""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(f"任务：{JOB_LABELS[self.kind]}", id="home-title")
        with VerticalScroll(id="form-box"):
            for label, key, default, hint in fields_for(self.kind):
                if isinstance(default, bool):
                    yield _field_widget(self.kind, label, key, default, hint)
                else:
                    suffix = f"（{hint}）" if hint else ""
                    yield Static(f"{label}{suffix}", classes="field-label")
                    yield _field_widget(self.kind, label, key, default, hint)
        with Horizontal(id="form-actions"):
            yield Button("开始运行", variant="primary", id="btn-run")
            yield Button("返回", id="btn-back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
            return
        if event.button.id != "btn-run":
            return
        try:
            params = self.collect_params()
        except ValueError as exc:
            self.app.notify(str(exc), severity="error", title="参数错误")
            return
        self.app.push_screen(RunScreen(self.kind, params))

    def collect_params(self) -> dict[str, Any]:
        """读取控件值并按默认值类型转换。"""
        params: dict[str, Any] = {}
        for _label, key, default, _hint in fields_for(self.kind):
            options = _options_for(self.kind, key)
            if options is not None:
                raw = self.query_one(f"#f-{key}", Select).value
                if raw is Select.BLANK:
                    raise ValueError(f"{key} 未选择")
                params[key] = str(raw)
            elif isinstance(default, bool):
                params[key] = self.query_one(f"#f-{key}", Checkbox).value
            else:
                text = self.query_one(f"#f-{key}", Input).value
                params[key] = parse_field_value(key, default, text)
        return params


class RunScreen(Screen):
    """运行任务并展示进度与日志。"""

    def __init__(self, kind: str, params: dict[str, Any]) -> None:
        super().__init__()
        self.kind = kind
        self.params = dict(params)
        self.job_id: str | None = None
        self._log_cursor = 0
        self._poll: Any = None
        self._confirm_leave = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(f"运行：{JOB_LABELS[self.kind]}", id="run-status")
        yield ProgressBar(total=100, show_eta=False, id="run-progress")
        yield RichLog(highlight=True, markup=False, wrap=True, id="run-log")
        with Horizontal(id="run-actions"):
            yield Button("取消任务", variant="warning", id="btn-cancel")
            yield Button("返回", id="btn-back")
        yield Footer()

    def on_mount(self) -> None:
        manager = get_job_manager()
        try:
            self.job_id = manager.submit(self.kind, self.params)
        except JobError as exc:
            self.app.notify(str(exc), severity="error", title="无法启动")
            self.app.pop_screen()
            return
        self.query_one("#run-status", Static).update(
            f"运行：{escape(JOB_LABELS[self.kind])}　任务 {self.job_id}　排队中…"
        )
        self._poll = self.set_interval(0.25, self.refresh_status)
        self.refresh_status()

    def refresh_status(self) -> None:
        if not self.job_id:
            return
        manager = get_job_manager()
        record = manager.get(self.job_id)
        if record is None:
            return
        status_label = record.status.label
        message = record.message or record.error or ""
        detail = f"　{escape(message)}" if message else ""
        self.query_one("#run-status", Static).update(
            f"运行：{escape(JOB_LABELS[self.kind])}　"
            f"{status_label}　"
            f"{record.processed}/{record.total or '?'}{detail}"
        )
        bar = self.query_one("#run-progress", ProgressBar)
        if record.total > 0:
            bar.update(total=record.total, progress=record.processed)
        elif record.status.value == "running":
            bar.update(progress=bar.progress)

        self._append_logs(manager)
        if record.status.is_finished:
            if self._poll is not None:
                self._poll.stop()
                self._poll = None
            self.query_one("#btn-cancel", Button).disabled = True
            self._append_logs(manager)
            self._write_result(record)

    def _append_logs(self, manager) -> None:
        if not self.job_id:
            return
        lines = manager.log_lines(self.job_id, start=self._log_cursor)
        if not lines:
            return
        log = self.query_one("#run-log", RichLog)
        for line in lines:
            log.write(line.rstrip("\n"))
        self._log_cursor += len(lines)

    def _write_result(self, record) -> None:
        log = self.query_one("#run-log", RichLog)
        if record.status.value == "success":
            log.write(Text(f"完成：{record.message}", style="green"))
            paths = (record.result or {}).get("paths") or {}
            for kind_name, path in paths.items():
                log.write(Text(f"  {kind_name}: {path}", style="cyan"))
        elif record.status.value == "failed":
            log.write(Text(f"失败：{record.error or record.message}", style="red"))
        elif record.status.value == "cancelled":
            log.write(Text("已取消", style="yellow"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            if not self.job_id:
                return
            get_job_manager().cancel(self.job_id)
            self.app.notify("已请求取消（协作式，稍后生效）", severity="warning")
            return
        if event.button.id == "btn-back":
            if self.job_id:
                record = get_job_manager().get(self.job_id)
                if record is not None and not record.status.is_finished and not self._confirm_leave:
                    self._confirm_leave = True
                    self.app.notify(
                        "任务仍在后台运行，再次点击「返回」离开（可先取消任务）",
                        severity="warning",
                    )
                    return
            self.app.pop_screen()


class RegwatchApp(App[None]):
    """regwatch 终端应用。"""

    TITLE = "regwatch"
    CSS = _HOME_CSS
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+q", "quit", "退出"),
    ]

    def on_mount(self) -> None:
        configure_logging()

    def get_default_screen(self) -> Screen:
        return HomeScreen()


def main() -> None:
    """TUI 入口。"""
    RegwatchApp().run()


if __name__ == "__main__":  # pragma: no cover
    main()
