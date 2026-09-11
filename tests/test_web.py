"""网页端冒烟测试：用 Streamlit AppTest 真实渲染，无需浏览器。"""

from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from regwatch.web import components
from regwatch.web.components.data import clamp_page

WEB_DIR = Path(components.__file__).resolve().parents[1]
PAGES = ("overview", "cases", "statistics", "jobs", "settings")


class ClampPageTests(unittest.TestCase):
    def test_clamp_page_bounds(self):
        self.assertEqual(clamp_page(None, 5), 1)
        self.assertEqual(clamp_page(0, 5), 1)
        self.assertEqual(clamp_page(3, 5), 3)
        self.assertEqual(clamp_page(99, 5), 5)
        self.assertEqual(clamp_page("abc", 5), 1)
        self.assertEqual(clamp_page(2, 0), 1)


class WebAppTests(unittest.TestCase):
    """五个页面均应无异常渲染（基于真实数据）。"""

    def test_app_entry_renders(self):
        at = AppTest.from_file(str(WEB_DIR / "app.py"), default_timeout=300)
        at.run()
        self.assertEqual(len(at.exception), 0, "\n".join(e.message or "" for e in at.exception))

    def test_each_page_renders_without_exception(self):
        for name in PAGES:
            with self.subTest(page=name):
                at = AppTest.from_file(
                    str(WEB_DIR / "views" / f"{name}.py"), default_timeout=300
                )
                at.run()
                self.assertEqual(
                    len(at.exception),
                    0,
                    name + ": " + "\n".join(e.message or "" for e in at.exception),
                )


if __name__ == "__main__":
    unittest.main()
