---
feature: structure-modernize
status: designed
updated: 2026-09-11
branch: structure-modernize
commits: cd45c60..HEAD
---

# 仓库结构现代化重构

## Report

（实现完成后填写）

## [S1] Problem

仓库已完成「统一 regwatch 包 + Streamlit + CLI」的功能整合，但根目录仍是草稿形态：

1. **代码包不在 src 布局**：`regwatch/`、`web/`、`tests/`、数据目录、旧脚本全部平铺在项目根。
2. **数据与代码混在 AMAC/、CSRC/**：这两个目录同时承载案例 JSON（gitignore）与仍被 git 跟踪的薄封装旧脚本。
3. **早期入口残留**：根目录 `0-crawl_amac_cases_monthly.py`、`run_web.py`、`AMAC/*.py`、`CSRC/*.py` 仅为转发到 CLI/包，增加认知噪音。
4. **1.6GB PDF 落在根目录** `AMAC_Discipline_PDFs/`（用户稍后人工外迁，本次不动内容）。
5. **历史专题报告与脚本** 混在 `专项分析报告/`，与产品代码边界不清。
6. **web 靠 sys.path 注入** 才能 import，未纳入可安装包。

用户决策（Grill 已确认）：

- 强度：全面现代化（src 布局 + 数据迁 `data/` + 删旧脚本 + web 收编）
- 数据：非 PDF 数据迁到 `data/`；PDF 留原地由用户人工外迁
- 残留：旧脚本删除；专项分析报告归档
- 工作区：隔离 clone 分支 `structure-modernize`（环境阻止 `git worktree add`）

## [S2] Design

### S2.1 目标布局

```
.
├── src/
│   └── regwatch/                 # 可安装核心包
│       ├── config.py / llm.py / ... / cli.py / jobs.py
│       ├── sources/
│       └── web/                  # Streamlit 作为子包
│           ├── app.py
│           ├── views/
│           └── components/
├── data/                         # 运行时数据（gitignore；可整体搬迁）
│   ├── amac/{cases,summaries,reports}/
│   └── csrc/{cases,summaries,reports}/
├── docs/
│   ├── compose/spec/
│   └── archive/专项分析报告/      # 历史专题报告与分析脚本
├── scripts/
│   └── migrate_layout.py         # 一次性磁盘布局迁移
├── tests/
├── .streamlit/config.toml        # 项目级 Streamlit 配置
├── config.example.json
├── config.json                   # 本地，不入库
├── pyproject.toml
├── README.md
└── AGENTS.md
```

**保留不迁**：`AMAC_Discipline_PDFs/`（用户人工外迁）；磁盘上的旧 `AMAC/`、`CSRC/` 在迁移脚本成功后仅剩 PDF 相关或空壳，代码侧不再引用其脚本。

### S2.2 打包

- hatchling `packages = ["src/regwatch"]`（src 布局）。
- 入口不变：`regwatch = "regwatch.cli:app"`。
- `ruff.src = ["src", "tests"]`；mypy `packages = ["regwatch"]` 且 `mypy_path = "src"`。
- `uv sync` 可编辑安装后，`import regwatch` / `import regwatch.web` 均可用。

### S2.3 项目根解析

`PROJECT_ROOT` 不再假定包位于根下一层。改为从包文件向上查找同时存在 `pyproject.toml` 的目录：

```python
def _find_project_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start.parent  # 安装后的兜底：工作目录语义由 config 覆盖
```

`config.json` / `config.example.json` 仍相对 `PROJECT_ROOT` 解析。

### S2.4 默认数据根

| key | 旧 | 新 |
|-----|----|----|
| amac_cases | AMAC/cases | data/amac/cases |
| amac_summaries | AMAC/summaries | data/amac/summaries |
| amac_reports | AMAC/reports | data/amac/reports |
| csrc_cases | CSRC/cases | data/csrc/cases |
| csrc_summaries | CSRC/summaries | data/csrc/summaries |
| csrc_reports | CSRC/reports | data/csrc/reports |

`config.example.json` 同步。用户已有 `config.json` 中若仍写旧相对路径，迁移脚本负责改写；解析逻辑不自动改写（避免静默搬家）。

`AMAC_Discipline_PDFs` 默认仍为 `PROJECT_ROOT / "AMAC_Discipline_PDFs"`（用户外迁后可在后续再加配置项；本次 Out of Scope）。

### S2.5 web 收编

- `web/` → `src/regwatch/web/`，模块内 import 改为 `regwatch.web.components` / `regwatch.web.views`（若 Streamlit page 仍需文件路径，用包资源定位）。
- 删除各文件的 `sys.path` 注入。
- `cli.web` 通过 `importlib` / `regwatch.web.__file__` 定位 `app.py`。
- 删除根目录 `run_web.py`。
- Streamlit 配置移到项目根 `.streamlit/config.toml`（Streamlit 从 cwd 发现）。

### S2.6 残留清理

删除（git 跟踪的薄封装，实现已在包内）：

- `0-crawl_amac_cases_monthly.py`
- `run_web.py`
- `AMAC/1-case_fetcher.py`、`1.2-backfill_org_type.py`、`2-case_summarizer.py`、`3-report_generator.py`
- `CSRC/1-case_fetcher.py`、`2-case_summarizer.py`、`bureaus.py`、`test_live.py`、`test_smoke.py`

归档：

- `专项分析报告/` → `docs/archive/专项分析报告/`（`git mv` 保留历史；二进制仍被 gitignore）

可选清理（不阻塞）：根下 `__pycache__`、`.codebuddy` 本地计划不入库内容保持 ignore；不删除用户本地 `config.json`。

### S2.7 迁移脚本

`scripts/migrate_layout.py`（幂等、可 `--dry-run`）：

1. 若存在旧 `AMAC/cases` 等且目标不存在 → `shutil.move` 到 `data/amac/cases`。
2. 若目标已存在且源也存在 → 报告冲突，不覆盖（除非 `--force-merge` 不在首版，直接 fail 并提示人工处理）。
3. 更新 `config.json` 的 `data_roots` 旧相对路径为新默认。
4. 创建空的 `data/amac/*`、`data/csrc/*` 骨架。
5. 不触碰 `AMAC_Discipline_PDFs/`。

### S2.8 文档与 ignore

- `.gitignore`：`data/`、`AMAC_Discipline_PDFs/`、`docs/archive/**/*.json`；保留二进制排除。
- README / AGENTS：目录树、数据路径、启动命令、访问策略全部改为新布局；删除「旧脚本薄封装」表述。

### S2.9 测试与错误行为

- 现有单测改为从 `src` 导入（editable 安装即可，通常无需改 import 路径）。
- `tests/test_web.py`：去掉 `sys.path` 与 `web/` 拼接，改为 `from regwatch.web...` 与包内 `app.py` 路径。
- `tests/test_config.py`：默认 data_roots 断言改为 `PROJECT_ROOT / "data/amac/cases"` 等。
- 错误行为：配置缺失、路径不存在仍由现有 ConfigError / 空态处理；迁移脚本冲突时非零退出。

### S2.10 验收命令

```powershell
uv sync --extra web --extra dev
uv run python -m unittest discover -s tests -t .
uv run ruff check src tests
uv run mypy regwatch
uv run regwatch --help
uv run python scripts/migrate_layout.py --dry-run
```

## [S3] Out of Scope

- 不迁移 / 不删除 `AMAC_Discipline_PDFs/` 内容。
- 不改违规分类体系、磁盘 JSON 字段、索引格式。
- 不换 Streamlit / 引入 CI / 云端部署。
- 不自动清理用户本地 `config.json` 中的密钥模型条目。
- 不为 PDF 目录新增配置项（后续可做）。
- 不重写 `专项分析报告` 内历史脚本逻辑，仅搬迁归档。

## Tasks

- [ ] T1: 建立 src 布局并迁移 regwatch/web 包 — acceptance: 包在 `src/regwatch`，`uv sync` 后 `import regwatch.web` 可用，入口脚本路径正确 (covers: S2.1, S2.2, S2.5)
- [ ] T2: 修正 PROJECT_ROOT 与默认 data_roots / example 配置 — acceptance: 默认根为 `data/amac|csrc/...`；单测更新后通过 (covers: S2.3, S2.4)
- [ ] T3: 删除旧脚本并归档专项分析报告 — acceptance: 薄封装脚本不在树中；`docs/archive/专项分析报告/` 存在 (covers: S2.6)
- [ ] T4: 编写 scripts/migrate_layout.py — acceptance: `--dry-run` 可运行；真实迁移幂等；冲突不覆盖 (covers: S2.7)
- [ ] T5: 更新 .gitignore / README / AGENTS 与 web 测试路径 — acceptance: 文档与 ignore 一致；AppTest 可渲染 (covers: S2.8, S2.9)
- [ ] T6: 全量验证 + 独立评审 — acceptance: unittest/ruff/mypy/cli 全绿；评审无 CRITICAL (covers: S2.10; depends: T1–T5)
