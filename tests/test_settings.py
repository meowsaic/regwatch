"""配置层测试：默认值、环境变量覆盖与持久化。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from regwatch.domain import ModelProfile
from regwatch.settings import MAX_CONCURRENCY, ConfigError, ConfigStore, Settings


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


def test_save_model_from_fields_creates_entry(config_path: Path) -> None:
    store = ConfigStore(config_path)
    profile = store.save_model_from_fields(
        model_id=" deepseek ", base_url=" https://api.deepseek.com ", model=" deepseek-chat "
    )
    assert profile.id == "deepseek"
    saved = store.settings.model("deepseek")
    assert saved.base_url == "https://api.deepseek.com"
    assert saved.model == "deepseek-chat"
    # 首个模型仍自动绑定摘要任务
    assert store.settings.task_model("summarize") == "deepseek"


def test_save_model_from_fields_keeps_untouched_fields(config_path: Path) -> None:
    """只改一处时，同名条目的密钥 / 透传参数 / 备注必须保留。"""
    store = ConfigStore(config_path)
    store.upsert_model(
        ModelProfile(
            id="a",
            label="甲",
            base_url="http://a",
            api_key="sk-1",
            model="m1",
            extra={"thinking": {"type": "disabled"}},
            note="备注",
        )
    )
    store.save_model_from_fields(model_id="a", base_url="http://a2", model="m2")
    saved = store.settings.model("a")
    assert saved.base_url == "http://a2"
    assert saved.model == "m2"
    assert saved.api_key == "sk-1"
    assert saved.label == "甲"
    assert saved.extra == {"thinking": {"type": "disabled"}}
    assert saved.note == "备注"


def test_save_model_from_fields_validates_required(config_path: Path) -> None:
    store = ConfigStore(config_path)
    with pytest.raises(ConfigError, match="缺少必填项"):
        store.save_model_from_fields(model_id="a", base_url="", model="")


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
