"""任务中心：按 :data:`~regwatch.services.JOB_SPECS` 生成表单、提交与监控。"""

from __future__ import annotations

import html as html_lib
import time
from datetime import date

import streamlit as st

from regwatch.domain import CaseQuery, Dataset, JobKind, JobStatus
from regwatch.services import JOB_SPECS, JobError
from regwatch.sources.amac import first_run_start
from regwatch.sources.common import default_fetch_range
from regwatch.sources.csrc.constants import DEFAULT_START_DATE
from regwatch.web import access
from regwatch.web.components import ui
from regwatch.web.components.data import clear_data_cache, services
from regwatch.web.state import get, set_value

__all__ = ["render"]

_POLL_SECONDS = 2


def render() -> None:
    ui.page_header("任务中心", "网页端与命令行共用同一套任务编排")

    manager = services().jobs
    editable = access.require_admin("jobs")

    if editable:
        _render_submit(manager)

    ui.section_header("任务列表", "最近 20 条")
    records = manager.jobs(limit=20)
    if not records:
        if editable:
            ui.empty_state("暂无任务记录", "选择上方任务并提交即可开始。")
        else:
            ui.empty_state("只读模式", "云端不提供任务提交；更新数据请在本地运行后 push。")
        return

    for record in records:
        with st.container(border=True):
            left, right = st.columns([5, 1], vertical_alignment="center")
            with left:
                # status_badge 输出受信任 HTML；标题转义后拼接，避免 title 污染
                st.markdown(
                    f"**{html_lib.escape(record.title)}**　{ui.status_badge(record.status.value)}",
                    unsafe_allow_html=True,
                )
                st.caption(f"ID {record.id} · {record.kind.label} · 创建于 {record.created_at}")
                if record.total:
                    st.progress(record.progress, text=f"{record.processed}/{record.total}")
                if record.message:
                    st.caption(record.message)
                if record.error:
                    st.error(record.error)
            with right:
                if (
                    editable
                    and not record.status.is_finished
                    and st.button("取消", key=f"cancel-{record.id}", width="stretch")
                ):
                    manager.cancel(record.id)
                    st.rerun()
                if st.button(
                    "日志",
                    key=f"log-{record.id}",
                    width="stretch",
                    type="primary" if get("show_log") == record.id else "secondary",
                ):
                    set_value("show_log", record.id if get("show_log") != record.id else None)
                    st.rerun()

            if get("show_log") == record.id:
                st.code(manager.log_text(record.id) or "（暂无日志）", language=None)

    if any(not record.status.is_finished for record in records):
        st.caption(f"有任务进行中，约 {_POLL_SECONDS} 秒后自动刷新…")
        # 测试/脚本环境不睡眠不重入，避免 AppTest 因未完成任务卡死
        if _should_auto_refresh():
            time.sleep(_POLL_SECONDS)
            st.rerun()


def _should_auto_refresh() -> bool:
    """仅在真实 Streamlit 会话里自动刷新；AppTest / 裸脚本直接跳过。"""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _first_run_start(dataset: Dataset) -> date:
    """空库首次抓取的兜底起点（与 :mod:`regwatch.sources` 的抓取入口一致）。"""
    if dataset is Dataset.AMAC:
        return first_run_start()
    return DEFAULT_START_DATE


def _render_coverage() -> None:
    """各数据集「上次覆盖到的日期」与留空日期时的默认抓取区间。

    覆盖日期取库内案例的最大 ``date``，便于在提交抓取任务前预估增量工作量。
    """
    analysis = services().analysis
    cards: list[dict[str, object]] = []
    for dataset in Dataset.all():
        _, date_max = analysis.date_range(CaseQuery(datasets=(dataset,)))
        start, end = default_fetch_range(
            services().store, dataset, first_run_start=_first_run_start(dataset)
        )
        span = (end - start).days + 1
        cards.append(
            {
                "label": f"{dataset.label} 上次覆盖至",
                "value": date_max or "—",
                "hint": (
                    f"留空日期将从 {start.isoformat()} 抓到 {end.isoformat()}（{span} 天）"
                    if date_max
                    else "库中暂无案例：留空日期将全量扫描"
                ),
                "tone": "accent" if date_max else "muted",
            }
        )
    ui.metric_cards(cards, columns=2)


def _render_submit(manager: object) -> None:
    ui.section_header("提交任务", "抓取任务留空日期时按增量区间执行")
    _render_coverage()
    with st.form("job-submit", border=True):
        col1, col2 = st.columns([1, 3])
        with col1:
            kind_value = st.selectbox(
                "任务类型",
                options=[kind.value for kind in JobKind.all()],
                format_func=lambda value: JobKind.parse(value).label,
            )
        spec = JOB_SPECS[JobKind.parse(kind_value)]
        with col2:
            params: dict[str, object] = {}
            fields = list(spec.fields)
            columns = st.columns(max(1, min(3, len(fields) or 1)))
            for index, field in enumerate(fields):
                with columns[index % len(columns)]:
                    params[field.name] = _widget(field)
        submitted = st.form_submit_button("提交任务", type="primary", width="stretch")

    if not submitted:
        return

    try:
        job_id = manager.submit(spec.kind, params, title=spec.label)  # type: ignore[attr-defined]
    except JobError as exc:
        st.error(str(exc))
        return
    st.success(f"任务已提交：{job_id}")
    clear_data_cache()

    record = manager.wait(job_id, timeout=5)  # type: ignore[attr-defined]
    if record is not None and record.status is JobStatus.SUCCESS:
        clear_data_cache()
        st.rerun()


def _widget(field: object) -> object:
    name: str = field.name  # type: ignore[attr-defined]
    label: str = field.label  # type: ignore[attr-defined]
    kind: str = field.kind  # type: ignore[attr-defined]
    default = field.default  # type: ignore[attr-defined]
    hint: str = field.hint  # type: ignore[attr-defined]

    if kind == "bool":
        return st.checkbox(label, value=bool(default), help=hint)
    if kind == "int":
        return st.number_input(label, value=int(default or 0), step=1, help=hint)
    if kind == "date":
        # 与案例/统计页同一套日期语义：空=不填，选中写回 YYYY-MM-DD
        current = ui.parse_date_arg(default)
        picked = st.date_input(
            label,
            value=current,
            format="YYYY-MM-DD",
            help=hint or "留空表示使用任务默认日期",
            key=f"job-{name}",
        )
        parsed = ui.parse_date_arg(picked)
        return parsed.isoformat() if parsed else ""
    return st.text_input(label, value=str(default or ""), help=hint, key=f"job-{name}")
