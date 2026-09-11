"""案例浏览：多维筛选、结果表格、详情与原文展开。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_HERE = Path(__file__).resolve()
for _path in (str(_HERE.parents[2]), str(_HERE.parents[1])):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from components import ui  # noqa: E402
from components.data import (  # noqa: E402
    filter_case_dicts,
    load_case_payload,
    load_rows,
    violation_options,
)
from regwatch.storage import CSRC_CASE_TYPE_LABELS  # noqa: E402

ui.apply_theme()

ui.page_header("案例浏览", "按数据集、主体类型、违规类型、日期与关键词检索案例详情")

rows = load_rows()
if not rows:
    ui.empty_state("暂无案例数据", "请先在「任务中心」运行抓取任务，或检查数据目录配置")
    st.stop()

STATUS_LABELS = {"done": "已提取摘要", "skipped": "非基金相关", "failed": "提取失败"}

with st.container(border=True):
    col1, col2, col3, col4 = st.columns([1, 1, 1.4, 1.6], gap="small")
    with col1:
        datasets = st.multiselect(
            "数据集", ["amac", "csrc"],
            format_func=lambda x: "中基协（AMAC）" if x == "amac" else "证监会（CSRC）",
            placeholder="全部",
        )
        entity_types = st.multiselect(
            "主体类型", ["机构", "个人", "机构+个人"], placeholder="全部",
        )
    with col2:
        statuses = st.multiselect(
            "处理状态", ["done", "skipped", "failed"],
            format_func=STATUS_LABELS.get, placeholder="全部",
        )
        case_types = st.multiselect(
            "案例类型", ["penalty", "measure"],
            format_func=CSRC_CASE_TYPE_LABELS.get, placeholder="全部",
        )
    with col3:
        violation_types = st.multiselect(
            "违规类型", violation_options(), placeholder="全部（可多选）",
        )
        col_from, col_to = st.columns(2, gap="small")
        with col_from:
            date_from = st.text_input("起始日期", placeholder="YYYY-MM-DD")
        with col_to:
            date_to = st.text_input("结束日期", placeholder="YYYY-MM-DD")
    with col4:
        keyword = st.text_input("关键词", placeholder="标题 / 当事人 / 违规摘要 / 涉及基金")
        bureaus = sorted({row["bureau"] for row in rows if row.get("bureau")})
        selected_bureaus = st.multiselect(
            "来源局（仅 CSRC）", bureaus, placeholder="全部",
        )

filtered = filter_case_dicts(
    rows,
    datasets=datasets,
    entity_types=entity_types,
    violation_types=violation_types,
    statuses=statuses,
    bureaus=selected_bureaus,
    case_types=case_types,
    date_from=date_from.strip(),
    date_to=date_to.strip(),
    keyword=keyword,
)

count_html = ui.badge(f"共 {len(filtered):,} / {len(rows):,} 条", "ok" if filtered else "muted")
st.markdown(f'<div style="margin:2px 0 10px">{count_html}</div>', unsafe_allow_html=True)

page_size = 25
page_count = max(1, -(-len(filtered) // page_size))
page_no = int(st.number_input(
    "页码", min_value=1, max_value=page_count, value=1, step=1,
    help=f"每页 {page_size} 条，共 {page_count} 页",
))
page_rows = filtered[(page_no - 1) * page_size:page_no * page_size]

columns = ["date", "dataset_label", "entity_display", "entity_type",
           "violation_type", "punishment", "case_type_label", "status_label"]
frame = pd.DataFrame(page_rows) if page_rows else pd.DataFrame(columns=columns)
if not frame.empty:
    frame["status_label"] = [
        STATUS_LABELS.get(str(row.get("status")), str(row.get("status"))) for row in page_rows
    ]
frame = frame.reindex(columns=columns).rename(columns={
    "date": "日期", "dataset_label": "数据集", "entity_display": "受处分主体",
    "entity_type": "主体类型", "violation_type": "违规类型", "punishment": "处罚措施",
    "case_type_label": "案例类型", "status_label": "状态",
})

selection = st.dataframe(
    frame, width="stretch", hide_index=True, height=430,
    on_select="rerun", selection_mode="single-row", key="case_table",
)

# ── 案例详情 ──
selected_rows = selection.get("selection", {}).get("rows", []) if selection else []
picked = page_rows[selected_rows[0]] if selected_rows and selected_rows[0] < len(page_rows) else None
if picked is None:
    st.caption("点击表格中的任意一行，可在此处查看案例详情与决定书原文。")
    st.stop()

case = picked
payload = load_case_payload(case) or {}

head = str(case.get("entity_display") or case.get("title") or case.get("case_id"))
st.markdown(
    '<div style="margin:16px 0 8px">' + ui.badges([
        (head, "primary"),
        (str(case.get("dataset_label") or ""), "sky"),
        (str(case.get("case_type_label") or ""), "muted"),
        (str(case.get("entity_type") or "未标注"), "amber"),
    ]) + "</div>",
    unsafe_allow_html=True,
)

pairs = [
    ("案例编号", case.get("case_id")),
    ("日期", case.get("date")),
    ("来源局", case.get("bureau") or "—"),
    ("登记类型", case.get("org_type") or "—"),
    ("当事人", case.get("entity") or "—"),
    ("违规类型", case.get("violation_type") or "—"),
    ("处罚措施", case.get("punishment") or "—"),
    ("涉及基金", case.get("involved_fund") or "—"),
    ("法规依据", case.get("legal_basis") or "—"),
    ("罚款金额", case.get("penalty_amount") or "—"),
    ("市场禁入", case.get("market_ban") or "—"),
    ("文号", payload.get("document_number") or "—"),
    ("处理状态", STATUS_LABELS.get(str(case.get("status")), case.get("status"))),
    ("来源链接", case.get("source_url") or "—"),
]
if case.get("note"):
    pairs.append(("跳过理由", case.get("note")))
if payload.get("fund_evidence"):
    pairs.append(("基金判定依据", payload.get("fund_evidence")))

st.markdown(
    '<div class="rw-panel">' + ui.detail_rows(pairs) + "</div>",
    unsafe_allow_html=True,
)

st.markdown("#### 违规事实摘要")
st.info(str(case.get("violation_summary") or "（无摘要）"), icon=":material/info:")

st.markdown("#### 决定书原文")
raw_text = str(payload.get("raw_text") or "")
if raw_text:
    st.text_area(
        "原文",
        value=raw_text,
        height=420,
        disabled=True,
        label_visibility="collapsed",
    )
    st.caption(f"共 {len(raw_text):,} 字符")
else:
    ui.empty_state(
        "暂无原文",
        "该案例被判定为非基金相关（无摘要文件），或原文尚未抓取成功",
    )
