"""智能问答：Chat 式对话（参考 DeepSeek / ChatGPT 布局）。

结构：

1. 顶部紧凑工具条：模式切换、模型状态、清空会话
2. 「模型与 API 凭证」默认折叠（就绪时收起，不打断对话）
3. 中部消息流：``st.chat_message`` 用户气泡 + 助手全文（下载/证据在答案旁）
4. 底部 ``st.chat_input`` 输入框

权限与凭证策略不变：非管理员必须会话内手填；管理员默认全局模型。
手填凭证只存 ``session_state``，不写 config、不进日志。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from regwatch.clock import now_iso
from regwatch.llm import LLMError
from regwatch.web import access
from regwatch.web.components import ui
from regwatch.web.components.data import services, settings

__all__ = ["render"]

_HISTORY_KEY = "regwatch.qa.history"
_CRED_KEY = "regwatch.qa.credentials"
_MODE_KEY = "regwatch.qa.mode"

_CHAT_CSS = """
<style>
/* Chat 消息区：贴近主流对话产品，弱化页面表单感 */
div[data-testid="stChatMessage"] {
  border: 1px solid #E2E8F4;
  border-radius: 14px;
  padding: 0.85rem 1rem 0.7rem;
  margin-bottom: 0.65rem;
  background: #FFFFFF;
  box-shadow: 0 1px 4px rgba(15, 23, 42, .04);
}
div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
  background: linear-gradient(135deg, #EEF2FF, #E8F0FE);
  border-color: #C7D6F5;
}
div[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  font-size: 15px;
  line-height: 1.65;
}
div[data-testid="stChatInput"] {
  border-radius: 14px;
}
/* 长报告标题在消息流里略收一号，避免抢主标题 */
div[data-testid="stChatMessage"] h1 { font-size: 1.45rem; }
div[data-testid="stChatMessage"] h2 { font-size: 1.15rem; }
</style>
"""


def render() -> None:
    st.markdown(_CHAT_CSS, unsafe_allow_html=True)
    ui.page_header("智能问答", "基于案例库的合规对话 · 意图检索后作答或撰写专题报告")

    admin = access.is_admin()
    svc = services()
    qa = getattr(svc, "qa", None)
    if qa is None:  # pragma: no cover - 装配保证有 qa
        ui.callout("服务未装配智能问答模块，请检查代码版本。", tone="danger", icon="⚠")
        return

    client, source = _resolve_client(qa, admin)

    # ── 顶部工具条 ──
    bar_mode, bar_status, bar_clear = st.columns([2.2, 3.2, 1.2], vertical_alignment="center")
    with bar_mode:
        mode = st.radio(
            "模式",
            options=["问答", "专题报告"],
            horizontal=True,
            key=_MODE_KEY,
            label_visibility="collapsed",
            help="问答：直接回答问题；专题报告：撰写可下载的 Markdown 报告",
        )
    forced = "report" if mode == "专题报告" else "qa"
    with bar_status:
        _status_chip(source)
    with bar_clear:
        if st.button("清空会话", width="stretch", disabled=not _history()):
            st.session_state.pop(_HISTORY_KEY, None)
            st.rerun()

    _render_source_note(admin, source)

    if not client:
        ui.callout(
            "当前无法调用大模型：请先在上方「模型与 API 凭证」完成配置。"
            "公开访问请使用你自己的 OpenAI 兼容 Key。",
            tone="amber",
            icon="⚠",
        )

    _render_messages()

    prompt = st.chat_input(
        "输入监管合规问题，或描述专题报告主题…" if client else "请先配置模型后提问",
        disabled=not client,
        key="regwatch.qa.chat_input",
    )
    if prompt and client:
        _handle_prompt(qa, prompt, forced, source, client)


def _status_chip(source: str) -> None:
    if source == "global":
        st.caption("模型：全局配置")
    elif source == "byok":
        st.caption("模型：会话手填 API")
    else:
        st.caption("模型：未配置")


def _handle_prompt(qa: Any, prompt: str, forced: str, source: str, client: Any) -> None:
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("正在解析意图、检索案例并撰写…"):
            try:
                turn = qa.ask(prompt.strip(), mode=forced, client=client)
            except LLMError as exc:
                ui.callout(f"调用失败：{exc}", tone="danger", icon="⚠")
                return
            except Exception as exc:  # pragma: no cover - 兜底
                ui.callout(f"发生未预期错误：{type(exc).__name__}: {exc}", tone="danger", icon="⚠")
                return
        _append_history(turn, source)
        _render_assistant_body(_history()[-1], len(_history()) - 1)


# ──────────────────────────── 凭证 ────────────────────────────


def _credentials() -> dict[str, str]:
    raw = st.session_state.get(_CRED_KEY)
    if not isinstance(raw, dict):
        raw = {}
        st.session_state[_CRED_KEY] = raw
    return raw


def _resolve_client(qa: Any, admin: bool) -> tuple[Any, str]:
    """返回 ``(client, source)``；无可用客户端时 client 为 ``None``。

    客户端的构造统一在 :class:`~regwatch.services.qa.QaService`：
    全局走任务绑定，会话手填凭证走 BYOK 快速失败策略；
    本函数只负责权限判定与取值，凭证仅存 ``session_state``。
    """
    cred = _credentials()
    use_global = admin and not cred.get("force_byok")

    if use_global:
        profile = settings().resolve_profile(task="qa", required=False)
        if profile is not None and profile.api_key:
            try:
                return qa.resolve_client(), "global"
            except LLMError:
                pass

    if not (cred.get("base_url") and cred.get("model") and cred.get("api_key")):
        return None, "none"

    return (
        qa.resolve_client(
            qa.byok_profile(
                base_url=cred.get("base_url", ""),
                model=cred.get("model", ""),
                api_key=cred.get("api_key", ""),
            )
        ),
        "byok",
    )


def _render_source_note(admin: bool, source: str) -> None:
    with st.expander("模型与 API 凭证", expanded=source == "none"):
        cred = _credentials()
        if admin:
            st.checkbox(
                "使用我的 API（忽略全局模型）",
                value=bool(cred.get("force_byok")),
                key="regwatch.qa.force_byok",
                on_change=_sync_force_byok,
            )
            if source == "global":
                profile = settings().resolve_profile(task="qa", required=False)
                name = profile.display_name if profile else "（未命名）"
                model = profile.model if profile else ""
                ui.callout(
                    f"当前使用全局模型：**{name}**（`{model}`）。"
                    "非管理员在公开部署下会强制手填自己的 Key，避免消耗你的额度。",
                    tone="ok",
                    icon="✓",
                )
            elif source == "byok":
                ui.callout(
                    "当前使用会话内手填的 API（仅保存在本浏览器会话）。", tone="accent", icon="ℹ"
                )
            else:
                ui.callout(
                    "尚未选择可用模型。可在下方手填 OpenAI 兼容接口，或到「模型与配置」添加全局模型。",
                    tone="amber",
                    icon="⚠",
                )
        else:
            ui.callout(
                "公开访问模式：请使用**你自己的** OpenAI 兼容 API Key。"
                "凭证仅保存在当前浏览器会话，不会写入服务器配置。",
                tone="accent",
                icon="ℹ",
            )

        base_url = st.text_input(
            "接口地址 base_url",
            value=cred.get("base_url", ""),
            placeholder="https://api.deepseek.com 或 https://openrouter.ai/api/v1",
            key="regwatch.qa.base_url",
            disabled=source == "global",
        )
        model = st.text_input(
            "模型名称 model",
            value=cred.get("model", ""),
            placeholder="deepseek-chat",
            key="regwatch.qa.model",
            disabled=source == "global",
        )
        api_key = st.text_input(
            "API Key",
            value=cred.get("api_key", ""),
            type="password",
            key="regwatch.qa.api_key",
            disabled=source == "global",
        )
        if source != "global":
            cred.update(
                {
                    "base_url": base_url.strip(),
                    "model": model.strip(),
                    "api_key": api_key.strip(),
                }
            )
            st.session_state[_CRED_KEY] = cred


def _sync_force_byok() -> None:
    cred = _credentials()
    cred["force_byok"] = bool(st.session_state.get("regwatch.qa.force_byok"))
    st.session_state[_CRED_KEY] = cred


# ──────────────────────────── 消息流 ────────────────────────────


def _history() -> list[dict[str, Any]]:
    history = st.session_state.get(_HISTORY_KEY)
    if not isinstance(history, list):
        return []
    return history


def _append_history(turn: Any, source: str) -> None:
    history = _history()
    history.append(
        {
            "mode": turn.mode,
            "question": turn.question,
            "answer": turn.answer,
            "model": turn.model or source,
            "used_fallback_intent": turn.used_fallback_intent,
            "retrieval": turn.retrieval.to_dict(),
        }
    )
    st.session_state[_HISTORY_KEY] = history[-30:]


def _render_messages() -> None:
    history = _history()
    if not history:
        with st.chat_message("assistant"):
            st.markdown(
                "**你好，我是监管案例助手。** 可以直接提问，例如：\n\n"
                "- 近两年挪用基金财产的处分案例有哪些？\n"
                "- 写一份信息披露违规专题报告\n\n"
                "我会先检索案例库，再依据证据作答。"
            )
        return

    for index, item in enumerate(history):
        with st.chat_message("user"):
            st.markdown(item.get("question") or "")
        with st.chat_message("assistant"):
            _render_assistant_body(item, index)


def _render_assistant_body(item: dict[str, Any], real_index: int) -> None:
    mode = item.get("mode")
    meta = item.get("retrieval") or {}
    answer = item.get("answer") or ""
    mode_label = "专题报告" if mode == "report" else "问答"
    caption = (
        f"{mode_label} · 命中约 {meta.get('total_hits', 0)} 条 · "
        f"引用 {meta.get('returned', 0)} 条 · 模型 {item.get('model') or '—'}"
        + (" · 意图解析已回退" if item.get("used_fallback_intent") else "")
    )
    st.caption(caption)

    action_cols = st.columns([1, 3])
    with action_cols[0]:
        _download_button(answer, mode, real_index)

    st.markdown(answer)

    # 案例信息固定展示在回答/报告下方（不折叠），与【案例N】编号对应
    st.divider()
    _render_evidence(meta, real_index)
    if not (meta.get("rows") or []):
        st.info("本轮未检索到案例。可尝试换更短的关键词、放宽时间范围，或去掉违规类型限制。")


def _download_filename(mode: str | None, real_index: int) -> str:
    stamp = now_iso().replace(":", "").replace("-", "").replace("T", "-")[:15]
    kind = "report" if mode == "report" else "qa"
    return f"regwatch-{kind}-{stamp}-{real_index}.md"


def _download_button(answer: str, mode: str | None, real_index: int) -> None:
    label = "下载报告 .md" if mode == "report" else "下载回答 .md"
    # UTF-8 BOM：Windows 记事本对无 BOM 中文 Markdown 会乱码
    st.download_button(
        label,
        data=b"\xef\xbb\xbf" + (answer or "").encode("utf-8"),
        file_name=_download_filename(mode, real_index),
        mime="text/markdown",
        key=f"qa-dl-{real_index}",
        width="content",
    )


def _render_evidence(meta: dict[str, Any], real_index: int) -> None:
    """在助手回复下方常显案例表，编号与正文【案例N】一致。"""
    rows = meta.get("rows") or []
    if not rows:
        return

    st.markdown(f"**引用案例（{len(rows)}）**")
    st.caption("编号对应上文【案例N】；点击「原文」可跳转监管网站")

    table: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("source_url") or "").strip()
        table.append(
            {
                "n": f"【{row.get('n')}】",
                "dataset": str(row.get("dataset") or "").upper(),
                "date": row.get("date") or "—",
                "title": row.get("title") or row.get("case_id") or "—",
                "punished": row.get("punished") or "—",
                "violation": row.get("violation_type") or "—",
                "punishment": row.get("punishment") or "—",
                "case_id": row.get("case_id") or "—",
                "source_url": url,
            }
        )

    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "n": st.column_config.TextColumn("编号", width="small"),
            "dataset": st.column_config.TextColumn("数据集", width="small"),
            "date": st.column_config.TextColumn("日期", width="small"),
            "title": st.column_config.TextColumn("标题", width="large"),
            "punished": st.column_config.TextColumn("当事人", width="medium"),
            "violation": st.column_config.TextColumn("违规类型", width="medium"),
            "punishment": st.column_config.TextColumn("处罚", width="medium"),
            "case_id": st.column_config.TextColumn("案例编号", width="medium"),
            "source_url": st.column_config.LinkColumn("原文", width="small"),
        },
        column_order=(
            "n",
            "dataset",
            "date",
            "title",
            "punished",
            "violation",
            "punishment",
            "case_id",
            "source_url",
        ),
        key=f"qa-evidence-{real_index}",
    )
