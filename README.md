# regwatch · 基金监管案例采集与分析平台

自动采集 **中国证券投资基金业协会（AMAC）纪律处分** 与 **中国证监会及派出机构的行政处罚 / 监管措施**，
用大模型提取结构化字段，提供统计看板、案例检索与报告生成，并带网页操作界面。

数据统一存放于**单个 SQLite 库**（WAL 模式），不再使用散落的 JSON 与索引文件。

## 功能一览

| 能力 | 说明 |
|------|------|
| 统一采集 | AMAC 机构/人员纪律处分、CSRC 各来源 × 处罚/措施两类，支持日期范围、单链接、断点续传 |
| 结构化摘要 | 大模型提取违规类型、处罚措施、涉及基金、法规依据、罚款金额、市场禁入；CSRC 额外做基金相关性精判 |
| 统计与报告 | 违规分布、处罚归类、机构 vs 个人、法规引用 TOP、时间趋势；输出 Markdown / HTML / JSON |
| 网页看板 | 总览、案例浏览、统计分析、智能问答、任务中心、模型与配置六个页面 |
| 统一模型接入 | 只需填写 base_url / api_key / model，兼容 DeepSeek 与任意 OpenAI 兼容端点 |
| 双入口 | 同一套能力可在网页（`regwatch web`）或命令行（`regwatch`）执行 |

## 快速开始

```powershell
# 1. 安装依赖（Python 3.11+，推荐 uv）
uv sync --extra web --extra dev

# 2. 首次运行会自动生成 config.json（已加入 .gitignore，不会提交密钥）
uv run regwatch config show

# 3. 启动网页界面
uv run regwatch web --port 8501
```

打开浏览器访问 http://localhost:8501 ，在「模型与配置」页填写：

- 接口地址 base_url：例如 DeepSeek 官方 `https://api.deepseek.com`
- API Key（也可用环境变量 `REGWATCH_API_KEY_<模型ID大写>` 提供，优先级更高）
- 模型名称：例如 `deepseek-chat`

保存后点击「测试连接」确认可用，再到「任务中心」即可发起抓取 / 摘要 / 报告任务。

> 也可以不用网页端：
> `uv run regwatch config add-model --id deepseek --base-url https://api.deepseek.com --model deepseek-chat --api-key sk-xxx`

## 命令行

```powershell
uv run regwatch --help

# 数据库
uv run regwatch db init                       # 建库 / 升级结构
uv run regwatch db rebuild-violations         # 分类体系升级后回填违规类型关联表（不调用模型）
uv run regwatch db repair                     # 确定性数据质量修复（不调用模型）
uv run regwatch db stats                      # 规模与状态分布

# 抓取（不填 --start / --end 时增量抓取：上次覆盖日期前 7 天 → 今天）
uv run regwatch fetch amac     --start 2026-01-01 --end 2026-03-31
uv run regwatch fetch csrc     --start 2022-01-01 --bureaus HQ,Beijing --types penalty
uv run regwatch fetch url --url <案例链接> --dataset amac

# 摘要与报告（summarize 默认处理全部未提取案例，含失败重试）
uv run regwatch summarize --dataset all --workers 5
uv run regwatch summarize --dataset csrc --redo    # 提示词 / 分类体系升级后重跑已完成案例
uv run regwatch report --dataset amac --start 2026-01-01 --end 2026-03-31 --llm

# 机构登记类型回填
uv run regwatch org-type --dry-run
uv run regwatch org-type --interactive

# 配置
uv run regwatch config show
uv run regwatch config test
uv run regwatch config set-model summarize deepseek
uv run regwatch config set-database data/regwatch.db

# 其它
uv run regwatch jobs     # 最近任务
uv run regwatch info     # 环境概览
```

所有子命令都支持根选项 `--database <路径>` 临时指定库文件。

## 网页端

| 页面 | 能力 |
|------|------|
| 总览看板 | 核心指标卡、数据集构成、违规类型 TOP10、月度趋势、最新案例 |
| 案例浏览 | 按 数据集 / 状态 / 案例类型 / 来源局 / 违规类型 / 日期区间 / 关键词 组合筛选；点选查看详情与决定书原文 |
| 统计分析 | 违规分布、处罚构成、机构 vs 个人对比、法规引用 TOP、月度趋势、代表案例 |
| 智能问答 | 自然语言提问或撰写专题报告：意图解析 → 案例检索 → 依据证据作答；公开部署下访客使用自己的 API Key（接口地址与模型已预填默认值） |
| 任务中心 | 发起抓取 / 摘要 / 报告 / 机构类型回填任务，实时进度与日志，支持取消 |
| 模型与配置 | 模型条目增删改、连通性测试、任务到模型的绑定、并发与路径 |

性能说明：列表与统计走 SQL 聚合，案例正文在展开详情时才按需读取；
缓存键使用库内自增的「数据版本」，任何写入都会自动失效。

## 部署到 Streamlit Community Cloud

网页端可以直接部署到 Streamlit 官方免费的 Community Cloud，**支持私有 GitHub 仓库**
（同一 workspace 同时只能有 1 个私有应用）：

| 部署项 | 值 |
|--------|-----|
| Main file path | 公开只读看板：`deploy/streamlit_public.py`（只挂四个只读页）；自用私有：`deploy/streamlit_app.py`（六页，云端默认只读） |
| 依赖清单 | `deploy/requirements.txt`（与入口同目录，避开根目录 `uv.lock` 的优先级） |
| 数据与密钥 | 应用设置的 Secrets：`api_key_<模型ID>`、`database_url` |

入口脚本负责三件事：把 `src/` 加入 `sys.path`（云端不会安装本项目）、把 Secrets
桥接成 `REGWATCH_*` 环境变量、在库文件缺失时按需下载数据库；同时**默认打开只读模式**
（任务中心与「模型与配置」收起写操作，可用 `regwatch_admin_token` 解锁或
`regwatch_read_only = "0"` 关闭）。
完整步骤（含私有仓库限制、数据方案与排查表）见 [`docs/deploy/streamlit-cloud.md`](docs/deploy/streamlit-cloud.md)。

## 目录结构

```
src/regwatch/
├── domain/           领域层：枚举、数据模型、违规分类体系（无 IO）
├── settings.py       配置：单一库路径、模型条目、任务绑定、环境变量覆盖
├── logging_setup.py  统一日志与任务日志路由
├── db/               数据层：schema.sql / 迁移器 / 连接工厂 / 四个仓储
├── llm/              OpenAI 兼容客户端与输出解析
├── services/         用例层：摘要 / 统计 / 报告 / 机构类型 / 问答 / 任务编排 + 组合根
├── sources/          采集层：共享 HTTP/HTML/文档解析 + amac / csrc / bureaus
├── cli/              命令行（按子命令拆分）
└── web/              Streamlit 网页端（入口 / 状态 / 组件 / 六个页面）

deploy/streamlit_app.py     Streamlit Community Cloud 私有部署入口
deploy/streamlit_public.py  公开只读看板入口（依赖清单与入口同目录 requirements.txt）
data/regwatch.db      SQLite 主库（gitignore）
config.example.json   配置样例（入库）
config.json           本地配置（含密钥，gitignore）
tests/                pytest 测试（全部离线、内存库）
```

依赖方向自上而下：**交付层 → 用例层 → 领域层 ← 数据层**。
组合只发生在 `services/container.py`，各服务通过构造函数接收依赖，便于测试替换。

## 模型接入说明

统一走 OpenAI 兼容协议（`openai` SDK），一个模型条目包含：

| 字段 | 说明 |
|------|------|
| `base_url` | 接口根地址，如 `https://api.deepseek.com` |
| `api_key` | 密钥；留空时回落到环境变量 |
| `model` | 文本模型，如 `deepseek-chat` |
| `vision_model` | 视觉模型（识别 PDF 公告用），留空则回落到文本模型 |
| `token_param` | `max_tokens` 或 `max_completion_tokens`（少数端点要求后者） |
| `extra` | 透传给接口的附加参数，如 `{"thinking": {"type": "disabled"}}` |

任务与模型解耦：`tasks.summarize / tasks.report / tasks.vision / tasks.qa` 分别指定用哪个模型条目；
未绑定时使用第一个模型。若端点不支持某个参数，客户端会自动剥离该参数重试（有次数上限）。

## 测试与质量门禁

```powershell
uv run python -m pytest                       # 全量测试（离线、内存库）
uv run python -m pytest tests/test_db.py -v   # 单模块
uv run ruff check src tests deploy            # 静态检查
uv run ruff format src tests deploy           # 格式化
uv run mypy src/regwatch                      # 类型检查
uv run pre-commit run --all-files             # 提交前检查（需先 pre-commit install）
```

测试覆盖：领域模型与枚举、配置与环境变量覆盖、四个仓储、模型客户端降级与解析、
摘要流程（成功/跳过/失败）、统计与报告渲染、机构类型回填、智能问答、任务编排与持久化、CLI 子命令。

## 常见问题

- **网页打开但图表为空**：先到「任务中心」运行一次抓取与摘要，或点击侧边栏「刷新数据缓存」。
- **摘要任务报未配置模型**：到「模型与配置」页填写 base_url / api_key / model 并保存。
- **想换数据目录**：`regwatch config set-database <路径>`，相对路径基于项目根。
- **国内网络拉依赖慢**：`uv sync` 可配合镜像，例如
  `$env:UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"`
