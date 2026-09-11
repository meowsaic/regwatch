"""TUI 单元测试：表单字段、参数解析、应用冒烟（不触网）。"""

from __future__ import annotations

import unittest

from regwatch.jobs import JOB_PARAMS, reset_job_manager
from regwatch.tui import (
    TUI_JOB_KINDS,
    FormScreen,
    HomeScreen,
    RegwatchApp,
    fields_for,
    parse_field_value,
)


class FieldsForTests(unittest.TestCase):
    def test_all_mvp_kinds_have_fields(self):
        for kind in TUI_JOB_KINDS:
            with self.subTest(kind=kind):
                fields = fields_for(kind)
                self.assertIsInstance(fields, tuple)
                self.assertEqual(fields[: len(JOB_PARAMS[kind])], JOB_PARAMS[kind])

    def test_csrc_has_concurrency_and_summarize_has_limit(self):
        csrc_keys = {key for _l, key, _d, _h in fields_for("fetch_csrc")}
        sum_keys = {key for _l, key, _d, _h in fields_for("summarize")}
        self.assertIn("concurrency", csrc_keys)
        self.assertIn("limit", sum_keys)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            fields_for("nope")


class ParseFieldValueTests(unittest.TestCase):
    def test_int_default(self):
        self.assertEqual(parse_field_value("workers", 0, "5"), 5)
        self.assertEqual(parse_field_value("workers", 0, ""), 0)
        self.assertEqual(parse_field_value("workers", 3, "  "), 3)
        with self.assertRaises(ValueError):
            parse_field_value("workers", 0, "abc")

    def test_bool_default(self):
        self.assertTrue(parse_field_value("retry_failed", True, "true"))
        self.assertFalse(parse_field_value("use_llm", False, ""))
        self.assertTrue(parse_field_value("use_llm", False, "1"))

    def test_str_default(self):
        self.assertEqual(parse_field_value("start_date", "", " 2026-01-01 "), "2026-01-01")
        self.assertEqual(parse_field_value("categories", "all", ""), "")


class TuiAppSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_lists_all_tasks(self):
        reset_job_manager()
        app = RegwatchApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, HomeScreen)
            list_view = app.query_one("#task-list")
            items = list(list_view.query("ListItem"))
            self.assertEqual(len(items), len(TUI_JOB_KINDS))
            labels = {item.name for item in items}
            self.assertEqual(labels, set(TUI_JOB_KINDS))

    async def test_form_screen_has_inputs(self):
        reset_job_manager()
        app = RegwatchApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.push_screen(FormScreen("fetch_amac"))
            await pilot.pause()
            screen = app.screen
            self.assertIsInstance(screen, FormScreen)
            for key in ("start_date", "end_date", "categories"):
                self.assertEqual(len(screen.query(f"#f-{key}")), 1)

    async def test_form_collect_params_types(self):
        reset_job_manager()
        app = RegwatchApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.push_screen(FormScreen("summarize"))
            await pilot.pause()
            form = app.screen
            assert isinstance(form, FormScreen)
            params = form.collect_params()
            self.assertEqual(params["dataset"], "all")
            self.assertEqual(params["workers"], 0)
            self.assertTrue(params["retry_failed"])
            self.assertEqual(params["limit"], 0)


if __name__ == "__main__":
    unittest.main()
