# 监管处分案例数据集 — Agent 使用指南

本仓库包含两套独立的监管处分案例数据（CSRC 行政监管体系 + AMAC 自律处分体系），
并组织为 **regwatch 统一 Python 包 + 网页看板 + 统一 CLI**。
**阅读本指南后再访问数据或改代码**，可避免盲目遍历、显著节省 token。

---

## 0. 代码结构（先看这里）

```
src/regwatch/
├── domain/           领域层（纯数据，无 IO）
│   ├── enums.py      Dataset / CaseStatus / CaseType / Category / SourceType / JobKind / JobStatus
│   ├── models.py     CaseRecord / SummaryRecord / CaseRow / CaseQuery / TaskRecord / ModelProfile
│   └── violations.py 违规分类体系与防控建议（语义稳定，改动影响历史报告可比性）
├── settings.py       配置：单一库路径、模型条目、任务绑定、并发、旧键自动迁移
├── logging_setup.py  统一日志与任务日志路由
├── clock.py          时间工具
├── db/               数据层：schema.sql / migrations / connection / jsonio / transfer / repositories
├── llm/              OpenAI 兼容客户端 + 输出解析
├── services/         用例层：summarize / analyze / reporting / org_type / jobs + container（组合根）
├── sources/          采集层：http / common / htmlparse / docparse / progress + amac / csrc / amac_monthly / bureaus
├── cli/              命令行（main.py + commands/*）
└── web/              Streamlit（app.py / state.py / components / views/*）
```

**改代码的原则**：

- 功能一律改 `src/regwatch/` 包内模块；
- 依赖方向固定为 `交付层 → 用例层 → 领域层 ← 数据层`，不要反向引用；
- 组合只在 `services/container.py` 发生，服务通过构造函数接收依赖；
- 落盘一律走仓储，**不要直接写文件或裸用 `sqlite3`**。

常用入口：

| 需求 | 用法 |
|------|------|
| 拿到全部服务 | `from regwatch.services import get_services` |
| 载入案例（不含正文） | `services.analysis.rows(CaseQuery(...))` → `List[CaseRow]` |
| 读单条案例正文 | `services.cases.get_with_body(Dataset.AMAC, case_id)` |
| 统计聚合（SQL） | `services.analysis.overview(query)`；完整分析 `analyze_query(query)` |
| 报告 | `services.reporting.build_report(dataset)` + `.save(output, directory)` |
| 摘要提取 | `services.summarization.summarize(dataset, workers=..., on_progress=...)` |
| 任务编排 | `services.jobs.submit(JobKind.SUMMARIZE, params)`；参数规格见 `services.JOB_SPECS` |
| 模型客户端 | `services.llm.client(task="summarize")` |
| 旧布局导入 | `regwatch db import --data-root data` 或 `scripts/migrate_to_sqlite.py` |
| 回填违规类型关联表 | `regwatch db rebuild-violations`（分类体系升级后执行，不调用模型） |
| 确定性数据质量修复 | `regwatch db repair [--dry-run]`（标题回填当事人/文书号、落款处分日、重建关联表；不调用模型） |

---

## 1. 数据集概览

| 数据集 | 监管主体 | 规模 | 内容性质 |
|--------|----------|------|----------|
| **CSRC** | 证监会及派出机构 | 约 2800 例 | 监管措施（measure）+ 行政处罚（penalty） |
| **AMAC** | 中国证券投资基金业协会 | 约 1650 例 | 自律处分（纪律处分） |

（规模随抓取进度增长，以 `regwatch db stats` 为准。）

两者**共用一张 `cases` 表**，用 `dataset` 列区分，差异字段允许为空。

### 1.1 磁盘布局

```
data/regwatch.db             SQLite 主库（gitignore）
data/regwatch.db-wal / -shm  WAL 伴生文件
config.example.json          配置样例（入库）
config.json                  本地配置（含密钥，gitignore，首次运行自动生成）
```

核心表：`cases`（统一案例，主键 `(dataset, case_id)`）、`case_bodies`（`raw_text` 单独存放）、
`summaries`、`case_violations`（违规类型关联表，存 **canonical** 类型）、`tasks` / `task_logs`、
`org_type_cache` / `fetch_state`。

### 1.2 关键字段速查

**`CaseRecord`**：`dataset`、`case_id`、`source_url`、`title`、`date`、`status`、`status_note`、
`fetch_time`、`error`、`pdf_url`；
AMAC 专属 `category`(scfjg/scfry)、`org_type`、`punished_entity`、`source_type`、`ocr_success`；
CSRC 专属 `case_type`(penalty/measure)、`bureau`、`document_number`、`punished_entities`、
`is_fund_related`（三态，`None` 表示不适用）、`fund_evidence`、`doc_url`。

**`SummaryRecord`**：`entity_type`、`punished_entity`、`violation_type`（原始多值串）、`punishment`、
`punishment_date`、`involved_fund`、`violation_summary`、`legal_basis`、`penalty_amount`、`market_ban`、
`extract_success`、`error`、`extract_time`、`llm_provider`、`llm_model`。
`violation_types` 属性返回拆分后的元组。

**`CaseRow`**：案例 + 摘要字段的扁平组合，**不含正文**；另含 `has_body`。

### 1.3 访问策略（省 token 的正确姿势）

1. 要总览/统计 → `services.analysis.overview(query)`（纯 SQL 聚合，不载入行）。
2. 只要「基金相关且已提取」→ `CaseQuery(statuses=(CaseStatus.DONE,), fund_related_only=True)`。
3. 要全文 → `services.cases.get_body(...)`，按需读取，**不要批量载入 `raw_text`**。
4. 定位数据 → 一律走仓储方法，不要直接写 SQL 或遍历目录。

---

## 2. 违规类型分类体系

**单一权威来源**：`src/regwatch/domain/violations.py` 的 `_VIOLATION_DEFS`（18 类 canonical，
**改动会影响历史报告可比性，需同步本节**）。要点：

- 每个类型带 `aliases`（模型常输出的子项 / 异名，如「适当性管理不到位」→ 违规募集、
  「操纵证券价格」→ 操纵市场）与 `datasets`（适用数据集）；
  两套提取提示词的候选清单由 `violation_candidates(dataset)` 派生，**不再手工维护两份列表**，
  回归测试 `TestPromptCoverage` 保证提示词与清单不脱节；
- AMAC / CSRC 同义措辞归一到同一 canonical：「未尽勤勉尽责义务」= CSRC「未勤勉尽责」、
  「未配合自律管理」= CSRC「未配合监管」；展示时用 `violation_label(name, dataset)` 还原文书措辞；
- `case_violations` 落库存 canonical（`canonical_violations()` 归一，**丢弃未知碎片**，
  不把提示词分组标题等脏值写入关联表），`summaries.violation_type` 保留模型原文；
  归一还兼容中点 `·` 与「类型：子项」冒号写法；
- 分组速览（canonical 名）：
  - **募集行为类**：违规募集、未按规定备案、登记信息失实
  - **投资运作类**：违规投资运作、挪用基金财产、违规关联交易、未按规定托管、未按规定估值
  - **管理人义务类**：非专业化运营、未尽勤勉尽责义务
  - **内部治理类**：内控缺失、人员与场所违规、未持续符合登记条件
  - **信息披露与自律类**：信息披露违规、未配合自律管理
  - **CSRC 独有**：操纵市场、内幕交易
  - **其他**

分类要点：`非专业化运营` 专指兼营无关业务；`未尽勤勉尽责义务` 专指管理职责缺失；
`内控缺失` 专指内控制度本身不健全；`登记信息失实` 与 `未按规定备案` 相互区分。

> 升级分类体系（改别名 / 合并类型）后执行 `regwatch db rebuild-violations` 回填关联表；
> 提示词候选变化需重跑摘要时用 `regwatch summarize --dataset csrc --redo`（会消耗模型额度）。

---

## 3. 关键注意事项（数据质量坑，务必遵守）

1. **CSRC 标题粗筛会误判**：采集器靠关键词匹配，仅因法规名含「基金」即可能标记相关；
   摘要阶段的 `status == SKIPPED` 才是模型精判结果，可信。
2. **AMAC 的 `date`/`title` 可能与 `raw_text` 不符**（网站用同一 URL 更新内容），
   分析时间与当事人时以正文落款为准；`cases.date` 是公告日，`summaries.punishment_date`
   是处分决定日，二者大量不一致属正常，时序分析请明确选用哪一个。
   少数 2018 注销公告正文已被后续决定书覆盖，摘要字段已清空并在 `status_note` 标注。
3. **当事人字段**：AMAC 在 `cases.punished_entity`，CSRC 在 `cases.punished_entities`；
   模型提取的当事人写入 `summaries.punished_entity`，`CaseRow` 会合并展示。
   缺失/脏值用 `regwatch db repair` 从标题/正文确定性回填与清洗
   （会去掉 CSRC「采取出具警示函措施」等尾巴，并规范 `entity_type`）。
4. **CSRC `case_type` 轻重有别**：`measure`（警示函、责令改正等）轻于 `penalty`（罚款、市场禁入）。
5. **`status == SKIPPED` 的案例没有摘要**，理由记录在 `cases.status_note`。
6. **CSRC `bureau="HQ"`** 表示证监会总部，其余为派出机构（见 `sources/bureaus.py`）。
7. **抓取状态不再依赖索引文件**：是否已抓过 = 库中是否存在且已有正文，断点续传天然成立。
8. **非法 `punishment_date`**（如 `2024-08-74`）会被 `db repair` 清空，不会猜真实日期。

---

## 4. 典型任务操作模板

| 任务 | 做法 |
|------|------|
| 某时段违规类型分布 | `analysis.overview(CaseQuery(date_from=..., date_to=...))["violations"]` |
| 分析某局（如 Beijing） | `CaseQuery(datasets=(Dataset.CSRC,), bureaus=("Beijing",))` |
| 某违规类型全部案例 | `CaseQuery(violations=("挪用基金财产",))`（多类型为「或」语义） |
| 生成季度报告 | `regwatch report --dataset amac --start ... --end ... --llm` |
| 补充/重跑摘要 | `regwatch summarize --dataset csrc --workers 5`（`--redo` 连同已完成案例一起重跑，用于提示词升级后回填） |
| 补机构类型 | `regwatch org-type` / `regwatch org-type --interactive` |
| 撰写新分析 | 参考 `data/*/reports/*.md` 结构与 `services/reporting.py` 的渲染函数 |

网页端（`regwatch web`）与 CLI 完全等价，演示或非技术用户优先用网页。

---

## 5. Token 节省清单

- ✅ 先 `services.analysis.rows(query)` 拿 `CaseRow`，需要正文再 `get_body()` 单条读取
- ✅ 统计聚合用 `services.analysis`，不要逐个读摘要
- ✅ 大文件用 `limit`/`offset`；用 glob 定位而不是遍历目录
- ✅ 改功能只改 `src/regwatch/`
- ❌ 不要遍历 `data/` 目录读原文
- ❌ 不要把 `raw_text` 批量载入内存
- ❌ 不要信任 AMAC 的 `date`/`title` 做时序分析

---

## 6. 测试与验证

```powershell
uv sync --extra web --extra dev                   # 推荐安装方式
uv run python -m pytest                           # 全量测试（离线、内存库）
uv run ruff check src tests                       # lint
uv run ruff format --check src tests              # 格式
uv run mypy src/regwatch                          # 类型检查
```

改代码前后的最低验收：pytest 全绿 + `ruff check` 通过 + `mypy` 通过。
