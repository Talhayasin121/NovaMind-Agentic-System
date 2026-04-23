"""
core/config.py — Centralized NovaMind configuration.
All environment variables, rate limits, and the agent registry live here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Supabase ────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# ─── Security ─────────────────────────────────────────────────────────────────
AGENT_API_KEY: str = os.getenv("AGENT_API_KEY", "dev-secret-key-123")

# ─── LLM Providers ────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Groq free tier: 30 RPM, 1,000 RPD, 6,000 TPM
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_RPM_LIMIT = 30
GROQ_RPD_LIMIT = 1000

# Gemini free tier (use for long-form / QA tasks — larger context)
GEMINI_MODEL = "gemini-2.5-flash"

# ─── Third-Party Services ─────────────────────────────────────────────────────
NOTION_API_KEY: str        = os.getenv("NOTION_API_KEY", "")
NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
DISCORD_WEBHOOK_URL: str   = os.getenv("DISCORD_WEBHOOK_URL", "")

# HubSpot Free CRM (250K req/day)
HUBSPOT_TOKEN: str = os.getenv("HUBSPOT_TOKEN", "")

# Brevo SMTP (300 emails/day free)
BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
SENDER_EMAIL: str  = os.getenv("SENDER_EMAIL", "hello@novamind.ai")
SENDER_NAME: str   = os.getenv("SENDER_NAME",  "Alex @ NovaMind")

# ─── Task Poller Settings ─────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS: int  = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
MAX_CONCURRENT_TASKS: int   = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

# ─── COO Health Settings ──────────────────────────────────────────────────────
# Tasks stuck in_progress longer than this are considered stalled
STALL_THRESHOLD_MINUTES: int = int(os.getenv("STALL_THRESHOLD_MINUTES", "30"))
# Max QA rejections before escalating to human review
MAX_QA_REJECT_CYCLES: int = int(os.getenv("MAX_QA_REJECT_CYCLES", "2"))
# Minimum QA score (out of 10) to approve content
QA_MIN_SCORE: float = float(os.getenv("QA_MIN_SCORE", "7.0"))
# Max content self-critique rewrites before sending to QA anyway
MAX_CONTENT_REWRITES: int = int(os.getenv("MAX_CONTENT_REWRITES", "2"))

# ─── Agent Registry ───────────────────────────────────────────────────────────
# Maps agent name → import path of the run function.
# Adding a new agent = adding ONE line here. No touching main.py.
AGENT_REGISTRY: dict = {
    "ceo_agent":       "agents.ceo.agent:run_ceo_agent",
    "coo_agent":       "agents.coo.agent:run_coo_agent",
    "content_agent":   "agents.content_agent.agent:run_content_agent",
    "design_agent":    "agents.design_agent.agent:run_design_agent",
    "seo_agent":       "agents.seo_agent.agent:run_seo_agent",
    "ads_agent":       "agents.ads_agent.agent:run_ads_agent",
    "qa_agent":        "agents.qa_agent.agent:run_qa_agent",
    "sales_agent":     "agents.sales_agent.agent:run_sales_agent",
    "crm_agent":       "agents.crm_agent.agent:run_crm_agent",
    "email_agent":     "agents.email_agent.agent:run_email_agent",
    "analytics_agent": "agents.analytics_agent.agent:run_analytics_agent",
    "finance_agent":   "agents.finance_agent.agent:run_finance_agent",
    # Phase 3 God Tier agents
    "intel_agent":     "agents.intel_agent.agent:run_intel_agent",
    "proposal_agent":  "agents.proposal_agent.agent:run_proposal_agent",
}
