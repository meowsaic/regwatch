"""模型与配置页：模型条目、连通性测试、任务绑定与数据目录。

支持新增、编辑（自动预填表单）、删除（二次确认）模型配置。
"""

from __future__ import annotations

import json

import streamlit as st

from regwatch.config import (
    TASK_KINDS,
    TASK_LABELS,
    ConfigError,
    get_config,
)
from regwatch.datamodels import ModelProfile
from regwatch.llm import reset_clients, test_profile
from regwatch.web.components import ui

ui.apply_theme()

config = get_config()

# 表单控件 key 清单，编辑/取消/保存后统一清理，避免残留旧值
_FORM_KEYS = (
    "mf_id",
    "mf_label",
    "mf_url",
    "mf_key",
    "mf_env",
    "mf_model",
    "mf_vision",
    "mf_vct",
    "mf_token",
    "mf_extra",
)


def _clear_model_form() -> None:
    st.session_state.pop("editing_model", None)
    for key in _FORM_KEYS:
        st.session_state.pop(key, None)


def _start_edit(profile: ModelProfile) -> None:
    st.session_state["editing_model"] = profile.id
    st.session_state["mf_id"] = profile.id
    st.session_state["mf_label"] = profile.label
    st.session_state["mf_url"] = profile.base_url
    st.session_state["mf_key"] = profile.api_key
    st.session_state["mf_env"] = profile.api_key_env
    st.session_state["mf_model"] = profile.model
    st.session_state["mf_vision"] = profile.vision_model
    st.session_state["mf_vct"] = profile.vision_content_type or "image_url"
    st.session_state["mf_token"] = profile.token_param or "max_tokens"
    st.session_state["mf_extra"] = (
        json.dumps(profile.extra, ensure_ascii=False) if profile.extra else ""
    )


st.markdown("#### 模型接入")

profiles = config.models()
editing_id = st.session_state.get("editing_model")

if not profiles:
    ui.callout(
        "尚未配置任何模型。请在下方填写接口地址、API Key 与模型名称后保存，"
        "或复制 config.example.json 为 config.json 后填写。",
        tone="amber",
    )
else:
    for profile in profiles:
        is_editing = profile.id == editing_id
        with st.container(border=True):
            top, bottom = st.columns([4, 3])
            with top:
                badges = [
                    (profile.display_name, "primary"),
                    (profile.id, "muted"),
                    (f"{profile.model}", "sky"),
                ]
                if is_editing:
                    badges.append(("编辑中", "amber"))
                st.markdown(ui.badges(badges), unsafe_allow_html=True)
                st.caption(
                    f"{profile.base_url} · 密钥 {profile.masked_key}"
                    + (f" · 视觉模型 {profile.vision_model}" if profile.vision_model else "")
                )
            with bottom:
                col_test, col_edit, col_del = st.columns(3, gap="small")
                with col_test:
                    if st.button("测试连接", key=f"test-{profile.id}", width="stretch"):
                        with st.spinner("正在测试连通性…"):
                            ok, message = test_profile(profile)
                        (st.success if ok else st.error)(message, icon=None)
                with col_edit:
                    if st.button("编辑", key=f"edit-{profile.id}", width="stretch"):
                        _start_edit(profile)
                        st.rerun()
                with col_del:
                    if st.button("删除", key=f"del-{profile.id}", width="stretch"):
                        st.session_state["confirm_delete"] = profile.id
                        st.rerun()

# ── 删除确认 ──
confirm_id = st.session_state.get("confirm_delete")
if confirm_id:
    bound_kinds = [
        TASK_LABELS[kind] for kind, mid in config.task_models().items() if mid == confirm_id
    ]
    hint = (
        f"模型 **{confirm_id}** 的任务绑定（{'、'.join(bound_kinds)}）将一并清除，"
        "相关任务回落为使用第一个模型。"
        if bound_kinds
        else f"确认删除模型 **{confirm_id}**？"
    )
    st.warning(f"⚠ {hint}", icon=None)
    col_ok, col_cancel, _ = st.columns([1, 1, 2])
    with col_ok:
        if st.button("确认删除", key="confirm_delete_yes", type="primary", width="stretch"):
            config.delete_model(confirm_id)
            reset_clients()
            if st.session_state.get("editing_model") == confirm_id:
                _clear_model_form()
            st.session_state.pop("confirm_delete", None)
            st.toast(f"已删除模型配置：{confirm_id}")
            st.rerun()
    with col_cancel:
        if st.button("取消", key="confirm_delete_no", width="stretch"):
            st.session_state.pop("confirm_delete", None)
            st.rerun()

st.divider()

editing_profile = config.get_model(editing_id) if editing_id else None

form_head, form_cancel = st.columns([3, 1])
with form_head:
    st.markdown(f"##### {'编辑模型：' + editing_profile.id if editing_profile else '新增模型'}")
with form_cancel:
    if editing_profile and st.button("取消编辑", key="cancel_edit", width="stretch"):
        _clear_model_form()
        st.rerun()

with st.form("model_form", border=False):
    col1, col2 = st.columns(2, gap="small")
    with col1:
        mid = st.text_input(
            "配置标识 *",
            placeholder="deepseek",
            key="mf_id",
            disabled=bool(editing_profile),
            help="唯一 ID，任务绑定用；编辑时不可修改",
        )
        mlabel = st.text_input("展示名称", placeholder="DeepSeek 通用", key="mf_label")
        murl = st.text_input(
            "接口地址 base_url *",
            placeholder="https://api.deepseek.com",
            key="mf_url",
            help="兼容 OpenAI 的接口根地址，DeepSeek 也可写 https://api.deepseek.com/v1",
        )
        mkey = st.text_input(
            "API Key",
            type="password",
            key="mf_key",
            help="留空则使用下方环境变量或 REGWATCH_API_KEY_<ID大写>",
        )
        menv = st.text_input(
            "API Key 环境变量名（可选）",
            placeholder="DEEPSEEK_API_KEY",
            key="mf_env",
            help="设置后优先于上方密钥读取环境变量",
        )
    with col2:
        mmodel = st.text_input("文本模型 model *", placeholder="deepseek-chat", key="mf_model")
        mvision = st.text_input(
            "视觉模型（可选）",
            placeholder="留空则回落到文本模型",
            key="mf_vision",
        )
        mvct = st.selectbox(
            "视觉消息类型",
            ["image_url", "file_url"],
            key="mf_vct",
            help="图片/通用端点用 image_url；智谱等端点传 PDF 需选 file_url。"
            "选错时程序会自动互换重试，但直选正确值可省一次失败请求",
        )
        mtoken = st.selectbox(
            "token 参数字段",
            ["max_tokens", "max_completion_tokens"],
            key="mf_token",
            help="少数端点（如 MiMo）要求 max_completion_tokens",
        )
        mextra = st.text_input(
            "附加参数（可选，JSON）",
            placeholder='{"thinking": {"type": "disabled"}}',
            key="mf_extra",
            help='透传给 chat.completions.create 的额外参数，如 {"thinking": {"type": "enabled"}}',
        )
    submitted = st.form_submit_button(
        "保存修改" if editing_profile else "保存模型配置",
        type="primary",
        width="stretch",
    )
    if submitted:
        extra: dict = {}
        if mextra.strip():
            try:
                parsed = json.loads(mextra)
                if not isinstance(parsed, dict):
                    raise ValueError("必须是 JSON 对象")
                extra = parsed
            except Exception as exc:
                st.error(
                    f"附加参数不是合法 JSON 对象：{exc}\n\n"
                    '正确示例：{"thinking": {"type": "enabled"}}（注意最外层大括号）'
                )
                st.stop()
        profile = ModelProfile(
            id=str(mid or "").strip(),
            label=mlabel.strip(),
            base_url=murl.strip(),
            api_key=mkey.strip(),
            api_key_env=menv.strip(),
            model=mmodel.strip(),
            vision_model=mvision.strip(),
            vision_content_type=mvct,
            token_param=mtoken,
            extra=extra,
            note=editing_profile.note if editing_profile else "",
        )
        missing = profile.missing_fields()
        if missing:
            st.error(f"缺少必填项：{'、'.join(missing)}")
        else:
            try:
                config.upsert_model(profile)
                reset_clients()
                was_editing = bool(editing_profile)
                _clear_model_form()
                st.success(f"已{'更新' if was_editing else '新增'}模型配置：{profile.id}")
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
                kind,
                ["", *model_ids],
                index=(["", *model_ids].index(current) if current in ["", *model_ids] else 0),
                format_func=lambda x: (
                    "（未绑定，使用第一个模型）"
                    if not x
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
with col_conc, st.form("conc_form", border=False):
    st.markdown("**并发设置**")
    fetch_c = st.number_input(
        "抓取并发", value=config.concurrency("fetch", 8), min_value=1, max_value=64
    )
    sum_c = st.number_input(
        "摘要并发", value=config.concurrency("summarize", 5), min_value=1, max_value=64
    )
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
        "案例数据默认位于 data/amac 与 data/csrc 子目录。"
    )
