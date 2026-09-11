"""统计分析层。

把两套异构数据（AMAC / CSRC）的案例清单 :class:`~regwatch.storage.CaseRow`
归一后计算各维度统计，供报告渲染与网页看板复用：

- 基础指标（总量、机构/个人构成、日期范围）
- 违规类型分布（含多值字段拆分与分类归一）
- 处罚措施分布（自由文本归入粗类别）
- 法规引用 TOP
- 机构 vs 个人对比
- 时间趋势（按月）
- 来源局分布（仅 CSRC）

统计口径与历史版本保持一致：``violation_type`` 按顿号/分号拆分后，
对每个片段做「包含即归一」匹配到分类体系（:data:`regwatch.prompts.VIOLATION_TYPES`），
未匹配到的新类型原样保留，避免数据丢失。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .prompts import PUNISHMENT_CATEGORIES, VIOLATION_TYPES
from .storage import (
    DATASET_LABELS,
    CaseRow,
    split_multi_value,
)

__all__ = [
    "AnalysisResult",
    "analyze",
    "compute_basic_stats",
    "compute_bureau_stats",
    "compute_dataset_split",
    "compute_entity_comparison",
    "compute_legal_basis_stats",
    "compute_punishment_stats",
    "compute_time_trend",
    "compute_violation_stats",
    "normalize_violation_types",
    "pick_representative_cases",
]


# ──────────────────────────── 归一化 ────────────────────────────


def normalize_violation_types(raw: str) -> list[str]:
    """把 ``violation_type`` 多值字段拆分并归一到分类体系。

    拆分后逐片段匹配：片段中包含某个体系内类型（如「承诺保本保收益」包含
    「违规募集」为否，但「违规募集（向不合格投资者...）」包含「违规募集」为是）
    即归一；全部未命中时保留原片段，保证新类型不被丢弃。
    """
    result: list[str] = []
    for fragment in split_multi_value(raw):
        for known in VIOLATION_TYPES:
            if known in fragment:
                if known not in result:
                    result.append(known)
                break
        else:
            if fragment not in result:
                result.append(fragment)
    return result or ["未分类"]


def _percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


# ──────────────────────────── 基础指标 ────────────────────────────


def compute_basic_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """总量、构成与日期范围。"""
    institutions = [row for row in rows if row.entity_type == "机构"]
    personnel = [row for row in rows if row.entity_type == "个人"]
    dates = sorted(row.date for row in rows if row.date)

    status_counter = Counter(row.status for row in rows)
    return {
        "total": len(rows),
        "institution_count": len(institutions),
        "personnel_count": len(personnel),
        "unknown_entity_count": len(rows) - len(institutions) - len(personnel),
        "date_range": f"{dates[0]} ~ {dates[-1]}" if dates else "",
        "date_min": dates[0] if dates else "",
        "date_max": dates[-1] if dates else "",
        "status_counts": dict(status_counter),
        "institutions": list(institutions),
        "personnel": list(personnel),
    }


# ──────────────────────────── 违规类型 ────────────────────────────


def compute_violation_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """违规类型分布；一个案例可计入多个类型。"""
    counter: Counter[str] = Counter()
    case_map: dict[str, list[CaseRow]] = {}

    for row in rows:
        for vtype in normalize_violation_types(row.violation_type):
            counter[vtype] += 1
            case_map.setdefault(vtype, []).append(row)

    ranked = counter.most_common()
    total_mentions = sum(counter.values())
    return {
        "counter": counter,
        "ranked": ranked,
        "total_mentions": total_mentions,
        "type_count": len(counter),
        "case_map": case_map,
    }


# ──────────────────────────── 处罚措施 ────────────────────────────


def _categorize_punishment(text: str) -> str:
    for category, keywords in PUNISHMENT_CATEGORIES.items():
        if category == "其他":
            continue
        for keyword in keywords:
            if keyword in text:
                return category
    return "其他"


def compute_punishment_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """处罚措施分布：精确文本 + 粗类别两层。"""
    counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    case_map: dict[str, list[CaseRow]] = {}

    for row in rows:
        punishment = row.punishment.strip()
        if not punishment:
            punishment = "未明确"
        counter[punishment] += 1
        case_map.setdefault(punishment, []).append(row)
        category_counter[_categorize_punishment(punishment)] += 1

    return {
        "counter": counter,
        "ranked": counter.most_common(),
        "category_ranked": category_counter.most_common(),
        "case_map": case_map,
    }


# ──────────────────────────── 法规依据 ────────────────────────────


_ARTICLE_PREFIX = re.compile(r"^第[一二三四五六七八九十百千零〇]+条")


def compute_legal_basis_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """法规引用 TOP：提取书名号内的法规名，跳过以「第X条」开头的片段。"""
    counter: Counter[str] = Counter()

    for row in rows:
        for item in split_multi_value(row.legal_basis):
            item = item.strip()
            if not item:
                continue
            match = re.search(r"《([^》]+)》", item)
            if match:
                law_name = match.group(1)
            elif _ARTICLE_PREFIX.match(item):
                continue
            else:
                law_name = item
            counter[law_name] += 1

    return {"counter": counter, "ranked": counter.most_common(20)}


# ──────────────────────────── 机构 vs 个人 ────────────────────────────


def _violation_counter(rows: Iterable[CaseRow]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for vtype in normalize_violation_types(row.violation_type):
            counter[vtype] += 1
    return counter


def _punishment_counter(rows: Iterable[CaseRow], limit: int = 5) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        punishment = row.punishment.strip()
        if punishment:
            counter[punishment] += 1
    return Counter(dict(counter.most_common(limit)))


def compute_entity_comparison(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """机构与个人在违规类型、处罚措施上的对比。"""
    institutions = [row for row in rows if row.entity_type == "机构"]
    personnel = [row for row in rows if row.entity_type == "个人"]
    return {
        "inst_violations": _violation_counter(institutions).most_common(10),
        "pers_violations": _violation_counter(personnel).most_common(10),
        "inst_punishments": _punishment_counter(institutions),
        "pers_punishments": _punishment_counter(personnel),
    }


# ──────────────────────────── 时间趋势 ────────────────────────────


def compute_time_trend(rows: Sequence[CaseRow], granularity: str = "month") -> list[dict[str, Any]]:
    """按月（或按年）统计案例数量趋势。

    Args:
        granularity: ``month`` 或 ``year``。
    """
    prefix_len = 7 if granularity == "month" else 4
    buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        key = row.date[:prefix_len] if len(row.date) >= prefix_len else ""
        if not key:
            continue
        bucket = buckets.setdefault(key, {"count": 0, "institutions": 0, "personnel": 0})
        bucket["count"] += 1
        if row.entity_type == "机构":
            bucket["institutions"] += 1
        elif row.entity_type == "个人":
            bucket["personnel"] += 1

    return [{"period": key, **values} for key, values in sorted(buckets.items())]


# ──────────────────────────── 来源分布 ────────────────────────────


def compute_bureau_stats(rows: Sequence[CaseRow], limit: int = 15) -> list[tuple[str, int]]:
    """来源局分布（仅 CSRC 有意义，AMAC 行的 ``bureau`` 为空会被忽略）。"""
    counter: Counter[str] = Counter()
    for row in rows:
        if row.bureau:
            counter[row.bureau] += 1
    return counter.most_common(limit)


def compute_dataset_split(rows: Sequence[CaseRow]) -> list[dict[str, Any]]:
    """按数据集拆分统计，便于网页看板展示两套数据的构成。"""
    counter: Counter[str] = Counter()
    for row in rows:
        counter[row.dataset] += 1
    return [
        {"dataset": dataset, "label": DATASET_LABELS.get(dataset, dataset), "count": count}
        for dataset, count in counter.most_common()
    ]


# ──────────────────────────── 代表案例 ────────────────────────────


def pick_representative_cases(
    violation_case_map: dict[str, list[CaseRow]],
    max_per_type: int = 2,
) -> dict[str, list[CaseRow]]:
    """每个违规类型挑选 ``violation_summary`` 最长的案例作为代表。"""
    result: dict[str, list[CaseRow]] = {}
    for vtype, cases in violation_case_map.items():
        ordered = sorted(cases, key=lambda row: len(row.violation_summary), reverse=True)
        result[vtype] = ordered[:max_per_type]
    return result


# ──────────────────────────── 汇总 ────────────────────────────


@dataclass
class AnalysisResult:
    """一次完整分析的聚合结果。"""

    rows: list[CaseRow] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def basic(self) -> dict[str, Any]:
        return self.stats["basic"]

    @property
    def violation(self) -> dict[str, Any]:
        return self.stats["violation"]

    @property
    def punishment(self) -> dict[str, Any]:
        return self.stats["punishment"]

    @property
    def legal(self) -> dict[str, Any]:
        return self.stats["legal"]

    def to_json_payload(self) -> dict[str, Any]:
        """导出为可 JSON 序列化的统计数据（不含 CaseRow 对象）。"""
        basic = dict(self.basic)
        basic.pop("institutions", None)
        basic.pop("personnel", None)
        return {
            "basic": basic,
            "violation_distribution": [
                {"type": name, "count": count} for name, count in self.violation["ranked"]
            ],
            "punishment_categories": [
                {"category": name, "count": count}
                for name, count in self.punishment["category_ranked"]
            ],
            "punishment_details": [
                {"punishment": name, "count": count}
                for name, count in self.punishment["ranked"][:30]
            ],
            "legal_basis_top": [
                {"law": name, "count": count} for name, count in self.legal["ranked"]
            ],
            "entity_comparison": {
                "inst_violations": self.stats["comparison"]["inst_violations"],
                "pers_violations": self.stats["comparison"]["pers_violations"],
                "inst_punishments": self.stats["comparison"]["inst_punishments"],
                "pers_punishments": self.stats["comparison"]["pers_punishments"],
            },
            "time_trend": self.stats["time_trend"],
            "time_trend_yearly": self.stats["time_trend_yearly"],
            "bureau_top": self.stats["bureau_top"],
            "dataset_split": self.stats["dataset_split"],
        }


def analyze(rows: Sequence[CaseRow]) -> AnalysisResult:
    """对案例清单做全维度统计。"""
    rows = list(rows)
    basic = compute_basic_stats(rows)
    violation = compute_violation_stats(rows)
    punishment = compute_punishment_stats(rows)
    legal = compute_legal_basis_stats(rows)

    return AnalysisResult(
        rows=rows,
        stats={
            "basic": basic,
            "violation": violation,
            "punishment": punishment,
            "legal": legal,
            "comparison": compute_entity_comparison(rows),
            "time_trend": compute_time_trend(rows),
            "time_trend_yearly": compute_time_trend(rows, granularity="year"),
            "bureau_top": compute_bureau_stats(rows),
            "dataset_split": compute_dataset_split(rows),
            "representative": pick_representative_cases(violation["case_map"]),
        },
    )
