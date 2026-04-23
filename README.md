# 🚀 NovaMind: The Autonomous AI Agency (v3.0)

NovaMind is a production-grade, multi-agent autonomous system designed to operate a full-scale digital marketing agency without human intervention. Built on a foundation of **Evolutionary Prompting (PromptDNA)** and **Asynchronous Task Orchestration**, it manages everything from high-level strategy to cold outreach and lead generation.

---

## 🏛️ System Architecture

NovaMind uses a **hub-and-spoke task model**. Agents do not talk to each other directly; instead, they communicate via a centralized **Message Bus** (Supabase), ensuring total persistence and observability.

### 1. The Core Infrastructure
- **`TaskPoller`:** A robust async engine that monitors the task queue, handles concurrency, and routes jobs to the correct agent logic.
- **`PromptEvolver`:** A genetic algorithm for prompts. It tracks version history, manages mutations, and selects high-fitness "DNA" for production.
- **`LLMPool`:** A unified interface for Groq (Llama 3.3 70B) and Google Gemini (2.0 Pro), featuring automatic retries and model-tier routing.
- **`AgentMemory`:** Each agent maintains its own vector/relational memory to learn from past successes and failures.

### 2. The 13-Agent Departmental Structure

| Department | Agent | Primary Responsibility |
| :--- | :--- | :--- |
| **Strategy** | `ceo_agent` | Market research, daily strategy synthesis, and task dispatching. |
| **Operations** | `coo_agent` | System health monitoring, stalled task recovery, and efficiency analysis. |
| **Creative** | `content_agent` | SEO-optimized blog writing, social media posts, and multi-round self-critique. |
| **Growth** | `ads_agent` | PPC strategy, ROAS monitoring, and ad copy generation. |
| **Growth** | `seo_agent` | Keyword gap analysis, meta-tag generation, and technical SEO auditing. |
| **Growth** | `design_agent` | UI/UX strategy, asset briefs, and visual concept generation. |
| **Assurance** | `qa_agent` | Multi-dimensional scoring of all agent outputs (Accuracy, Voice, SEO). |
| **Sales** | `sales_agent` | Prospecting, candidate company research, and lead qualification. |
| **Outreach** | `email_agent` | Automated cold outreach, newsletter drafting, and sequence management. |
| **Outreach** | `proposal_agent` | Bespoke project proposal generation for high-value leads. |
| **Admin** | `crm_agent` | Bi-directional sync with HubSpot/Notion for lead and deal tracking. |
| **Intelligence** | `intel_agent` | Competitive landscape sweeps and market sentiment analysis. |
| **Finance** | `finance_agent` | ROI calculation, budget pacing, and agency profit/loss tracking. |

---

## 🧬 PromptDNA: The Evolutionary Engine

NovaMind's secret weapon is its **Self-Evolving Prompting System**. Every agent retrieves its instructions through the `PromptEvolver` API.

### How it Works:
1.  **Retrieve:** Agent calls `get_prompt()`. The system usually provides the "Best" prompt (Exploitation) but occasionally "Mutates" a new version (Exploration).
2.  **Execute:** The agent performs its task using the retrieved DNA.
3.  **Score:** The `qa_agent` reviews the result and gives it a fitness score (0.0 to 10.0).
4.  **Feedback:** The score is reported back to the Evolver via `record_outcome()`.
5.  **Evolution:** Over time, the agency "breeds" a perfect persona for every task type.

---

## 💾 Database Schema (Supabase)

NovaMind relies on a high-performance PostgreSQL schema:
- **`tasks`**: The central queue tracking every job status (`pending`, `in_progress`, `done`, `dead_letter`).
- **`agent_outputs`**: Stores the actual work product (JSON/Markdown) for every completed task.
- **`prompt_templates`**: The library of DNA versions, fitness scores, and usage counts.
- **`metrics`**: Real-time operational data (latency, tokens, success rates).
- **`leads`**: Centralized repository for prospects found by the Sales agent.
- **`alerts`**: High-priority system warnings for the COO agent.

---

## 🛠️ Installation & Setup

### 1. Environment Configuration
Create a `.env` file with the following keys:

```ini
# CORE
AGENT_API_KEY=your_secure_random_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_key

# INTELLIGENCE
GROQ_API_KEY=gsk_...
GEMINI_API_KEY=AIza...

# INTEGRATIONS (Optional)
NOTION_API_KEY=secret_...
NOTION_PARENT_PAGE_ID=...
DISCORD_WEBHOOK_URL=...
HUBSPOT_ACCESS_TOKEN=pat-na1-...
BREVO_API_KEY=xkeysib-...
```

### 2. Database Setup
Execute the following scripts in your Supabase SQL Editor in order:
1.  `database/schema.sql` (Tables and RLS)
2.  `database/migration_phase2.sql` (Advanced metrics)
3.  `database/migration_phase3.sql` (Evolutionary DNA support)

### 3. Running the Agency
NovaMind is designed to run 24/7. Use the `start.py` script which initializes the health check and starts the dual-engine (API + Poller).

```bash
# Install dependencies
pip install -r requirements.txt

# Start the agency
python start.py
```

---

## 🖥️ Monitoring (The Dashboard)

The `dashboard_v2.html` is a standalone, high-performance monitoring tool.
- **Live WebSocket Feed:** No page refreshes; watch tasks zip across the "Neural Map."
- **Evolutionary Tracking:** See the fitness levels of your agents rise in real-time.
- **Interactive Controls:** Manually trigger tasks or view detailed agent outputs.

---

## 📜 Operational Flow

1.  **CEO Trigger:** Starts the daily cycle with market research.
2.  **Intel Sweep:** Competitor data is gathered.
3.  **Production:** Content, SEO, and Ads agents generate assets.
4.  **Assurance:** QA agent verifies quality. If a task fails, it is sent back for rewrite.
5.  **Distribution:** CRM, Email, and Sales agents push the work to the market.
6.  **Self-Healing:** COO agent monitors stalled tasks and restarts them automatically.

---

## ⚖️ License & Contribution
NovaMind is licensed under the MIT License. Built for the Advanced Agentic Systems Course.

**Disclaimer:** This is an autonomous system. Monitor your API costs (Groq/Gemini) and ensure your integrations are properly sandboxed.
