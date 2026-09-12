"""智能问答用例：意图解析 → 案例检索 → 作答 / 专题报告。

设计约束：

1. **两阶段调用**：先让模型产出结构化意图（JSON），再按意图走既有
   :class:`~regwatch.domain.CaseQuery` 检索，最后把证据包交给模型撰写。
   不依赖 function calling，免费/弱 tool 模型也能用。
2. **不加载正文**：证据来自 ``CaseRow`` 摘要字段，避免把大段 ``raw_text``
   塞进上下文。
3. **客户端可注入**：网页端「使用我的 API」时传入临时
   :class:`~regwatch.llm.LLMClient`，不落盘、不进全局工厂缓存。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain import CaseRow, QaIntent
from ..llm import LLMClientFactory, LLMError, parse_json_response
from ..logging_setup import get_logger
from ..prompts import QA_ANSWER_SYSTEM, QA_INTENT_PROMPT, QA_REPORT_SYSTEM
from .analyze import AnalysisService

logger = get_logger("qa")

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_EVIDENCE_CHARS",
    "MAX_LIMIT",
    "QaRetrieval",
    "QaService",
    "QaTurn",
    "build_evidence_pack",
]

DEFAULT_LIMIT = 15
MAX_LIMIT = 40
#: 单条案例证据的摘要截断长度，防止超长违规摘要打爆上下文
MAX_EVIDENCE_CHARS = 400


def _clip(text: str, limit: int = MAX_EVIDENCE_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


@dataclass(frozen=True, slots=True)
class QaRetrieval:
    """一次检索的结果：证据包 + 命中行 + 轻量统计。"""

    intent: QaIntent
    rows: tuple[CaseRow, ...]
    overview: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    total_hits: int = 0
    used_keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_mode": self.intent.mode,
            "total_hits": self.total_hits,
            "returned": len(self.rows),
            "keywords": list(self.used_keywords),
            "violations": list(self.intent.violations),
            "date_from": self.intent.date_from,
            "date_to": self.intent.date_to,
            "notes": self.intent.notes,
            "overview_total": self.overview.get("total", 0),
            "rows": [
                {
                    "n": index + 1,
                    "dataset": row.dataset.value,
                    "case_id": row.case_id,
                    "title": row.title,
                    "date": row.date,
                    "punished": row.entity_display,
                    "violation_type": row.violation_type,
                    "punishment": row.punishment,
                    "source_url": row.source_url,
                }
                for index, row in enumerate(self.rows)
            ],
        }


@dataclass(frozen=True, slots=True)
class QaTurn:
    """一次完整问答/报告的结果。"""

    mode: str
    question: str
    answer: str
    intent: QaIntent
    retrieval: QaRetrieval
    model: str = ""
    used_fallback_intent: bool = False


def build_evidence_pack(rows: Sequence[CaseRow], overview: dict[str, Any] | None = None) -> str:
    """把案例行与统计摘要拼成模型可读的证据文本。"""
    lines: list[str] = []
    ov = overview or {}
    if ov:
        lines.append("## 库内统计摘要")
        lines.append(f"- 筛选命中总数：{ov.get('total', 0)}")
        date_min = ov.get("date_min") or ""
        date_max = ov.get("date_max") or ""
        if date_min or date_max:
            lines.append(f"- 日期范围：{date_min or '—'} ~ {date_max or '—'}")
        violations = ov.get("violations") or []
        if violations:
            top = violations[:8]
            rendered = "、".join(f"{name}({count})" for name, count in top)
            lines.append(f"- 违规类型 TOP：{rendered}")
        lines.append("")

    if not rows:
        lines.append("## 案例证据包")
        lines.append("（无命中案例）")
        return "\n".join(lines)

    lines.append("## 案例证据包")
    for index, row in enumerate(rows, start=1):
        entity = row.entity_display
        title = _clip(row.title, 80) or "（无标题）"
        lines.append(
            f"### 【案例{index}】{row.dataset.value.upper()} | {row.date or '—'} | {title}"
        )
        lines.append(f"- 编号：{row.case_id}")
        lines.append(f"- 当事人：{entity}")
        if row.bureau:
            lines.append(f"- 派出机构：{row.bureau}")
        if row.case_type.value and row.case_type.value != "unknown":
            lines.append(f"- 文书类型：{row.case_type.label}")
        lines.append(f"- 违规类型：{_clip(row.violation_type, 120) or '—'}")
        lines.append(f"- 处罚：{_clip(row.punishment, 120) or '—'}")
        if row.legal_basis:
            lines.append(f"- 法规依据：{_clip(row.legal_basis, 160)}")
        if row.penalty_amount:
            lines.append(f"- 罚款：{row.penalty_amount}")
        if row.market_ban:
            lines.append(f"- 市场禁入：{row.market_ban}")
        summary = _clip(row.violation_summary, MAX_EVIDENCE_CHARS)
        lines.append(f"- 事实摘要：{summary or '—'}")
        if row.source_url:
            lines.append(f"- 来源：{row.source_url}")
        lines.append("")
    return "\n".join(lines).strip()


class QaService:
    """智能问答用例层。"""

    def __init__(self, llm: Any, analysis: AnalysisService) -> None:
        self._llm = llm
        self._analysis = analysis

    # ── 客户端 ──

    def _client(self, client: Any = None) -> Any:
        if client is not None:
            return client
        if isinstance(self._llm, LLMClientFactory) or hasattr(self._llm, "client"):
            return self._llm.client(task="qa")
        raise LLMError("未配置可用的模型客户端")

    # ── 意图 ──

    def parse_intent(self, question: str, client: Any = None) -> tuple[QaIntent, bool, str]:
        """解析用户问题为检索意图。

        Returns:
            ``(intent, used_fallback, model_text)``：解析失败时回退为关键词意图。
        """
        prompt = QA_INTENT_PROMPT.replace("__QUESTION__", question.strip() or "（空问题）")
        try:
            raw = self._client(client).chat_text(prompt, temperature=0)
        except LLMError:
            logger.warning("意图解析调用失败，回退为纯关键词意图", exc_info=True)
            return self.fallback_intent(question), True, ""

        parsed = parse_json_response(raw)
        if parsed is None:
            logger.warning("意图 JSON 解析失败，回退为纯关键词意图")
            return self.fallback_intent(question), True, raw
        return QaIntent.from_llm_dict(parsed), False, raw

    def fallback_intent(self, question: str) -> QaIntent:
        """无 JSON 时用问题原文前 30 字作关键词，避免整句 LIKE 几乎不命中。"""
        text = re.sub(r"\s+", " ", (question or "").strip())
        keywords = (text[:30],) if text else ()
        return QaIntent(keywords=keywords, notes="意图解析失败，使用原文关键词回退")

    # ── 检索 ──

    def retrieve(self, intent: QaIntent) -> QaRetrieval:
        """按意图检索案例行（多关键词 OR 合并）并生成证据包。"""
        base = intent.to_case_query(keyword="", limit=0)
        collected: list[CaseRow] = []
        seen: set[tuple[str, str]] = set()
        used: list[str] = []

        def absorb(rows: Sequence[CaseRow]) -> None:
            for row in rows:
                key = (row.dataset.value, row.case_id)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(row)

        if intent.keywords:
            for word in intent.keywords:
                rows = self._analysis.rows(base.replace(keyword=word, limit=intent.limit))
                if rows:
                    used.append(word)
                    absorb(rows)
        else:
            rows = self._analysis.rows(base.replace(limit=intent.limit))
            absorb(rows)

        # 稳定排序：日期降序，再按 case_id
        collected.sort(key=lambda r: (r.date or "", r.case_id), reverse=True)
        unique_count = len(collected)
        limited = collected[: intent.limit]

        # 统计：多关键词 OR 合并时不能再只取第一条关键词的 overview（会低估）。
        # 有结构化条件时用不带关键词的宽查询做聚合；否则从证据行本地聚合。
        structured = bool(
            intent.violations
            or intent.date_from
            or intent.date_to
            or intent.bureaus
            or intent.fund_related_only
            or intent.datasets
        )
        overview: dict[str, Any]
        if structured and not intent.keywords:
            try:
                overview = self._analysis.overview(
                    intent.to_case_query(keyword="", limit=0).replace(statuses=())
                )
            except Exception:  # pragma: no cover
                logger.warning("问答统计聚合失败", exc_info=True)
                overview = {"total": unique_count}
            total_hits = int(overview.get("total") or unique_count)
        else:
            result = self._analysis.analyze(limited)
            overview = {
                "total": unique_count,
                "violations": result.violation["ranked"][:10],
                "date_min": result.basic["date_min"],
                "date_max": result.basic["date_max"],
            }
            total_hits = unique_count

        evidence = build_evidence_pack(limited, overview)
        return QaRetrieval(
            intent=intent,
            rows=tuple(limited),
            overview=overview,
            evidence=evidence,
            total_hits=total_hits,
            used_keywords=tuple(used),
        )

    # ── 撰写 ──

    def compose_answer(
        self,
        question: str,
        retrieval: QaRetrieval,
        client: Any = None,
    ) -> str:
        system = QA_ANSWER_SYSTEM
        user = (
            f"用户问题：{question.strip()}\n\n"
            f"检索意图备注：{retrieval.intent.notes or '（无）'}\n\n"
            f"{retrieval.evidence}"
        )
        return self._client(client).chat_text(user, system=system, temperature=0.2).strip()

    def compose_report(
        self,
        question: str,
        retrieval: QaRetrieval,
        client: Any = None,
    ) -> str:
        title_hint = question.strip().replace("\n", " ")[:60] or "监管处分专题报告"
        system = QA_REPORT_SYSTEM.format(title_hint=title_hint)
        user = (
            f"专题要求：{question.strip()}\n\n"
            f"检索意图备注：{retrieval.intent.notes or '（无）'}\n\n"
            f"{retrieval.evidence}"
        )
        return self._client(client).chat_text(user, system=system, temperature=0.3).strip()

    # ── 组合入口 ──

    def ask(
        self,
        question: str,
        *,
        mode: str | None = None,
        client: Any = None,
    ) -> QaTurn:
        """完整管线：意图 → 检索 → 问答或专题报告。

        Args:
            question: 用户自然语言问题或报告主题。
            mode: 强制模式（``qa`` / ``report``）；``None`` 时听从意图解析。
            client: 可选的临时模型客户端（BYOK）。
        """
        intent, fallback, _raw = self.parse_intent(question, client=client)
        if mode in {"qa", "report"}:
            intent = intent.replace(mode=mode)
        retrieval = self.retrieve(intent)
        if intent.mode == "report":
            answer = self.compose_report(question, retrieval, client=client)
        else:
            answer = self.compose_answer(question, retrieval, client=client)

        model_name = ""
        resolved = client
        if resolved is None:
            try:
                resolved = self._client(client)
            except LLMError:
                resolved = None
        if resolved is not None:
            model_name = str(getattr(getattr(resolved, "profile", None), "model", "") or "")

        return QaTurn(
            mode=intent.mode,
            question=question,
            answer=answer,
            intent=intent,
            retrieval=retrieval,
            model=model_name,
            used_fallback_intent=fallback,
        )
