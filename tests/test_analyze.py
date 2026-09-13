"""统计分析测试。"""

from __future__ import annotations

import json

from regwatch.db import DataStore
from regwatch.domain import CaseQuery, CaseRecord, CaseStatus, Dataset, SummaryRecord
from regwatch.services import Services


def _seed(store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord) -> None:
    store.cases.upsert(amac_case)
    store.cases.upsert(csrc_case)
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.AMAC,
            case_id=amac_case.case_id,
            entity_type="机构",
            violation_type="违规募集、内控缺失",
            punishment="公开谴责",
            legal_basis="《私募投资基金监督管理暂行办法》第三十八条",
            violation_summary="摘要" * 10,
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.CSRC,
            case_id=csrc_case.case_id,
            entity_type="个人",
            violation_type="信息披露违规",
            punishment="罚款",
            legal_basis="《证券投资基金法》第一百三十三条",
            violation_summary="摘要",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )


def test_basic_stats(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    result = services.analysis.analyze_query(CaseQuery())
    assert result.basic["total"] == 2
    assert result.basic["institution_count"] == 1
    assert result.basic["personnel_count"] == 1
    assert result.basic["date_min"] == "2026-01-05"
    assert result.basic["status_counts"] == {"done": 2}


def test_violation_stats_count_each_mention(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    result = services.analysis.analyze_query(CaseQuery())
    counts = dict(result.violation["ranked"])
    assert counts["违规募集"] == 1
    assert counts["内控缺失"] == 1
    assert counts["信息披露违规"] == 1
    assert result.violation["total_mentions"] == 3
    assert result.violation["type_count"] == 3


def test_punishment_categories(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    categories = dict(services.analysis.analyze_query(CaseQuery()).punishment["category_ranked"])
    assert categories == {"公开谴责": 1, "罚款": 1}


def test_legal_basis_extracts_book_titles(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    laws = dict(services.analysis.analyze_query(CaseQuery()).legal["ranked"])
    assert laws["私募投资基金监督管理暂行办法"] == 1
    assert laws["证券投资基金法"] == 1


def test_entity_comparison(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    comparison = services.analysis.analyze_query(CaseQuery()).stats["comparison"]
    assert dict(comparison["inst_violations"])
    assert dict(comparison["pers_violations"]) == {"信息披露违规": 1}


def test_time_trend_month_and_year(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    stats = services.analysis.analyze_query(CaseQuery()).stats
    assert stats["time_trend"] == [
        {"period": "2026-01", "count": 2, "institutions": 1, "personnel": 1}
    ]
    assert stats["time_trend_yearly"] == [
        {"period": "2026", "count": 2, "institutions": 1, "personnel": 1}
    ]


def test_bureau_and_dataset_split(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    stats = services.analysis.analyze_query(CaseQuery()).stats
    assert stats["bureau_top"] == [("Beijing", 1)]
    assert {item["dataset"] for item in stats["dataset_split"]} == {"amac", "csrc"}


def test_json_payload_is_serializable(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    payload = services.analysis.analyze_query(CaseQuery()).to_json_payload()
    assert json.loads(json.dumps(payload, ensure_ascii=False))["basic"]["total"] == 2


def test_representative_cases_pick_longest_summary(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    typical = services.analysis.analyze_query(CaseQuery()).stats["representative"]
    assert typical["违规募集"][0].case_id == amac_case.case_id


def test_overview_uses_sql_aggregation(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    overview = services.analysis.overview(CaseQuery())
    assert overview["total"] == 2
    assert overview["status"] == {"done": 2}
    assert overview["dataset"] == {"amac": 1, "csrc": 1}
    assert overview["date_min"] == "2026-01-05"
    assert ("违规募集", 1) in overview["violations"]
    # 年度趋势与月度趋势同源（SQL 聚合）
    assert overview["time_trend_yearly"] == [
        {"period": "2026", "count": 2, "institutions": 1, "personnel": 1}
    ]


def test_overview_bureau_drops_empty_bucket(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    """bureau 只有 CSRC 有值；AMAC 的空 bureau 不得伪装成「未知」霸榜。"""
    _seed(store, amac_case, csrc_case)
    overview = services.analysis.overview(CaseQuery())
    assert overview["bureau"] == [("Beijing", 1)]


def test_rows_are_sorted_by_date_desc(
    services: Services, store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord
) -> None:
    _seed(store, amac_case, csrc_case)
    store.cases.upsert(csrc_case.replace(case_id="20250101_c1", date="2025-01-01"))
    rows = services.analysis.rows(CaseQuery())
    assert [row.date for row in rows] == ["2026-01-05", "2026-01-05", "2025-01-01"]
