"""网页端测试：组件纯函数 + AppTest 入口冒烟。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from regwatch.domain import CaseQuery, CaseStatus, CaseType, Category, Dataset
from regwatch.web import app_path
from regwatch.web.components.data import cache_key, clamp_page
from regwatch.web.state import DEFAULTS, FILTER_KEYS

#: 合法 SQLite 文件头（用于构造「看着像库」的假文件）
SQLITE_HEADER = b"SQLite format 3\x00" + b"\x00" * 512

#: 单独渲染某个视图的脚本（绕过 st.navigation，便于逐页断言）
_VIEW_SCRIPT = """
from regwatch.web.components import ui
ui.apply_theme()
from regwatch.web.views import {view}
{view}.render()
"""


def test_app_path_exists() -> None:
    assert app_path().is_file()


def test_app_runs_without_exception() -> None:
    """回归：五个页面的 url_path 必须唯一，默认页渲染不能抛异常。

    曾经所有视图入口都叫 ``render``，Streamlit 按函数名推断出重复
    pathname 而直接拒绝启动；随后又暴露出 ``CaseRow.to_dict`` 缺展示字段。
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(app_path()), default_timeout=120)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]


def test_cloud_entry_runs_without_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """部署入口（``deploy/streamlit_app.py``）在没有 config.json 与数据库时也能启动。

    这正是 Streamlit Community Cloud 上的初始状态：仓库里只有代码。
    同时确认入口默认打开只读模式（写操作页面对外收起）。
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    # 本地若存在 .streamlit/secrets.toml，不应影响这条离线用例
    import regwatch.web.cloud as cloud

    monkeypatch.setattr(cloud, "_load_secrets", dict)
    monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)

    entry = Path(__file__).resolve().parents[1] / "deploy" / "streamlit_app.py"
    assert entry.is_file(), f"缺少部署入口：{entry}"
    at = AppTest.from_file(str(entry), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert os.environ.get("REGWATCH_READ_ONLY") == "1", "云端入口应默认只读"
    monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)  # 别影响后续用例


def test_public_entry_runs_without_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """公开入口（``deploy/streamlit_public.py``）的三个页面能正常启动。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    import regwatch.web.cloud as cloud

    monkeypatch.setattr(cloud, "_load_secrets", dict)

    entry = Path(__file__).resolve().parents[1] / "deploy" / "streamlit_public.py"
    assert entry.is_file(), f"缺少公开入口：{entry}"
    at = AppTest.from_file(str(entry), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]


class TestCloudBootstrap:
    """云端引导：Secrets 桥接与数据库按需下载。"""

    def test_api_key_secret_becomes_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web.cloud import apply_env_secrets

        monkeypatch.setenv("REGWATCH_API_KEY_DEEPSEEK", "")
        applied = apply_env_secrets({"api_key_deepseek": "sk-1"})
        assert applied == ("REGWATCH_API_KEY_DEEPSEEK",)
        assert os.environ["REGWATCH_API_KEY_DEEPSEEK"] == "sk-1"

    def test_prefixed_secret_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web.cloud import apply_env_secrets

        monkeypatch.setenv("REGWATCH_DATABASE", "")
        assert apply_env_secrets({"regwatch_database": "data/x.db"}) == ("REGWATCH_DATABASE",)
        assert os.environ["REGWATCH_DATABASE"] == "data/x.db"

    def test_real_env_wins_over_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web.cloud import apply_env_secrets

        monkeypatch.setenv("REGWATCH_API_KEY_DEEPSEEK", "real")
        assert apply_env_secrets({"api_key_deepseek": "from-secret"}) == ()
        assert os.environ["REGWATCH_API_KEY_DEEPSEEK"] == "real"

    def test_nested_and_unknown_keys_are_ignored(self) -> None:
        from regwatch.web.cloud import apply_env_secrets

        secrets = {"api_keys": {"deepseek": "sk"}, "other": "x", "flag": True, "empty": "  "}
        assert apply_env_secrets(secrets) == ()

    def test_database_downloaded_when_missing(self, tmp_path: Path) -> None:
        from regwatch.web.cloud import ensure_database

        target = tmp_path / "regwatch.db"
        calls: list[tuple[str, str | None]] = []

        def fake_download(url: str, path: Path, *, token: str | None = None) -> None:
            calls.append((url, token))
            Path(path).write_bytes(b"SQLite format 3")

        result = ensure_database(
            {"database_url": "https://example.com/regwatch.db", "database_token": "t"},
            target=target,
            download=fake_download,
        )
        assert result == target
        assert calls == [("https://example.com/regwatch.db", "t")]

    def test_database_download_skipped_when_present(self, tmp_path: Path) -> None:
        from regwatch.web.cloud import ensure_database

        target = tmp_path / "regwatch.db"
        target.write_bytes(SQLITE_HEADER)

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("库已存在时不应再下载")

        assert (
            ensure_database({"database_url": "https://x/y.db"}, target=target, download=explode)
            is None
        )

    def test_no_url_is_a_noop(self, tmp_path: Path) -> None:
        from regwatch.web.cloud import ensure_database

        assert ensure_database({}, target=tmp_path / "r.db") is None

    def test_only_real_sqlite_files_count_as_ready(self, tmp_path: Path) -> None:
        """LFS 指针 / 半截下载 / HTML 报错页都不能当成可用数据库。"""
        from regwatch.web.cloud import _database_ready

        pointer = tmp_path / "pointer.db"
        pointer.write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:916051d59c1d0896b64108fb9e5a610ec4fd55f24054ef25a635f9d0472ed160\n"
            "size 35319808\n",
            encoding="utf-8",
        )
        assert _database_ready(pointer) is False

        html = tmp_path / "error.db"
        html.write_bytes(b"<!DOCTYPE html>" + b"x" * 4096)
        assert _database_ready(html) is False

        assert _database_ready(tmp_path / "missing.db") is False
        assert _database_ready(tmp_path / "empty.db") is False
        (tmp_path / "empty.db").write_bytes(b"")

        real = tmp_path / "real.db"
        real.write_bytes(SQLITE_HEADER)
        assert _database_ready(real) is True

    def test_lfs_pointer_is_overwritten_by_download(self, tmp_path: Path) -> None:
        from regwatch.web.cloud import ensure_database

        target = tmp_path / "regwatch.db"
        target.write_text("version https://git-lfs.github.com/spec/v1\n", encoding="utf-8")

        def fake_download(url: str, path: Path, *, token: str | None = None) -> None:
            Path(path).write_bytes(SQLITE_HEADER)

        assert (
            ensure_database(
                {"database_url": "https://x/y.db"}, target=target, download=fake_download
            )
            == target
        )

    def test_prepare_runtime_without_secrets(self) -> None:
        from regwatch.web.cloud import prepare_runtime

        assert prepare_runtime({}) == {"env": (), "database": None, "error": "", "warning": ""}

    def test_prepare_runtime_warns_on_broken_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """库文件存在但不是 SQLite 时给出明确提示，而不是让 SQLite 抛晦涩错误。"""
        from regwatch.web import cloud

        broken = tmp_path / "regwatch.db"
        broken.write_text("not a database", encoding="utf-8")
        monkeypatch.setattr(cloud, "resolve_database_path", lambda: broken)

        report = cloud.prepare_runtime({"other": "x"})
        assert "不是有效的 SQLite 库" in report["warning"]
        assert report["database"] is None

    def test_prepare_runtime_skips_ready_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from regwatch.web import cloud

        ready = tmp_path / "regwatch.db"
        ready.write_bytes(SQLITE_HEADER)
        monkeypatch.setattr(cloud, "resolve_database_path", lambda: ready)

        report = cloud.prepare_runtime({"database_url": "https://x/y.db"})
        assert report == {"env": (), "database": None, "error": "", "warning": ""}


class TestReadOnlyMode:
    """公开部署时的只读限制：写操作入口必须收起。"""

    def _render(self, view: str):
        pytest.importorskip("streamlit.testing.v1")
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_string(_VIEW_SCRIPT.format(view=view), default_timeout=180)
        at.run()
        assert not at.exception, [str(item.value) for item in at.exception]
        return at

    def test_flag_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web import access

        for value in ("1", "true", "Yes", "ON"):
            monkeypatch.setenv(access.READ_ONLY_ENV, value)
            assert access.read_only() is True
        for value in ("0", "false", "", "no"):
            monkeypatch.setenv(access.READ_ONLY_ENV, value)
            assert access.read_only() is False

    def test_jobs_page_locked_in_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.delenv("REGWATCH_ADMIN_TOKEN", raising=False)

        at = self._render("jobs")
        # 「任务类型」下拉只属于提交表单，只读时不应出现
        assert at.selectbox == []
        assert [str(item.value) for item in at.info], "缺少只读说明"

    def test_jobs_page_editable_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)

        at = self._render("jobs")
        assert len(at.selectbox) == 1

    def test_settings_page_locked_in_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.delenv("REGWATCH_ADMIN_TOKEN", raising=False)

        at = self._render("settings")
        assert at.text_input == []  # 新增 / 更新模型的输入框全部收起
        assert at.selectbox == []

    def test_admin_token_unlocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.setenv("REGWATCH_ADMIN_TOKEN", "s3cret")

        at = self._render("jobs")
        assert at.selectbox == []

        at.get_by_key("regwatch.admin-token").set_value("s3cret")
        at.get_by_key("regwatch.admin-unlock").click()
        at.run()

        assert not at.exception, [str(item.value) for item in at.exception]
        assert len(at.selectbox) == 1, "解锁后应恢复提交表单"


class TestNavVisibility:
    """云端只读时，两个写操作页面必须从导航里消失（点不到）。"""

    def test_all_pages_available_when_not_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web import nav

        monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)
        titles = [item.title for item in nav.visible_items()]
        assert titles == ["总览看板", "案例浏览", "统计分析", "智能问答", "任务中心", "模型与配置"]

    def test_public_items_are_read_only_pages(self) -> None:
        """公开入口的页面清单与权限开关无关；智能问答公开可见但强制 BYOK。"""
        from regwatch.web import nav

        titles = [item.title for item in nav.public_items()]
        assert titles == ["总览看板", "案例浏览", "统计分析", "智能问答"]
        assert all(not item.admin_only for item in nav.public_items())

    def test_admin_pages_hidden_in_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from regwatch.web import nav

        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.delenv("REGWATCH_ADMIN_TOKEN", raising=False)

        items = nav.visible_items()
        assert [item.title for item in items] == [
            "总览看板",
            "案例浏览",
            "统计分析",
            "智能问答",
        ]
        assert all(item.url_path not in {"jobs", "settings"} for item in items)
        assert items[0].url_path == nav.DEFAULT_URL_PATH  # 默认页始终在

    def test_sidebar_shows_lock_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("streamlit.testing.v1")
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.delenv("REGWATCH_ADMIN_TOKEN", raising=False)

        at = AppTest.from_file(str(app_path()), default_timeout=180)
        at.run()
        assert not at.exception, [str(item.value) for item in at.exception]
        assert any("只读" in str(item.value) for item in at.sidebar.caption)

    def test_sidebar_unlock_sets_session_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("streamlit.testing.v1")
        from streamlit.testing.v1 import AppTest

        monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
        monkeypatch.setenv("REGWATCH_ADMIN_TOKEN", "s3cret")

        at = AppTest.from_file(str(app_path()), default_timeout=180)
        at.run()
        assert not at.exception, [str(item.value) for item in at.exception]

        at.get_by_key("regwatch.admin-token@sidebar").set_value("s3cret")
        at.get_by_key("regwatch.admin-unlock@sidebar").click()
        at.run()

        assert not at.exception, [str(item.value) for item in at.exception]
        assert at.session_state["regwatch.admin_unlocked"] is True


class TestClampPage:
    def test_within_range(self) -> None:
        assert clamp_page(3, 10) == 3

    def test_above_range(self) -> None:
        assert clamp_page(99, 10) == 10

    def test_invalid_values_fall_back_to_one(self) -> None:
        assert clamp_page(None, 5) == 1
        assert clamp_page("abc", 5) == 1
        assert clamp_page(0, 5) == 1

    def test_page_count_is_at_least_one(self) -> None:
        assert clamp_page(1, 0) == 1


class TestCacheKey:
    def test_roundtrip_preserves_query(self) -> None:
        from regwatch.web.components.data import _query_from_key

        query = CaseQuery(
            datasets=(Dataset.AMAC,),
            statuses=(CaseStatus.DONE,),
            case_types=(CaseType.PENALTY,),
            categories=(Category.INSTITUTION,),
            violations=("违规募集",),
            bureaus=("Beijing",),
            entity_types=("机构",),
            date_from="2026-01-01",
            date_to="2026-03-31",
            keyword="基金",
            fund_related_only=True,
            limit=20,
            offset=40,
        )
        restored = _query_from_key(cache_key(query))
        assert restored.datasets == query.datasets
        assert restored.statuses == query.statuses
        assert restored.case_types == query.case_types
        assert restored.violations == query.violations
        assert restored.date_from == query.date_from
        assert restored.keyword == query.keyword
        assert restored.limit == 20 and restored.offset == 40

    def test_empty_query_roundtrip(self) -> None:
        from regwatch.web.components.data import _query_from_key

        restored = _query_from_key(cache_key(CaseQuery()))
        assert restored == CaseQuery()

    def test_different_queries_have_different_keys(self) -> None:
        assert cache_key(CaseQuery()) != cache_key(CaseQuery(keyword="x"))


def test_filter_state_defaults_are_defined() -> None:
    for name in FILTER_KEYS:
        assert name in DEFAULTS


def test_session_helpers_are_importable() -> None:
    """会话状态相关函数依赖 Streamlit 运行时，这里只校验接口存在。"""
    from regwatch.web import state

    assert callable(state.build_query)
    assert callable(state.reset_filters)
    assert state.get.__name__ == "get"


class TestParseDateArg:
    def test_empty_and_none(self) -> None:
        from regwatch.web.components.ui import parse_date_arg

        assert parse_date_arg(None) is None
        assert parse_date_arg("") is None
        assert parse_date_arg("   ") is None

    def test_iso_string_and_date(self) -> None:
        from datetime import date

        from regwatch.web.components.ui import parse_date_arg

        assert parse_date_arg("2026-01-15") == date(2026, 1, 15)
        assert parse_date_arg(date(2026, 2, 1)) == date(2026, 2, 1)

    def test_datetime_and_sequence(self) -> None:
        from datetime import date, datetime

        from regwatch.web.components.ui import parse_date_arg

        assert parse_date_arg(datetime(2026, 3, 4, 12, 0)) == date(2026, 3, 4)
        assert parse_date_arg([date(2026, 5, 6)]) == date(2026, 5, 6)
        assert parse_date_arg((None, None)) is None

    def test_invalid_text(self) -> None:
        from regwatch.web.components.ui import parse_date_arg

        assert parse_date_arg("not-a-date") is None


def test_cases_page_renders_date_and_multiselect_filters() -> None:
    """案例浏览：起止日期为 date_input，多选可渲染且不抛异常。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="cases"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    # 起始/结束日期
    assert len(at.date_input) >= 2
    assert len(at.multiselect) >= 6
    # 结果摘要条（空库也应显示命中 0）
    assert any("命中" in (item.value or "") for item in at.markdown)


def test_cases_page_wires_export_when_rows_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：案例页在有结果时必须挂上 CSV 导出与摘要条（T3/T8）。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    sample = {
        "date": "2024-01-02",
        "dataset": "amac",
        "dataset_label": "中基协",
        "title": "示例处分",
        "punished_entities": "示例机构",
        "violation_type": "违规募集",
        "punishment": "警告",
        "status": "done",
        "status_label": "已完成",
        "case_id": "demo-1",
        "entity_type": "机构",
        "involved_fund": "",
        "legal_basis": "",
        "document_number": "",
        "source_url": "https://example.com/demo",
    }
    monkeypatch.setattr(
        "regwatch.web.views.cases.load_rows",
        lambda _key: [sample],
    )
    at = AppTest.from_string(_VIEW_SCRIPT.format(view="cases"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert len(at.download_button) >= 1
    assert any("命中 1" in (item.value or "") for item in at.markdown)


def test_statistics_page_renders_date_filters() -> None:
    """统计分析：统计范围使用日期选择器。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="statistics"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert len(at.date_input) >= 2


def test_reset_filters_clears_widget_keys() -> None:
    """回归：清除筛选必须弹出控件 key，否则 Streamlit 会把旧选择写回 session。"""
    from regwatch.web import state

    fake: dict = {}
    for name in state.FILTER_KEYS:
        fake[state.PREFIX + name] = ["stale"]
    for widget_key in state.FILTER_WIDGET_KEYS:
        fake[widget_key] = ["stale"]

    import streamlit as st

    original = st.session_state
    try:
        st.session_state = fake  # type: ignore[assignment]
    except Exception:
        pytest.skip("无法替换 streamlit.session_state")
    try:
        state.reset_filters()
        for name in state.FILTER_KEYS:
            assert fake[state.PREFIX + name] == state.DEFAULTS.get(name)
        for widget_key in state.FILTER_WIDGET_KEYS:
            assert widget_key not in fake
    finally:
        st.session_state = original  # type: ignore[assignment]


def test_clear_filters_button_resets_cases_widgets() -> None:
    """案例浏览点「清除筛选」后，多选与关键词控件状态应为空。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="cases"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]

    # 选中数据集并填关键词
    datasets = [item for item in at.multiselect if item.key == "cases-datasets"]
    assert datasets, "缺少 cases-datasets 多选"
    datasets[0].set_value(["amac"])
    keywords = [item for item in at.text_input if item.key == "cases-keyword"]
    assert keywords, "缺少 cases-keyword 输入框"
    keywords[0].set_value("测试")
    at.run()
    assert at.session_state["regwatch.datasets"] == ["amac"]
    assert at.session_state["regwatch.keyword"] == "测试"

    clear_buttons = [item for item in at.button if "清除筛选" in (item.label or "")]
    assert clear_buttons, "缺少清除筛选按钮"
    clear_buttons[0].click()
    at.run()

    assert not at.exception, [str(item.value) for item in at.exception]
    assert at.session_state["regwatch.datasets"] == []
    assert at.session_state["regwatch.keyword"] == ""
    # 清除后控件本身应显示为空（widget key 会在重渲染时重建，故不断言 key 消失）
    datasets_after = [item for item in at.multiselect if item.key == "cases-datasets"]
    assert datasets_after and list(datasets_after[0].value) == []
    keywords_after = [item for item in at.text_input if item.key == "cases-keyword"]
    assert keywords_after and (keywords_after[0].value or "") == ""


class TestSafeUrlAndSourceLink:
    def test_rejects_non_http(self) -> None:
        from regwatch.web.components.ui import safe_url, source_link

        assert safe_url("javascript:alert(1)") == ""
        assert safe_url("data:text/html,x") == ""
        assert safe_url(None) == ""
        assert safe_url("  ") == ""
        assert "javascript" not in source_link("javascript:alert(1)")

    def test_accepts_http_https(self) -> None:
        from regwatch.web.components.ui import safe_url, source_link

        assert safe_url("https://example.com/a") == "https://example.com/a"
        assert safe_url("http://example.com") == "http://example.com"
        html = source_link("https://example.com/decision")
        assert 'href="https://example.com/decision"' in html
        assert "noopener" in html


def test_csv_bytes_has_utf8_bom() -> None:
    import pandas as pd

    from regwatch.web.components.ui import csv_bytes

    frame = pd.DataFrame({"日期": ["2024-01-01"], "标题": ["测试案"]})
    data = csv_bytes(frame)
    assert data.startswith(b"\xef\xbb\xbf")
    assert "测试案".encode() in data


def test_heat_from_periods_builds_matrix() -> None:
    from regwatch.web.components.charts import heat_from_periods

    fig = heat_from_periods(
        ["2024-01", "2024-02", "2025-01"],
        [3, 5, 1],
        title="月份密度",
    )
    assert fig.layout.title.text == "月份密度"
    heat = fig.data[0]
    assert len(heat.z) == 2  # 2024 / 2025
    assert heat.z[0][0] == 3
    assert heat.z[0][1] == 5
    assert heat.z[1][0] == 1


def test_detail_rows_escapes_by_default_and_raw_keys() -> None:
    from regwatch.web.components.ui import detail_rows, source_link

    escaped = detail_rows([("标题", "<script>x</script>")])
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped

    raw = detail_rows([("来源链接", source_link("https://example.com"))], raw_keys={"来源链接"})
    assert 'href="https://example.com"' in raw


def test_jobs_submit_form_uses_date_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务中心：起止日期应为 date_input，而不是手输文本框。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    # 云端入口用例可能把进程环境留在只读；本用例需要可编辑表单
    monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)
    at = AppTest.from_string(_VIEW_SCRIPT.format(view="jobs"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    # 默认任务类型为 JobKind.all() 第一个（FETCH_AMAC），含 start/end 日期
    date_keys = {item.key for item in at.date_input}
    assert "job-start_date" in date_keys
    assert "job-end_date" in date_keys
    # 日期字段不应再以文本框形式出现
    text_keys = {item.key for item in at.text_input}
    assert "job-start_date" not in text_keys
    assert "job-end_date" not in text_keys


def test_jobs_view_skips_auto_rerun_without_script_ctx() -> None:
    """未完成任务时：非真实 Streamlit 会话不得 sleep+rerun，避免 AppTest 卡死。"""
    from regwatch.web.views import jobs as jobs_view

    assert jobs_view._should_auto_refresh() is False


def test_section_header_and_filter_summary_render() -> None:
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    script = """
from regwatch.web.components import ui
ui.section_header("分区标题", "说明文字")
ui.filter_summary(3, ["数据集：AMAC", "关键词：挪用"])
ui.filter_summary(0, [])
"""
    at = AppTest.from_string(script, default_timeout=60)
    at.run()
    assert not at.exception
    assert any("命中 3" in (item.value or "") for item in at.markdown)
    assert any("未设置筛选条件" in (item.value or "") for item in at.markdown)


def test_qa_page_renders_credentials_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """智能问答页：公开可见，Chat 输入与凭证表单齐备。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("REGWATCH_READ_ONLY", "1")
    monkeypatch.delenv("REGWATCH_ADMIN_TOKEN", raising=False)

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="qa"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    keys = {item.key for item in at.text_input}
    assert "regwatch.qa.base_url" in keys
    assert "regwatch.qa.model" in keys
    assert "regwatch.qa.api_key" in keys
    # 非管理员且无 Key：对话输入禁用
    assert len(at.chat_input) == 1
    assert at.chat_input[0].disabled is True
    # 欢迎气泡
    assert any("监管案例助手" in (item.value or "") for item in at.markdown)


def test_qa_chat_submit_renders_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chat 布局：提交后用户/助手消息可见，且答案旁有下载按钮。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    from regwatch.domain import QaIntent
    from regwatch.services.qa import QaRetrieval, QaTurn

    intent = QaIntent(keywords=("某某",))
    retrieval = QaRetrieval(
        intent=intent,
        rows=(),
        overview={"total": 0},
        evidence="（无）",
    )
    turn = QaTurn(
        mode="report",
        question="某某基金处分专题",
        answer="# 专题报告\n\n当前案例库证据不足。",
        intent=intent,
        retrieval=retrieval,
        model="fake-model",
        used_fallback_intent=False,
    )

    class _Qa:
        @staticmethod
        def ask(*_a: object, **_k: object) -> QaTurn:
            return turn

    class _Services:
        qa = _Qa()

    monkeypatch.setattr("regwatch.web.views.qa.services", lambda: _Services())
    monkeypatch.setattr("regwatch.web.views.qa._resolve_client", lambda admin: (object(), "byok"))
    monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="qa"), default_timeout=180)
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert at.chat_input[0].disabled is False

    at.chat_input[0].set_value("某某基金处分专题")
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert any("证据不足" in (item.value or "") for item in at.markdown)
    labels = [item.label for item in at.download_button]
    assert any("下载" in (label or "") for label in labels)
    # 历史写入 session，重跑后仍以消息流展示
    assert at.session_state["regwatch.qa.history"]
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert any("证据不足" in (item.value or "") for item in at.markdown)


def test_qa_answer_lists_cases_below(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：问答/报告下方必须常显引用案例表（不再折叠隐藏）。"""
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    from regwatch.domain import CaseRow, Dataset, QaIntent
    from regwatch.services.qa import QaRetrieval, QaTurn

    row = CaseRow(
        dataset=Dataset.AMAC,
        case_id="P20260101000000000001",
        title="关于对某某基金管理有限公司的纪律处分",
        date="2026-01-05",
        punished_entities="某某基金管理有限公司",
        violation_type="违规募集",
        punishment="公开谴责",
        source_url="https://example.com/case",
    )
    intent = QaIntent(keywords=("某某",))
    retrieval = QaRetrieval(
        intent=intent,
        rows=(row,),
        overview={"total": 1},
        evidence="证据",
        total_hits=1,
    )
    turn = QaTurn(
        mode="qa",
        question="某某基金",
        answer="存在违规【案例1】。",
        intent=intent,
        retrieval=retrieval,
        model="fake",
        used_fallback_intent=False,
    )

    class _Qa:
        @staticmethod
        def ask(*_a: object, **_k: object) -> QaTurn:
            return turn

    class _Services:
        qa = _Qa()

    monkeypatch.setattr("regwatch.web.views.qa.services", lambda: _Services())
    monkeypatch.setattr("regwatch.web.views.qa._resolve_client", lambda admin: (object(), "byok"))
    monkeypatch.delenv("REGWATCH_READ_ONLY", raising=False)

    at = AppTest.from_string(_VIEW_SCRIPT.format(view="qa"), default_timeout=180)
    at.run()
    at.chat_input[0].set_value("某某基金")
    at.run()
    assert not at.exception, [str(item.value) for item in at.exception]
    assert any("引用案例" in (item.value or "") for item in at.markdown)
    assert len(at.dataframe) >= 1
    frame = at.dataframe[0].value
    csv = frame.to_csv()
    assert "某某基金管理有限公司" in csv
    assert "https://example.com/case" in csv
