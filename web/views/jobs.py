"""任务中心：在网页上触发抓取、摘要、报告任务，实时查看进度与日志。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve()
for _path in (str(_HERE.parents[2]), str(_HERE.parents[1])):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from regwatch.jobs import (
    JOB_KINDS,
    JOB_LABELS,
    JOB_PARAMS,
    JobError,
    get_job_manager,
)
from regwatch.storage import DATASET_LABELS

from components import ui
from components.data import clear_data_cache

ui.apply_theme()

manager = get_job_manager()

HEADER_HTML = (
    '<div class="rw-title" style="color:#354e92">任务中心</div>'
    '<div style="color:#475569;margin:2px 0 6px">'
    "后台执行采集 / 摘要 / 报告任务，实时查看进度与日志</div>"
)
st.markdown(HEADER_HTML, unsafe_allow_html=True)

st.markdown("#### 发起新任务")

with st.container(border=True):
    col_form, col_help = st.columns([3, 2], gap="large")
    with col_form:
        kind = st.selectbox("任务类型", list(JOB_KINDS), format_func=JOB_LABELS.get)
        values: dict = {}
        for label, key, default, hint in JOB_PARAMS.get(kind, ()):
            if isinstance(default, bool):
                values[key] = st.toggle(label, value=default, help=hint)
            elif key == "dataset":
                options = ["all", "amac", "csrc"]
                values[key] = st.selectbox(
                    label,
                    options,
                    index=options.index(str(default)) if default in options else 0,
                    format_func=lambda x: "全部" if x == "all" else DATASET_LABELS.get(x, x),
                    help=hint,
                )
            elif key in ("start_date", "end_date"):
                # 日期参数用 date_input；留空表示不限制（底层仍收 YYYY-MM-DD 字符串）
                picked = st.date_input(label, value=None, help=hint, key=f"job_{kind}_{key}")
                if isinstance(picked, (tuple, list)):
                    picked = picked[0] if picked else None
                values[key] = picked.isoformat() if picked else ""
            elif isinstance(default, int):
                values[key] = st.number_input(label, value=int(default), step=1, help=hint)
            else:
                values[key] = st.text_input(label, value=str(default), help=hint)
    with col_help:
        st.markdown("**说明**")
        st.markdown(
            "- 抓取任务需访问官网，耗时取决于日期范围与来源数量\n"
            "- 摘要任务调用「模型与配置」中绑定的模型，按并发数执行\n"
            "- 同类型任务执行期间不可重复提交\n"
            "- 任务完成后点击下方「刷新数据缓存」查看最新结果"
        )
        if st.button("启动任务", type="primary", width="stretch"):
            try:
                job_id = manager.submit(kind, values)
                st.session_state["last_job"] = job_id
                st.success(f"任务已提交：{JOB_LABELS[kind]}（ID {job_id}）")
                time.sleep(0.4)
                st.rerun()
            except JobError as exc:
                st.error(str(exc))

# ── 任务列表 ──
records = manager.jobs(limit=20)
st.markdown("#### 任务记录")

if not records:
    ui.empty_state("暂无任务记录", "从上方选择任务类型并点击「启动任务」")
else:
    any_running = False
    for record in records:
        is_running = record.status.value == "running"
        any_running = any_running or is_running
        progress = record.progress
        title_row = ui.badges(
            [
                (record.title, "primary"),
                (record.id, "muted"),
            ]
        )
        status_html = ui.status_badge(record.status.value)
        meta = f"创建 {record.created_at}"
        if record.started_at:
            meta += f" · 开始 {record.started_at}"
        if record.finished_at:
            meta += f" · 结束 {record.finished_at}"
        with st.container(border=True):
            top_left, top_right = st.columns([4, 1])
            with top_left:
                st.markdown(
                    f"<div>{title_row}{status_html}</div>"
                    f'<div style="font-size:12px;color:#94A3B8;margin-top:2px">{meta}</div>',
                    unsafe_allow_html=True,
                )
            with top_right:
                if is_running and st.button("取消", key=f"cancel-{record.id}"):
                    manager.cancel(record.id)
                    st.rerun()

            if is_running:
                if record.total:
                    st.progress(min(progress, 1.0), text=f"{record.processed}/{record.total}")
                else:
                    st.caption("执行中（该任务暂无进度信息）…")
                if record.message:
                    st.caption(record.message)
            elif record.status.value == "failed":
                st.error(record.error or "任务失败")
            elif record.status.value == "success":
                st.success(record.message or "任务完成")
            elif record.status.value == "cancelled":
                st.warning("任务已取消")

            logs = manager.log_lines(record.id)
            if logs:
                tail = logs[-200:]
                with st.expander("查看日志", expanded=is_running):
                    st.code("\n".join(tail), language="log", line_numbers=False)

    if any_running:
        auto_refresh = st.toggle("自动刷新（每 2 秒）", value=True)
        if auto_refresh:
            time.sleep(2)
            st.rerun()
    else:
        if st.button("刷新数据缓存"):
            clear_data_cache()
            st.toast("数据缓存已刷新")
