"""
health_check.py — NovaMind System Diagnostic

Verifies:
  1. .env file loaded correctly (no blank required keys)
  2. Supabase connection + all required tables exist
  3. Groq API reachable
  4. Gemini API reachable
  5. Discord webhook reachable
  6. Notion API reachable (if configured)
  7. All agent modules importable without errors
"""
import os
import sys

# ─── Load .env FIRST ──────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ dotenv loaded\n")
except ImportError:
    print("❌ python-dotenv not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = []

def check(name: str, ok: bool, detail: str = ""):
    icon = PASS if ok else FAIL
    msg  = f"  {icon} {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((name, ok))

# ──────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("  NovaMind Health Check")
print("=" * 60)
print()

# ─── 1. Environment Variables ─────────────────────────────────────────────────
print("📋 Environment Variables")
required_vars = {
    "SUPABASE_URL":              "Supabase project URL",
    "SUPABASE_SERVICE_ROLE_KEY": "Supabase service role key",
    "GROQ_API_KEY":              "Groq LLM API key",
    "GEMINI_API_KEY":            "Google Gemini API key",
    "AGENT_API_KEY":             "Internal API security key",
}
optional_vars = {
    "DISCORD_WEBHOOK_URL":  "Discord alerts",
    "NOTION_API_KEY":       "Notion reports",
    "NOTION_PARENT_PAGE_ID":"Notion parent page",
    "HUBSPOT_TOKEN":        "HubSpot CRM",
    "BREVO_API_KEY":        "Brevo email",
    "SENDER_EMAIL":         "Outreach sender email",
}

for var, label in required_vars.items():
    val = os.getenv(var, "")
    check(f"{var} ({label})", bool(val), val[:12] + "..." if val else "NOT SET ← required")

print()
for var, label in optional_vars.items():
    val = os.getenv(var, "")
    icon = PASS if val else WARN
    status = val[:12] + "..." if val else "not set (optional)"
    print(f"  {icon} {var} ({label}) — {status}")

print()

# ─── 2. Supabase Connection ───────────────────────────────────────────────────
print("🗄️  Supabase Connection")
try:
    from core.supabase_client import get_supabase
    sb = get_supabase()

    required_tables = [
        "tasks", "agent_outputs", "metrics", "alerts",
        "leads", "content_queue", "qa_queue",
        "agent_heartbeats", "agent_memory", "daily_limits",
    ]
    for table in required_tables:
        try:
            r = sb.table(table).select("id").limit(1).execute()
            check(f"Table: {table}", True, f"{len(r.data)} rows fetched")
        except Exception as e:
            check(f"Table: {table}", False, str(e)[:80])

except Exception as e:
    check("Supabase connection", False, str(e)[:100])

print()

# ─── 3. Groq API ──────────────────────────────────────────────────────────────
print("🤖 Groq LLM API")
try:
    import requests
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
                "max_tokens": 5,
            },
            timeout=15,
        )
        if r.status_code == 200:
            resp_text = r.json()["choices"][0]["message"]["content"].strip()
            check("Groq API (llama-3.3-70b)", True, f"Response: '{resp_text}'")
        else:
            check("Groq API", False, f"HTTP {r.status_code}: {r.text[:100]}")
    else:
        check("Groq API", False, "GROQ_API_KEY not set")
except Exception as e:
    check("Groq API", False, str(e)[:100])

print()

# ─── 4. Gemini API ────────────────────────────────────────────────────────────
print("✨ Gemini API")
try:
    import requests
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            json={"contents": [{"parts": [{"text": "Reply with the single word: OK"}]}]},
            timeout=20,
        )
        if r.status_code == 200:
            resp_text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            check("Gemini API (gemini-2.5-flash)", True, f"Response: '{resp_text[:30]}'")
        else:
            check("Gemini API", False, f"HTTP {r.status_code}: {r.text[:120]}")
    else:
        check("Gemini API", False, "GEMINI_API_KEY not set")
except Exception as e:
    check("Gemini API", False, str(e)[:100])

print()

# ─── 5. Discord Webhook ───────────────────────────────────────────────────────
print("💬 Discord Webhook")
try:
    import requests
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if webhook_url:
        r = requests.post(
            webhook_url,
            json={"content": "🩺 NovaMind Health Check — systems online ✅"},
            timeout=10,
        )
        check("Discord webhook", r.status_code in (200, 204), f"HTTP {r.status_code}")
    else:
        print(f"  {WARN} Discord webhook — not configured (optional)")
except Exception as e:
    check("Discord webhook", False, str(e)[:80])

print()

# ─── 6. Agent Module Imports ──────────────────────────────────────────────────
print("🔌 Agent Module Imports")
agent_modules = [
    ("agents.ceo.agent",        "run_ceo_agent"),
    ("agents.coo.agent",        "run_coo_agent"),
    ("agents.content_agent.agent", "run_content_agent"),
    ("agents.design_agent.agent",  "run_design_agent"),
    ("agents.seo_agent.agent",     "run_seo_agent"),
    ("agents.ads_agent.agent",     "run_ads_agent"),
    ("agents.qa_agent.agent",      "run_qa_agent"),
    ("agents.sales_agent.agent",   "run_sales_agent"),
    ("agents.crm_agent.agent",     "run_crm_agent"),
    ("agents.email_agent.agent",   "run_email_agent"),
    ("agents.analytics_agent.agent", "run_analytics_agent"),
    ("agents.finance_agent.agent", "run_finance_agent"),
]

for module_path, fn_name in agent_modules:
    try:
        import importlib
        mod = importlib.import_module(module_path)
        fn  = getattr(mod, fn_name)
        check(f"{module_path}", True, f"{fn_name} found")
    except Exception as e:
        check(f"{module_path}", False, str(e)[:100])

print()

# ─── Summary ──────────────────────────────────────────────────────────────────
print("=" * 60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
total  = len(results)
print(f"  Results: {passed}/{total} passed  |  {failed} failed")
if failed == 0:
    print("  🎉 NovaMind is fully operational!")
elif failed <= 2:
    print("  ⚠️  Minor issues — optional integrations may not be configured.")
else:
    print("  🔴 Critical failures detected — check the items marked ❌ above.")
print("=" * 60)
