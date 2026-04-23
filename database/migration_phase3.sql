-- Phase 3 Migration: God Tier Upgrade
-- Run this in the Supabase SQL Editor AFTER migration_phase2.sql

-- ─── prompt_templates: add evolution columns ──────────────────────────────────
ALTER TABLE prompt_templates ADD COLUMN IF NOT EXISTS generation  INTEGER DEFAULT 0;
ALTER TABLE prompt_templates ADD COLUMN IF NOT EXISTS parent_id   UUID REFERENCES prompt_templates(id);

-- ─── New table: debates ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS debates (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic        TEXT NOT NULL,
    context      TEXT,
    moderator    VARCHAR NOT NULL,
    participants JSONB DEFAULT '[]',
    max_rounds   INTEGER DEFAULT 3,
    status       VARCHAR DEFAULT 'open',    -- open | deliberating | resolved
    consensus    TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ
);

-- ─── New table: debate_positions ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS debate_positions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debate_id  UUID REFERENCES debates(id) ON DELETE CASCADE,
    agent_id   VARCHAR NOT NULL,
    argument   TEXT NOT NULL,
    round_num  INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ─── New table: competitor_targets ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitor_targets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR NOT NULL,
    url          TEXT NOT NULL UNIQUE,
    category     VARCHAR DEFAULT 'agency',   -- agency | tool | media
    active       BOOLEAN DEFAULT TRUE,
    last_scraped TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ─── New table: competitor_intel ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitor_intel (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor_id   UUID REFERENCES competitor_targets(id),
    competitor_name VARCHAR,
    detected_urls   JSONB DEFAULT '[]',      -- new pages/posts found
    content_diff    TEXT,                    -- what changed since last scrape
    analysis        TEXT,                    -- LLM competitive analysis
    opportunities   JSONB DEFAULT '[]',      -- keyword / content gaps
    scraped_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_debates_status       ON debates(status, created_at);
CREATE INDEX IF NOT EXISTS idx_debate_positions     ON debate_positions(debate_id, round_num);
CREATE INDEX IF NOT EXISTS idx_prompt_gen           ON prompt_templates(agent_id, prompt_name, generation);
CREATE INDEX IF NOT EXISTS idx_competitor_active    ON competitor_targets(active, last_scraped);
CREATE INDEX IF NOT EXISTS idx_intel_competitor     ON competitor_intel(competitor_id, scraped_at);

-- ─── Seed competitor targets (default set — update via dashboard) ─────────────
INSERT INTO competitor_targets (name, url, category) VALUES
    ('HubSpot Blog',        'https://blog.hubspot.com',          'media'),
    ('Neil Patel',          'https://neilpatel.com/blog',         'media'),
    ('Backlinko',           'https://backlinko.com/blog',         'media'),
    ('Content Marketing Institute', 'https://contentmarketinginstitute.com/articles', 'media')
ON CONFLICT (url) DO NOTHING;
