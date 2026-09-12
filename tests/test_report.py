"""报告生成与渲染测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from regwatch.db import DataStore
from regwatch.domain import CaseRecord, CaseStatus, Dataset, SummaryRecord
from regwatch.services import Services
from regwatch.services.reporting import report_title


def _seed(store: DataStore, amac_case: CaseRecord) -> None:
    store.cases.upsert(amac_case)
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.AMAC,
            case_id=amac_case.case_id,
            entity_type="机构",
            violation_type="违规募集、内控缺失",
            punishment="公开谴责",
            legal_basis="《私募投资基金监督管理暂行办法》第三十八条",
            violation_summary="该公司向不合格投资者募集资金。",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )


def test_markdown_contains_all_sections(
    services: Services, store: DataStore, amac_case: CaseRecord
) -> None:
    _seed(store, amac_case)
    output = services.reporting.build_report(Dataset.AMAC)

    assert output.title == report_title(Dataset.AMAC)
    assert output.row_count == 1
    for section in (
        "## 一、概况",
        "## 二、违规类型统计",
        "## 三、处罚措施分析",
        "## 四、机构 vs 个人对比",
        "## 五、法规依据分析",
        "## 六、典型案例",
        "## 七、合规建议",
        "## 附件：案例列表",
    ):
        assert section in output.markdown


def test_html_is_rendered(services: Services, store: DataStore, amac_case: CaseRecord) -> None:
    _seed(store, amac_case)
    output = services.reporting.build_report(Dataset.AMAC)
    assert output.html.startswith("<!doctype html>")
    assert "<table>" in output.html


def test_period_label_defaults_to_data_range(
    services: Services, store: DataStore, amac_case: CaseRecord
) -> None:
    _seed(store, amac_case)
    assert "2026-01-05 ~ 2026-01-05" in services.reporting.build_report(Dataset.AMAC).period_label


def test_explicit_period_label(services: Services, store: DataStore, amac_case: CaseRecord) -> None:
    _seed(store, amac_case)
    output = services.reporting.build_report(
        Dataset.AMAC, start_date="2026-01-01", end_date="2026-03-31"
    )
    assert output.period_label == "2026-01-01 ~ 2026-03-31"


def test_unknown_dataset_raises(services: Services) -> None:
    with pytest.raises(ValueError):
        services.reporting.build_report("nope")


def test_save_writes_three_formats(
    services: Services, store: DataStore, amac_case: CaseRecord, tmp_path: Path
) -> None:
    _seed(store, amac_case)
    output = services.reporting.build_report(Dataset.AMAC)
    paths = services.reporting.save(output, tmp_path / "reports")

    assert set(paths) == {"md", "html", "json"}
    assert all(Path(path).stat().st_size > 0 for path in paths.values())
    assert output.paths == paths


def test_save_can_be_limited_to_one_format(
    services: Services, store: DataStore, amac_case: CaseRecord, tmp_path: Path
) -> None:
    _seed(store, amac_case)
    output = services.reporting.build_report(Dataset.AMAC)
    assert set(services.reporting.save(output, tmp_path, formats=("md",))) == {"md"}


def test_model_advice_is_used_when_long_enough(
    services: Services, store: DataStore, amac_case: CaseRecord
) -> None:
    _seed(store, amac_case)
    services.llm.payload = "模" * 200  # type: ignore[union-attr]
    output = services.reporting.build_report(Dataset.AMAC, use_llm=True)
    assert "模" * 200 in output.markdown


def test_short_model_advice_falls_back_to_builtin(
    services: Services, store: DataStore, amac_case: CaseRecord
) -> None:
    _seed(store, amac_case)
    services.llm.payload = "太短"  # type: ignore[union-attr]
    output = services.reporting.build_report(Dataset.AMAC, use_llm=True)
    assert "通用合规建议" in output.markdown
