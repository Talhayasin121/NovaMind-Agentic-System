-- Phase 2 Migration: Add missing columns and new tables
-- Run this in the Supabase SQL Editor

-- ─── Add missing columns to existing tables ───────────────────────────────────

-- tasks: add updated_at and error_message
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error_message TEXT;

-- agent_outputs: add id column default
ALTER TABLE agent_outputs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- ─── New table: agent_memory ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    VARCHAR NOT NULL,
    memory_type VARCHAR DEFAULT 'learning',   -- learning | decision | pattern
    content     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─── New table: prompt_templates ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prompt_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    VARCHAR NOT NULL,
    prompt_name VARCHAR NOT NULL,
    template    TEXT NOT NULL,
    avg_score   NUMERIC DEFAULT 0,
    use_count   INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─── New table: daily_limits ──────────────────────────────────────────────────
-- Track API usage to stay within free tiers
CREATE TABLE IF NOT EXISTS daily_limits (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider    VARCHAR NOT NULL,   -- groq | gemini | brevo | hubspot
    date        DATE DEFAULT CURRENT_DATE,
    call_count  INTEGER DEFAULT 0,
    UNIQUE(provider, date)
);

-- ─── Indexes for performance ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_to_agent   ON tasks(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);
CREATE INDEX IF NOT EXISTS idx_metrics_agent    ON metrics(agent_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved  ON alerts(resolved, created_at);
CREATE INDEX IF NOT EXISTS idx_qa_status        ON qa_queue(check_status, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_content_status   ON content_queue(status);
