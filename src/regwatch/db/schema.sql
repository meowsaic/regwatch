-- regwatch 全量表结构（schema version 1）
--
-- 设计要点：
--   * AMAC 与 CSRC 共用 cases 表，用 dataset 区分，双方差异字段允许为空；
--   * 正文 raw_text 单独放 case_bodies，列表与统计查询不会触碰大字段；
--   * 违规类型是多值字段，拆到 case_violations 关联表，统计直接 GROUP BY；
--   * 任务与任务日志入库，进程重启后仍可追溯；
--   * meta 表保存 revision（数据变更计数，供网页端缓存失效）与各类键值缓存。

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

-- ──────────────────────────── 案例 ────────────────────────────

CREATE TABLE IF NOT EXISTS cases (
    dataset           TEXT    NOT NULL,
    case_id           TEXT    NOT NULL,
    source_url        TEXT    NOT NULL DEFAULT '',
    title             TEXT    NOT NULL DEFAULT '',
    date              TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'pending',
    status_note       TEXT    NOT NULL DEFAULT '',
    fetch_time        TEXT    NOT NULL DEFAULT '',
    error             TEXT    NOT NULL DEFAULT '',
    pdf_url           TEXT    NOT NULL DEFAULT '',
    updated_at        TEXT    NOT NULL DEFAULT '',
    -- AMAC 专属
    category          TEXT    NOT NULL DEFAULT '',   -- scfjg 机构 / scfry 人员
    org_type          TEXT    NOT NULL DEFAULT '',
    punished_entity   TEXT    NOT NULL DEFAULT '',
    source_type       TEXT    NOT NULL DEFAULT '',   -- html / pdf_direct / pdf_embedded
    ocr_success       INTEGER NOT NULL DEFAULT 0,
    -- CSRC 专属
    case_type         TEXT    NOT NULL DEFAULT '',   -- penalty 处罚 / measure 措施
    bureau            TEXT    NOT NULL DEFAULT '',
    document_number   TEXT    NOT NULL DEFAULT '',
    punished_entities TEXT    NOT NULL DEFAULT '',
    is_fund_related   INTEGER,                        -- NULL = 不适用（AMAC）
    fund_evidence     TEXT    NOT NULL DEFAULT '',
    doc_url           TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (dataset, case_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_cases_date       ON cases (date DESC);
CREATE INDEX IF NOT EXISTS idx_cases_status     ON cases (status);
CREATE INDEX IF NOT EXISTS idx_cases_bureau     ON cases (bureau);
CREATE INDEX IF NOT EXISTS idx_cases_type       ON cases (dataset, case_type);
CREATE INDEX IF NOT EXISTS idx_cases_fund       ON cases (is_fund_related);

-- 正文单独存放：避免列表/统计查询读取大字段
CREATE TABLE IF NOT EXISTS case_bodies (
    dataset  TEXT NOT NULL,
    case_id  TEXT NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (dataset, case_id),
    FOREIGN KEY (dataset, case_id) REFERENCES cases (dataset, case_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- ──────────────────────────── 摘要 ────────────────────────────

CREATE TABLE IF NOT EXISTS summaries (
    dataset           TEXT    NOT NULL,
    case_id           TEXT    NOT NULL,
    entity_type       TEXT    NOT NULL DEFAULT '',
    punished_entity   TEXT    NOT NULL DEFAULT '',   -- 模型从正文识别的受处分主体全称
    violation_type    TEXT    NOT NULL DEFAULT '',   -- 原始多值字符串
    punishment        TEXT    NOT NULL DEFAULT '',
    punishment_date   TEXT    NOT NULL DEFAULT '',
    involved_fund     TEXT    NOT NULL DEFAULT '',
    violation_summary TEXT    NOT NULL DEFAULT '',
    legal_basis       TEXT    NOT NULL DEFAULT '',
    penalty_amount    TEXT    NOT NULL DEFAULT '',
    market_ban        TEXT    NOT NULL DEFAULT '',
    extract_success   INTEGER NOT NULL DEFAULT 0,
    error             TEXT    NOT NULL DEFAULT '',
    extract_time      TEXT    NOT NULL DEFAULT '',
    llm_provider      TEXT    NOT NULL DEFAULT '',
    llm_model         TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (dataset, case_id),
    FOREIGN KEY (dataset, case_id) REFERENCES cases (dataset, case_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- 多值违规类型拆表：统计与筛选直接 GROUP BY
CREATE TABLE IF NOT EXISTS case_violations (
    dataset   TEXT NOT NULL,
    case_id   TEXT NOT NULL,
    violation TEXT NOT NULL,
    PRIMARY KEY (dataset, case_id, violation),
    FOREIGN KEY (dataset, case_id) REFERENCES cases (dataset, case_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_violations_type ON case_violations (violation);

-- ──────────────────────────── 任务 ────────────────────────────

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT    PRIMARY KEY,
    kind        TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL DEFAULT '',
    started_at  TEXT    NOT NULL DEFAULT '',
    finished_at TEXT    NOT NULL DEFAULT '',
    params      TEXT    NOT NULL DEFAULT '{}',
    processed   INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    message     TEXT    NOT NULL DEFAULT '',
    error       TEXT    NOT NULL DEFAULT '',
    result      TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks (status);

CREATE TABLE IF NOT EXISTS task_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL DEFAULT 0,
    level      TEXT NOT NULL DEFAULT 'INFO',
    message    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs (task_id, seq);

-- ──────────────────────────── 键值缓存 ────────────────────────────

-- 机构登记类型查询结果缓存
CREATE TABLE IF NOT EXISTS org_type_cache (
    name       TEXT PRIMARY KEY,
    org_type   TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

-- 抓取断点状态（按来源 + 作用域 + 键 三级定位）
CREATE TABLE IF NOT EXISTS fetch_state (
    source     TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source, scope, key)
) WITHOUT ROWID;
