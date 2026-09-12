---
feature: data-quality-repair
status: delivered
updated: 2026-09-28
branch: main
commits: working-tree
---

# 数据质量修复（无 LLM）

## Report

**What was delivered** — 扩展 `services/data_repair` 与 CSRC 标题抽取：清洗措施尾巴当事人、注销公告/嵌套括号主体、回填 `summaries.punished_entity`、规范 `entity_type`、清空非法处分日；定点修复标题截断与 AMAC 同 URL 正文错配。CLI 仍为 `regwatch db repair [--dry-run]`。

**Verification** — `pytest` 293 passed；`ruff check` / `ruff format` / `mypy` 通过。实跑库修复：CSRC 脏当事人 638、摘要当事人 3732、entity_type 204、无效处分日 6、AMAC 主体 39。

## [S1] Problem

`data/regwatch.db` 中约 4481 例案例已具备正文与多数摘要，但多处系统质量问题影响统计与检索准确性：

1. **AMAC 当事人大量为空**：done 案例中 `cases.punished_entity` 对个人案几乎全空；摘要服务虽从模型拿到 `punished_entity`，却因 `summaries` 表无该列而**静默丢弃**。
2. **CSRC 文书号大量为空**：done 中约 1422 条 `document_number` 为空，标题里常有 `〔YYYY〕N号` 可确定性抽取。
3. **`case_violations` 混入非 canonical 值**（`募集行为类`/`场所`/`未配合自律检查`/`资本金等基本条件缺失）` 等），因 `normalize_violations` 对未命中片段原样保留。
4. **8 条 extract_success=1 但 `violation_type` 为空**，关联表无映射，统计漏计。
5. **部分 CSRC 正文过短**（<200 字），可能是抓取/解析失败，摘要可信度存疑。
6. **公告日 `cases.date` 与处分日 `summaries.punishment_date` 不一致约 1521 条**（AMAC 站点公告日 ≠ 决定落款日），时序分析若混用会失真。
7. **CSRC 当事人混入措施文案**（如「张西湖采取出具警示函措施」）约 600+ 条：三字人名过不了 `len>=4`，回退正则吞掉「采取…措施」。
8. **AMAC 注销公告 / 嵌套括号截断当事人**；少数标题以 `...` 截断；部分 2018 注销公告正文被同 URL 后续决定书覆盖。

约束：**不调用 config 中的模型 API**；在当前 main 工作区继续；可用子代理阅读案例原文做逐条审查。

## [S2] Design

### 代码契约（防再次丢失 + 可重复修复）

| 模块 | 契约 |
|------|------|
| `db/schema.sql` + `migrations` v3 | `summaries` 增加 `punished_entity TEXT NOT NULL DEFAULT ''` |
| `db/repositories/summaries.py` | 读写 `SummaryRecord.punished_entity`；新增 `set_punished_entity` / `set_entity_type` / `set_punishment` / `list_for_field_fill` |
| `db/repositories/cases.py` | `CaseRow.punished_entities` 取 `COALESCE(c.punished_entities, s.punished_entity, c.punished_entity)` |
| `sources/csrc/models.py` | `clean_entity_name`；标题动作词优先截断；人名最短 2 字 |
| `services/data_repair.py` | 脏当事人清洗、注销公告/嵌套括号、摘要当事人回填、entity_type 规范、非法日期清空 |
| CLI | `regwatch db repair [--dry-run]` |

### 确定性规则

1. **AMAC 当事人**：标题括号 / 注销公告 / 未闭合嵌套；截断后缀可覆盖。
2. **CSRC 脏当事人**：`clean_csrc_entity` 去掉「采取/出具/责令…措施」尾巴（可覆盖非空）。
3. **处分日期**：仅填空且 `is_valid_date`；非法值清空不猜日期。
4. **摘要当事人**：从 cases 回填空的 `summaries.punished_entity`。
5. **正文错配**：标题与正文主体明显不符时清空污染摘要并写 `status_note`。

## [S3] Out of Scope

- 对 pending/可疑案例的大规模模型重提取
- `org_type` 全量回填
- 正文自动重抓
- 改变违规分类语义

## Tasks

- [x] T1: schema v3 + summaries.punished_entity 落库 + CaseRow 读取合并
- [x] T2: summarize AMAC 回写 cases.punished_entity
- [x] T3: violations 别名补齐 + canonical 落库过滤未知片段
- [x] T4: services/data_repair + CLI `db repair`（含脏值清洗扩展）
- [x] T5: 子代理审查空处罚 / 「其他」违规 / 空处分日 / 抽样准确性
- [x] T6: 全量 pytest + ruff + mypy
