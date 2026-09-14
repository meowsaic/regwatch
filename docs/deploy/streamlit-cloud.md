# 部署到 Streamlit Community Cloud

面向「把看板放到公网、代码用**私有** GitHub 仓库托管」的场景。全流程三步：
**推代码 → 建应用 → 配 Secrets**。

---

## 0. 先决条件：代码必须已经在 GitHub 上

Community Cloud 只从 GitHub 仓库拉代码（不支持 GitLab / Gitee，也不支持直接上传本地目录）。
如果部署页面报：

> The app's code is not connected to a remote GitHub repository.

含义就是**本地目录还没有推送到 GitHub**，按下面两步处理：

```powershell
# 1) 先提交本地改动（本仓库有大量已暂存但未提交的改动时，部署出来的是旧代码）
git status --short
git commit -m "chore: 准备部署"

# 2) 在 GitHub 网页上新建仓库（Private 即可），然后关联并推送
git remote add origin https://github.com/<你的账号>/regwatch.git
git push -u origin main
```

推送成功后，云端每次 `git push` 到部署分支都会自动重新部署。

---

## 1. 私有仓库支持吗？

**支持**，但有两条硬限制，先确认能接受再往下走：

| 事项 | 说明 |
|------|------|
| 可见性继承仓库 | 私有仓库 → 应用默认**私有**；公开仓库 → 默认公开可搜索 |
| 私有应用数量 | **同一个 workspace 同时只能有 1 个私有应用**；想再部署第二个私有应用，必须先把第一个改成公开或删除 |
| 谁能看 | 默认只有 workspace 内的 developer 可见。给别人看有两条路：① 在 GitHub 把对方加为仓库协作者；② 应用页右上角 **Share** 或 App settings → Sharing，用**邮箱邀请 viewer**（对方用 Google 登录或邮件一次性链接进入，不需要 GitHub 账号） |
| 谁能改可见性 | 只有 developer 能在 public / private 之间切换 |
| Analytics 副作用 | 添加 viewer 会同时开放该应用以及账号下**所有公开应用**的 analytics，且 viewer 可以继续邀请他人 |

**可见性与仓库是解耦的**：私有仓库的应用也能被改成公开（App settings → Sharing →
"This app is public and searchable"），此时**代码保持私有、看板对所有人开放**；
反过来公开仓库的应用也能设为私有。切换仅限 developer。

⚠️ 应用一旦公开，任何拿到链接的人都能用「任务中心」发起抓取 / 摘要任务。若同时在
Secrets 里配了 `api_key_*`，等于把模型额度开放给陌生人；要公开就**不要**配模型密钥
（任务中心会提示未配置模型），或者保持私有、用邮箱邀请指定的 viewer。

参考：Streamlit 官方文档 *Share your app*。

---

## 2. 建应用

登录 <https://share.streamlit.io> → **Create app** → *Deploy a public app from GitHub*（私有仓库也走这个入口，只是可见性继承为私有）→ 填写：
> 下拉框里看不到私有仓库时，去 GitHub → **Settings → Applications → Streamlit** 的
> Repository access 里把该仓库加入授权列表（首次授权常选了 "Only select repositories"）。

| 字段 | 值 |
|------|-----|
| Repository | `<你的账号>/regwatch` |
| Branch | `main` |
| **Main file path** | 公开只读看板填 **`deploy/streamlit_public.py`**；自用私有部署填 `deploy/streamlit_app.py` |
| Advanced settings → Python version | `3.11` 及以上（`pyproject.toml` 要求 `>=3.11`） |
| Advanced settings → Secrets | 见第 3 节，可稍后补 |

### 两个入口怎么选

| 入口 | 挂载的页面 | 适用场景 |
|------|------------|----------|
| **`deploy/streamlit_public.py`** | 总览看板 / 案例浏览 / 统计分析 / 智能问答（**写死的四个只读页**） | 公开看板：不需要口令，也不存在被误配打开写操作的可能 |
| `deploy/streamlit_app.py` | 六个页面；云端默认只读，可配 `regwatch_admin_token` 解锁 | 自用 / 私有部署，需要在云端临时跑任务（见第 5 节） |

两个入口共用 `src/regwatch/web/shell.py` 与同一份依赖清单，改代码两边同时生效；
本地 `uv run regwatch web` 完全不受影响（六个页面全开）。

### 为什么入口必须放在 `deploy/` 目录

两个坑都在这个目录选择上：

1. **src 布局**：云端不会安装本项目（不能指望 `pip install -e .`），
   所以入口脚本先把 `src/` 加进 `sys.path`，再委托给 `regwatch/web/shell.py`；
2. **依赖文件优先级**：Community Cloud 只会使用它找到的**第一个**依赖文件，
   查找顺序是「**入口脚本所在目录 → 仓库根目录**」，同一目录内的优先级为
   `uv.lock` > `Pipfile` > `environment.yml` > `requirements.txt` > `pyproject.toml`
   （见官方文档 *App dependencies*）。
   本仓库根目录有本地开发用的 `uv.lock`，它会盖掉根目录的 `pyproject.toml`；
   把依赖清单放到入口同目录 `deploy/requirements.txt` 才能确定性地生效。

所以：**不要**把入口或依赖文件挪回仓库根目录，也**不要**在 `deploy/requirements.txt`
里写 `-e .`（云端对可编辑安装没有保证，入口脚本已经解决导入问题）。

想本地复现云端行为：

```powershell
streamlit run deploy/streamlit_public.py   # 公开入口：只有四个只读页面
streamlit run deploy/streamlit_app.py      # 完整入口：六个页面（默认只读）
```

---

## 3. Secrets（模型密钥与数据库）

应用部署好后，在 **App settings → Secrets** 里粘贴 TOML，保存后应用会自动重启：

```toml
# 模型密钥：桥接为环境变量 REGWATCH_API_KEY_<模型ID大写>
api_key_deepseek = "sk-xxxxxxxx"

# 可选：任意 REGWATCH_ 前缀的键都会原样大写后写入环境变量
regwatch_database = "data/regwatch.db"

# 可选：启动时按需下载数据库（见第 4 节）
database_url = "https://example.com/regwatch.db"
database_token = "ghp_xxx"   # 私有仓库 Release 资产需要
```

桥接规则（`regwatch/web/cloud.py`）：

| Secrets 键 | 环境变量 | 用途 |
|------------|----------|------|
| `api_key_<id>` | `REGWATCH_API_KEY_<ID>` | 某个模型条目的 API Key（与本地用法一致） |
| `regwatch_*` | 同名大写 | 如 `regwatch_database` → `REGWATCH_DATABASE` |
| `regwatch_read_only` | `REGWATCH_READ_ONLY` | `"0"` 可关掉云端默认的只读模式（见第 5 节） |
| `regwatch_admin_token` | `REGWATCH_ADMIN_TOKEN` | 页面解锁口令（见第 5 节） |
| `database_url` | — | 库文件缺失时启动下载 |
| `database_token` | — | 下载时带 `Authorization: Bearer`（GitHub 私有 Release 资产） |

真实环境变量优先于同名 Secrets；嵌套表（`[api_keys]` 这类）不处理，请用扁平的
`api_key_xxx = "..."` 写法。

> **问答页不需要配密钥**：「智能问答」对访客强制使用访客自己的 Key，页面已按
> `config.example.json` 里 `tasks.qa` 绑定的模型条目（默认 `deepseek-v4-flash`，
> base_url `https://llm.ouyeelf.com/v1`、模型 `deepseek-chat`）预填接口地址与模型，
> 访客粘贴自己的 Key 即可提问。想换默认端点：改 `config.example.json` 里的
> `tasks.qa` 绑定（或本地「模型与配置」页的任务绑定），两边同时生效。

---

## 4. 数据从哪来（三选一）

云端容器里**没有** `data/regwatch.db`（该目录已被 gitignore），需要选一种方案：

| 方案 | 做法 | 优点 | 代价 |
|------|------|------|------|
| **A. 随仓库走 + Git LFS（推荐）** | 用 LFS 跟踪 `data/regwatch.db`，跟代码一起推（本仓库已配好，细节见下） | 零配置、数据与代码同源；git 历史只留约 130 字节指针，**更新数据不会让历史膨胀** | 本机需要 `git-lfs`；LFS 免费额度 1 GB |
| B. 远端下载 | 把库放到对象存储 / 私有仓库 Release 资产，Secrets 里配 `database_url` | 仓库里**完全不出现**库文件，本地可以不留 | 需要一处可直链存储；私有仓库要 PAT；每次冷启动多一次 35 MB 下载 |
| C. 从零采集 | 什么都不配，部署后在「任务中心」跑 `fetch amac` / `fetch csrc` | 不用本地数据 | 需要云端能访问 AMAC/CSRC 站点；摘要还要先配模型 |

> LFS 与「仓库里不出现该文件」是两件事：LFS 下文件**仍然属于仓库**（本地也保留一份，
> 否则下次提交会把库里那份删掉），只是大体积内容不进 git 历史。真正要「本地不留、
> 仓库里也不出现」，只能用方案 B。

### 方案 A 细节：Git LFS（本仓库已配好）

`.gitattributes` 里已有这一行（由 `git lfs track` 生成）：

```
data/regwatch.db filter=lfs diff=lfs merge=lfs -text
```

Streamlit 官方文档 *File organization* 原话：**"If your GitHub repository uses LFS, it
will just work with Streamlit Community Cloud."** 也就是说云端克隆时会自动拉取 LFS 实体，
应用侧不需要任何改动。

首次登记（`data/` 还在 `.gitignore` 里，所以这次要 `-f`；登记后它就成了被跟踪文件）：

```powershell
git lfs install                                   # 本机一次性
git add -f data/regwatch.db .gitattributes
git commit -m "data: 用 LFS 纳入完整数据库快照"
git push                                          # LFS 实体随 push 自动上传
```

后续更新数据（本地跑完抓取 / 摘要之后）：

```powershell
# 1) 先把 WAL 合并进主库，保证只提交主库文件就够了（无任务在跑时执行）
uv run python -c 'import sqlite3; con = sqlite3.connect("data/regwatch.db"); con.execute("PRAGMA wal_checkpoint(TRUNCATE)"); con.close()'

# 2) 提交推送；已被跟踪，不需要 -f，LFS 会自动上传新的实体
git add data/regwatch.db
git commit -m "data: 更新看板数据库快照"
git push
```

不要提交 `data/regwatch.db-wal` / `-shm`（半成品文件，`.gitignore` 已覆盖，也别用 `-f`）。

配额：GitHub 免费账号的 LFS 是 **1 GB 存储 / 1 GB 月流量**，一个完整版本约 34 MB，
大约能存 28 个版本；旧版本可以在仓库的 LFS 对象里清理，或者按下面的瘦身版把每版压到 10 MB。

当前库的体积构成（`dbstat` 实测）：

| 部分 | 大小 | 说明 |
|------|------|------|
| `case_bodies` | 23.1 MB | 案例正文，占大头 |
| `summaries` | 6.8 MB | 结构化摘要 |
| `cases` | 1.9 MB | 案例元数据 |
| 违规关联 + 索引 | 约 2.7 MB | |
| 任务 / 缓存类小表 | ~0 | |

想更小就做**瘦身版**（删正文，实测 35.3 MB → **10.3 MB**，代价是案例详情页看不到
决定书原文）：

```powershell
uv run python -c 'import sqlite3; sqlite3.connect("data/regwatch.db").execute("VACUUM INTO ''data/regwatch-slim.db''")'
uv run python -c 'import sqlite3; con = sqlite3.connect("data/regwatch-slim.db"); con.isolation_level = None; con.execute("DELETE FROM case_bodies"); con.execute("VACUUM"); con.close()'
git add -f data/regwatch-slim.db
```

瘦身版不会自动被使用，要么在 Secrets 里加 `regwatch_database = "data/regwatch-slim.db"`，
要么把它直接命名成 `data/regwatch.db`；用 LFS 的话记得先 `git lfs track "data/regwatch-slim.db"`。

#### 不用 LFS 直接提交（备选）

`git add -f data/regwatch.db` 也能用，但每次数据更新都会在 git 历史里留下一个 35 MB blob，
只适合「几个月才更新一次」的场景；更新频繁就用 LFS（上面）或方案 B（`database_url`）。

> **容器磁盘是临时的**：实例休眠或重启后会回到仓库快照状态，云端新增的抓取、
> 摘要、报告都会丢失。长期使用请走方案 A / B，把数据放回仓库或远端。

方案 B 用私有仓库的 Release 资产时，`database_url` 要填 **GitHub API 资产地址**
（形如 `https://api.github.com/repos/<账号>/<仓库>/releases/assets/<id>`）并配
`database_token`，普通 `releases/download/...` 链接对私有仓库需要登录，会 404。

---

## 5. 权限边界：只读模式与口令解锁（`deploy/streamlit_app.py` 专用）

> 用 `deploy/streamlit_public.py` 部署的话，本节可以跳过：那条路根本没有这两个页面，
> 也没有任何开关或口令需要记。

云端实例是个「用完即弃的沙盒」：跑任务只写容器内的临时副本，不会回写 GitHub。
因此 **`deploy/streamlit_app.py` 默认打开只读模式**（`REGWATCH_READ_ONLY=1`）：
**任务中心与「模型与配置」直接不进导航**——侧边栏里看不到、点不到，页面代码也不会渲染。

| 环境 | 页面表现 |
|------|----------|
| 云端（默认） | 侧边栏只有 总览看板 / 案例浏览 / 统计分析 / 智能问答，另有一行「🔒 云端只读」提示 |
| 本地 `regwatch web` | 六个页面全开（只读默认关闭） |
| 云端 + 口令解锁后 | 六页全开，仅当前浏览器会话有效 |

页面内部仍然留了一道 `require_admin` 防护（即使被别的方式渲染到，也只显示只读视图），
但正常情况下访客根本走不到那里。

为什么值得这么做（公开应用尤其要看）：

- 「测试连接」会**带着密钥去请求 base_url**：不设防的话，陌生人可以把 base_url 改成自己的
  服务器，再触发摘要任务，把你的 API Key 骗走；
- 任何人都能提交抓取任务，白耗你的实例 CPU/带宽，并把共享实例的数据搅乱；
- 陌生人能删改你的模型条目、任务绑定与并发设置。

需要自己在云端试跑时，两种解锁方式（都在 Secrets 里配，保存后自动重启）：

```toml
# 方式一：整体关掉只读（公开应用慎用）
regwatch_read_only = "0"

# 方式二（推荐）：保留只读，配一个口令，页面上按需解锁
regwatch_admin_token = "换成你自己的口令"
```

方式二下，侧边栏会出现一行口令输入框；输入正确后「任务中心」「模型与配置」立刻出现在
导航里。口令只放在浏览器会话里，不落盘、不进日志，服务重启或新开浏览器即失效。

---

## 6. 故障排查

| 现象 | 原因与处理 |
|------|------------|
| 页面能打开但图表／列表为空 | 空库，见第 4 节；也可点侧边栏「刷新数据缓存」 |
| 侧边栏里看不到「任务中心」「模型与配置」 | 两种情况：① 用 `streamlit_public.py` 部署（本来就只挂四页）；② 用 `streamlit_app.py` 且处于只读模式（见第 5 节）。本地运行不会出现 |
| 顶部提示「不是有效的 SQLite 库」 | 仓库里的库文件是 Git LFS 指针、但云端没拉到实体：确认 `.gitattributes` 已提交、本地 `git lfs pull` 后再推一次 |
| `ModuleNotFoundError: plotly` / `No module named 'streamlit'` | 依赖解析走到了仓库根目录的 `uv.lock`，检查 Main file path 是否填的 `deploy/streamlit_app.py` |
| `ModuleNotFoundError: regwatch` | 入口文件被换成了 `src/regwatch/web/app.py`，请改回 `deploy/streamlit_app.py` |
| 任务中心报「尚未配置任何模型」 | 在 Secrets 配 `api_key_<id>`，并在「模型与配置」页补 `base_url` 与模型名 |
| 「模型与配置」页改完，重启后配置丢了 | 云端 `config.json` 写在容器里，重启即失效；固定配置请用 Secrets |
| 抓取任务超时／失败 | 云端机房在海外，AMAC/CSRC 站点可能较慢甚至不可达，建议本地抓完再按方案 A / B 更新 |
| 应用变慢或内存超限 | 免费实例内存有限，先用筛选条件缩小查询范围，避免一次载入全部案例 |

---

## 7. 更新与下线

- 改代码：`git push` 到**部署分支**即可，云端会「近乎实时」自动更新，没有开关；
  如果动到了 `deploy/requirements.txt`，它会自动做一次完整重建（慢一些）。
- 手动 Reboot：工作区里应用右侧 ⋮ → Reboot（卡住时用），会重新拉取仓库。
- 休眠：**12 小时无流量**应用会休眠，任何人访问时点一下即可唤醒；唤醒后容器是重建的，
  容器内的临时改动（抓取/摘要结果）不保证保留。
- 改 Secrets：保存后自动重启，无需重新部署。
- 下线：App settings → **Danger zone** → Delete app；私有应用删除后才能再部署新的私有应用。
