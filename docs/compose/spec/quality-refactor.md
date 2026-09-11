---
feature: quality-refactor
status: designed
updated: 2026-09-11
branch: quality-refactor
commits: 037d6d1..<head>
---

# regwatch 质量重构与 UI 优化

## Report

## [S1] Problem

仓库已完成「两套脚本 → 统一 regwatch 包 + Streamlit 五页 + CLI」的第一轮整合，但工程基线仍偏草稿：

1. **无现代打包与质量门禁**：仅有 `requirements.txt`，没有 `pyproject.toml`、ruff、mypy；贡献者无法一键安装/格式化/类型检查。
2. **核心包质量参差**：`sources/csrc.py` 等超大模块存在大量宽泛 `except Exception`，类型注解不完整，公共 API 边界不够清晰。
3. **网页端体验有缺口**：案例浏览日期仍是自由文本框；空态/错误提示不统一；侧边栏与页面信息密度可再收敛。
4. **根目录有无关临时脚本**（`_fill_form*.py`），污染项目观感与 git 历史。

用户明确：可接受破坏性变更；工具链用 uv + ruff + mypy；UI 继续用 Streamlit，信息架构由实现方判断是否重排。

## [S2] Design

### S2.1 打包与工具链

- 新增 `pyproject.toml`（PEP 621）：项目元数据、运行时依赖、可选依赖组 `dev` 与 `web`。
- 使用 **uv** 作为首选包管理：`uv sync --extra web --extra dev` + `uv.lock`；`requirements.txt` 降为 pip 兼容参考。
- `[tool.ruff]`：line-length 100，规则集 E/W/F/I/UP/B/SIM/RUF；对中文全角标点关闭 RUF001–003。
- `[tool.mypy]`：`packages = ["regwatch"]`，`ignore_missing_imports = true`；验收为 **0 error**。
- 可安装入口：`regwatch = "regwatch.cli:app"`。

### S2.2 核心包质量

原则：行为以测试为准；磁盘 JSON 布局与 `CaseRow` 字段语义不迁移。

- 源模块统一 `datetime.date` 注解（避免 `from datetime import datetime` 后把 `datetime.date` 当方法）。
- 新增 `regwatch/sources/_html.py`：`as_tag` / `attr_str` 收窄 BeautifulSoup 结果，避免 `Tag | NavigableString` 误用。
- `write_json` 支持 list/dict；`jobs._parse_date` 接受 `str | None` 并返回 `date`。
- 清理根目录 `_fill_form*.py`；`.gitignore` 增加 `.worktrees/`。

### S2.3 UI（Streamlit 内优化）

保留五页信息架构：总览 / 案例浏览 / 统计分析 / 任务中心 / 模型与配置。

- **案例浏览**：日期改为 `st.date_input`；一键「清除筛选」；空结果区分「有筛选」与「无数据」；页码越界自动钳制。
- **任务中心**：起止日期改用 `date_input`（空=不限制）。
- 沿用深蓝 + 琥珀主题；AppTest 冒烟覆盖五页。

### S2.4 测试与文档

- `uv run python -m unittest discover -s tests -t .` → **146 tests OK**（含 3 skip，不发外网）。
- `uv run ruff check regwatch tests web` / `uv run mypy regwatch` 通过。
- README / AGENTS 更新 uv 安装、质量门禁命令与镜像提示。

### S2.5 错误行为

- 配置缺字段：`ConfigError`，网页/CLI 可见提示。
- 模型连通失败：保留降级重试。
- 数据目录为空：空态引导，不抛未捕获异常。

### S2.6 测试边界

- 单测不访问真实官网、不调用真实 LLM。
- Streamlit 用 AppTest 冒烟即可。

## [S3] Out of Scope

- 不迁移磁盘案例 JSON 布局，不重写违规分类体系。
- 不更换前端框架。
- 不引入云端 CI（本地命令可复现即可）。
- 专项分析报告历史脚本不纳入清理。
- 未对 1660 行 `csrc.py` 做全量拆分（仅类型与局部防御性修正）。

## Tasks

- [ ] T1: 建立 pyproject/uv/ruff/mypy 与项目入口 — acceptance: `uv sync` 后 ruff/mypy/`regwatch --help` 可执行 (covers: S2.1)
- [ ] T2: 核心包异常与类型质量整改 — acceptance: mypy 0 error；ruff 通过；单测全绿 (covers: S2.2, S2.5)
- [ ] T3: 清理根目录非产品脚本与 .gitignore — acceptance: `_fill_form*.py` 移除；`.worktrees/` 忽略 (covers: S2.2)
- [ ] T4: 案例浏览与全局 UX 优化 — acceptance: 日期选择器、清除筛选、空态；AppTest 通过 (covers: S2.3)
- [ ] T5: 测试与文档同步 — acceptance: 146 项通过；README/AGENTS 含 uv 与质量命令 (covers: S2.4; depends: T1, T2, T4)
- [ ] T6: 评审与定稿 — acceptance: 独立评审通过或 critical 修复完毕；spec 更新为 delivered (covers: S2; depends: T1–T5)
