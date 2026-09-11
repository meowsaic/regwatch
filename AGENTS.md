# 监管处分案例数据集 — Agent 使用指南

本仓库包含两套独立的监管处分案例数据（CSRC 行政监管体系 + AMAC 自律处分体系），
并已重构为 **regwatch 统一 Python 包 + 网页看板 + 统一 CLI** 的体系。
**阅读本指南后再访问数据或改代码**，可避免盲目遍历、显著节省 token。

---

## 0. 代码结构（先看这里）

```
regwatch/                核心包
├── config.py            配置：数据路径 / 模型条目 / 任务绑定 / 并发（config.json，不入库）
├── llm.py               统一 OpenAI 兼容客户端（参数自动降级、连通性测试）
├── datamodels.py        AmacCase/AmacSummary/CsrcCase/CsrcSummary/TaskRecord
├── storage.py           数据访问层：CaseRow 统一视图、索引读写、目录清单缓存
├── prompts.py           AMAC/CSRC 提取提示词 + 违规分类体系 + 针对性合规建议
├── summarize.py         结构化摘要提取（两阶段扫描、并发、断点续传、非基金跳过）
├── org_type.py          机构登记类型查询与回填
├── analyze.py           统计聚合（违规/处罚/法规/对比/趋势）
├── report.py            报告渲染（md / html / json）
├── jobs.py              后台任务编排（网页与 CLI 共用）
├── cli.py               统一命令行（typer）
└── sources/             采集子包：amac.py / csrc.py / amac_monthly.py / csrc_bureaus.py
web/                     Streamlit 网页端（app.py + views/ 五页 + components/）
tests/                   纯本地单元测试 + AppTest 网页冒烟测试 + clamp_page 等组件单测
pyproject.toml           打包与工具链（uv / ruff / mypy）；权威依赖声明
AMAC/*.py、CSRC/*.py      旧脚本已改为「薄封装」，仅转发到 regwatch 包
```

**改代码的原则**：功能一律改 `regwatch/` 包内模块；`AMAC/`、`CSRC/`、`0-crawl_amac_cases_monthly.py`
下的旧脚本只是兼容入口，不要在里面加新逻辑。

常用入口：

| 需求 | 用法 |
|------|------|
| 载入案例清单（不含正文） | `from regwatch.storage import build_catalog, build_all_catalogs` → `List[CaseRow]` |
| 读单条案例正文 | `regwatch.storage.read_case(dataset, case_id, bureau=..., case_type=..., category=...)` |
| 统计聚合 | `from regwatch.analyze import analyze` → `AnalysisResult`（`to_json_payload()` 可直接 JSON 化） |
| 报告 | `from regwatch.report import build_report, save_report_files` |
| 摘要提取 | `from regwatch.summarize import summarize`（`summarize_one` 处理单条） |
| 任务编排 | `from regwatch.jobs import run_task`（kind：fetch_amac / fetch_csrc / fetch_monthly / summarize / report / org_type） |
| 模型调用 | `from regwatch.llm import get_llm` → `client.chat_text(...)` / `client.vision(url, prompt)` |

---

## 1. 数据集概览

| 数据集 | 监管主体 | 目录 | 案例规模 | 内容性质 |
|--------|----------|------|----------|----------|
| **CSRC** | 证监会及 36 家派出机构 | `CSRC/` | 千级 | 行政监管措施 + 行政处罚 |
| **AMAC** | 中国证券投资基金业协会 | `AMAC/` | 百级 | 自律处分（纪律处分） |

两者**独立**，目录结构不同、字段不同，但 `regwatch.storage` 已把它们归一成统一的 `CaseRow` 视图。

### 1.1 磁盘布局（保持与历史数据兼容，未做迁移）

```
AMAC/
├── cases/                          # 案例原文
│   ├── _index.json                 # 抓取索引（regwatch.storage.AmacIndex）
│   ├── _org_type_cache.json        # 机构类型缓存（OrgTypeCache）
│   ├── _org_type_manual.json       # 需人工补全清单
│   ├── institution/{case_id}.json  # scfjg 受处分机构
│   └── personnel/{case_id}.json    # scfry 受处分人员
├── summaries/{case_id}_summary.json  # 摘要（扁平）+ _summary_index.json
└── reports/                        # 报告产物

CSRC/
├── cases/{Bureau}/{measure|penalty}/{case_id}.json
├── summaries/{Bureau}/{case_type}/{case_id}_summary.json  # 分目录
└── reports/                        # 报告产物（新）
```

命名：CSRC 为 `{YYYYMMDD}_c{cid}`（c 开头），AMAC 为纯数字或 `P{20位}`。

### 1.2 关键字段速查

**案例原文**（AMAC：`raw_text`/`ocr_success`/`org_type`/`punished_entity`；
CSRC：`raw_text`/`is_fund_related`/`fund_evidence`/`document_number`/`punished_entities`/`case_type`/`bureau`）

**摘要**（AMAC 17 字段、CSRC 24 字段，字段名见 `regwatch/datamodels.py`，两边一致的部分为
`violation_type`、`punishment`、`involved_fund`、`violation_summary`、`legal_basis`、`entity_type`）：

- CSRC 摘要**不含 raw_text**，正文需回 `cases/` 目录读；CSRC 摘要独有
  `penalty_amount`、`market_ban`、`llm_provider`、`llm_model`。
- AMAC 摘要独有 `punishment_date`、`org_type`。

### 1.3 访问策略（省 token 的正确姿势）

1. **要总览/统计** → `build_catalog()` 拿 `CaseRow`（不含正文），或直接 `regwatch.analyze.analyze(rows)`；
   网页端用 `web/components/data.py` 里的缓存封装。
2. **只想要"基金相关且已提取"的案例** → 过滤 `CaseRow.status == "done"`；
   CSRC 的 `status == "skipped"` 表示模型精判非基金相关（无摘要文件，`note` 存判定理由）。
3. **要全文** → `read_case(...)` 或读 `CaseRow.case_file` 指向的 JSON，按需读取，**不要批量载入 raw_text**。
4. **批量聚合某字段** → 基于 `CaseRow`（已拆好 `violation_types` 列表）统计，
   或 `grep` 摘要目录 + `output_mode=count`；不要逐个 Read 大 JSON。
5. **定位文件** → `resolve_case_path()`；不要 `LS` 遍历大目录（cases 下数千文件）。

---

## 2. 违规类型分类体系

统一维护在 `regwatch/prompts.py`（**改动会影响历史报告可比性，需同步本节**），
多值字段用顿号 `、` 分隔，`regwatch.storage.split_multi_value` 负责拆分：

- **募集行为类**：违规募集（向不合格投资者募集、承诺保本、适当性缺失、违规外包销售）、未按规定备案、登记信息失实
- **投资运作类**：违规投资运作、挪用基金财产、违规关联交易、未按规定托管、未按规定估值
- **管理人义务类**：非专业化运营、未尽勤勉尽责义务（CSRC 侧提示词写作「未勤勉尽责」）
- **内部治理类**：内控缺失、人员与场所违规、未持续符合登记条件
- **信息披露与自律类**：信息披露违规、未配合自律管理（CSRC 为「未配合监管」）
- **CSRC 独有**：操纵市场、内幕交易
- **其他**

分类要点：`非专业化运营` 专指兼营无关业务（民间借贷、担保等）；`未尽勤勉尽责义务` 专指管理职责缺失；
`内控缺失` 专指内控制度本身不健全；`登记信息失实` 与 `未按规定备案` 相互区分。

## 3. 关键注意事项（数据质量坑，务必遵守）

1. **CSRC 标题粗筛会误判**：fetcher 靠关键词匹配，仅因法规名含"基金"即标记相关；
   摘要阶段的 `status=="skipped"` 才是 LLM 精判结果，可信。
2. **AMAC 的 `date`/`title` 可能与 `raw_text` 不符**（网站用同一 URL 更新内容）。
   分析时间与当事人时以 `raw_text` 正文落款为准，`date`/`title` 仅作参考。
3. **CSRC `case_type` 轻重有别**：`measure`（警示函、责令改正等）轻于 `penalty`（罚款、市场禁入）。
4. **`status=="skipped"` 的案例没有摘要文件**，只存在于 `_summary_index.json`，不要去找文件。
5. **CSRC `bureau="HQ"`** 表示证监会总部，其余 36 个为派出机构。
6. **AMAC `_summary_index.json` 存在与摘要文件不一致的悬挂条目**（约 1 条），
   `build_catalog` 以摘要文件为准构建清单，因此不受影响。

## 4. 典型任务操作模板

| 任务 | 做法 |
|------|------|
| 统计某时段违规类型分布 | `build_catalog()` → `filter_rows(date_from=..., date_to=...)` → `analyze(rows)` |
| 分析某局（如安徽） | `filter_rows(bureaus=["Anhui"])`，详情用 `read_case()` |
| 找某违规类型全部案例 | `filter_rows(violation_types=["挪用基金财产"])`（多类型为「或」语义） |
| 生成季度报告 | CLI：`python -m regwatch.cli report --dataset amac --start ... --end ... --llm` |
| 补充/重跑摘要 | `python -m regwatch.cli summarize --dataset csrc --workers 5` |
| 撰写新分析 | 参考 `AMAC/reports/*.md` 的章节结构与 `regwatch/report.py` 的渲染函数 |

网页端（`python run_web.py`）与 CLI 完全等价，演示或非技术用户优先用网页。

## 5. Token 节省清单

- ✅ 先 `build_catalog()` 拿 `CaseRow`，需要正文再 `read_case()` 单条读取
- ✅ 统计聚合用 `regwatch.analyze`，不要逐个 Read 摘要 JSON
- ✅ 大文件用 `limit`/`offset`；`Glob` 定位而不是 `LS` 遍历
- ✅ 改功能只改 `regwatch/`，旧脚本薄封装不要动逻辑
- ❌ 不要遍历 `cases/` 目录读原文（1.6GB）
- ❌ 不要把 `raw_text` 批量载入内存
- ❌ 不要信任 AMAC 的 `date`/`title` 做时序分析

## 6. 测试与验证

```powershell
uv sync --extra web --extra dev                   # 推荐安装方式
uv run python -m unittest discover -s tests -t .  # 全量单测，全部本地、不发网络请求
uv run ruff check regwatch tests                  # lint
uv run mypy regwatch                              # 类型检查（核心包）
$env:REGWATCH_LIVE_TEST = "1"; uv run python -m unittest tests.test_live -v   # 可选实网测试
```

测试覆盖：配置读写、模型客户端（假客户端 + 参数降级）、数据层路径与索引、
37 局配置、采集纯函数、机构类型解析、摘要流程（成功/跳过/失败）、任务编排（取消/互斥/进度）、
统计与报告渲染、网页五页面 AppTest 渲染。

改代码前后的最低验收：上述 unittest 全绿 + `ruff check` 通过。
