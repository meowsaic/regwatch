"""机构登记类型解析与回填测试（网络与模型均为假实现）。"""

from __future__ import annotations

from typing import Any

from regwatch.db import DataStore
from regwatch.domain import CaseRecord, Category, Dataset
from regwatch.services import Services
from regwatch.services.org_type import (
    ORG_TYPE_VALUES,
    extract_full_org_name_from_text,
    extract_org_name_from_title,
    extract_org_type_from_text,
    query_org_type_from_amac,
    resolve_org_type,
)


class FakeHttp:
    def __init__(self, payload: Any = None) -> None:
        self.payload = payload
        self.requests: list[tuple[str, str]] = []

    def get_text(self, url: str, **kwargs: Any) -> str:
        self.requests.append(("GET", url))
        return ""

    def post_json(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(("POST", url))
        return self.payload


def test_title_with_penalty_suffix() -> None:
    assert (
        extract_org_name_from_title("关于对某某基金管理有限公司的纪律处分决定书")
        == "某某基金管理有限公司"
    )


def test_csrc_title_pattern() -> None:
    name = extract_org_name_from_title("关于对某某证券股份有限公司采取警示函措施的决定")
    assert name is not None and name.startswith("某某证券")


def test_full_name_from_text() -> None:
    text = "当事人：某某资产管理有限公司（以下简称某某资产）存在违规行为。"
    assert extract_full_org_name_from_text("某某资产", text) == "某某资产管理有限公司"


def test_short_text_returns_none() -> None:
    assert extract_full_org_name_from_text("简称", "太短") is None


def test_text_regex_match() -> None:
    text = "经查明，该机构登记类型为私募证券投资基金管理人，存在违规募集行为。" * 4
    assert extract_org_type_from_text("某某基金", text) == "私募证券投资基金管理人"


def test_generic_name_is_skipped() -> None:
    assert query_org_type_from_amac("有限公司", FakeHttp()) is None


def test_cache_short_circuits_http() -> None:
    http = FakeHttp()
    got = resolve_org_type(
        "关于对某某基金管理有限公司的纪律处分",
        "正文",
        punished_entity="某某基金管理有限公司",
        cache_get=lambda name: "私募股权投资基金管理人",
        cache_set=lambda name, value: None,
        http=http,
    )
    assert got == "私募股权投资基金管理人"
    assert http.requests == []


def _case(store: DataStore, case_id: str = "P1", raw_text: str = "") -> CaseRecord:
    case = CaseRecord(
        dataset=Dataset.AMAC,
        case_id=case_id,
        title="关于对某某基金管理有限公司的纪律处分",
        category=Category.INSTITUTION,
        raw_text=raw_text
        or "经查明，该机构登记类型为私募证券投资基金管理人，存在违规募集与内控缺失行为。" * 3,
    )
    store.cases.upsert(case)
    return case


def test_backfill_fills_org_type(services: Services, store: DataStore) -> None:
    _case(store)
    result = services.org_type.backfill()
    assert result.total == 1
    assert result.org_type_filled == 1
    assert store.cases.get(Dataset.AMAC, "P1").org_type in ORG_TYPE_VALUES


def test_backfill_counts_existing(services: Services, store: DataStore) -> None:
    _case(store)
    services.org_type.backfill()
    assert services.org_type.backfill().already_have == 1


def test_backfill_dry_run_does_not_persist(services: Services, store: DataStore) -> None:
    _case(store)
    result = services.org_type.backfill(dry_run=True)
    assert result.dry_run and result.org_type_filled == 1
    assert store.cases.get(Dataset.AMAC, "P1").org_type == ""


def test_backfill_unresolved_goes_to_manual_list(services: Services, store: DataStore) -> None:
    _case(store, case_id="P2", raw_text="未能识别机构类型的正文内容。")
    result = services.org_type.backfill()
    assert result.unresolved == 1
    assert result.manual_list[0]["case_id"] == "P2"


def test_backfill_progress_hook(services: Services, store: DataStore) -> None:
    _case(store)
    seen: list[tuple[int, int, str]] = []
    services.org_type.backfill(
        on_progress=lambda done, total, label: seen.append((done, total, label))
    )
    assert seen == [(1, 1, "P1")]


def test_interactive_fill_by_index(services: Services, store: DataStore) -> None:
    store.cases.upsert(
        CaseRecord(
            dataset=Dataset.AMAC,
            case_id="P3",
            title="标题",
            category=Category.INSTITUTION,
            punished_entity="某某基金管理有限公司",
            raw_text="正文",
        )
    )
    filled, skipped = services.org_type.interactive_fill(
        input_func=lambda prompt: "1", output_func=lambda text: None
    )
    assert (filled, skipped) == (1, 0)
    assert store.cases.get(Dataset.AMAC, "P3").org_type == ORG_TYPE_VALUES[0]


def test_interactive_fill_empty_input_skips(services: Services, store: DataStore) -> None:
    store.cases.upsert(
        CaseRecord(
            dataset=Dataset.AMAC,
            case_id="P4",
            title="标题",
            category=Category.INSTITUTION,
            raw_text="正文",
        )
    )
    assert services.org_type.interactive_fill(
        input_func=lambda prompt: "", output_func=lambda text: None
    ) == (0, 1)
