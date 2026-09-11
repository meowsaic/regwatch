# 监管处分案例数据集 — Agent 使用指南

本仓库包含两套独立的监管处分案例数据，供分析、统计、撰写报告之用。**阅读本指南后再访问数据**，可避免盲目遍历、显著节省 token。

---

## 1. 数据集概览

| 数据集 | 监管主体 | 目录 | 案例规模 | 内容性质 |
|--------|----------|------|----------|----------|
| **CSRC** | 中国证监会及 36 家派出机构（证监局） | `CSRC/` | 千级 | 行政监管措施 + 行政处罚 |
| **AMAC** | 中国证券投资基金业协会 | `AMAC/` | 百级 | 自律处分（纪律处分） |

两者**独立**，目录结构不同，字段不同，访问策略也不同（见下文）。

---

## 2. CSRC 数据集

### 2.1 目录结构

```
CSRC/
├── cases/                          # 案例原文（fetcher 产出）
│   ├── _index.json                 # 抓取索引：按 {bureau}/{case_type} 分组，含每条 link 的 status
│   ├── Anhui/
│   │   ├── measure/                # 行政监管措施（责令改正、警示函等，较轻）
│   │   │   └── {YYYYMMDD}_c{cid}.json
│   │   └── penalty/                # 行政处罚（罚款、市场禁入等，较重）
│   │       └── {YYYYMMDD}_c{cid}.json
│   ├── Beijing/measure/  penalty/
│   ├── HQ/measure/  penalty/       # HQ = 证监会总部（非地方局）
│   └── ...（共 37 个 bureau 目录）
├── summaries/                      # 结构化摘要（summarizer 产出）
│   ├── _summary_index.json         # ★ 总索引：每个 case_id 的 status / summary_file / reason
│   └── {Bureau}/{measure|penalty}/{case_id}_summary.json
└── 2-case_summarizer.py            # 生成 summary 的脚本
```

**文件命名**：`{YYYYMMDD}_c{cid}.json`，其中日期为发文日期，`c{cid}` 为 CSRC 网站content id。summary 文件名加 `_summary` 后缀。

### 2.2 字段速查

#### 案例原文 `cases/{Bureau}/{case_type}/{case_id}.json`
| 字段 | 说明 |
|------|------|
| `case_id` | `{日期}_c{cid}` |
| `bureau` | 局名（Anhui / Beijing / HQ / ...） |
| `case_type` | `measure`（措施）/ `penalty`（处罚） |
| `title`, `date`, `source_url` | 标题、发文日期、原文URL |
| `document_number` | 文号 |
| `punished_entities` | 当事人（原文级别，未细分） |
| `raw_text` | ★ 决定书全文（最重要，千字级） |
| `is_fund_related`, `fund_evidence` | fetcher 粗筛标志，**会误判**（见 §5） |
| `pdf_url` | 附件 PDF 链接（若有） |

#### 结构化摘要 `summaries/{Bureau}/{case_type}/{case_id}_summary.json`
**summary 是原文字段的超集**：继承原文字段（含 `raw_text`），并新增以下 LLM 提取字段。

| 新增字段 | 说明 |
|----------|------|
| `entity_type` | `机构` / `个人` |
| `violation_type` | 违规类型（多值用顿号分隔，如 `违规募集、内控缺失`） |
| `punishment` | 处罚/措施内容 |
| `involved_fund` | 涉及基金产品 |
| `violation_summary` | 违规摘要（LLM 生成，百字级） |
| `legal_basis` | 法律依据 |
| `penalty_amount` | 罚款金额 |
| `market_ban` | 市场禁入情况 |
| `extract_success` | 提取是否成功 |
| `llm_provider`, `llm_model` | 使用的 LLM |

#### 总索引 `summaries/_summary_index.json`
```json
{
  "cases": {
    "{case_id}": {
      "status": "done | skipped | failed",   // skipped = LLM判定非基金相关，无 summary 文件
      "summary_file": "{Bureau}/{case_type}/{case_id}_summary.json",  // skipped 时无此字段
      "reason": "...",                        // skipped/failed 时的理由
      "extract_time": "..."
    }
  }
}
```

### 2.3 CSRC 高效访问策略

**核心原则：summary 是原文超集，优先读 summary；先索引后文件。**

1. **要总览/统计** → 只读 `CSRC/summaries/_summary_index.json`，按 `status` 聚合，不碰案例文件。
2. **要分析某案例结构化信息** → 直接读对应 `_summary.json`，**跳过 `raw_text` 字段**（用 Read 的 offset/limit 或读后人工忽略）。
3. **要全文细节** → 读 `_summary.json` 的 `raw_text`（CSRC summary 含原文，无需回 cases 目录）。
4. **定位文件** → 用 `Glob` 匹配 `CSRC/summaries/{Bureau}/{case_type}/*.json`，**不要 LS 大目录**（cases 下有数千文件）。
5. **批量聚合某字段**（如统计 violation_type 分布）→ 用 `Grep` 在 summaries 目录搜正则 + `output_mode=count`，或写脚本，**不要逐个 Read**。
6. **只想要"基金相关且成功"的案例** → 从 `_summary_index.json` 筛 `status=="done"`（`skipped` 已被 LLM 判定为非基金相关）。

---

## 3. AMAC 数据集

### 3.1 目录结构

```
AMAC/
├── cases/                          # 案例原文
│   ├── _index.json                 # 抓取索引：按 Institution/Personnel 分组，含 link status
│   ├── _org_type_manual.json       # 机构类型手动补全清单（case_id → title/entity/file）
│   ├── _org_type_cache.json        # 机构类型 API 查询缓存
│   ├── institution/                # 受处分机构（私募管理人等）
│   │   └── {YYYYMMDD}_{cid}.json   # cid 为纯数字或 P 开头附件ID
│   └── personnel/                  # 受处分个人
│       └── {YYYYMMDD}_{cid}.json
├── summaries/                      # 结构化摘要（扁平结构，无子目录）
│   └── {case_id}_summary.json
└── reports/                        # 已生成的季度分析报告（.md/.html/.json）
```

**注意**：AMAC summary 是**扁平结构**（直接在 `summaries/` 下），不像 CSRC 按 bureau/case_type 分目录。

### 3.2 字段速查

#### 案例原文 `cases/{institution|personnel}/{case_id}.json`
| 字段 | 说明 |
|------|------|
| `case_id` | `{日期}_{cid}` |
| `category` | `scfjg`（机构）/ `scfry`（人员） |
| `source_type` | `pdf_embedded` 等 |
| `title`, `date`, `source_url` | ⚠ date/title 可能与 raw_text 不符（见 §5） |
| `raw_text` | ★ 决定书全文 |
| `org_type` | 机构登记类型（如 `私募股权、创业投资基金管理人`），仅 institution 有 |
| `punished_entity` | 当事人 |
| `ocr_success`, `pdf_url` | 抓取状态 |

#### 结构化摘要 `summaries/{case_id}_summary.json`
**AMAC summary 不含 raw_text**，是精简结构化版。深挖需回 cases 原文。

| 字段 | 说明 |
|------|------|
| `punished_entity`, `entity_type`, `org_type` | 当事人信息 |
| `violation_type` | 多值顿号分隔 |
| `punishment`, `punishment_date` | 处分内容与日期 |
| `involved_fund` | 涉及基金 |
| `violation_summary` | 违规摘要 |
| `legal_basis` | 法律依据 |
| `extract_success` | 提取是否成功 |

### 3.3 AMAC 高效访问策略

1. **总览** → 读 `AMAC/cases/_index.json`（含每条 link 的 status）。
2. **结构化分析** → 读 `summaries/{case_id}_summary.json`（精简，省 token）。
3. **要全文** → 需回 `cases/{institution|personnel}/{case_id}.json` 读 `raw_text`（summary 没存）。
4. **按机构类型分类** → 用 `org_type` 字段聚合，或参考 `_org_type_manual.json`。
5. **已有报告参考** → `AMAC/reports/` 下有季度报告 .md/.html，可直接复用格式。

---

## 4. 违规类型分类体系

`violation_type` 字段统一使用以下分类（多值用顿号 `、` 分隔）：

- **募集行为类**：违规募集（向不合格投资者募集、承诺保本、适当性缺失、违规外包销售）
- **登记备案类**：未按规定备案、登记信息失实
- **投资运作类**：违规投资运作、挪用基金财产、违规关联交易、未按规定托管、未按规定估值
- **管理人义务类**：非专业化运营、未尽勤勉尽责义务
- **内部治理类**：内控缺失、人员与场所违规、未持续符合登记条件
- **信息披露与自律类**：信息披露违规、未配合自律管理
- **其他**

分类要点：`非专业化运营` 专指兼营无关业务（民间借贷、担保等），不指人员/场所问题；`未尽勤勉尽责义务` 专指管理职责缺失，不指内控制度缺失。

---

## 5. 关键注意事项（数据质量坑）

1. **`is_fund_related` 是粗筛，会误判**：fetcher 靠关键词匹配，仅因法规名含"基金"（如《证券公司和证券投资基金管理公司合规管理办法》）即标记为相关，但实际可能是证券公司营业部的非基金业务。CSRC summary 阶段的 `status=="skipped"` 才是 LLM 精判结果，可信。

2. **AMAC 的 `date`/`title` 可能与 `raw_text` 实际内容不符**：AMAC 网站会用同一 URL 更新内容（如 `20150120_21673` 的 title/date 是 2015 年北京中益汇金，但 raw_text 实际是 2025 年上海长富的处分）。**分析时以 `raw_text` 正文落款日期和当事人为准**，`date`/`title` 仅作参考。

3. **CSRC `case_type` 轻重有别**：`measure`（行政监管措施，如责令改正、警示函）轻于 `penalty`（行政处罚，如罚款、市场禁入）。统计严重程度时需区分。

4. **`status=="skipped"` 的案例无 summary 文件**：这些是 LLM 判定非基金相关的案例，只在 `_summary_index.json` 中有 `reason` 记录，不要去找 summary 文件。

5. **CSRC `bureau="HQ"`** 表示证监会总部，其余 36 个为派出机构（证监局）。分析地方监管力度时需单独处理 HQ。

6. **文件命名差异**：CSRC 用 `c{cid}`（c开头），AMAC 用纯数字 `{cid}` 或 `P{20位}`（附件ID）。

---

## 6. 典型任务操作模板

### 任务 A：统计某时段违规类型分布
1. 读 `CSRC/summaries/_summary_index.json` → 筛 `status=="done"` 的 case_id 列表
2. 用 Grep 在 `CSRC/summaries/` 搜 `"violation_type":` 提取值并计数（或写脚本遍历）
3. AMAC 同理，但搜 `AMAC/summaries/`

### 任务 B：分析某局（如安徽）的处罚情况
1. `Glob` 匹配 `CSRC/summaries/Anhui/**/*.json`
2. 逐个 Read summary，**只看结构化字段**（entity_type/violation_type/punishment/violation_summary），跳过 raw_text
3. 如需某案例细节，再读其 raw_text

### 任务 C：查找涉及某违规类型（如"挪用基金财产"）的全部案例
1. `Grep` 在 `CSRC/summaries/` 和 `AMAC/summaries/` 搜 `挪用基金财产`，`glob: "*_summary.json"`
2. 对命中的文件，按需 Read 结构化字段

### 任务 D：撰写季度报告
1. 参考 `AMAC/reports/AMAC_季度分析报告_*.md` 的既有格式
2. 按 §4 分类体系组织违规统计
3. 用 `pick_representative_cases` 思路：每个违规类型选 1-2 个 `violation_summary` 最长的代表性案例
4. 时间范围用 `date` 字段过滤

### 任务 E：定位某具体案例全文
1. 已知 case_id → CSRC: `CSRC/summaries/{Bureau}/{case_type}/{case_id}_summary.json`（含 raw_text）；AMAC: 先读 `summaries/{case_id}_summary.json`，要全文再去 `cases/{institution|personnel}/{case_id}.json`
2. 不知 case_id → 用 Grep 搜当事人名称或关键词

---

## 7. Token 节省清单

- ✅ 先索引（`_summary_index.json` / `_index.json`）后文件
- ✅ CSRC 优先读 summary（含原文），不重复读 cases
- ✅ AMAC 结构化分析只读 summary，全文才回 cases
- ✅ Read 大文件用 `limit`/`offset`
- ✅ 用 `Glob` 定位，不用 `LS` 遍历大目录（cases 下数千文件）
- ✅ 批量统计用 `Grep` + `output_mode=count`，不逐个 Read
- ✅ 分析结构化字段时主动忽略 `raw_text`（千字级，最费 token）
- ❌ 不要遍历整个 `cases/` 目录读原文
- ❌ 不要对 skipped 案例去找 summary 文件
- ❌ 不要信任 AMAC 的 `date`/`title` 做时序分析，以 raw_text 落款为准
