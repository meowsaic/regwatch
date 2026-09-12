"""pytest 公共夹具。

所有测试都跑在**内存数据库**上，模型调用与网络请求一律由假实现替换，
因此整套测试不触网、不写磁盘（除 ``tmp_path``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from regwatch.db import DataStore
from regwatch.domain import CaseRecord, Category, Dataset, ModelProfile, TaskRecord
from regwatch.llm import ChatResult
from regwatch.services import (
    AnalysisService,
    JobManager,
    JobRunner,
    OrgTypeService,
    QaService,
    ReportService,
    Services,
    SummarizationService,
)
from regwatch.settings import Settings


@pytest.fixture(autouse=True)
def isolate_project_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把所有用例的「项目根」指向临时目录。

    这样读取 / 写入 ``config.json`` 的路径不会落到仓库里，
    测试不会改动开发者的本地配置。
    """
    root = tmp_path_factory.mktemp("project-root")
    monkeypatch.setattr("regwatch.settings.PROJECT_ROOT", root, raising=False)


# ──────────────────────────── 假模型客户端 ────────────────────────────


@dataclass
class FakeClient:
    """把预设文本当作模型输出返回。"""

    payload: str
    profile: ModelProfile = field(
        default_factory=lambda: ModelProfile(id="fake", model="fake-model", base_url="http://x")
    )

    def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
        return ChatResult(content=self.payload, model=self.profile.model)

    def chat_text(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        return self.payload

    def vision(self, file_url: str, prompt: str, **kwargs: Any) -> str:
        return self.payload


@dataclass
class FakeLLM:
    """替代 :class:`~regwatch.llm.LLMClientFactory`。"""

    payload: Any = "{}"
    calls: list[str] = field(default_factory=list)

    def _text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)

    def client(self, task: str | None = None, model_id: str | None = None) -> FakeClient:
        self.calls.append(task or model_id or "")
        return FakeClient(payload=self._text())

    def refresh(self, models: Any = None, tasks: Any = None) -> None:
        return None

    def test_profile(self, profile: ModelProfile, timeout: float = 30.0) -> tuple[bool, str]:
        return True, "连接成功（假实现）"

    def resolve(self, model_id: str | None = None, task: str | None = None) -> ModelProfile:
        return ModelProfile(id="fake", model="fake-model")


# ──────────────────────────── 假 HTTP 客户端 ────────────────────────────


class FakeHttp:
    """返回预设响应的 HTTP 客户端。"""

    def __init__(self, text: str = "", payload: Any = None) -> None:
        self.text = text
        self.payload = payload if payload is not None else {}
        self.requests: list[tuple[str, str]] = []

    def get_text(self, url: str, **kwargs: Any) -> str:
        self.requests.append(("GET", url))
        return self.text

    def post_json(self, url: str, **kwargs: Any) -> Any:
        self.requests.append(("POST", url))
        return self.payload


# ──────────────────────────── 夹具 ────────────────────────────


@pytest.fixture
def store() -> DataStore:
    """内存数据库（每个用例独立）。"""
    return DataStore.in_memory()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database=tmp_path / "test.db", reports_dir=tmp_path / "reports")


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def services(store: DataStore, settings: Settings, fake_llm: FakeLLM, http: FakeHttp) -> Services:
    """装配一套完整服务（模型与网络均为假实现）。"""
    analysis = AnalysisService(store.cases, store.summaries)
    summarizing = SummarizationService(
        store.cases,
        store.summaries,
        fake_llm,
        default_concurrency=1,
        on_data_changed=store.touch,
    )
    bundle = Services(
        settings=settings,
        store=store,
        http=http,
        llm=fake_llm,
        analysis=analysis,
        reporting=ReportService(analysis, fake_llm),
        summarization=summarizing,
        org_type=OrgTypeService(store.cases, store.meta, fake_llm, http=http, request_delay=0),
        qa=QaService(fake_llm, analysis),
        jobs=None,  # type: ignore[arg-type] - 下方回填
    )
    bundle.jobs = JobManager(store.tasks, JobRunner(bundle))
    return bundle


@pytest.fixture
def amac_case() -> CaseRecord:
    """一条 AMAC 机构类案例。"""
    return CaseRecord(
        dataset=Dataset.AMAC,
        case_id="P20260101000000000001",
        title="关于对某某基金管理有限公司的纪律处分",
        date="2026-01-05",
        category=Category.INSTITUTION,
        punished_entity="某某基金管理有限公司",
        raw_text=(
            "经查，某某基金管理有限公司在开展私募基金业务过程中，"
            "存在向不合格投资者募集、内控制度不健全等违规事实，"
            "违反了《私募投资基金监督管理暂行办法》的相关规定，"
            "现决定对其作出公开谴责的纪律处分。"
        ),
    )


@pytest.fixture
def csrc_case() -> CaseRecord:
    """一条 CSRC 行政处罚案例。"""
    return CaseRecord(
        dataset=Dataset.CSRC,
        case_id="20260105_c7615688",
        title="关于对某某证券股份有限公司的行政处罚决定",
        date="2026-01-05",
        case_type="penalty",
        bureau="Beijing",
        punished_entities="某某证券股份有限公司",
        is_fund_related=True,
        raw_text=(
            "当事人某某证券股份有限公司管理的基金产品存在信息披露违规，"
            "未按规定披露定期报告，违反了《证券投资基金法》相关规定，"
            "现决定责令改正并处以罚款。"
        ),
    )


@pytest.fixture
def task_record() -> TaskRecord:
    return TaskRecord(id="abc123", kind="summarize", title="测试任务")
