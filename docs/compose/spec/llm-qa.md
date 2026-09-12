---
feature: llm-qa
status: delivered
updated: 2026-03-09
branch: main
commits: c76fcce..working-tree (未提交)
---

# LLM 智能问答与专题报告

## Report

**What was built** — 网页端新增公开页「智能问答」，布局为 Chat 式（用户气泡 + 助手全文 + 底部输入）。自然语言问题经 LLM 解析为结构化检索意图，走既有 `CaseQuery` 检索案例行（不加载正文），再基于证据包作答或撰写自由格式 Markdown 专题报告；每条助手回复顶部可下载 .md。非管理员必须在会话内手填 OpenAI 兼容凭证（不落盘）；管理员默认任务绑定 `qa` 的全局模型，可切换手填。历史仅存 `session_state`。

**Verification** — `uv run pytest -q` → 248 passed；`uv run ruff check src tests` / `ruff format --check` / `uv run mypy src/regwatch` 均通过。独立评审 0 Critical、3 Major 已修复；页面曾因回写 widget key 崩溃、长报告下载难找，已改 Chat 布局并补测。

**Journey log** — ① 不引入 OpenRouter 特判，任意 OpenAI 兼容端点即可。② 意图 JSON 提示词不能用 `str.format`（花括号），改占位符替换。③ `violations.py` 反向依赖 `models`，归一在 `QaIntent` 内延迟导入。④ 多关键词 OR 检索不能用单 keyword overview 计数。⑤ 云端公开页强制 BYOK。⑥ 带 `key` 的控件不可在同轮再写 `session_state`。⑦ 长报告页应用 `st.chat_message`/`chat_input`，下载放答案顶栏。

## [S1] Problem

用户希望在网页端用自然语言提问监管合规问题，由大模型自动检索案例库作答；并能按主题生成自由格式专题报告，而不必先手工筛选再读统计页。现有能力只有「筛选 + 统计 + 固定结构季度报告」，缺少对话式入口。

## [S2] Design

### 产品行为

新增公开导航页「智能问答」，布局参考主流 Chat 产品（用户气泡 + 助手全文 + 底部输入框）：

1. **问答模式**：用户输入问题 → 模型产出结构化检索意图 → 后端按意图查库 → 模型基于证据作答，答案中引用案例编号/标题/来源链接。
2. **报告模式**：用户描述专题主题与范围 → 同样走意图检索 → 模型撰写**自由格式** Markdown 专题报告；助手消息顶部提供「下载报告 .md」。
3. **会话历史**：仅存 `st.session_state`，以 `st.chat_message` 消息流展示；支持清空；每条助手回复旁可下载。

### 凭证与权限

| 角色 | 行为 |
|------|------|
| 非管理员（只读/云端公开） | **必须**在会话内手填 OpenAI 兼容凭证（base_url / model / api_key）；不得使用全局配置模型 |
| 管理员 / 本地未开只读 | 默认使用任务绑定 `qa`（回退首条全局模型）；可切换为「使用我的 API」手填 |

手填凭证只存在 `session_state`，不写入 `config.json`、不落盘、日志脱敏。页面挂在 `public_items`，不因只读模式消失。

### 检索管线（意图 → 筛选 → 作答）

1. **意图解析**（一次 chat，JSON 输出）：从用户文本提取
   - `mode`: `qa` | `report`
   - `keywords` / `datasets` / `violations` / `bureaus` / `date_from` / `date_to` / `limit`
   - `fund_related_only`（CSRC 侧）
   - `notes`：模型对检索意图的简短说明
2. **检索**：构造 `CaseQuery` 调用 `AnalysisService.rows` / `overview`；关键词沿用现有 LIKE；违规类型走 `case_violations`。取 TOP-N（默认 15，上限 40）案例行（含摘要字段，不加载正文）。
3. **证据打包**：每条案例输出编号、数据集、日期、当事人、违规类型、处罚、摘要、来源 URL；同时附 overview 关键计数（总数、违规 TOP、日期范围）。
4. **作答 / 写报告**：第二次 chat，system 约束「仅依据证据、标注案例编号、证据不足时明说」；报告模式额外要求完整 Markdown 结构（标题、摘要、发现、代表案例、局限说明）。

失败路径：意图 JSON 解析失败 → 回退为纯关键词检索；LLM 调用失败 → 页面展示可重试错误（密钥已脱敏）；检索 0 条 → 提示放宽条件，仍可让模型基于「无命中」说明（不编造案例）。

### 模块契约

| 模块 | 契约 |
|------|------|
| `settings.TASK_KINDS` | 追加 `qa`，标签「智能问答」 |
| `prompts.py` | 新增 `QA_INTENT_PROMPT` / `QA_ANSWER_SYSTEM` / `QA_REPORT_SYSTEM` |
| `domain` | 新增不可变 `QaIntent`（fields + `to_case_query()` + `from_llm_dict()` 容错；非法日期丢弃；未知违规类型降级为关键词） |
| `services/qa.py` | `QaService(llm, analysis)`：`parse_intent` / `retrieve` / `answer` / `report` / `ask`（组合） |
| `services/container.py` | `Services.qa`；依赖现有 `analysis` + `llm` |
| `llm/client.py` | 不改协议；网页用手填 `ModelProfile` 直接 `LLMClient(profile)` |
| `web/nav.py` | `NavItem("qa", "智能问答", "💬", "qa")`，**非** admin_only；`public_items` 自动包含 |
| `web/views/qa.py` | Chat 式布局：工具条 + 凭证折叠区 + `chat_message` 消息流 + `chat_input` + 下载 |
| `web/views/__init__.py` | 导出 `qa` |

不改数据库 schema；不新增后台 Job；不接入向量库。

### 错误与边界

- 意图 `limit` 裁剪到 `[1, 40]`；非法日期丢弃（仅接受可解析的 `YYYY-MM-DD`）。
- `violations` 经 `normalize_violations` 归一；未知类型降级为关键词。
- 非管理员且未填 Key → 禁用提交并提示。
- 全局模型未配置时管理员也可手填。
- 云端只读下不展示、不写入全局 Key；测试连接类操作仅管理员且走已有 settings 页。

## [S3] Out of Scope

- 向量嵌入 / FTS5 索引改造
- 多轮工具调用（function calling 循环）
- 问答历史落库或任务中心异步长报告 Job
- 固定章节季度报告的网页入口（仍走 CLI/任务中心）
- 流式输出（现有 LLMClient 无 stream）
- 对 OpenRouter 的特殊适配（任意 OpenAI 兼容端点即可）

## Tasks

- [x] T1: 领域意图模型 `QaIntent` + 从容错 dict 构造 — acceptance: 单测覆盖非法/缺失字段与 `to_case_query` 映射（covers: S2 检索管线）
- [x] T2: 提示词三件套写入 `prompts.py` — acceptance: 常量可 import，含 JSON 意图与证据引用约束（covers: S2 检索管线）
- [x] T3: `QaService` 意图解析/检索/作答/报告/ask — acceptance: 用假 LLM + 内存库单测管线与 0 命中路径（covers: S2 检索管线、错误与边界）
- [x] T4: 容器装配 + `TASK_KINDS` 追加 `qa` — acceptance: `get_services().qa` 可用；settings 序列化不破坏旧配置（covers: S2 模块契约）
- [x] T5: 网页视图 + 导航公开项 — acceptance: AppTest 可见页面；非管理员无 Key 禁止提交；管理员默认全局模型；BYOK 不落盘（covers: S2 产品行为、凭证与权限）
- [x] T6: 回归：pytest / ruff / mypy — acceptance: 全绿（covers: S2）
- [x] T7: 独立评审 critical 清零 — acceptance: 评审报告无 critical 或已修复（covers: S2）
