# 🚀 NovaMind: Autonomous Agentic Agency

NovaMind is a production-grade, fully autonomous digital marketing agency powered by **13 interconnected AI agents**. It features a self-optimizing "PromptDNA" architecture that evolves agent personas and strategies based on real-time fitness feedback.

![NovaMind Dashboard](https://raw.githubusercontent.com/Talhayasin121/NovaMind-Agentic-System/main/dashboard_v2.html) *(Note: Placeholder for actual screenshot)*

## 🧠 The Architecture

NovaMind operates with a departmentalized 13-agent structure:

1.  **CEO Agent:** Strategy, market intelligence, and team orchestration.
2.  **COO Agent:** System health, task monitoring, and self-healing.
3.  **Content Agent:** High-quality blog and social media generation.
4.  **SEO Agent:** Keyword gap analysis and meta-tag optimization.
5.  **Design Agent:** Visual asset generation and UI/UX strategy.
6.  **Ads Agent:** PPC campaign management and ROAS optimization.
7.  **QA Agent:** Automated quality scoring and fitness reporting.
8.  **Sales Agent:** Lead generation and candidate company research.
9.  **CRM Agent:** Contact management and pipeline synchronization (HubSpot).
10. **Email Agent:** Cold outreach and newsletter automation (Brevo).
11. **Analytics Agent:** Data synthesis and performance reporting.
12. **Finance Agent:** ROI tracking and budget allocation.
13. **Intel Agent:** Competitive analysis and market sweeping.

## 🧬 PromptDNA: Evolutionary Prompting

Unlike static agent systems, NovaMind uses an **evolutionary loop**:
- **Exploration:** Agents occasionally try "mutated" prompts to find better ways of working.
- **Exploitation:** Agents prioritize "Winning DNA" (prompts with high QA scores).
- **Survival of the Fittest:** Low-performing prompts are pruned, ensuring the agency gets smarter every day.

## 🛠️ Tech Stack

- **Core:** Python 3.12+ (Asyncio)
- **Intelligence:** Groq (Llama 3.3 70B), Google Gemini 2.0
- **Database:** Supabase (PostgreSQL + Real-time)
- **Monitoring:** FastAPI + WebSocket Dashboard
- **Integrations:** Notion, HubSpot, Brevo, Discord, DuckDuckGo

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.12+
- A Supabase Project (Run `database/schema.sql` first)
- API Keys: Groq, Google AI, Discord, Notion

### 2. Installation
```bash
git clone https://github.com/Talhayasin121/NovaMind-Agentic-System.git
cd NovaMind-Agentic-System
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your credentials:
```bash
SUPABASE_URL=your_url
SUPABASE_KEY=your_key
GROQ_API_KEY=your_key
GEMINI_API_KEY=your_key
...
```

### 4. Running the Agency
Start the main orchestration layer and task poller:
```bash
python start.py
```

### 5. Access the Dashboard
Open `dashboard_v2.html` in any modern browser to watch the neural map in real-time.

## 📊 Monitoring & Health
The system includes a built-in `health_check.py` to verify all integrations (Supabase, Groq, Gemini) are online before starting.

## 📜 License
MIT License. Free for personal and commercial use.

---
*Built with ⚡ by the NovaMind Team.*
