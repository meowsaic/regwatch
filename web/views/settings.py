"""模型与配置页：模型条目、连通性测试、任务绑定与数据目录。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve()
for _path in (str(_HERE.parents[2]), str(_HERE.parents[1])):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from components import ui  # noqa: E402
from regwatch.config import (  # noqa: E402
    TASK_KINDS,
    TASK_LABELS,
    ConfigError,
    get_config,
)
from regwatch.datamodels import ModelProfile  # noqa: E402
from regwatch.llm import reset_clients, test_profile  # noqa: E402

ui.apply_theme()

config = get_config()

st.markdown("#### 模型接入")

profiles = config.models()
if not profiles:
    ui.callout(
        "尚未配置任何模型。请在下方填写接口地址、API Key 与模型名称后保存，"
        "或复制 config.example.json 为 config.json 后填写。", tone="amber",
    )
else:
    for profile in profiles:
        with st.container(border=True):
            top, bottom = st.columns([4, 1])
            with top:
                st.markdown(
                    ui.badges([
                        (profile.display_name, "primary"),
                        (profile.id, "muted"),
                        (f"{profile.model}", "sky"),
                    ]),
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"{profile.base_url} · 密钥 {profile.masked_key}"
                    + (f" · 视觉模型 {profile.vision_model}" if profile.vision_model else "")
                )
            with bottom:
                if st.button("测试连接", key=f"test-{profile.id}", width="stretch"):
                    with st.spinner("正在测试连通性…"):
                        ok, message = test_profile(profile)
                    (st.success if ok else st.error)(message, icon=None)

st.divider()

with st.form("model_form", border=False):
    st.markdown("##### 新增 / 更新模型")
    col1, col2 = st.columns(2, gap="small")
    with col1:
        mid = st.text_input("配置标识 *", placeholder="deepseek", help="唯一 ID，任务绑定用")
        mlabel = st.text_input("展示名称", placeholder="DeepSeek 通用")
        murl = st.text_input(
            "接口地址 base_url *", placeholder="https://api.deepseek.com",
            help="兼容 OpenAI 的接口根地址，DeepSeek 也可写 https://api.deepseek.com/v1",
        )
        mkey = st.text_input("API Key", type="password", help="留空则使用环境变量 REGWATCH_API_KEY_<ID大写>")
    with col2:
        mmodel = st.text_input("文本模型 model *", placeholder="deepseek-chat")
        mvision = st.text_input("视觉模型（可选）", placeholder="留空则回落到文本模型")
        mtoken = st.selectbox(
            "token 参数字段", ["max_tokens", "max_completion_tokens"],
            help="少数端点（如 MiMo）要求 max_completion_tokens",
        )
        mextra = st.text_input(
            "附加参数（可选，JSON）", placeholder='{"thinking": {"type": "disabled"}}',
            help="透传给 chat.completions.create 的额外参数",
        )
    submitted = st.form_submit_button("保存模型配置", type="primary", width="stretch")
    if submitted:
        extra: dict = {}
        if mextra.strip():
            try:
                import json as _json

                parsed = _json.loads(mextra)
                if not isinstance(parsed, dict):
                    raise ValueError("必须是 JSON 对象")
                extra = parsed
            except Exception as exc:  # noqa: BLE001
                st.error(f"附加参数不是合法 JSON 对象：{exc}")
                st.stop()
        profile = ModelProfile(
            id=mid.strip(), label=mlabel.strip(), base_url=murl.strip(),
            api_key=mkey.strip(), model=mmodel.strip(), vision_model=mvision.strip(),
            token_param=mtoken, extra=extra,
        )
        missing = profile.missing_fields()
        if missing:
            st.error(f"缺少必填项：{'、'.join(missing)}")
        else:
            try:
                config.upsert_model(profile)
                reset_clients()
                st.success(f"已保存模型配置：{profile.id}")
                st.rerun()
            except ConfigError as exc:
                st.error(str(exc))

st.divider()
st.markdown("#### 任务 → 模型绑定")

with st.container(border=True):
    bound = config.task_models()
    for kind in TASK_KINDS:
        col_task, col_model = st.columns([2, 3])
        with col_task:
            st.markdown(f"**{TASK_LABELS[kind]}**")
            st.caption(kind)
        with col_model:
            model_ids = [profile.id for profile in profiles] or [""]
            current = bound.get(kind) or ""
            chosen = st.selectbox(
                kind, ["", *model_ids], index=(["", *model_ids].index(current) if current in ["", *model_ids] else 0),
                format_func=lambda x: (
                    "（未绑定，使用第一个模型）" if not x
                    else next((p.display_name for p in profiles if p.id == x), x)
                ),
                label_visibility="collapsed",
                key=f"bind-{kind}",
            )
            if chosen != current:
                try:
                    config.set_task_model(kind, chosen)
                    reset_clients()
                    st.toast(f"已绑定：{TASK_LABELS[kind]}")
                    st.rerun()
                except ConfigError as exc:
                    st.error(str(exc))

st.divider()
st.markdown("#### 并发与数据目录")

col_conc, col_roots = st.columns([1, 2], gap="large")
with col_conc:
    with st.form("conc_form", border=False):
        st.markdown("**并发设置**")
        fetch_c = st.number_input("抓取并发", value=config.concurrency("fetch", 8), min_value=1, max_value=64)
        sum_c = st.number_input("摘要并发", value=config.concurrency("summarize", 5), min_value=1, max_value=64)
        if st.form_submit_button("保存并发设置", width="stretch"):
            try:
                config.set_concurrency("fetch", int(fetch_c))
                config.set_concurrency("summarize", int(sum_c))
                st.success("已保存")
            except ConfigError as exc:
                st.error(str(exc))
with col_roots:
    st.markdown("**数据目录**（相对路径基于项目根目录）")
    for key, path in config.data_roots().items():
        mark = "✓" if path.exists() else "✗"
        st.markdown(f"`{key}` → `{path}` {mark}")
    st.caption(
        "如需调整目录，请编辑 config.json 中的 data_roots 字段；"
        "案例数据默认位于 AMAC/ 与 CSRC/ 子目录。"
    )
