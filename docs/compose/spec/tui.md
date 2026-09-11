---
feature: tui
status: delivered
updated: 2026-09-12
branch: main
commits: 3b7e71b..(working-tree, uncommitted)
---

# Textual 终端界面（TUI）

## Report

**What was built** — 新增 `regwatch tui`：基于 Textual 的全屏终端界面。首页菜单列出五类任务（AMAC 抓取、CSRC 抓取、月度公告、摘要、报告）；表单按 `JOB_PARAMS` 动态生成（含 CLI 对等的 `concurrency` / `limit`），布尔用 Checkbox、枚举用 Select（带字段标签）、其余用 Input；运行页通过 `JobManager.submit` 与网页端同一通道执行，0.25s 轮询进度/日志，支持协作式取消。同类型互斥失败会 toast 并返回表单；运行中「返回」需二次确认。未配置模型时首页给出黄色提示。CLI 全部子命令保留，供脚本与自动化使用。

**Verification** —
- `python -m unittest tests.test_tui`：9 passed
- `python -m unittest discover -s tests -t .`：156 passed, 3 skipped
- `ruff check src tests`：PASS
- `mypy`（pyproject files=src/regwatch）：PASS
- `python -m regwatch.cli --help`：可见 `tui`
- `uv lock`：已写入 textual 8.2.8

**Journey log** —
1. `uv sync` / 默认源装包易卡住，改用 `uv pip install` + 清华镜像 `uv lock`。
2. Textual 已是 8.x，不用 0.80 老 API；默认屏用 `get_default_screen()`，测试里 `app.query` 窗口问题用 `screen.query`。
3. Pilot 无 `push_screen`，用 `await app.push_screen(...)`。
4. 评审指出：Select 无标签、互斥失败未回表单、运行中返回无确认——均已修。
5. 网页任务表单允许 `report.dataset=all`，而 `run_task` 会拒绝；TUI 已特殊处理（仅 amac/csrc），网页侧为潜在既有问题。

## [S1] Problem

现有 `regwatch` CLI 功能完整，但用户必须记住多层子命令与选项（如
`fetch amac --start ... --categories ...`），非技术或低频用户上手成本高。
仓库已有 Streamlit 网页端，但无浏览器 / SSH / 纯终端场景仍缺一个
「看得见、可点选」的操作入口。

用户诉求：
1. 做成 TUI（或类似形态），避免记命令；
2. 有 TUI 后 CLI 不必删除——脚本与自动化仍需要非交互入口。

## [S2] Design

### 决策（已确认）

| 轴 | 选择 |
|----|------|
| 交互形态 | Textual 全屏 TUI |
| CLI 去留 | **保留**全部现有子命令；TUI 为并行人类入口 |
| 入口 | `regwatch tui`（不改变 `regwatch` 无参数时的 help 行为） |
| MVP 范围 | 仅任务类：`fetch_amac` / `fetch_csrc` / `fetch_monthly` / `summarize` / `report` |
| 工作区 | 直接在 main 实现（用户明确选择） |

不在 TUI MVP：模型配置管理、案例浏览、统计看板、`org-type`、`fetch url`
（仍走 CLI 或网页端）。

### 架构

```
regwatch tui
    │
    ▼
regwatch.tui.RegwatchApp          # Textual App
    ├── HomeScreen                # 任务清单（ListView）
    ├── FormScreen                # 按 JOB_PARAMS 动态生成表单
    └── RunScreen                 # JobManager 提交 + 轮询进度/日志
              │
              ▼
        regwatch.jobs.JobManager  # 与网页端同一执行通道
              │
              ▼
           run_task(...)
```

- **不重写业务逻辑**：TUI 只做参数收集与状态展示；执行一律走
  `JobManager.submit` → `run_task`，与 CLI / Streamlit 同源。
- **取消**：`JobManager.cancel(job_id)`，协作式取消（进度回调检查点）。
- **同类型互斥**：复用 `JobManager.is_kind_running`。

### 界面契约

**HomeScreen**
- 列表项（顺序固定）：
  1. AMAC 案例抓取 (`fetch_amac`)
  2. CSRC 案例抓取 (`fetch_csrc`)
  3. AMAC 月度公告下载 (`fetch_monthly`)
  4. 结构化摘要提取 (`summarize`)
  5. 报告生成 (`report`)
- 选择后 `push_screen(FormScreen(kind))`；`q` 退出应用。
- 未配置模型时顶部显示黄色提示（读 `Config.models()`）。

**FormScreen**
- 字段来自 `jobs.JOB_PARAMS[kind]`，并附加 CLI 对等项：
  - `fetch_csrc`: `concurrency`（int, 默认 0）
  - `summarize`: `limit`（int, 默认 0）
- 控件映射：
  - `bool` 默认值 → `Checkbox`（自带标签）
  - 枚举键 → 先 `Static` 标签再 `Select`：
    - `categories`: all / Institution / Personnel
    - `case_types`: all / penalty / measure
    - `dataset`（summarize）: all / amac / csrc
    - `dataset`（report）: amac / csrc
  - 其余 → `Static` 标签 + `Input`（日期用文本 `YYYY-MM-DD`）
- 「开始运行」收集控件值并做类型转换后 `push_screen(RunScreen)`；
  「返回」`pop_screen`。

**RunScreen**
- 启动时 `JobManager.submit(kind, params)`；同类型已在跑则 toast 错误并 **返回表单**。
- 每 0.25s 轮询 `TaskRecord`：状态、进度条、`message`/`error`、任务日志增量。
- 「取消」请求协作式取消；「返回」：结束后直接离开；运行中需 **二次确认**（任务仍在后台）。
- 结束态：success 绿色摘要（含报告路径）；failed 红色 error；cancelled 黄色。

### CLI 变更

- 新增子命令 `regwatch tui`（`cli.py`），延迟导入 `regwatch.tui.main`。
- `pyproject.toml` 增加运行依赖 `textual>=0.80`（锁文件 8.2.8）。
- README「功能一览 / 快速开始 / 终端界面」说明三入口。

### 错误与边界

- 未配置模型导致任务失败：与网页端一致，状态 failed + 日志可见。
- 窗口过窄：表单区可滚动（`VerticalScroll`）。
- 不在 TUI 内改配置；缺模型时在 Home 顶部提示。

### 测试边界

- 单测不启动真实网络任务：
  - `fields_for` / `parse_field_value`；
  - `RegwatchApp` 冒烟：首页 5 个任务项；
  - FormScreen 字段 id 存在、`collect_params` 类型正确。
- 用 `reset_job_manager` 隔离全局单例。

## [S3] Out of Scope

- 配置/模型管理界面、案例浏览与统计、`org-type`、`fetch url`。
- 删除或精简现有 CLI 子命令。
- 改变 `regwatch` 无参数行为（仍为 help）。
- TUI 内嵌浏览器预览报告文件。

## Tasks

- [x] T1: 增加 `textual` 依赖并实现 `src/regwatch/tui.py` — acceptance: 可导入；App 含 Home/Form/Run 三屏 (covers: S2)
- [x] T2: CLI 增加 `regwatch tui` — acceptance: help 列表可见 tui（covers: S2）
- [x] T3: 更新 README 三入口说明 — acceptance: README 含 TUI 启动命令与范围说明（covers: S2）
- [x] T4: 新增 `tests/test_tui.py` — acceptance: `unittest tests.test_tui` 全绿；不触网（covers: S2）
- [x] T5: 全量验收 `unittest` + `ruff check` + `mypy` — acceptance: 全绿（covers: S2）
