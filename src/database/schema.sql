-- Deep Researcher AI persistence. Every per-run table references runs(id) ON DELETE CASCADE, and the repository also
-- deletes child rows explicitly (remote databases may not enforce cascades per connection). No secrets are stored.
-- Ownership: runs and documents carry owner_id (the signed-in user's stable id, "local" without login, or a
-- per-session guest id). Every repository read and write is filtered by owner.

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,               -- stable identity-provider subject, or "local"
    email TEXT,
    name TEXT,
    picture TEXT,
    provider TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    last_login TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,                -- login | run_started | run_completed | run_failed | run_cancelled | run_deleted | qa | export | upload | data_deleted
    run_id TEXT,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_owner ON activity(owner_id, created_at);
-- Indexed columns hold identifiers/relationships; `data` holds the full validated Pydantic record as JSON.

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    question TEXT NOT NULL,
    status TEXT NOT NULL,              -- running | completed | failed
    mode TEXT NOT NULL,                -- demo | live
    created_at TEXT NOT NULL,
    completed_at TEXT,
    parent_run_id TEXT,
    request TEXT NOT NULL,
    plan TEXT,
    usage TEXT,
    statuses TEXT,
    stop_reason TEXT,
    summary TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    errors TEXT NOT NULL DEFAULT '[]',
    provider_notes TEXT NOT NULL DEFAULT '[]',
    counts TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sources (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    source_type TEXT NOT NULL,
    origin TEXT NOT NULL,
    published_date TEXT,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS passages (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    location TEXT,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS assessments (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, source_id)
);

CREATE TABLE IF NOT EXISTS claims (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    subquestion_id TEXT,
    claim_type TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS matrix_rows (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    subquestion_id TEXT,
    agreement TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS evidence_links (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    claim_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_id, row_id)
);

CREATE TABLE IF NOT EXISTS contradictions (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    claim_a_id TEXT NOT NULL,
    claim_b_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS gaps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS insights (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS search_log (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    provider TEXT NOT NULL,
    query TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS qa_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Uploaded documents are owner-scoped (reusable across that owner's runs) and can be deleted independently.
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sections TEXT NOT NULL,
    warnings TEXT NOT NULL DEFAULT '[]',
    uploaded_at TEXT NOT NULL,
    UNIQUE (owner_id, sha256)
);

CREATE TABLE IF NOT EXISTS search_cache (
    key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    query TEXT NOT NULL,
    results TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Chart payloads contain values and passage provenance, scoped through run ownership.
CREATE TABLE IF NOT EXISTS charts (
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, id TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY (run_id, id)
);
