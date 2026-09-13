"""统计分析服务。

把两套异构数据（AMAC / CSRC）的案例行 :class:`~regwatch.domain.CaseRow`
归一后计算各维度统计，供报告渲染与网页看板复用。

统计口径与历史版本保持一致：``violation_type`` 拆分后对每个片段做
「包含即归一」匹配到分类体系，未匹配到的新类型原样保留，避免数据丢失。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain import (
    CaseQuery,
    CaseRow,
    Dataset,
    split_multi_value,
)
from ..domain.violations import (
    PUNISHMENT_CATEGORIES,
    VIOLATION_TYPES,
    categorize_punishment,
    normalize_violations,
)

__all__ = [
    "PUNISHMENT_CATEGORIES",
    "VIOLATION_TYPES",
    "AnalysisResult",
    "AnalysisService",
    "analyze",
    "compute_basic_stats",
    "compute_bureau_stats",
    "compute_dataset_split",
    "compute_entity_comparison",
    "compute_legal_basis_stats",
    "compute_punishment_stats",
    "compute_violation_stats",
    "pick_representative_cases",
]


def _percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


# ──────────────────────────── 各维度统计 ────────────────────────────


def compute_basic_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """总量、构成与日期范围。"""
    institutions = [row for row in rows if row.entity_type == "机构"]
    personnel = [row for row in rows if row.entity_type == "个人"]
    dates = sorted(row.date for row in rows if row.date)

    status_counter = Counter(row.status.value for row in rows)
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


def compute_violation_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """违规类型分布；一个案例可计入多个类型。"""
    counter: Counter[str] = Counter()
    case_map: dict[str, list[CaseRow]] = {}

    for row in rows:
        for vtype in normalize_violations(row.violation_type):
            counter[vtype] += 1
            case_map.setdefault(vtype, []).append(row)

    ranked = counter.most_common()
    return {
        "counter": counter,
        "ranked": ranked,
        "total_mentions": sum(counter.values()),
        "type_count": len(counter),
        "case_map": case_map,
    }


def compute_punishment_stats(rows: Sequence[CaseRow]) -> dict[str, Any]:
    """处罚措施分布：精确文本 + 粗类别两层。"""
    counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    case_map: dict[str, list[CaseRow]] = {}

    for row in rows:
        punishment = row.punishment.strip() or "未明确"
        counter[punishment] += 1
        case_map.setdefault(punishment, []).append(row)
        category_counter[categorize_punishment(punishment)] += 1

    return {
        "counter": counter,
        "ranked": counter.most_common(),
        "category_ranked": category_counter.most_common(),
        "case_map": case_map,
    }


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


def _violation_counter(rows: Iterable[CaseRow]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for vtype in normalize_violations(row.violation_type):
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


def compute_bureau_stats(rows: Sequence[CaseRow], limit: int = 15) -> list[tuple[str, int]]:
    """来源局分布（仅 CSRC 有意义）。"""
    counter: Counter[str] = Counter()
    for row in rows:
        if row.bureau:
            counter[row.bureau] += 1
    return counter.most_common(limit)


def compute_dataset_split(rows: Sequence[CaseRow]) -> list[dict[str, Any]]:
    """按数据集拆分统计。"""
    counter: Counter[str] = Counter()
    for row in rows:
        counter[row.dataset.value] += 1
    return [
        {"dataset": key, "label": (Dataset.parse(key) or Dataset.AMAC).label, "count": count}
        for key, count in counter.most_common()
    ]


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
                "inst_punishments": dict(self.stats["comparison"]["inst_punishments"]),
                "pers_punishments": dict(self.stats["comparison"]["pers_punishments"]),
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

    return AnalysisResult(
        rows=rows,
        stats={
            "basic": basic,
            "violation": violation,
            "punishment": compute_punishment_stats(rows),
            "legal": compute_legal_basis_stats(rows),
            "comparison": compute_entity_comparison(rows),
            # 趋势统一由 SQL 聚合产出（见 AnalysisService.time_trend）：
            # 纯行分析没有查询条件，这里留空，由 analyze_query 回填。
            "time_trend": [],
            "time_trend_yearly": [],
            "bureau_top": compute_bureau_stats(rows),
            "dataset_split": compute_dataset_split(rows),
            "representative": pick_representative_cases(violation["case_map"]),
        },
    )


class AnalysisService:
    """分析用例：从仓储取数并聚合。"""

    def __init__(self, cases: Any, summaries: Any) -> None:
        self._cases = cases
        self._summaries = summaries

    def rows(self, query: CaseQuery) -> list[CaseRow]:
        """按条件取出案例行（不含正文）。"""
        return self._cases.search(query)

    def analyze(self, rows: Sequence[CaseRow]) -> AnalysisResult:
        return analyze(rows)

    def time_trend(self, query: CaseQuery, granularity: str = "month") -> list[dict[str, Any]]:
        """按月 / 年统计趋势；总览看板与统计页共用这一份 SQL 口径。"""
        return self._cases.time_trend(query, granularity)

    def analyze_query(self, query: CaseQuery) -> AnalysisResult:
        """取数并做全维度统计，趋势用 SQL 口径回填，与总览看板一致。"""
        result = analyze(self._cases.search(query))
        result.stats["time_trend"] = self.time_trend(query)
        result.stats["time_trend_yearly"] = self.time_trend(query, granularity="year")
        return result

    def date_range(self, query: CaseQuery) -> tuple[str, str]:
        """筛选结果的日期上下界。"""
        return self._cases.date_range(query)

    def overview(self, query: CaseQuery) -> dict[str, Any]:
        """纯 SQL 聚合的看板指标（不载入全部行）。"""
        date_min, date_max = self._cases.date_range(query)
        # bureau 只有 CSRC 有值，空桶（AMAC 案例）会伪装成「未知」霸榜，直接剔除
        bureau_items = [
            (name, count) for name, count in self._cases.aggregate(query, "bureau") if name
        ]
        return {
            "total": self._cases.count(query),
            "status": dict(self._cases.aggregate(query, "status")),
            "dataset": dict(self._cases.aggregate(query, "dataset")),
            "bureau": bureau_items[:15],
            "case_type": dict(self._cases.aggregate(query, "case_type")),
            "entity_type": dict(self._cases.aggregate(query, "entity_type")),
            "violations": self._summaries.violation_distribution(query),
            "time_trend": self.time_trend(query),
            "time_trend_yearly": self.time_trend(query, granularity="year"),
            "date_min": date_min,
            "date_max": date_max,
        }
