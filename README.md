# 📡 RepoAlpha — Enterprise Open Source M&A Intelligence

> **Bright Data AI Agents Hackathon 2026** · Bright Data · Groq · LangChain · Supabase · Streamlit

RepoAlpha turns VC and M&A teams into "God Mode" investors by detecting corporate developer adoption **before it becomes public news**. A *"Software Engineer at Nvidia"* starring your repo is a 15-point signal. An anonymous account is noise.

---

## 🆕 Enterprise v2 — What's New

| Feature | Detail | Cost |
|---|---|---|
| **Pydantic v2 models** | All pipeline data validated before Supabase writes | Free |
| **Circuit breaker** | Auto-opens on sustained API failures, heals after 2 min | Free |
| **Exponential retry** | tenacity wraps every external call — no silent failures | Free |
| **Structured logging** | loguru JSON lines + Supabase `pipeline_runs` audit trail | Free |
| **TTL in-process cache** | Spare Supabase reads within a pipeline run | Free |
| **Watchlist** | Pin repos, personal tracking across sessions | Free |
| **Score history** | Auto-snapshotted by Supabase trigger → sparkline charts | Free |
| **pgvector semantic search** | `all-MiniLM-L6-v2` embeddings, ANN search via `match_repos()` | Free |
| **Alerter** | Slack + Discord + Resend email on BUY threshold breach | Free |
| **FastAPI REST layer** | Full CRUD + CSV/JSON export, Swagger at `/docs` | Free |
| **GitHub Actions CI/CD** | Hourly pipeline + lint + type-check, no paid scheduler | Free |
| **Render.com deploy** | API on free web service + cron worker | Free |
| **pytest suite** | 20+ unit tests covering models, scoring, cache, license | Free |

---

## ⚡ 30-Minute Setup

### 1 — Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/repoalpha.git
cd repoalpha
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2 — API keys

| Service | Where | Notes |
|---|---|---|
| **Bright Data** | brightdata.com → Account Settings | API Token + Web Unlocker zone |
| **Groq** | console.groq.com → API Keys | `gsk_...` key, free |
| **Supabase** | supabase.com → Settings → API | URL + service_role key |
| **GitHub PAT** | github.com/settings/tokens | No scopes — raises limit 60→5000 req/hr |
| **Slack webhook** | Your workspace → Apps → Incoming Webhooks | Optional alerts |
| **Discord webhook** | Server → Integrations → Webhooks | Optional alerts |
| **Resend** | resend.com → API Keys | 100 emails/day free |

```bash
cp .env.example .env
nano .env   # paste all keys
```

### 3 — Database (run once in Supabase SQL Editor)

```bash
# Paste schema_v2.sql into: https://supabase.com/dashboard → SQL Editor → New Query → Run
# This creates all tables, the pgvector extension, the snapshot trigger,
# the match_repos() RPC, and Row Level Security policies.
```

### 4 — See the dashboard immediately (no API credits needed)

```bash
python main.py --demo          # seeds 5 repos + 18 signals + score history
streamlit run dashboard.py     # → http://localhost:8501
```

### 5 — Run the live pipeline

```bash
python main.py                 # one full cycle (all 5 phases)
python main.py --loop          # hourly daemon
python main.py --phase 1       # single phase: 1=harvest 2=enrich 3=analyse 4=alert 5=vector
```

### 6 — Start the REST API

```bash
uvicorn api.main:app --reload --port 8000
# Swagger UI → http://localhost:8000/docs
```

### 7 — Run tests

```bash
pytest tests/ -v
```

---

## 🌐 Free Deployment Stack

```
Streamlit Community Cloud  →  dashboard.py        (public URL, unlimited)
Render.com free tier       →  api/main.py          (auto-sleep, wakes on request)
Render.com cron (free)     →  main.py             (hourly pipeline run)
GitHub Actions (free)      →  lint + type-check   (on every push to main)
Supabase free tier         →  PostgreSQL + pgvector (500 MB, unlimited API)
```

**Streamlit Community Cloud:**
1. Push repo to GitHub (`.env` is gitignored ✅)
2. share.streamlit.io → New App → select `dashboard.py`
3. App Settings → Secrets:
```toml
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_KEY = "your_anon_key"   # anon key is read-only — safe to expose
```

**Render.com:**
1. New → Blueprint → connect GitHub repo → it reads `render.yaml` automatically
2. Set env vars in Render Dashboard → Environment

**GitHub Actions:**
- Add all `.env` values as GitHub repository secrets
- Pipeline runs automatically every hour at :05 past
- Manually trigger any phase from Actions → workflow_dispatch

---

## 📊 Corporate Scoring Reference

| Tier | Score | Companies |
|---|---|---|
| 🏆 AI Leaders | +15 | Nvidia · OpenAI · Anthropic · DeepMind · Mistral |
| 🥇 Hyperscalers | +12 | Google · Meta · Microsoft · Apple · Amazon · Tesla |
| 🥈 Notable Tech | +10 | Netflix · Stripe · Databricks · Snowflake · Hugging Face |
| 🥉 Enterprise | +6 | Salesforce · IBM · Intel · Oracle · AMD |
| 🏅 Fortune 500 | +3 | JPMorgan · Goldman · Boeing · Samsung · Sony |
| 👤 Generic signal | +1 | Any bio mentioning "Engineer/Developer/Researcher" |

**Ratings:** BUY ≥ 60 pts · HOLD 25–59 · SELL < 25

---

## 🗂️ Project Structure

```
repoalpha/
├── dashboard.py              # Streamlit War Room UI (596 lines)
├── main.py                   # Enterprise orchestrator — all 5 phases (435 lines)
├── schema_v2.sql             # Full Supabase schema + pgvector + triggers + RLS
├── schema.sql                # v1 schema (kept for reference)
├── render.yaml               # Render.com free deployment blueprint
├── requirements.txt          # All dependencies
├── .env.example              # Full env variable template
├── .gitignore
│
├── agents/
│   ├── harvester.py          # Phase 1: Bright Data GitHub Trending scan
│   ├── enricher.py           # Phase 2: Web Unlocker corporate signal detection
│   ├── analyst.py            # Phase 3: Groq AI hype + license + dossier
│   └── alerter.py            # Phase 4: Slack / Discord / Email alerts
│
├── api/
│   └── main.py               # FastAPI REST API (Swagger at /docs)
│
├── utils/
│   ├── models.py             # Pydantic v2 data models — full type safety
│   ├── logger.py             # loguru structured logging + Supabase audit trail
│   ├── retry.py              # tenacity retry + circuit breaker + rate limiters
│   ├── cache.py              # TTL in-process cache (no Redis needed)
│   └── vector.py             # pgvector semantic search embeddings
│
├── tests/
│   ├── test_models.py        # Pydantic model validation tests
│   ├── test_enricher.py      # Corporate scoring engine unit tests
│   ├── test_analyst.py       # License classification tests
│   └── test_cache.py         # TTL cache tests
│
└── .github/
    └── workflows/
        └── pipeline.yml      # GitHub Actions: hourly pipeline + CI lint/typecheck

.streamlit/
├── config.toml               # Dark terminal theme (#0E1117 · #00FFAA)
└── secrets.toml.example      # Streamlit Cloud secrets template
```

---

## 💰 Zero-Cost Budget Tracker

| Service | Free Limit | RepoAlpha Usage | Status |
|---|---|---|---|
| Bright Data | $250 credit | ~$30–50/hackathon | ✅ 5× headroom |
| Groq 70B | 500 req/day | ~40 req/cycle | ✅ 12× headroom |
| Groq 8B | 14,400 req/day | ~200 req/cycle | ✅ 72× headroom |
| Supabase DB | 500 MB | ~10 MB for 50 repos + history | ✅ 50× headroom |
| Supabase API | Unlimited | Unlimited | ✅ No limit |
| Streamlit Cloud | Unlimited public | 1 app | ✅ Free |
| Render API | 750 hrs/month | ~1 instance | ✅ Free |
| Render Cron | 750 hrs/month | 24 runs/day | ✅ Free |
| GitHub Actions | 2,000 min/month (private) | ~5 min/run × 24 = 120 min/day | ✅ Free (public repo) |
| Resend | 100 emails/day | 1–5 alerts/day | ✅ Free |
| sentence-transformers | Open source, CPU-only | Local embed, no API | ✅ Free |

**Total infrastructure cost: $0.00/month**

---

## 🎯 Judging Criteria Map

| Criterion | How RepoAlpha wins |
|---|---|
| **Presentation** | Bloomberg Terminal UI. Demo: show an M&A analyst's before/after workflow — 5 minutes, no prep needed. |
| **Business Value** | VCs pay $50k+/yr for market mosaic. RepoAlpha delivers Corporate Signal + Acqui-hire targeting for $0. |
| **Tech Application** | Bright Data (scrape) → LangChain (orchestrate) → Groq (analyse) → pgvector (cluster) → Supabase (store) → Streamlit (display). Novel 5-layer chain. |
| **Originality** | "Corporate Signal" engine — behavioural data (who is interested) beats metric data (how many stars). No tool does this. |
