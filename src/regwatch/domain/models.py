"""领域模型。

纯数据结构，**不含任何 IO**：不读文件、不连数据库、不发网络请求。
序列化细节由仓储层负责，领域层只负责表达业务概念。

设计约定：

- 全部为 ``dataclass``，字段均带默认值，``from_dict`` 忽略未知键并对布尔值做宽松转换，
  因此历史数据缺字段或将来新增字段都不会导致载入失败。
- 枚举字段在 ``__post_init__`` 中统一归一化，外部传字符串也能得到枚举实例。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from datetime import date as _date
from typing import Any, Self

from .enums import CaseStatus, CaseType, Category, Dataset, JobKind, JobStatus

__all__ = [
    "CaseQuery",
    "CaseRecord",
    "CaseRow",
    "ModelProfile",
    "QaIntent",
    "SummaryRecord",
    "TaskRecord",
    "split_multi_value",
]


# ──────────────────────────── 通用工具 ────────────────────────────


def _to_bool(value: Any) -> bool | None:
    """宽松布尔转换。

    返回 ``None`` 表示「该字段不适用 / 缺失」（对应数据库 NULL），
    这样 AMAC（恒为基金相关）与 CSRC（需模型判定）可以共用一个字段。
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "t"}:
            return True
        if text in {"0", "false", "no", "n", "f"}:
            return False
    return None


#: 多值字段分隔符（顿号优先，兼容逗号、斜杠与中点「·」）
_MULTI_SEP = re.compile(r"[、,，/;；·]")


def split_multi_value(value: str | None) -> tuple[str, ...]:
    """拆分多值字段（``"信息披露违规、操纵市场"`` → 两项元组）。

    历史上模型偶尔输出中点分隔（``"未配合自律管理·未按规定备案"``），
    因此把「·」也计入分隔符；冒号「类型：子项」的拆分由
    :func:`regwatch.domain.violations.normalize_violations` 负责。
    """
    if not value:
        return ()
    parts = (part.strip() for part in _MULTI_SEP.split(str(value)))
    return tuple(part for part in parts if part)


def coerce_date(value: _date | str | None) -> str | None:
    """把日期归一为 ``YYYY-MM-DD`` 字符串，无法识别时返回 ``None``。"""
    if value is None:
        return None
    if isinstance(value, _date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


class _Record:
    """JSON 记录基类：提供容错的 ``from_dict`` 与 ``to_dict``。"""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Self:
        field_map = {f.name: f for f in fields(cls)}  # type: ignore[arg-type]
        kwargs: dict[str, Any] = {}
        for key, value in (data or {}).items():
            spec = field_map.get(key)
            if spec is None:
                continue  # 忽略未知字段，保证向前兼容
            kwargs[key] = value
        return cls(**kwargs)  # type: ignore[call-arg]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    def replace(self, **changes: Any) -> Self:
        """返回改动若干字段后的副本（原对象不变）。"""
        merged = {**{f.name: getattr(self, f.name) for f in fields(self)}, **changes}  # type: ignore[arg-type]
        return self.__class__(**merged)  # type: ignore[arg-type]


# ──────────────────────────── 模型接入配置 ────────────────────────────


@dataclass
class ModelProfile(_Record):
    """一条 OpenAI 兼容的模型接入配置。

    Attributes:
        id: 唯一标识，供任务绑定引用。
        label: 展示名称，留空时回落到 ``id``。
        base_url: 兼容 OpenAI 的接口根地址，如 ``https://api.deepseek.com``。
        api_key: 密钥；存于 ``config.json``（已 gitignore）。
        api_key_env: 可选的环境变量名，优先级高于 ``api_key``。
        model: 文本模型名，如 ``deepseek-chat``。
        vision_model: 视觉模型名（PDF/图片识别），留空则回落到 ``model``。
        vision_content_type: 视觉消息内容类型，多数端点用 ``image_url``，
            少数（如智谱）需要 ``file_url``。
        token_param: 上限参数字段名，``max_tokens`` 或 ``max_completion_tokens``。
        extra: 透传给 ``chat.completions.create`` 的附加参数。
        note: 备注，供界面展示。
    """

    id: str = ""
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

    def __post_init__(self) -> None:
        if self.token_param not in ("max_tokens", "max_completion_tokens"):
            self.token_param = "max_tokens"
        if not self.vision_content_type:
            self.vision_content_type = "image_url"
        if not isinstance(self.extra, dict):
            self.extra = {}

    @property
    def display_name(self) -> str:
        return self.label or self.id

    @property
    def masked_key(self) -> str:
        key = self.api_key or ""
        if not key:
            return "（未填写）"
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}****{key[-4:]}"

    def resolved_vision_model(self) -> str:
        return self.vision_model or self.model

    def missing_fields(self) -> list[str]:
        """返回缺失的必填项中文名，供界面校验提示。"""
        missing: list[str] = []
        if not self.id.strip():
            missing.append("配置标识")
        if not self.base_url.strip():
            missing.append("接口地址 base_url")
        if not self.model.strip():
            missing.append("文本模型 model")
        return missing


# ──────────────────────────── 案例与摘要 ────────────────────────────


@dataclass
class CaseRecord(_Record):
    """案例原文记录（AMAC 与 CSRC 统一表，差异字段允许为空）。

    ``raw_text`` 单独存放（库中 ``case_bodies`` 表），列表与统计查询不会触碰大字段。
    """

    dataset: Dataset = Dataset.AMAC
    case_id: str = ""

    # 通用字段
    source_url: str = ""
    title: str = ""
    date: str = ""
    status: CaseStatus = CaseStatus.PENDING
    status_note: str = ""
    fetch_time: str = ""
    error: str = ""
    pdf_url: str = ""

    # AMAC 专属
    category: Category = Category.UNKNOWN  # scfjg 机构 / scfry 人员
    org_type: str = ""
    punished_entity: str = ""
    source_type: str = ""  # html / pdf_direct / pdf_embedded
    ocr_success: bool = False

    # CSRC 专属
    case_type: CaseType = CaseType.UNKNOWN  # penalty 处罚 / measure 措施
    bureau: str = ""  # HQ 总部 / 各派出机构
    document_number: str = ""
    punished_entities: str = ""
    is_fund_related: bool | None = None  # None = 不适用（AMAC）
    fund_evidence: str = ""
    doc_url: str = ""

    # 正文（通常为空，仅读写单条时填充）
    raw_text: str = ""

    def __post_init__(self) -> None:
        self.dataset = Dataset.parse(self.dataset, Dataset.AMAC) or Dataset.AMAC
        self.status = CaseStatus.parse(self.status, CaseStatus.PENDING) or CaseStatus.PENDING
        self.case_type = CaseType.parse(self.case_type, CaseType.UNKNOWN) or CaseType.UNKNOWN
        self.category = Category.parse(self.category, Category.UNKNOWN) or Category.UNKNOWN
        self.is_fund_related = _to_bool(self.is_fund_related)
        self.ocr_success = bool(self.ocr_success)

    @property
    def entity_kind(self) -> str:
        """机构 / 个人展示文本。"""
        if self.category is Category.INSTITUTION:
            return "机构"
        if self.category is Category.PERSONNEL:
            return "个人"
        return ""

    @property
    def display_entities(self) -> str:
        """受处分主体：AMAC 用 ``punished_entity``，CSRC 用 ``punished_entities``。"""
        return self.punished_entities or self.punished_entity

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["dataset"] = self.dataset.value
        data["status"] = self.status.value
        data["case_type"] = self.case_type.value
        data["category"] = self.category.value
        return data


@dataclass
class SummaryRecord(_Record):
    """结构化摘要记录。

    ``violation_type`` 保留原始多值字符串（顿号分隔），
    ``violation_types`` 为拆分后的元组，用于统计与筛选。
    """

    dataset: Dataset = Dataset.AMAC
    case_id: str = ""

    entity_type: str = ""
    #: AMAC 摘要会带上模型从正文识别出的受处分机构全称
    punished_entity: str = ""
    violation_type: str = ""
    punishment: str = ""
    punishment_date: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""

    # CSRC 专属
    penalty_amount: str = ""
    market_ban: str = ""

    # 提取元信息
    extract_success: bool = False
    error: str = ""
    extract_time: str = ""
    llm_provider: str = ""
    llm_model: str = ""

    def __post_init__(self) -> None:
        self.dataset = Dataset.parse(self.dataset, Dataset.AMAC) or Dataset.AMAC
        self.extract_success = bool(self.extract_success)

    @property
    def violation_types(self) -> tuple[str, ...]:
        return split_multi_value(self.violation_type)

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["dataset"] = self.dataset.value
        return data


@dataclass(frozen=True, slots=True)
class CaseRow:
    """案例统一视图：案例字段 + 摘要字段的扁平组合（**不含正文**）。

    列表、统计、报告、网页端全部消费这一结构，避免各处自行拼装字典。
    """

    dataset: Dataset
    case_id: str
    title: str = ""
    date: str = ""
    source_url: str = ""
    status: CaseStatus = CaseStatus.PENDING
    status_note: str = ""

    bureau: str = ""
    case_type: CaseType = CaseType.UNKNOWN
    category: Category = Category.UNKNOWN
    entity_type: str = ""
    org_type: str = ""
    punished_entities: str = ""
    document_number: str = ""
    is_fund_related: bool | None = None
    fund_evidence: str = ""

    violation_type: str = ""
    punishment: str = ""
    punishment_date: str = ""
    involved_fund: str = ""
    violation_summary: str = ""
    legal_basis: str = ""
    penalty_amount: str = ""
    market_ban: str = ""

    extract_time: str = ""
    fetch_time: str = ""
    pdf_url: str = ""
    doc_url: str = ""
    error: str = ""
    has_body: bool = False

    def __post_init__(self) -> None:
        # 冻结数据类只能这样改写字段；归一化保证外部传字符串也能得到枚举
        object.__setattr__(
            self, "dataset", Dataset.parse(self.dataset, Dataset.AMAC) or Dataset.AMAC
        )
        object.__setattr__(
            self, "status", CaseStatus.parse(self.status, CaseStatus.PENDING) or CaseStatus.PENDING
        )
        object.__setattr__(
            self, "case_type", CaseType.parse(self.case_type, CaseType.UNKNOWN) or CaseType.UNKNOWN
        )
        object.__setattr__(
            self, "category", Category.parse(self.category, Category.UNKNOWN) or Category.UNKNOWN
        )

    @property
    def violation_types(self) -> tuple[str, ...]:
        return split_multi_value(self.violation_type)

    @property
    def dataset_label(self) -> str:
        return self.dataset.label

    @property
    def entity_display(self) -> str:
        """受处分主体展示文本（缺失时给出占位）。"""
        return self.punished_entities or "（未标注）"

    @property
    def status_label(self) -> str:
        return self.status.label

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["dataset"] = self.dataset.value
        data["status"] = self.status.value
        data["case_type"] = self.case_type.value
        data["category"] = self.category.value
        data["violation_types"] = list(self.violation_types)
        # 展示用派生字段：网页端表格直接按 dict 键取用，避免各处重复做枚举到文案的映射
        data["dataset_label"] = self.dataset_label
        data["status_label"] = self.status_label
        return data


@dataclass(frozen=True, slots=True)
class CaseQuery:
    """案例筛选条件——**CLI 与网页端共用的唯一真源**。

    空元组表示该维度不限制。``keyword`` 走 FTS5 全文索引（标题 + 正文 + 当事人）。
    """

    datasets: tuple[Dataset, ...] = ()
    statuses: tuple[CaseStatus, ...] = ()
    case_types: tuple[CaseType, ...] = ()
    categories: tuple[Category, ...] = ()
    violations: tuple[str, ...] = ()
    bureaus: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    date_from: str | None = None
    date_to: str | None = None
    keyword: str = ""
    fund_related_only: bool = False
    limit: int = 0
    offset: int = 0
    order_by: str = "date"
    descending: bool = True

    def with_datasets(self, value: str | None) -> CaseQuery:
        return self.replace(datasets=Dataset.parse_many(value))

    def replace(self, **changes: Any) -> CaseQuery:
        return CaseQuery(**{**{f.name: getattr(self, f.name) for f in fields(self)}, **changes})  # type: ignore[arg-type]


# ──────────────────────────── 智能问答意图 ────────────────────────────


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """把模型输出的列表/字符串归一为去空去重的字符串元组。"""
    if value is None:
        return ()
    items: Iterable[Any] = value if isinstance(value, (list, tuple, set)) else [value]
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _clamp_limit(value: Any, default: int = 15, lo: int = 1, hi: int = 40) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _strict_date(value: _date | str | None) -> str | None:
    """只接受真实可解析的 ``YYYY-MM-DD``；垃圾日期一律丢弃为 ``None``。"""
    text = coerce_date(value)
    if not text or not _ISO_DATE.match(text):
        return None
    try:
        _date.fromisoformat(text)
    except ValueError:
        return None
    return text


@dataclass(frozen=True, slots=True)
class QaIntent:
    """智能问答的结构化检索意图（模型 JSON 输出的归一结果）。

    ``mode`` 为 ``qa``（问答）或 ``report``（专题报告）。
    ``keywords`` 会逐条检索后合并；``violations`` / ``datasets`` 等映射到
    :class:`CaseQuery`。非法值一律丢弃，绝不因脏输出抛错。
    """

    mode: str = "qa"
    keywords: tuple[str, ...] = ()
    datasets: tuple[Dataset, ...] = ()
    violations: tuple[str, ...] = ()
    bureaus: tuple[str, ...] = ()
    date_from: str | None = None
    date_to: str | None = None
    limit: int = 15
    fund_related_only: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        # 违规类型归一与未知类型降级为关键词；violations.py 反向依赖 models，
        # 故在此延迟导入避免环。
        from .violations import UNCLASSIFIED, VIOLATION_TYPES, normalize_violations

        object.__setattr__(self, "mode", "report" if self.mode == "report" else "qa")
        known_v: list[str] = []
        demoted: list[str] = []
        for item in _as_str_tuple(self.violations):
            for normed in normalize_violations(item):
                if normed == UNCLASSIFIED:
                    continue
                if normed in VIOLATION_TYPES:
                    if normed not in known_v:
                        known_v.append(normed)
                elif normed not in demoted:
                    demoted.append(normed)
        object.__setattr__(self, "violations", tuple(known_v))

        keywords: list[str] = []
        for word in (*_as_str_tuple(self.keywords), *demoted):
            if word and word not in keywords:
                keywords.append(word)
        object.__setattr__(self, "keywords", tuple(keywords))

        object.__setattr__(self, "bureaus", _as_str_tuple(self.bureaus))
        object.__setattr__(self, "limit", _clamp_limit(self.limit))
        object.__setattr__(self, "fund_related_only", bool(self.fund_related_only))
        object.__setattr__(self, "notes", str(self.notes or "").strip())
        clean_ds: list[Dataset] = []
        for item in self.datasets:
            parsed = item if isinstance(item, Dataset) else Dataset.parse(str(item).strip().lower())
            if parsed is not None and parsed not in clean_ds:
                clean_ds.append(parsed)
        object.__setattr__(self, "datasets", tuple(clean_ds))
        date_from = _strict_date(self.date_from)
        date_to = _strict_date(self.date_to)
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
        object.__setattr__(self, "date_from", date_from)
        object.__setattr__(self, "date_to", date_to)

    @classmethod
    def from_llm_dict(cls, data: dict[str, Any] | None) -> QaIntent:
        """从容错的模型 JSON 字典构造意图；``None`` / 非法字段回落默认。"""
        raw = data if isinstance(data, dict) else {}
        mode = str(raw.get("mode") or "qa").strip().lower()
        if mode not in {"qa", "report"}:
            mode = "report" if "报告" in str(raw.get("mode") or "") else "qa"
        return cls(
            mode=mode,
            keywords=_as_str_tuple(raw.get("keywords") or raw.get("keyword")),
            datasets=tuple(
                parsed
                for item in _as_str_tuple(raw.get("datasets") or raw.get("dataset"))
                for parsed in [Dataset.parse(item.lower())]
                if parsed is not None
            ),
            violations=_as_str_tuple(raw.get("violations") or raw.get("violation")),
            bureaus=_as_str_tuple(raw.get("bureaus") or raw.get("bureau")),
            date_from=_strict_date(raw.get("date_from") or raw.get("start_date")),
            date_to=_strict_date(raw.get("date_to") or raw.get("end_date")),
            limit=_clamp_limit(raw.get("limit"), default=15),
            fund_related_only=_to_bool(raw.get("fund_related_only")) is True,
            notes=str(raw.get("notes") or ""),
        )

    def to_case_query(self, *, keyword: str = "", limit: int | None = None) -> CaseQuery:
        """映射为检索条件；``keyword`` 覆盖关键词（多关键词由服务逐条检索合并）。

        状态默认排除「判定非基金相关」（skipped）——这些是采集期确定性排除
        或模型精判为无关的占位记录，没有摘要，检出只会挤占证据名额。
        """
        return CaseQuery(
            datasets=self.datasets,
            statuses=(CaseStatus.DONE,)
            if self.violations
            else (CaseStatus.DONE, CaseStatus.PENDING),
            violations=self.violations,
            bureaus=self.bureaus,
            date_from=self.date_from,
            date_to=self.date_to,
            keyword=keyword or (self.keywords[0] if self.keywords else ""),
            fund_related_only=self.fund_related_only,
            limit=limit if limit is not None else self.limit,
            order_by="date",
            descending=True,
        )

    def primary_keyword(self) -> str:
        return self.keywords[0] if self.keywords else ""

    def replace(self, **changes: Any) -> QaIntent:
        return QaIntent(**{**{f.name: getattr(self, f.name) for f in fields(self)}, **changes})  # type: ignore[arg-type]


# ──────────────────────────── 任务 ────────────────────────────


@dataclass
class TaskRecord(_Record):
    """一个后台任务的完整状态快照。"""

    id: str = ""
    kind: JobKind = JobKind.SUMMARIZE
    title: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    processed: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.kind = JobKind.parse(self.kind, JobKind.SUMMARIZE) or JobKind.SUMMARIZE
        self.status = JobStatus.parse(self.status, JobStatus.PENDING) or JobStatus.PENDING
        if not isinstance(self.params, dict):
            self.params = {}
        if not isinstance(self.result, dict):
            self.result = {}

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
        data = super().to_dict()
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        data["progress"] = self.progress
        data["progress_percent"] = self.progress_percent
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskRecord:
        raw = dict(data or {})
        raw.pop("progress", None)
        raw.pop("progress_percent", None)
        record = super().from_dict(raw)
        # __post_init__ 已完成枚举与字典字段的归一化
        return record


@dataclass(frozen=True, slots=True)
class TaskLogLine:
    """任务日志的一行。"""

    task_id: str
    seq: int
    level: str = "INFO"
    message: str = ""
    created_at: str = ""

    def format(self) -> str:
        stamp = self.created_at or ""
        prefix = f"{stamp} " if stamp else ""
        return f"{prefix}[{self.level}] {self.message}"
