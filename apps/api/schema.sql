CREATE TABLE IF NOT EXISTS "agent_events" (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES agent_turns(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at REAL NOT NULL, payload_stored TEXT,
    UNIQUE(turn_id, seq)
);

CREATE TABLE IF NOT EXISTS "agent_proposals" (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    proposal_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT,
    prepared_turn INTEGER NOT NULL,
    submitted_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
    updated_at REAL NOT NULL
, assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL, payload_stored TEXT);

CREATE TABLE IF NOT EXISTS agent_turns (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    user_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    assistant_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'running',
    last_event_seq INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
, branch_id TEXT REFERENCES conversation_branches(id) ON DELETE SET NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, resume_token TEXT);

CREATE TABLE IF NOT EXISTS conversation_branches (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    root_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    head_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '主线',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_folders (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '新建对话',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
, folder_id TEXT REFERENCES conversation_folders(id) ON DELETE SET NULL, deleted_at REAL, source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, activity_at REAL);

CREATE TABLE IF NOT EXISTS generation_admissions (
    id TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS "job_candidates" (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 0,
    partial_text TEXT DEFAULT '',
    raw_text TEXT DEFAULT '',
    title TEXT,
    content TEXT,
    error TEXT,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL, evaluation_json TEXT, partial_text_stored TEXT, raw_text_stored TEXT, title_stored TEXT, content_stored TEXT, error_stored TEXT, evaluation_stored TEXT,
    UNIQUE(job_id, ordinal)
);

CREATE TABLE IF NOT EXISTS "job_events" (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    created_at REAL NOT NULL, payload_stored TEXT,
    UNIQUE(job_id, seq)
);

CREATE TABLE IF NOT EXISTS "jobs" (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT,
    request_hash TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    error_message TEXT,
    idempotency_key TEXT UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    worker_id TEXT,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
, user_id TEXT REFERENCES users(id) ON DELETE SET NULL, conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, agent_turn_id TEXT, request_stored TEXT, result_stored TEXT, error_message_stored TEXT, forest_version INTEGER);

CREATE TABLE IF NOT EXISTS local_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS "message_versions" (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    version_group_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT,
    created_at REAL NOT NULL, author_id TEXT REFERENCES users(id) ON DELETE SET NULL, content_stored TEXT,
    UNIQUE(version_group_id, version_number)
);

CREATE TABLE IF NOT EXISTS "messages" (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT,
    created_at REAL NOT NULL
, status TEXT NOT NULL DEFAULT 'completed', job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL, version_group_id TEXT, version_number INTEGER NOT NULL DEFAULT 1, parent_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL, turn_id TEXT REFERENCES agent_turns(id) ON DELETE SET NULL, updated_at REAL, author_id TEXT REFERENCES users(id) ON DELETE SET NULL, content_stored TEXT);

CREATE TABLE IF NOT EXISTS poem_collection_items (
    collection_id TEXT NOT NULL REFERENCES poem_collections(id) ON DELETE CASCADE,
    poem_id TEXT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY(collection_id, poem_id)
);

CREATE TABLE IF NOT EXISTS poem_collections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS poem_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    poem_id TEXT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY(user_id, poem_id)
);

CREATE TABLE IF NOT EXISTS poem_forest_markers (
    forest_key TEXT PRIMARY KEY,
    marked_poem_id TEXT REFERENCES poems(id) ON DELETE SET NULL,
    updated_at REAL NOT NULL
, user_id TEXT REFERENCES users(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS poem_revision_lines (
    child_poem_id TEXT NOT NULL REFERENCES poems(id) ON DELETE CASCADE,
    change_index INTEGER NOT NULL,
    old_line_number INTEGER,
    old_text TEXT,
    new_line_number INTEGER,
    new_text TEXT, old_text_stored TEXT, new_text_stored TEXT,
    PRIMARY KEY(child_poem_id, change_index)
);

CREATE TABLE IF NOT EXISTS "poems" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT,
    title TEXT NOT NULL,
    content TEXT,
    meter_type TEXT NOT NULL DEFAULT '',
    form_name TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
, work_type TEXT NOT NULL DEFAULT '其他', candidate_ordinal INTEGER, evaluation_json TEXT, is_public INTEGER NOT NULL DEFAULT 0, parent_poem_id TEXT REFERENCES poems(id) ON DELETE SET NULL, root_poem_id TEXT REFERENCES poems(id) ON DELETE SET NULL, deleted_at REAL, prosody_context_json TEXT, content_stored TEXT, evaluation_stored TEXT, prosody_context_stored TEXT, variant_name TEXT, content_reference_id TEXT REFERENCES poems(id) ON DELETE RESTRICT, forest_id TEXT, sequence_stored TEXT, derived_from_poem_id TEXT REFERENCES poems(id) ON DELETE SET NULL);

CREATE TABLE IF NOT EXISTS user_collection_items (
    collection_id TEXT NOT NULL REFERENCES user_collections(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL CHECK (item_type = 'poem'),
    item_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(collection_id, item_type, item_id)
);

CREATE TABLE IF NOT EXISTS user_collections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, public_id TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL UNIQUE DEFAULT 'local-user',
            display_name TEXT NOT NULL DEFAULT '请输入用户名',
            created_at REAL NOT NULL DEFAULT (unixepoch()),
            bio TEXT NOT NULL DEFAULT '', avatar_url TEXT, avatar_stored BLOB
        );

CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    capabilities_json TEXT NOT NULL,
    last_seen_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS generation_admissions_identity_time ON generation_admissions(identity, category, created_at);

CREATE INDEX IF NOT EXISTS generation_admissions_job ON generation_admissions(job_id);

CREATE INDEX IF NOT EXISTS idx_agent_events_turn_seq ON agent_events(turn_id, seq);

CREATE INDEX IF NOT EXISTS idx_agent_turns_branch_status ON agent_turns(branch_id, status);

CREATE INDEX IF NOT EXISTS idx_agent_turns_conversation ON agent_turns(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_collection_items_poem ON poem_collection_items(poem_id);

CREATE INDEX IF NOT EXISTS idx_conversation_branches_conversation ON conversation_branches(conversation_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_conversation_folders_user ON conversation_folders(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_user_activity ON conversations(user_id, deleted_at, activity_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_user_deleted_updated ON conversations(user_id, deleted_at, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_candidates_job ON job_candidates(job_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_job_events_job_seq ON job_events(job_id, seq);

CREATE INDEX IF NOT EXISTS idx_jobs_agent_turn_id ON jobs(agent_turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_cancel_requested ON jobs(status, cancel_requested);

CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_user_conversation_status ON jobs(user_id, conversation_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_message_versions_group ON message_versions(version_group_id, version_number);

CREATE INDEX IF NOT EXISTS idx_messages_author ON messages(author_id);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_parent
ON messages(conversation_id, parent_message_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(conversation_id, parent_message_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(conversation_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_poem_forest_markers_forest
    ON poem_forest_markers(forest_key);

CREATE UNIQUE INDEX IF NOT EXISTS idx_poem_forest_markers_user_forest
    ON poem_forest_markers(user_id, forest_key);

CREATE INDEX IF NOT EXISTS idx_poems_content_reference ON poems(content_reference_id);

CREATE INDEX IF NOT EXISTS idx_poems_derived_from ON poems(derived_from_poem_id);

CREATE INDEX IF NOT EXISTS idx_poems_forest ON poems(forest_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_poems_job_candidate
ON poems(job_id, candidate_ordinal);

CREATE INDEX IF NOT EXISTS idx_poems_parent ON poems(parent_poem_id);

CREATE INDEX IF NOT EXISTS idx_poems_root ON poems(root_poem_id);

CREATE INDEX IF NOT EXISTS idx_poems_user_created ON poems(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_collection_items_lookup ON user_collection_items(item_type, item_id);

CREATE UNIQUE INDEX IF NOT EXISTS jobs_user_idempotency ON jobs(COALESCE(user_id, ''), idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_one_active_branch
ON conversation_branches(conversation_id)
WHERE is_active = 1;

PRAGMA user_version = 100;
