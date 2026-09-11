# regwatch · 基金监管案例采集与分析平台

自动采集 **中国证券投资基金业协会（AMAC）纪律处分** 与 **中国证监会及 37 家派出机构的行政处罚 / 监管措施**，
用大模型提取结构化字段，提供统计看板、案例检索与报告生成，并带网页操作界面。

> 本仓库只纳入代码、配置样例与文档；案例 JSON / PDF（约 1.6GB）保留在本地数据目录，不入 git。

## 功能一览

| 能力 | 说明 |
|------|------|
| 统一采集 | AMAC 机构/人员纪律处分、CSRC 37 个来源 × 处罚/措施两类，支持日期范围、单链接、断点续传 |
| 结构化摘要 | 大模型提取违规类型、处罚措施、涉及基金、法规依据、罚款金额、市场禁入；CSRC 额外做基金相关性精判 |
| 统计与报告 | 违规分布、处罚归类、机构 vs 个人、法规引用 TOP、时间趋势；输出 Markdown / HTML / JSON |
| 网页看板 | 总览、案例浏览、统计分析、任务中心、模型与配置五个页面 |
| 统一模型接入 | 只需填写 base_url / api_key / model，兼容 DeepSeek 与任意 OpenAI 兼容端点 |
| 双入口 | 同一套能力既可在网页操作，也可用 `regwatch` 命令行执行 |

## 快速开始

```powershell
# 1. 安装依赖（Python 3.11+，推荐 uv）
uv sync --extra web --extra dev
# 等价：pip install -e ".[web,dev]"

# 2. 首次运行会自动生成 config.json（已加入 .gitignore，不会提交密钥）
uv run regwatch config show
# 或：python -m regwatch.cli config show

# 3. 启动网页界面
uv run regwatch web --port 8501
```

打开浏览器访问 http://localhost:8501 ，在「模型与配置」页填写：

- 接口地址 base_url：例如 DeepSeek 官方 `https://api.deepseek.com`
- API Key
- 模型名称：例如 `deepseek-chat`（推理模型用 `deepseek-reasoner`）

保存后点击「测试连接」确认可用，再到「任务中心」即可发起抓取 / 摘要 / 报告任务。

> 也可以不用网页端：`python -m regwatch.cli config add-model --id deepseek --base-url https://api.deepseek.com --model deepseek-chat --api-key sk-xxx`

## 网页端

| 页面 | 能力 |
|------|------|
| 总览看板 | 核心指标卡、数据集构成、违规类型 TOP10、年度趋势、最新案例 |
| 案例浏览 | 按 数据集 / 主体类型 / 违规类型 / 处理状态 / 来源局 / 日期区间 / 关键词 组合筛选；支持一键清除筛选；表格点选查看详情与决定书原文 |
| 统计分析 | 违规分布、处罚构成、机构 vs 个人对比、法规引用 TOP、月度趋势、年度×月热力图 |
| 任务中心 | 网页发起抓取 / 摘要 / 报告 / 机构类型回填任务，实时进度条与分流日志，支持取消与自动刷新 |
| 模型与配置 | 模型条目增删改、连通性测试、任务到模型的绑定、并发与数据目录展示 |

性能说明：看板与列表只读结构化字段并以数据变更信号作为缓存键（数据更新自动失效），
案例正文在展开详情时才按需读取，因此 1.6GB 数据也能秒级打开。

## 命令行

```powershell
python -m regwatch.cli --help

# 抓取
python -m regwatch.cli fetch amac     --start 2026-01-01 --end 2026-03-31
python -m regwatch.cli fetch csrc     --start 2022-01-01 --bureaus HQ,Beijing --types penalty
python -m regwatch.cli fetch monthly                            # 上月公告 PDF 归档
python -m regwatch.cli fetch url --url <案例链接> --dataset amac  # 单条抓取

# 摘要与报告
python -m regwatch.cli summarize  --dataset all --workers 5
python -m regwatch.cli report     --dataset amac --start 2026-01-01 --end 2026-03-31 --llm

# 机构登记类型回填
python -m regwatch.cli org-type --dry-run

# 配置
python -m regwatch.cli config show
python -m regwatch.cli config test                     # 测试全部模型连通性
python -m regwatch.cli config add-model --id deepseek --base-url https://api.deepseek.com --model deepseek-chat --api-key sk-xxx
python -m regwatch.cli config set-model --task summarize --model deepseek
```

常用参数缺省值取自 `config.json`；任务实现与网页端完全一致（同一套 `regwatch.jobs` 编排）。

## 目录结构

```
.
├── src/regwatch/                  # 可安装核心包（src 布局）
│   ├── config.py                  #   配置管理（数据路径 / 模型条目 / 任务绑定 / 并发）
│   ├── llm.py                     #   统一 OpenAI 兼容客户端（参数自动降级、连通性测试）
│   ├── datamodels.py              #   案例与摘要数据模型（与磁盘字段严格一致）
│   ├── storage.py                 #   数据访问层（案例 / 摘要 / 索引 / 缓存）
│   ├── prompts.py                 #   提取提示词与违规分类体系
│   ├── summarize.py               #   统一结构化摘要提取
│   ├── org_type.py                #   机构登记类型查询与回填
│   ├── analyze.py / report.py     #   统计聚合与报告渲染（md / html / json）
│   ├── jobs.py                    #   后台任务编排（线程、进度、取消、日志分流）
│   ├── cli.py                     #   统一命令行入口
│   ├── sources/                   #   采集子包（amac / csrc / amac_monthly / csrc_bureaus）
│   └── web/                       #   Streamlit 网页端（app / views / components）
├── data/                          # 运行时数据（gitignore，不入库）
│   ├── amac/{cases,summaries,reports}/
│   └── csrc/{cases,summaries,reports}/
├── docs/
│   ├── compose/spec/              # Compose 功能规格
│   └── archive/专项分析报告/        # 历史专题分析产物（归档）
├── scripts/migrate_layout.py      # 旧 AMAC/CSRC 根目录 → data/ 一次性迁移
├── tests/                         # 纯本地单元测试 + AppTest 网页冒烟测试
├── .streamlit/config.toml         # Streamlit 项目配置
├── config.example.json            # 配置样例（入库）
├── config.json                    # 本地配置（含密钥，不入库）
└── AMAC_Discipline_PDFs/          # 月度公告 PDF（本地，待外迁，不入库）
```

## 数据与版本管理

- 案例正文与摘要在 `data/amac/*`、`data/csrc/*`，由 `.gitignore` 排除，**不会进入版本库**；
  数据目录可整体移动，只需改 `config.json` 的 `data_roots`。
- 从旧布局迁移：`uv run python scripts/migrate_layout.py --dry-run` 确认后去掉 dry-run。
- `AMAC_Discipline_PDFs/` 为历史 PDF 归档，默认仍在项目根，请自行外迁到仓库外。
- 索引文件（`_index.json`、`_summary_index.json`）由脚本维护，是断点续传与增量更新的事实来源。
- 提交历史保持小步快照，出问题可 `git log --oneline` + `git revert` / `git checkout <commit>` 回滚。

## 模型接入说明

统一走 OpenAI 兼容协议（`openai` SDK），一个模型条目包含：

| 字段 | 说明 |
|------|------|
| `base_url` | 接口根地址，如 `https://api.deepseek.com` |
| `api_key` | 密钥；也可用环境变量 `REGWATCH_API_KEY_<ID大写>`，优先级更高 |
| `model` | 文本模型，如 `deepseek-chat` |
| `vision_model` | 视觉模型（识别 PDF 公告用），留空则回落到文本模型 |
| `token_param` | `max_tokens` 或 `max_completion_tokens`（少数端点要求后者） |
| `extra` | 透传给接口的附加参数，如 `{"thinking": {"type": "disabled"}}` |

任务与模型解耦：`tasks.summarize / tasks.report / tasks.vision` 分别指定摘要、报告建议、
视觉识别用哪个模型条目；未绑定时使用第一个模型。若端点不支持某个参数，客户端会自动剥离该参数重试。

## 测试与质量门禁

```powershell
uv run python -m unittest discover -s tests -t .   # 全量单测（不发网络请求）
uv run python -m unittest tests.test_storage -v    # 单模块
uv run ruff check src tests                        # 静态检查
uv run ruff format src tests                       # 格式化
uv run mypy regwatch                               # 类型检查（核心包，mypy_path=src）

$env:REGWATCH_LIVE_TEST = "1"; uv run python -m unittest tests.test_live -v   # 可选实网测试
```

依赖与工具链以 `pyproject.toml` 为准；`requirements.txt` 仅作 pip 兼容参考。
开发环境建议 Python 3.11+（uv 会自动选择解释器）。

## 常见问题

- **网页打开但图表为空**：先在「任务中心」运行一次摘要提取，或点击侧边栏「刷新数据缓存」。
- **摘要任务报未配置模型**：到「模型与配置」页填写 base_url / api_key / model 并保存。
- **想换数据目录**：修改 `config.json` 的 `data_roots`，相对路径基于项目根目录。
- **国内网络拉依赖慢**：`uv sync` 可配合镜像，例如  
  `$env:UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"`
