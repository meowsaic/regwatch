"""任务中心：按 :data:`~regwatch.services.JOB_SPECS` 生成表单、提交与监控。"""

from __future__ import annotations

import streamlit as st

from regwatch.domain import JobKind, JobStatus
from regwatch.services import JOB_SPECS, JobError
from regwatch.web.components import ui
from regwatch.web.components.data import clear_data_cache, services
from regwatch.web.state import get, set_value

__all__ = ["render"]

_POLL_SECONDS = 2


def render() -> None:
    ui.page_header("任务中心", "网页端与命令行共用同一套任务编排")

    manager = services().jobs

    _render_submit(manager)

    st.markdown("#### 任务列表")
    records = manager.jobs(limit=20)
    if not records:
        ui.empty_state("暂无任务记录", "选择上方任务并提交即可开始。")
        return

    for record in records:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.markdown(
                    f"**{record.title}**　{ui.status_badge(record.status.value)}",
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
                if not record.status.is_finished and st.button("取消", key=f"cancel-{record.id}"):
                    manager.cancel(record.id)
                    st.rerun()
                if st.button("日志", key=f"log-{record.id}"):
                    set_value("show_log", record.id if get("show_log") != record.id else None)

            if get("show_log") == record.id:
                st.text(manager.log_text(record.id) or "（暂无日志）")

    if any(not record.status.is_finished for record in records):
        st.caption(f"有任务进行中，约 {_POLL_SECONDS} 秒后自动刷新…")
        st.rerun()


def _render_submit(manager: object) -> None:
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
        submitted = st.form_submit_button("提交任务", type="primary")

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
    return st.text_input(label, value=str(default or ""), help=hint, key=f"job-{name}")
