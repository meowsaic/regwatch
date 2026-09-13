"""模型与配置：模型条目增删改、任务绑定、并发与路径。"""

from __future__ import annotations

import streamlit as st

from regwatch.domain import ModelProfile
from regwatch.settings import TASK_KINDS, TASK_LABELS, ConfigError, Settings, store
from regwatch.web import access
from regwatch.web.components import ui
from regwatch.web.components.data import services, settings

__all__ = ["render"]


def render() -> None:
    ui.page_header("模型与配置", "OpenAI 兼容端点、任务绑定与数据路径")

    editable = access.require_admin("settings")

    _render_paths()

    ui.section_header("模型条目", "摘要 / 报告等任务可绑定不同模型")
    current = settings()
    if editable and not current.models:
        ui.callout("尚未配置任何模型，摘要与报告任务将无法执行。", tone="amber", icon="⚠")

    for profile in current.models:
        with st.container(border=True):
            left, right = st.columns([5, 1.2], vertical_alignment="center")
            with left:
                st.markdown(f"**{profile.display_name}**（`{profile.id}`）")
                st.caption(
                    f"{profile.base_url} · 文本 {profile.model} · "
                    f"视觉 {profile.resolved_vision_model()} · 密钥 {profile.masked_key}"
                )
                if profile.note:
                    st.caption(profile.note)
            with right:
                # 只读模式下连「测试连接」也收起：它会带着密钥请求 base_url
                if editable and st.button("测试连接", key=f"test-{profile.id}", width="stretch"):
                    _test(profile)
                if editable and st.button("删除", key=f"del-{profile.id}", width="stretch"):
                    store().delete_model(profile.id)
                    _refresh_models()
                    st.rerun()

    if not editable:
        _render_readonly_binding(current)
        return

    ui.section_header("新增 / 更新模型")
    with st.form("model-form", border=True):
        col1, col2 = st.columns(2)
        with col1:
            model_id = st.text_input("配置标识 id*", placeholder="deepseek")
            base_url = st.text_input("接口地址 base_url*", placeholder="https://api.deepseek.com")
            model = st.text_input("文本模型*", placeholder="deepseek-chat")
        with col2:
            label = st.text_input("展示名称", placeholder="DeepSeek 通用")
            api_key = st.text_input("API Key", type="password")
            vision_model = st.text_input("视觉模型（留空回落文本模型）")
        token_param = st.selectbox("上限参数名", options=["max_tokens", "max_completion_tokens"])
        submitted = st.form_submit_button("保存", type="primary", width="stretch")

    if submitted:
        _save(model_id, base_url, model, label, api_key, vision_model, token_param)

    _render_task_binding()
    _render_concurrency()


def _render_paths() -> None:
    cfg = settings()
    ui.section_header("数据路径")
    st.markdown(
        ui.detail_rows(
            [
                ("配置文件", cfg.source_path),
                ("数据库", cfg.database),
                ("报告目录", cfg.reports_dir),
            ]
        ),
        unsafe_allow_html=True,
    )


def _render_readonly_binding(current: Settings) -> None:
    """只读模式下用纯文本展示绑定与并发，不渲染任何可写控件。"""
    ui.section_header("任务与模型绑定")
    st.markdown(
        ui.detail_rows(
            [
                (TASK_LABELS.get(task, task), current.task_model(task) or "未绑定")
                for task in TASK_KINDS
            ]
        ),
        unsafe_allow_html=True,
    )
    ui.section_header("并发")
    st.markdown(
        ui.detail_rows(
            [
                ("抓取", current.concurrency_for("fetch", 8)),
                ("摘要", current.concurrency_for("summarize", 5)),
            ]
        ),
        unsafe_allow_html=True,
    )


def _save(
    model_id: str,
    base_url: str,
    model: str,
    label: str,
    api_key: str,
    vision_model: str,
    token_param: str,
) -> None:
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
        st.error(str(exc))
        return
    _refresh_models()
    st.success(f"已保存模型配置：{profile.id}")


def _test(profile: ModelProfile) -> None:
    factory = services().llm
    success, message = factory.test_profile(profile)
    if success:
        st.success(message)
    else:
        st.error(message)


def _render_task_binding() -> None:
    ui.section_header("任务与模型绑定")
    current = settings()
    columns = st.columns(len(TASK_KINDS))
    for index, task in enumerate(TASK_KINDS):
        with columns[index]:
            options = ["", *current.model_ids]
            current_value = current.task_model(task)
            choice = st.selectbox(
                TASK_LABELS.get(task, task),
                options=options,
                index=options.index(current_value) if current_value in options else 0,
                key=f"bind-{task}",
            )
            if choice != current_value:
                store().set_task_model(task, choice)
                _refresh_models()
                st.rerun()


def _render_concurrency() -> None:
    ui.section_header("并发")
    current = settings()
    col1, col2 = st.columns(2)
    with col1:
        value = st.number_input(
            "抓取并发", min_value=1, max_value=64, value=current.concurrency_for("fetch", 8)
        )
        if value != current.concurrency_for("fetch", 8):
            store().set_concurrency("fetch", int(value))
            st.rerun()
    with col2:
        value = st.number_input(
            "摘要并发", min_value=1, max_value=64, value=current.concurrency_for("summarize", 5)
        )
        if value != current.concurrency_for("summarize", 5):
            store().set_concurrency("summarize", int(value))
            st.rerun()


def _refresh_models() -> None:
    """配置变更后同步刷新进程内的模型客户端缓存。"""
    services().refresh_models()
