---
feature: quality-refactor
status: delivered
updated: 2026-09-11
branch: quality-refactor
commits: 037d6d1..<head>
---

# regwatch 质量重构与 UI 优化

## Report

**What was built** — 为 regwatch 建立了现代 Python 工程基线：`pyproject.toml` + `uv.lock`（`web`/`dev` 可选依赖）、可安装 CLI 入口 `regwatch`、ruff/mypy 质量门禁（核心包 mypy 0 error、ruff 通过）。核心包收窄了 sources 的类型与 BeautifulSoup 结果（新增 `sources/_html.py`），清理了根目录临时脚本。网页端在 Streamlit 五页架构内完成体验优化：案例浏览日期选择器、一键清除筛选、分页键钳制修复、任务中心日期控件；空态文案可操作。

**Verification** —
- `uv run mypy regwatch` → PASS（0 errors）
- `uv run ruff check regwatch tests web` → PASS
- `uv run python -m unittest discover -s tests -t .` → PASS（147 tests, 3 skipped）
- `uv run regwatch --help` → PASS
- 独立评审指出的分页双键 CRITICAL 已修复并补 `clamp_page` 单测

**Journey log** —
1. 环境阻止 `git worktree add`，改用 `.worktrees/` 下本地 clone 隔离。
2. 默认 PyPI 拉依赖过慢，锁定清华镜像后 `uv sync` 数秒完成。
3. 中文全角标点触发 RUF001–003 噪声，规则中显式关闭。
4. `from datetime import datetime` + `datetime.date` 注解会 mypy 报错，统一改为导入 `date`。
5. Streamlit 分页钳制必须写入 **widget key** 的 session_state，否则清除筛选/缩结果会越界。

## [S1] Problem

仓库已完成「两套脚本 → 统一 regwatch 包 + Streamlit 五页 + CLI」的第一轮整合，但工程基线仍偏草稿：无现代打包与质量门禁、核心包类型不完整、网页日期筛选仍是文本框、根目录有临时脚本。

用户决策：可接受破坏性变更；工具链 uv + ruff + mypy；UI 继续用 Streamlit。

## [S2] Design

### S2.1 打包与工具链
- `pyproject.toml`（PEP 621）+ `uv.lock`；`requirements.txt` 为 pip 兼容参考。
- ruff：line-length 100；RUF001–003 关闭（中文标点）。
- mypy：`packages=["regwatch"]`，验收 0 error。
- 入口：`regwatch = "regwatch.cli:app"`。

### S2.2 核心包质量
- sources 统一 `date` 注解；`sources/_html.py` 提供 `as_tag`/`attr_str`。
- `write_json` 支持 list/dict；`jobs._parse_date` 接受 `str | None`。
- 删除 `_fill_form*.py`；`.gitignore` 增加 `.worktrees/`。

### S2.3 UI
- 案例浏览：`st.date_input`、清除筛选、空态、`clamp_page` 分页钳制（写 widget key）。
- 任务中心：起止日期 `date_input`。
- 保留五页信息架构与主题。

### S2.4–S2.6
- 测试边界：不访问官网/真实 LLM；AppTest 冒烟 + clamp 单测。
- 错误行为：ConfigError / LLM 降级 / 空态引导保持。

## [S3] Out of Scope
- 磁盘 JSON 布局与违规分类体系不迁移。
- 不换前端框架；不引入云端 CI。
- `csrc.py` 未全量拆分（仅类型与局部防御）。
- 专项分析报告历史脚本不清理。

## Tasks

- [x] T1: 建立 pyproject/uv/ruff/mypy 与项目入口 — acceptance: `uv sync` 后 ruff/mypy/`regwatch --help` 可执行 (covers: S2.1)
- [x] T2: 核心包异常与类型质量整改 — acceptance: mypy 0 error；ruff 通过；单测全绿 (covers: S2.2, S2.5)
- [x] T3: 清理根目录非产品脚本与 .gitignore — acceptance: `_fill_form*.py` 移除；`.worktrees/` 忽略 (covers: S2.2)
- [x] T4: 案例浏览与全局 UX 优化 — acceptance: 日期选择器、清除筛选、页码钳制；AppTest 通过 (covers: S2.3)
- [x] T5: 测试与文档同步 — acceptance: unittest 通过；README/AGENTS 含 uv 与质量命令 (covers: S2.4; depends: T1, T2, T4)
- [x] T6: 评审与定稿 — acceptance: CRITICAL 分页双键已修复并复验；spec delivered (covers: S2; depends: T1–T5)
