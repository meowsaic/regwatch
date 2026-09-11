"""regwatch 数据模型。

三类模型：

1. ``ModelProfile`` —— 一条 OpenAI 兼容模型接入配置。
2. 案例与摘要记录 —— ``AmacCase`` / ``AmacSummary`` / ``CsrcCase`` / ``CsrcSummary``，
   字段名与现有磁盘 JSON **严格一致**，不做重命名，避免破坏已有数据与索引。
3. ``TaskRecord`` / ``TaskStatus`` —— 后台任务状态机。

设计取舍：所有字段都带默认值，``from_dict`` 忽略未知键并对布尔值做宽松转换，
这样即使历史数据缺字段、或后续版式新增字段，旧文件依然可以正常载入。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any

__all__ = [
    "AmacCase",
    "AmacSummary",
    "CsrcCase",
    "CsrcSummary",
    "ModelProfile",
    "TaskRecord",
    "TaskStatus",
]


def _to_bool(value: Any) -> bool:
    """宽松布尔转换，兼容 ``"true"`` / ``"false"`` 等字符串写法。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "t"}
    return bool(value)


class _Record:
    """JSON 记录基类：提供容错的 ``from_dict`` 与 ``to_dict``。"""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):
        field_map = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
        kwargs: dict[str, Any] = {}
        for key, value in (data or {}).items():
            spec = field_map.get(key)
            if spec is None:
                continue  # 忽略未知字段，保证向前兼容
            if spec.type is bool or spec.type == "bool":
                value = _to_bool(value)
            kwargs[key] = value
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


# ──────────────────────────── 模型接入配置 ────────────────────────────


@dataclass
class ModelProfile(_Record):
    """一条 OpenAI 兼容的模型接入配置。

    Attributes:
        id: 唯一标识，供 ``tasks`` 引用。
        label: 展示名称，留空时回落到 ``id``。
        base_url: 兼容 OpenAI 的接口根地址，如 ``https://api.deepseek.com``。
        api_key: 密钥；入库存于 ``config.json``（已在 .gitignore 中排除）。
        api_key_env: 可选的环境变量名，优先级高于 ``api_key``。
        model: 文本模型名，如 ``deepseek-chat``。
        vision_model: 视觉模型名（用于 PDF/图片识别），留空则回落到 ``model``。
        vision_content_type: 视觉消息内容类型，多数端点用 ``image_url``，
            少数（如智谱）需要 ``file_url``。
        token_param: 上限参数字段名，``max_tokens`` 或 ``max_completion_tokens``。
        extra: 透传给 ``chat.completions.create`` 的附加参数
            （如 ``{"thinking": {"type": "disabled"}}``、``{"top_p": 0.95}``）。
        note: 备注，供界面展示。
    """

    id: str
    label: str = ""
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model: str = ""
    vision_model: str = ""
    vision_content_type: str = "image_url"
    token_param: str = "max_tokens"
    extra: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def display_name(self) -> str:
        return self.label or self.id

    def resolved_vision_model(self) -> str:
        return self.vision_model or self.model

    @property
    def masked_key(self) -> str:
        key = self.api_key or ""
        if not key:
            return "（未填写）"
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}****{key[-4:]}"

    def missing_fields(self) -> list[str]:
        """返回缺失的必填项中文名，供界面校验提示。"""
        missing: list[str] = []
        if not (self.id or "").strip():
            missing.append("配置标识")
        if not (self.base_url or "").strip():
            missing.append("接口地址 base_url")
        if not (self.model or "").strip():
            missing.append("文本模型 model")
        return missing


# ──────────────────────────── AMAC 案例与摘要 ────────────────────────────


@dataclass
class AmacCase(_Record):
    """中基协纪律处分案例原文（对应 data/amac/cases/{institution|personnel}/*.json）。"""

    case_id: str = ""
    source_url: str = ""
    source_type: str = ""  # html / pdf_direct / pdf_embedded
    category: str = ""  # scfjg(机构) / scfry(人员)
    title: str = ""
    date: str = ""
    raw_text: str = ""
    fetch_time: str = ""
    ocr_success: bool = False
    error: str = ""
    pdf_url: str = ""
    org_type: str = ""
    punished_entity: str = ""

    @property
    def entity_kind(self) -> str:
        """机构/个人，用于界面展示（category 为空时返回空串）。"""
        if self.category == "scfjg":
            return "机构"
        if self.category == "scfry":
            return "个人"
        return ""


@dataclass
class AmacSummary(_Record):
    """中基协纪律处分结构化摘要（对应 data/amac/summaries/{case_id}_summary.json）。"""

    case_id: str = ""
    source_url: str = ""
    category: str = ""
    title: str = ""
    date: str = ""
    punished_entity: str = ""
    entity_type: str = ""  # 机构 / 个人
    org_type: str = ""
    violation_type: str = ""  # 多值以顿号分隔
    punishment: str = ""
    punishment_date: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    extract_success: bool = False
    error: str = ""
    extract_time: str = ""


# ──────────────────────────── CSRC 案例与摘要 ────────────────────────────


@dataclass
class CsrcCase(_Record):
    """证监会案例原文（对应 data/csrc/cases/{Bureau}/{measure|penalty}/*.json）。"""

    case_id: str = ""
    source_url: str = ""
    case_type: str = ""  # penalty(行政处罚) / measure(监管措施)
    bureau: str = ""  # HQ / Beijing / ...
    title: str = ""
    date: str = ""
    raw_text: str = ""
    fetch_time: str = ""
    error: str = ""
    is_fund_related: bool = False
    fund_evidence: str = ""
    document_number: str = ""
    punished_entities: str = ""
    pdf_url: str = ""
    doc_url: str = ""

    @property
    def case_type_cn(self) -> str:
        return {"penalty": "行政处罚", "measure": "监管措施"}.get(self.case_type, self.case_type)


@dataclass
class CsrcSummary(_Record):
    """证监会案例结构化摘要（对应 data/csrc/summaries/{Bureau}/{case_type}/*_summary.json）。

    注意：磁盘上的 CSRC summary **不包含** ``raw_text``，正文需回 ``cases`` 目录读取。
    """

    case_id: str = ""
    source_url: str = ""
    case_type: str = ""
    bureau: str = ""
    title: str = ""
    date: str = ""
    document_number: str = ""
    punished_entities: str = ""
    is_fund_related: bool = False
    fund_evidence: str = ""
    pdf_url: str = ""
    entity_type: str = ""
    violation_type: str = ""
    punishment: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    penalty_amount: str = ""
    market_ban: str = ""
    extract_success: bool = False
    error: str = ""
    extract_time: str = ""
    llm_provider: str = ""
    llm_model: str = ""


# ──────────────────────────── 任务状态机 ────────────────────────────


class TaskStatus(StrEnum):
    """后台任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "pending": "排队中",
            "running": "执行中",
            "success": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
        }[self.value]

    @property
    def is_finished(self) -> bool:
        return self in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class TaskRecord:
    """一个后台任务的完整状态快照。"""

    id: str
    kind: str  # fetch_amac / fetch_csrc / summarize / report / org_type
    title: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    processed: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def progress(self) -> float:
        """完成度 0~1；总数未知时返回 0。"""
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.processed / self.total))

    @property
    def progress_percent(self) -> int:
        return round(self.progress * 100)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["progress"] = self.progress
        data["progress_percent"] = self.progress_percent
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskRecord:
        raw = dict(data or {})
        status = raw.get("status", TaskStatus.PENDING.value)
        try:
            raw["status"] = TaskStatus(status)
        except ValueError:
            raw["status"] = TaskStatus.PENDING
        raw.pop("progress", None)
        raw.pop("progress_percent", None)
        return super().from_dict(raw)  # type: ignore[misc]
