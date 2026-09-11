"""配置管理的纯本地测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from regwatch.config import (
    DEFAULT_DATA_ROOTS,
    PROJECT_ROOT,
    TASK_KINDS,
    Config,
    ConfigError,
)
from regwatch.datamodels import ModelProfile


class ConfigTests(unittest.TestCase):
    """Config 的读写、模型管理与任务映射。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.json"
        self.cfg = Config(self.path)

    # ── 基础 ──

    def test_default_data_roots_present(self) -> None:
        roots = self.cfg.data_roots()
        self.assertEqual(set(roots), set(DEFAULT_DATA_ROOTS))
        for key, value in roots.items():
            self.assertTrue(value.is_absolute(), f"{key} 应解析为绝对路径")
        self.assertEqual(
            self.cfg.data_root("csrc_cases"),
            PROJECT_ROOT / "CSRC" / "cases",
        )

    def test_relative_path_resolves_under_project_root(self) -> None:
        self.assertEqual(
            Config.resolve_path("some/dir"),
            PROJECT_ROOT / "some" / "dir",
        )
        absolute = Path(self._tmp.name)
        self.assertEqual(Config.resolve_path(absolute), absolute)

    def test_unknown_data_root_raises(self) -> None:
        with self.assertRaises(ConfigError):
            self.cfg.data_root("not_exist")

    def test_set_data_root_persists(self) -> None:
        self.cfg.set_data_root("amac_cases", "custom/amac")
        self.assertTrue(self.path.exists())
        reloaded = Config(self.path)
        self.assertEqual(reloaded.data_root("amac_cases"), PROJECT_ROOT / "custom" / "amac")

    def test_concurrency_clamped(self) -> None:
        self.assertEqual(self.cfg.concurrency("fetch"), 8)
        self.cfg.set_concurrency("fetch", 999)
        self.assertEqual(self.cfg.concurrency("fetch"), 64)
        self.cfg.set_concurrency("fetch", 0)
        self.assertEqual(self.cfg.concurrency("fetch"), 1)
        self.assertEqual(self.cfg.concurrency("missing", default=3), 3)

    def test_comment_keys_are_stripped(self) -> None:
        self.assertNotIn("_readme", self.cfg.snapshot())

    # ── 模型管理 ──

    def test_upsert_and_delete_model(self) -> None:
        profile = ModelProfile(id="unit-test", label="单测", base_url="https://x/v1", model="m")
        self.cfg.upsert_model(profile)

        fetched = self.cfg.get_model("unit-test")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.base_url, "https://x/v1")
        self.assertEqual(fetched.display_name, "单测")

        # 覆盖更新
        self.cfg.upsert_model(
            ModelProfile(id="unit-test", label="单测2", base_url="https://y/v1", model="m2")
        )
        self.assertEqual(self.cfg.get_model("unit-test").label, "单测2")
        self.assertEqual(len([m for m in self.cfg.models() if m.id == "unit-test"]), 1)

        # 持久化
        self.assertTrue(Config(self.path).get_model("unit-test"))

        # 删除并清理任务绑定
        self.cfg.set_task_model("report", "unit-test")
        self.assertTrue(self.cfg.delete_model("unit-test"))
        self.assertIsNone(self.cfg.get_model("unit-test"))
        self.assertEqual(self.cfg.task_model_id("report"), "")
        self.assertFalse(self.cfg.delete_model("unit-test"))

    def test_first_model_binds_summarize_task(self) -> None:
        self.cfg.data["models"] = []
        self.cfg.data["tasks"] = {kind: "" for kind in TASK_KINDS}
        self.cfg.upsert_model(ModelProfile(id="only", base_url="https://x", model="m"))
        self.cfg.set_task_model("report", "other")
        self.assertEqual(self.cfg.task_model_id("summarize"), "only")

    def test_set_task_model_rejects_unknown_task(self) -> None:
        with self.assertRaises(ConfigError):
            self.cfg.set_task_model("unknown-task", "x")

    def test_task_models_always_has_all_keys(self) -> None:
        self.assertEqual(set(self.cfg.task_models()), set(TASK_KINDS))

    # ── 解析优先级 ──

    def test_resolved_profile_priority(self) -> None:
        self.cfg.data["models"] = [
            ModelProfile(id="first", base_url="https://1", model="m").to_dict(),
            ModelProfile(id="second", base_url="https://2", model="m").to_dict(),
        ]
        self.cfg.data["tasks"] = {"summarize": "", "report": "second", "vision": ""}

        self.assertEqual(self.cfg.resolved_profile().id, "first")
        self.assertEqual(self.cfg.resolved_profile(task="report").id, "second")
        self.assertEqual(self.cfg.resolved_profile(model_id="second").id, "second")
        # 显式 model_id 优先于 task
        self.assertEqual(self.cfg.resolved_profile(model_id="first", task="report").id, "first")

    def test_resolved_profile_errors(self) -> None:
        self.cfg.data["models"] = []
        with self.assertRaises(ConfigError):
            self.cfg.resolved_profile()
        with self.assertRaises(ConfigError):
            self.cfg.resolved_profile(model_id="ghost")

    def test_env_var_overrides_api_key(self) -> None:
        self.cfg.data["models"] = []
        self.cfg.upsert_model(
            ModelProfile(id="env-test", base_url="https://x", model="m", api_key="from-file")
        )
        env_name = Config.env_key_name("env-test")
        self.assertEqual(env_name, "REGWATCH_API_KEY_ENV_TEST")

        os.environ[env_name] = "from-env"
        self.addCleanup(os.environ.pop, env_name, None)
        self.assertEqual(self.cfg.get_model("env-test").api_key, "from-env")

        del os.environ[env_name]
        self.assertEqual(self.cfg.get_model("env-test").api_key, "from-file")

    def test_api_key_env_field_takes_precedence(self) -> None:
        self.cfg.data["models"] = [
            ModelProfile(
                id="named",
                base_url="https://x",
                model="m",
                api_key="file",
                api_key_env="UNIT_TEST_KEY",
            ).to_dict()
        ]
        os.environ["UNIT_TEST_KEY"] = "named-env"
        self.addCleanup(os.environ.pop, "UNIT_TEST_KEY", None)
        self.assertEqual(self.cfg.get_model("named").api_key, "named-env")

    def test_saved_file_is_valid_json_without_comments(self) -> None:
        self.cfg.upsert_model(ModelProfile(id="x", base_url="https://x", model="m"))
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        # 样例配置中的模型会被合并进来，这里只校验目标条目存在且无注释键
        self.assertIn("x", [item["id"] for item in raw["models"]])
        self.assertEqual([k for k in raw if k.startswith("_")], [])
        self.assertEqual(set(raw["tasks"]), set(TASK_KINDS))


if __name__ == "__main__":
    unittest.main()
