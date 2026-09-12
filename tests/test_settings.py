"""配置层测试：默认值、旧键迁移、环境变量覆盖与持久化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from regwatch.domain import ModelProfile
from regwatch.settings import (
    MAX_CONCURRENCY,
    ConfigError,
    ConfigStore,
    Settings,
    migrate_legacy_roots,
)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """空配置：不含任何模型条目（避免样例里的示例模型干扰断言）。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"models": [], "tasks": {"summarize": "", "report": "", "vision": ""}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_defaults_are_applied(config_path: Path) -> None:
    settings = ConfigStore(config_path).settings
    assert settings.database.name == "regwatch.db"
    assert settings.reports_dir.name == "reports"
    assert settings.concurrency_for("fetch", 8) == 8


def test_legacy_data_roots_are_migrated(config_path: Path) -> None:
    config_path.write_text(
        json.dumps(
            {"data_roots": {"amac_cases": "data/amac/cases"}, "models": []}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    store = ConfigStore(config_path)
    assert store.settings.database.parent.name == "data"
    assert store.settings.database.name == "regwatch.db"
    assert store.settings.reports_dir.name == "reports"
    assert "data_roots" not in json.loads(config_path.read_text(encoding="utf-8"))
    assert config_path.with_name(config_path.name + ".legacy.bak").exists()


def test_migrate_legacy_roots_is_a_noop_for_new_config() -> None:
    raw = {"database": "data/x.db"}
    assert migrate_legacy_roots(raw) == raw


def test_env_overrides_database(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REGWATCH_DATABASE", str(tmp_path / "env.db"))
    settings = ConfigStore(config_path).settings
    assert settings.database == (tmp_path / "env.db").resolve()


def test_env_provides_api_key(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGWATCH_API_KEY_FAKE", "sk-from-env")
    store = ConfigStore(config_path)
    store.upsert_model(ModelProfile(id="fake", base_url="http://x", model="m"))
    assert store.settings.model("fake").api_key == "sk-from-env"


def test_upsert_and_delete_model(config_path: Path) -> None:
    store = ConfigStore(config_path)
    store.upsert_model(ModelProfile(id="a", base_url="http://a", model="m1"))
    store.upsert_model(ModelProfile(id="a", base_url="http://a2", model="m2"))
    assert len(store.settings.models) == 1
    assert store.settings.model("a").model == "m2"

    store.delete_model("a")
    assert store.settings.model("a") is None


def test_first_model_binds_to_summarize_task(config_path: Path) -> None:
    store = ConfigStore(config_path)
    store.upsert_model(ModelProfile(id="a", base_url="http://a", model="m"))
    assert store.settings.task_model("summarize") == "a"


def test_task_binding_is_cleared_on_delete(config_path: Path) -> None:
    store = ConfigStore(config_path)
    store.upsert_model(ModelProfile(id="a", base_url="http://a", model="m"))
    store.set_task_model("report", "a")
    store.delete_model("a")
    assert store.settings.task_model("report") == ""


def test_unknown_task_raises(config_path: Path) -> None:
    store = ConfigStore(config_path)
    with pytest.raises(ConfigError):
        store.set_task_model("nope", "a")


def test_concurrency_is_clamped(config_path: Path) -> None:
    store = ConfigStore(config_path)
    store.set_concurrency("fetch", 999)
    assert store.settings.concurrency_for("fetch") == MAX_CONCURRENCY
    store.set_concurrency("fetch", 0)
    assert store.settings.concurrency_for("fetch") == 1


def test_missing_model_raises_when_required() -> None:
    with pytest.raises(ConfigError):
        Settings().resolve_profile()


def test_settings_to_raw_roundtrip(tmp_path: Path) -> None:
    settings = Settings(database=tmp_path / "a.db", reports_dir=tmp_path / "r")
    raw = json.loads(json.dumps(settings.to_raw()))
    assert raw["database"] == str(tmp_path / "a.db")
