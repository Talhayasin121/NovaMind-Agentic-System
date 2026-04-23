-- Phase 1 Foundation: Execute this in the Supabase SQL Editor

-- 1. tasks
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    from_agent VARCHAR,
    to_agent VARCHAR,
    type VARCHAR,
    priority VARCHAR DEFAULT 'normal',
    payload JSONB,
    status VARCHAR DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. agent_outputs
CREATE TABLE agent_outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR,
    output_type VARCHAR,
    content JSONB,
    qa_status VARCHAR DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. company_goals
CREATE TABLE company_goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    week VARCHAR,
    okrs JSONB,
    set_by VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. clients
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR,
    email VARCHAR,
    status VARCHAR,
    hubspot_id VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. leads
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR,
    email VARCHAR,
    company VARCHAR,
    score INTEGER,
    status VARCHAR,
    outreach_step VARCHAR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. content_queue
CREATE TABLE content_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR,
    title VARCHAR,
    body TEXT,
    platform VARCHAR,
    status VARCHAR,
    scheduled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. metrics
CREATE TABLE metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR,
    metric_name VARCHAR,
    value NUMERIC,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. alerts
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    severity VARCHAR,
    agent_id VARCHAR,
    message TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 9. qa_queue
CREATE TABLE qa_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    output_id UUID REFERENCES agent_outputs(id),
    agent_id VARCHAR,
    check_status VARCHAR,
    feedback TEXT,
    reviewed_at TIMESTAMPTZ
);

-- 10. finance_ledger
CREATE TABLE finance_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR,
    amount NUMERIC,
    category VARCHAR,
    stripe_id VARCHAR,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);
