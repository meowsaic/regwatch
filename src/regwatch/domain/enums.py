"""领域枚举。

把原先散落在 ``storage`` / ``summarize`` / ``analyze`` / ``web`` 中的字符串字面量
（``"amac"``、``"done"``、``"skipped"``、``"penalty"``、``"scfjg"``……）收敛为枚举，
避免拼写错误与多处不一致。

所有枚举都继承 :class:`enum.StrEnum`，因此可以直接与 JSON / SQLite 中的字符串
比较和存取，无需额外转换。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

__all__ = [
    "CaseStatus",
    "CaseType",
    "Category",
    "Dataset",
    "EntityType",
    "JobKind",
    "JobStatus",
    "SourceType",
]


class _Labelled(StrEnum):
    """带中文展示名的字符串枚举基类。"""

    @property
    def label(self) -> str:
        return _LABELS.get(self.value, self.value)

    @classmethod
    def parse(cls, value: str | None, default: Self | None = None) -> Self | None:
        """宽松解析：无法识别时返回 ``default``（默认 ``None``）。"""
        if value is None:
            return default
        try:
            return cls(str(value).strip())
        except ValueError:
            return default


class Dataset(_Labelled):
    """数据集（监管主体）。"""

    AMAC = "amac"
    CSRC = "csrc"

    @classmethod
    def all(cls) -> tuple[Dataset, ...]:
        return (cls.AMAC, cls.CSRC)

    @classmethod
    def parse_many(cls, values: str | None) -> tuple[Dataset, ...]:
        """解析 ``"amac,csrc"`` 或 ``"all"`` 形式的取值。"""
        text = (values or "").strip().lower()
        if not text or text in {"all", "*"}:
            return cls.all()
        parsed = [cls.parse(item) for item in text.replace("、", ",").split(",")]
        return tuple(item for item in parsed if isinstance(item, Dataset))


class CaseStatus(_Labelled):
    """案例处理状态。

    - ``PENDING`` 已抓取尚未提取摘要
    - ``DONE`` 已成功提取摘要
    - ``SKIPPED`` 模型精判为「非基金相关」（CSRC）或无需提取
    - ``FAILED`` 提取失败
    """

    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"

    @property
    def is_finished(self) -> bool:
        return self in (CaseStatus.DONE, CaseStatus.SKIPPED, CaseStatus.FAILED)


class CaseType(_Labelled):
    """CSRC 案例类型（处罚重于措施）。"""

    PENALTY = "penalty"
    MEASURE = "measure"
    UNKNOWN = ""

    @classmethod
    def parse_many(cls, values: str | None) -> tuple[CaseType, ...]:
        text = (values or "").strip().lower()
        if not text or text in {"all", "*"}:
            return (cls.PENALTY, cls.MEASURE)
        parsed = [cls.parse(item, cls.UNKNOWN) for item in text.replace("、", ",").split(",")]
        return tuple(
            item for item in parsed if isinstance(item, CaseType) and item is not cls.UNKNOWN
        )


class Category(_Labelled):
    """AMAC 受处分主体类别。"""

    INSTITUTION = "scfjg"
    PERSONNEL = "scfry"
    UNKNOWN = ""


class EntityType(_Labelled):
    """受处分主体类型（摘要字段）。"""

    INSTITUTION = "机构"
    PERSONNEL = "个人"
    BOTH = "机构+个人"
    UNKNOWN = ""


class SourceType(_Labelled):
    """AMAC 原文来源形态。"""

    HTML = "html"
    PDF_DIRECT = "pdf_direct"
    PDF_EMBEDDED = "pdf_embedded"
    UNKNOWN = ""


class JobKind(_Labelled):
    """后台任务类型。"""

    FETCH_AMAC = "fetch_amac"
    FETCH_CSRC = "fetch_csrc"
    FETCH_MONTHLY = "fetch_monthly"
    SUMMARIZE = "summarize"
    REPORT = "report"
    ORG_TYPE = "org_type"

    @classmethod
    def all(cls) -> tuple[JobKind, ...]:
        return (
            cls.FETCH_AMAC,
            cls.FETCH_CSRC,
            cls.FETCH_MONTHLY,
            cls.SUMMARIZE,
            cls.REPORT,
            cls.ORG_TYPE,
        )


class JobStatus(_Labelled):
    """后台任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_finished(self) -> bool:
        return self in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.CANCELLED)


_LABELS: dict[str, str] = {
    # 数据集
    "amac": "中基协 AMAC",
    "csrc": "证监会 CSRC",
    # 案例状态
    "pending": "待提取",
    "done": "已提取",
    "skipped": "非基金相关",
    "failed": "提取失败",
    # 案例类型
    "penalty": "行政处罚",
    "measure": "监管措施",
    # 主体类别
    "scfjg": "机构",
    "scfry": "个人",
    # 主体类型
    "机构": "机构",
    "个人": "个人",
    "机构+个人": "机构+个人",
    # 来源形态
    "html": "网页",
    "pdf_direct": "PDF 直链",
    "pdf_embedded": "内嵌 PDF",
    # 任务类型
    "fetch_amac": "AMAC 案例抓取",
    "fetch_csrc": "CSRC 案例抓取",
    "fetch_monthly": "AMAC 月度公告下载",
    "summarize": "结构化摘要提取",
    "report": "报告生成",
    "org_type": "机构登记类型回填",
    # 任务状态
    "running": "执行中",
    "success": "已完成",
    "cancelled": "已取消",
    "排队中": "排队中",
}
