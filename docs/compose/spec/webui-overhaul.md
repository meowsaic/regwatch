---
feature: webui-overhaul
status: delivered
updated: 2026-02-27
branch: main
commits: ab4699ccd80a86fadc688bccb1207281a7937034..ed18cd9
---

## Amendment 2026-02-27b

**Why** — 用户反馈任务中心「起始日期 / 结束日期」仍是手输文本；并确认 deploy 入口是否同步。

**Contract** — `ParamSpec.kind` 新增 `date`；`JOB_SPECS` 中 `start_date`/`end_date` 标为 `date`；网页 `_widget` 用 `st.date_input`，空值写回 `""`，选中写 `YYYY-MM-DD`。`ParamSpec.coerce` 对 `date`/`datetime` 归一为 ISO 字符串。deploy 两入口（`streamlit_app.py` / `streamlit_public.py`）共用 `shell` + views，**无独立页面副本**，本轮 UI 优化自动生效，无需改 deploy 文件。

**Tasks** — T9 任务表单日期控件 + T10 回归测试（已完成）。

# WebUI Overhaul

## Report

**What was built** — 在 webui-ux-polish 之上，对 Streamlit 看板做一轮 Convention 模式全面优化：统一 `section_header` / `filter_summary` / CSV 导出 / 安全来源链接等共享组件；总览补齐来源局 TOP10 与年×月密度热力；案例浏览增加命中摘要条、条件 chips、可点击原文链接与全量筛选结果 CSV 导出；统计/任务/配置页补齐空态、分区与卡片层级；任务轮询改为有未完成任务时才 sleep+rerun，且非真实脚本上下文不自动重入。视觉 token 继续沿用既有 BRAND，系统字体栈与 `prefers-reduced-motion`、focus-visible 补齐。

**Verification** — `uv run pytest -q` → 230 passed；`uv run ruff check src/regwatch/web tests/test_web.py` 通过；`uv run ruff format --check` 通过；`uv run mypy src/regwatch` 通过。独立评审无 critical；已修复 M1（AppTest 轮询卡死风险）、M2（案例页导出/摘要回归断言）及 CSV 文件名误导。

**Journey log** — 用户选择在当前 main 目录实现（含未提交 polish 基线）并保持筛选默认展开。`detail_rows(raw_keys=...)` 是信任边界，仅允许 `source_link` 生成的 HTML。Jobs 自动刷新必须绑定真实 `ScriptRunContext`，否则 AppTest 会因未完成任务空转超时。任务参数规格 `ParamSpec.kind=date` 是网页与 CLI 共用契约；deploy 无独立视图，共享 `shell.run` 即同步。

## [S1] Problem

在已完成的 webui-ux-polish 基础上，看板作为内部监管数据工具仍有系统性粗糙：

1. **信息架构松散**：总览未使用已返回的 `bureau` 聚合与月份密度信息；统计页 tabs 空态、表格列宽不统一；各页分区标题节奏不一致。
2. **案例浏览可扫读性弱**：筛选虽可用，但缺少「当前命中 + 已选条件」摘要；详情是裸键值表，来源链接不可点，无法导出结果集。
3. **组件与页面重复造轮子**：表格、分区标题、筛选摘要、导出、链接在各视图各自拼装；`heat_by_month` 已实现却未接线。
4. **任务中心轮询过激**：有运行中任务时立即 `st.rerun()`，无间隔，易造成空转。
5. **视觉 token 未完全贯彻**：间距/描边/focus/动效 reduced-motion 仍有缝隙；图表字体与页面字体栈不完全一致。

用户要求：**全面优化**；筛选区**保持默认展开**，只做视觉打磨；技术栈保持 Streamlit；在当前 main 目录实现（含未提交 polish 基线）。

## [S2] Design

### 模式与视觉方向

- **Convention + Existing-codebase**：匹配既有 `BRAND` token，不引入第二套视觉语言，不做营销页式视觉冒险。
- Style anchor：监管机构内部数据工作台（浅色仪表盘 + 深蓝侧栏）。
- Palette（沿用）：primary `#354e92`，accent `#2563EB`，amber `#F59E0B`，bg `#F6F8FC`，card `#FFFFFF`，text `#0F172A`，muted `#475569`，line `#D8E0EE`。
- Typography：系统栈 `"Segoe UI","PingFang SC","Microsoft YaHei",system-ui`；正文 15px；页标题 1.85rem；分区 1.2rem。
- Layout：`max-width: 1400px`；侧栏 252px；间距节奏 8/12/16/24；筛选区默认展开。
- Signature（克制）：统一「页头 → 指标带 → 分区标题 → 内容」节奏；案例详情成为可扫读的「监管文书信息卡」。

### 组件层（`components/ui.py` + 可选小助手）

| 契约 | 行为 |
|------|------|
| `section_header(title, caption="")` | 统一分区标题（H2 级 + 可选说明），替代散落的 `st.markdown("#### …")` |
| `filter_summary(chips: Sequence[str], total: int)` | 结果上方摘要条：命中条数 + 当前条件 chips；无条件时只显示条数 |
| `download_button(df, filename, label)` | 统一 CSV 下载（`st.download_button` + UTF-8-BOM 便于 Excel） |
| `source_link(url)` / `safe_url` | 仅允许 `http(s)`，生成可点击链接 HTML；非法/空则 `—` |
| 主题 CSS | 补 `prefers-reduced-motion`；focus-visible 环；表格/表头/分区间距统一 |

### 页面

**总览**

- 保留指标卡四格；补齐 `overview["bureau"]` TOP 横向条（此前数据已返回未展示）。
- 数据集构成环图 + 违规 TOP10 + **月份密度热力**（由 `time_trend` 的 `period`/`count` 聚成 year×month，接入 `charts.heat_by_month` 或等价封装）。
- 月度趋势保留机构/个人副序列。
- 「最新案例」用 `section_header`；表格列宽与案例页对齐。

**案例浏览**

- 筛选默认展开不变；布局/描边/占位仅视觉打磨。
- 结果区上方：`filter_summary`（条数 + chips）；表格上方分页与条数信息合并为一条工具条。
- 表格保留单行选中；增加「导出当前结果 CSV」。
- 详情：徽标 + 双列 `detail_rows`（宽标签）+ 摘要突出 + **来源链接可点** + 正文 expander；空值统一 `—`。
- 分页按钮样式与全局 primary/secondary 一致。

**统计分析**

- 「统计范围」日期控件与案例页同一 `field`/`date_input_field` 交互。
- 每个 tab 独立空态；代表案例表列宽/空值统一；违规分布表可下载。

**任务中心**

- 运行中任务自动刷新：先 `time.sleep(_POLL_SECONDS)` 再 `st.rerun()`，避免无间隔空转。
- 任务卡片：标题行（名称 + 状态徽标）/ 元信息 / 进度 / 消息 / 操作（取消、日志）层级与间距统一。
- 空态与只读提示保持现有文案策略。

**模型与配置**

- 路径信息用 `section_header("数据路径")` + `detail_rows`。
- 模型列表卡片信息层级：展示名、id 徽标、端点/模型/掩码密钥；操作右对齐。
- 新增/更新表单分组标签清晰；任务绑定与并发用 section_header 分隔。

**侧栏**

- 继续紧凑统计 + 刷新 + 只读解锁；补充 `date_min ~ date_max`（来自 overview，有则显示）。

### 错误/边界

- 空库/无摘要/无趋势：页面仍启动，空态文案指向下一步动作。
- 来源 URL 非 http(s) 或空：显示 `—`，不生成 `javascript:` 等危险链接。
- CSV 导出：当前页筛选后的完整结果集（受 `load_rows` 已有 limit 约束）；列名中文。
- 任务轮询：仅在存在未完成任务时 sleep+rerun；无任务不循环。
- 日期控件沿用现有 `parse_date_arg` / 写回 `YYYY-MM-DD` 契约。

### 测试边界

- 纯函数：`safe_url`、`filter_summary` HTML 片段、CSV 生成辅助（若抽出）、热力图从 period 聚合。
- AppTest：五页（本地入口）不抛异常；案例页存在导出/摘要相关控件或标记；清除筛选仍清空 widget key。
- 不做像素级视觉回归。

## [S3] Out of Scope

- 不改数据层、CLI、抓取/摘要/任务业务逻辑与权限模型。
- 不引入自定义 Streamlit 组件包、新前端框架、深色主题切换、i18n。
- 不改公开部署页面清单（`public_items` 仍三页）。
- 不把筛选区改为默认折叠（用户明确要求保持展开）。
- 不实现登录/鉴权升级。

## Tasks

- [x] T1: 主题 CSS 与共享组件（section_header / filter_summary / download / safe_url / reduced-motion / focus） — acceptance: ui.py 导出新组件；CSS 含 reduced-motion 与 focus-visible；无 Google Fonts (covers: S2)
- [x] T2: 总览页接线 bureau TOP + 月份热力 + 分区节奏 — acceptance: 总览展示 bureau 条图与年×月热力；最新案例用 section_header (covers: S1 S2; depends: T1)
- [x] T3: 案例页摘要条、可点来源链接、详情信息卡、CSV 导出 — acceptance: 结果上方有命中数与条件 chips；详情 source_url 可点击或显示 —；可下载 CSV (covers: S1 S2; depends: T1)
- [x] T4: 统计页空态/列宽/范围控件对齐 — acceptance: tabs 空态齐全；日期 field 与案例页一致；代表案例列宽可用 (covers: S1 S2; depends: T1)
- [x] T5: 任务中心轮询节流 + 卡片层级 — acceptance: 有运行任务时 sleep 2s 后 rerun；卡片信息层级清晰 (covers: S1 S2; depends: T1)
- [x] T6: 配置页分区与模型卡片可读性 — acceptance: 路径/模型/绑定/并发分区明确；只读模式不泄露可写控件 (covers: S1 S2; depends: T1)
- [x] T7: 侧栏日期范围与细节 — acceptance: 有数据时显示 date_min~date_max；无数据不显示 (covers: S2; depends: T1)
- [x] T8: 补充测试并跑通 pytest / ruff / mypy — acceptance: 全量相关测试绿；ruff 与 mypy 通过 (covers: S1 S2; depends: T2 T3 T4 T5 T6 T7)
- [x] T9: 任务中心起止日期改为 date_input（ParamSpec kind=date） — acceptance: 提交表单出现 job-start_date/job-end_date 的 date_input；提交参数仍为 YYYY-MM-DD 或空 (covers: S2)
- [x] T10: 补充 jobs 日期 kind 与表单控件测试 — acceptance: coerce/表单 AppTest 覆盖日期字段 (covers: S2; depends: T9)
