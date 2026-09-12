"""领域层：枚举、数据模型与违规分类体系。

本层**不含任何 IO**，可被数据层、用例层与交付层自由引用。
"""

from __future__ import annotations

from .enums import (
    CaseStatus,
    CaseType,
    Category,
    Dataset,
    EntityType,
    JobKind,
    JobStatus,
    SourceType,
)
from .models import (
    CaseQuery,
    CaseRecord,
    CaseRow,
    ModelProfile,
    SummaryRecord,
    TaskLogLine,
    TaskRecord,
    split_multi_value,
)
from .violations import (
    PUNISHMENT_CATEGORIES,
    VIOLATION_ADVICE,
    VIOLATION_TYPES,
    VIOLATION_TYPES_AMAC,
    VIOLATION_TYPES_CSRC,
    normalize_violations,
)

__all__ = [
    "PUNISHMENT_CATEGORIES",
    "VIOLATION_ADVICE",
    "VIOLATION_TYPES",
    "VIOLATION_TYPES_AMAC",
    "VIOLATION_TYPES_CSRC",
    "CaseQuery",
    "CaseRecord",
    "CaseRow",
    "CaseStatus",
    "CaseType",
    "Category",
    "Dataset",
    "EntityType",
    "JobKind",
    "JobStatus",
    "ModelProfile",
    "SourceType",
    "SummaryRecord",
    "TaskLogLine",
    "TaskRecord",
    "normalize_violations",
    "split_multi_value",
]
