---
feature: overview-monthly-trend
status: delivered
updated: 2026-09-12
branch: main
commits: 
---

# 总览月度趋势与月份密度

## Report

**What was built** — 修复总览看板「案例数量月度趋势」被压成单点、「月份密度」恒为空的缺陷。根因是 `CaseRepository.time_trend` 把 `substr`/`length` 的 `?` 写在 WHERE 之前，却把 length 参数 append 到 `build_where` 参数末尾；网页默认 `Dataset.all()` 生成 `dataset IN (?, ?)` 后绑定错位，period 变成空串且只剩 CSRC 一桶。将受信常量 `length`（7/4）内联进 SQL 后，默认查询恢复 94 个月序列，热力图恢复 year×month 矩阵。

**Verification** — `uv run python -m pytest -q` → 294 passed；`ruff check` / `ruff format --check` / `mypy` 通过；本地 Streamlit 实测趋势 xLen=94、yMax=146，热力图含 2015–2026。独立评审 spec/correctness/consistency 均 PASS，无 critical。

**Journey log** — 空 `CaseQuery()` 不会触发该 bug（无 WHERE 参数时旧绑定碰巧正确），回归必须用 `Dataset.all()` 这类带占位符的查询。浏览器里 2832/1282/640 正好是 CSRC 总数/机构/个人，可作为「参数错位后只剩一桶」的快速对照。占位符顺序必须与 SQL 出现顺序一致；出现在 WHERE 之前的常量不能事后 extend。

## [S1] Problem

总览看板「案例数量月度趋势」几乎空白，只剩 3 个孤立点（Y≈2832/1282/640）；「月份密度」显示「暂无数据」。

根因：`CaseRepository.time_trend` 把 `substr(c.date, 1, ?)` / `length(c.date) >= ?` 的占位符写在 WHERE 之前，却把 `length` 参数 `append` 到 `build_where` 参数末尾。网页默认 `build_query()` 使用 `Dataset.all()`，生成 `dataset IN (?, ?)`，绑定错位后：

- `substr(c.date, 1, 'amac')` → period 空串
- `WHERE dataset IN ('csrc', 7)` → 只剩 CSRC 桶

于是趋势被压成单点，热力图因 period 长度 < 7 判定无数据。

## [S2] Design

- `time_trend` 中 `length`（仅 7 或 4）作为受信常量**内联进 SQL**，不再与 where 占位符抢位。
- 不改 `build_where`、不改网页默认查询；统计页走 Python `compute_time_trend`，本就不受影响。
- 回归：带 `CaseQuery(datasets=Dataset.all())` 的月/年趋势必须返回 `YYYY-MM` / `YYYY` 多桶结果。

## [S3] Out of Scope

- 不补全缺失月份的连续序列（有案月份才出现，属展示增强）。
- 不改 `cache_key` 是否纳入 `db revision`（文档与实现不一致是另一问题）。
- 不改热力图配色 / 趋势图 spline 等视觉参数。

## Tasks

- [x] T1: 回归测试 — acceptance: `test_time_trend_with_dataset_filter_keeps_monthly_periods` 失败于旧实现、通过于修复后 (covers: S1, S2)
- [x] T2: 修复 `time_trend` SQL 参数绑定 — acceptance: 默认网页查询下 overview 趋势为多月 period，月份密度非空 (covers: S2)
- [x] T3: 验证 — acceptance: pytest 相关用例 + ruff + mypy 通过 (covers: S2)
