"""
main.py — Enterprise Pipeline Orchestrator
RepoAlpha | Bright Data AI Agents Hackathon 2026

Wires every enterprise layer together:
  Phase 1  harvester   — Bright Data trending scan
  Phase 2  enricher    — Corporate signal detection (Web Unlocker)
  Phase 3  analyst     — Groq AI hype + license + dossier
  Phase 4  alerter     — Slack / Discord / Email on BUY signals
  Phase 5  vectoriser  — pgvector embeddings for semantic search
  Audit    logger      — Supabase pipeline_runs trail
  Cache    TTLCache    — in-process cache to spare Supabase reads
  Retry    tenacity    — exponential backoff on every external call
  Breaker  circuit     — auto-open on sustained API failures

Usage:
  python main.py              # run full pipeline once
  python main.py --loop       # run every 3600 s (for local daemon)
  python main.py --demo       # seed realistic mock data, no API keys
  python main.py --phase 1    # run a single phase (1/2/3/4/5)
"""

import sys
import os
import time
import argparse
import json
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from supabase import create_client, Client

from utils.logger import pipeline_phase, get_logger
from utils.cache  import repo_cache

log = get_logger("main")


# ── Supabase client (shared across all phases) ────────────────────────────────
def make_db() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Harvest
# ══════════════════════════════════════════════════════════════════════════════
def phase_harvest(db: Client) -> int:
    from agents.harvester import fetch_trending_repos, upsert_repository, upsert_stargazers, get_top_stargazers

    with pipeline_phase(db, "harvest") as ctx:
        repos = fetch_trending_repos(limit=50)
        ctx["repos_processed"] = len(repos)

        for repo in repos:
            full_name = repo.get("full_name", "")
            if not full_name:
                continue
            owner, _, name = full_name.partition("/")
            repo.update({"owner": owner, "name": name})
            try:
                repo_id = upsert_repository(repo)
                stargazers = get_top_stargazers(owner, name, top_n=20)
                upsert_stargazers(repo_id, stargazers)
                ctx["stargazers_processed"] += len(stargazers)
                time.sleep(2)
            except Exception as e:
                log.error(f"Harvest error on {full_name}: {e}")

    return ctx["repos_processed"]

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Enrich
# ══════════════════════════════════════════════════════════════════════════════
def phase_enrich(db: Client, batch: int = 50) -> int:
    from agents.enricher import enrich_pending_stargazers, recalculate_repo_scores

    with pipeline_phase(db, "enrich") as ctx:
        enriched = enrich_pending_stargazers(batch_size=batch)
        recalculate_repo_scores()
        ctx["stargazers_processed"] = enriched or 0
        # count new signals generated this run
        ctx["signals_detected"] = (
            db.table("corporate_signals").select("id", count="exact")
            .execute().count or 0
        )

    return ctx["signals_detected"]


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Analyse
# ══════════════════════════════════════════════════════════════════════════════
def phase_analyse(db: Client, batch: int = 20) -> int:
    from agents.analyst import run_analyst

    with pipeline_phase(db, "analyze") as ctx:
        run_analyst(batch_size=batch)
        ctx["repos_processed"] = batch  # analyst processes up to batch repos

    return batch


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — Alert
# ══════════════════════════════════════════════════════════════════════════════
def phase_alert(db: Client) -> int:
    from agents.alerter import check_and_alert

    with pipeline_phase(db, "alert") as ctx:
        # Only check repos with corporate_score >= 30 to avoid noise
        result = (
            db.table("repositories")
            .select("*")
            .gte("corporate_score", 30)
            .order("corporate_score", desc=True)
            .limit(50)
            .execute()
        )
        fired = 0
        for repo in result.data:
            try:
                if check_and_alert(db, repo):
                    fired += 1
            except Exception as e:
                log.warning(f"Alert check failed for {repo.get('full_name')}: {e}")

        ctx["alerts_fired"] = fired
        ctx["repos_processed"] = len(result.data)

    return fired


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5 — Vectorise (semantic search embeddings)
# ══════════════════════════════════════════════════════════════════════════════
def phase_vectorise(db: Client) -> int:
    from utils.vector import upsert_repo_embedding, VECTOR_AVAILABLE

    if not VECTOR_AVAILABLE:
        log.warning("sentence-transformers not installed — skipping vectorisation. "
                    "Run: pip install sentence-transformers")
        return 0

    with pipeline_phase(db, "vectorise") as ctx:
        # Embed repos that don't have vectors yet
        existing = (
            db.table("repo_embeddings").select("repo_id").execute().data
        )
        existing_ids = {r["repo_id"] for r in existing}

        repos = (
            db.table("repositories")
            .select("id, name, description, tech_vibe, market_category, commercial_summary, language")
            .execute()
            .data
        )
        to_embed = [r for r in repos if r["id"] not in existing_ids]
        log.info(f"Vectorising {len(to_embed)} new repos...")

        ok = 0
        for repo in to_embed:
            try:
                if upsert_repo_embedding(db, repo["id"], repo):
                    ok += 1
            except Exception as e:
                log.warning(f"Embed failed for {repo.get('id')}: {e}")

        ctx["repos_processed"] = ok

    return ok


# ══════════════════════════════════════════════════════════════════════════════
# Score-history snapshot (no phase wrapper — lightweight trigger call)
# ══════════════════════════════════════════════════════════════════════════════
def snapshot_scores(db: Client) -> None:
    """
    Manually snapshot current scores into score_history.
    The Supabase trigger handles this automatically on UPDATE,
    but we also call it here as a safety net when running locally.
    """
    log.info("Snapshotting score history...")
    repos = (
        db.table("repositories")
        .select("id, full_name, corporate_score, ai_hype_score, stars_count")
        .execute()
        .data
    )
    rows = [
        {
            "repo_id":        r["id"],
            "full_name":      r["full_name"],
            "corporate_score": r.get("corporate_score", 0),
            "ai_hype_score":   r.get("ai_hype_score",   0),
            "stars_count":     r.get("stars_count",      0),
            "recorded_at":    datetime.now(timezone.utc).isoformat(),
        }
        for r in repos
    ]
    if rows:
        db.table("score_history").insert(rows).execute()
    log.info(f"Snapshotted {len(rows)} repo scores.")


# ══════════════════════════════════════════════════════════════════════════════
# Demo seed — no API keys needed, good for UI showcase
# ══════════════════════════════════════════════════════════════════════════════
def seed_demo(db: Client) -> None:
    log.info("Seeding demo data...")

    demo_repos = [
        {
            "full_name": "openai/triton",            "name": "triton",
            "owner": "openai",
            "description": "A language and compiler for writing highly efficient custom Deep-Learning primitives.",
            "language": "Python",  "stars_count": 13200, "forks_count": 1540,
            "url": "https://github.com/openai/triton", "trending_rank": 1,
            "corporate_score": 145, "ai_hype_score": 9,
            "commercial_summary": "GPU kernel compiler that lets AI teams write CUDA-level performance without C++ — strategic acqui-hire for any hyperscaler building proprietary AI silicon.",
            "tech_vibe": "GPU Kernel Ops", "market_category": "AI/ML",
            "license_type": "MIT", "license_label": "Enterprise Ready", "license_color": "green",
            "rating": "BUY",
            "hiring_dossier": json.dumps({"contributors": [{"login": "ptillet", "contributions": 1823, "profile_url": "https://github.com/ptillet"}, {"login": "wunki", "contributions": 412, "profile_url": "https://github.com/wunki"}], "acqui_hire_rating": "High", "acqui_hire_rationale": "Elite GPU compiler team. Core contributor has deep LLVM background — extremely rare.", "key_talent": "ptillet", "red_flags": "Single-contributor dependency."}),
            "scraped_at": "2026-05-26T00:00:00Z", "analyzed_at": "2026-05-26T01:00:00Z",
        },
        {
            "full_name": "vllm-project/vllm",         "name": "vllm",
            "owner": "vllm-project",
            "description": "High-throughput and memory-efficient inference and serving engine for LLMs.",
            "language": "Python",  "stars_count": 28900, "forks_count": 4120,
            "url": "https://github.com/vllm-project/vllm", "trending_rank": 2,
            "corporate_score": 198, "ai_hype_score": 10,
            "commercial_summary": "De-facto open-source LLM runtime already in production at Nvidia, Amazon, and dozens of AI startups — critical infrastructure acquisition target.",
            "tech_vibe": "LLM Inference", "market_category": "AI/ML",
            "license_type": "apache-2.0", "license_label": "Enterprise Ready", "license_color": "green",
            "rating": "BUY",
            "hiring_dossier": json.dumps({"contributors": [{"login": "WoosukKwon", "contributions": 2201, "profile_url": "https://github.com/WoosukKwon"}, {"login": "zhuohan123", "contributions": 1890, "profile_url": "https://github.com/zhuohan123"}, {"login": "comaniac", "contributions": 934, "profile_url": "https://github.com/comaniac"}], "acqui_hire_rating": "High", "acqui_hire_rationale": "UC Berkeley RISE Lab team. High velocity, multi-contributor. Acqui-hire window closing fast.", "key_talent": "WoosukKwon", "red_flags": "Multiple active competing acquirers (Nvidia, Amazon)."}),
            "scraped_at": "2026-05-26T00:00:00Z", "analyzed_at": "2026-05-26T01:00:00Z",
        },
        {
            "full_name": "grafana/alloy",              "name": "alloy",
            "owner": "grafana",
            "description": "OpenTelemetry Collector distribution with programmable pipelines.",
            "language": "Go",  "stars_count": 5800, "forks_count": 780,
            "url": "https://github.com/grafana/alloy", "trending_rank": 3,
            "corporate_score": 67, "ai_hype_score": 6,
            "commercial_summary": "Vendor-neutral telemetry pipeline that could become the standard for enterprise observability, threatening Datadog and Splunk data ingestion moats.",
            "tech_vibe": "Observability", "market_category": "Infrastructure",
            "license_type": "apache-2.0", "license_label": "Enterprise Ready", "license_color": "green",
            "rating": "BUY",
            "hiring_dossier": json.dumps({"contributors": [{"login": "rfratto", "contributions": 3102, "profile_url": "https://github.com/rfratto"}, {"login": "mattdurham", "contributions": 890, "profile_url": "https://github.com/mattdurham"}], "acqui_hire_rating": "Medium", "acqui_hire_rationale": "Healthy multi-contributor project under Grafana Labs umbrella.", "key_talent": "rfratto", "red_flags": "Already owned by Grafana Labs — acqui-hire path is indirect."}),
            "scraped_at": "2026-05-26T00:00:00Z", "analyzed_at": "2026-05-26T01:00:00Z",
        },
        {
            "full_name": "lobehub/lobe-chat",          "name": "lobe-chat",
            "owner": "lobehub",
            "description": "An open-source, modern-design AI chat framework.",
            "language": "TypeScript", "stars_count": 45200, "forks_count": 9800,
            "url": "https://github.com/lobehub/lobe-chat", "trending_rank": 4,
            "corporate_score": 18,  "ai_hype_score": 8,
            "commercial_summary": "Viral consumer AI chat UI — significant as a distribution-channel acquisition for any LLM provider wanting a ready-made user base.",
            "tech_vibe": "AI Chat UX",  "market_category": "AI/ML",
            "license_type": "agpl-3.0", "license_label": "Legal Minefield 💀", "license_color": "red",
            "rating": "SELL",
            "hiring_dossier": json.dumps({"contributors": [{"login": "arvinxx", "contributions": 3812, "profile_url": "https://github.com/arvinxx"}, {"login": "canisminor1990", "contributions": 1205, "profile_url": "https://github.com/canisminor1990"}], "acqui_hire_rating": "High", "acqui_hire_rationale": "Exceptional frontend AI UX talent. AGPL license blocks direct product use but team is highly portable.", "key_talent": "arvinxx", "red_flags": "AGPL-3.0 requires full open-sourcing of any derivative — prohibitive for commercial SaaS."}),
            "scraped_at": "2026-05-26T00:00:00Z", "analyzed_at": "2026-05-26T01:00:00Z",
        },
        {
            "full_name": "cube-js/cube",               "name": "cube",
            "owner": "cube-js",
            "description": "The Semantic Layer for Building Data Applications.",
            "language": "TypeScript", "stars_count": 17900, "forks_count": 1780,
            "url": "https://github.com/cube-js/cube", "trending_rank": 5,
            "corporate_score": 32, "ai_hype_score": 5,
            "commercial_summary": "Open-source semantic layer that could be acquired by any major BI player (Looker, Tableau, PowerBI) to add developer distribution.",
            "tech_vibe": "Data Semantics", "market_category": "Data/Analytics",
            "license_type": "apache-2.0", "license_label": "Enterprise Ready", "license_color": "green",
            "rating": "HOLD",
            "hiring_dossier": json.dumps({"contributors": [{"login": "paveltiunov", "contributions": 4520, "profile_url": "https://github.com/paveltiunov"}], "acqui_hire_rating": "Medium", "acqui_hire_rationale": "Niche BI domain expert with deep semantic layer knowledge.", "key_talent": "paveltiunov", "red_flags": "Single core contributor — key-person risk."}),
            "scraped_at": "2026-05-26T00:00:00Z", "analyzed_at": "2026-05-26T01:00:00Z",
        },
    ]

    for repo in demo_repos:
        db.table("repositories").upsert(repo, on_conflict="full_name").execute()

    demo_signals = [
        ("openai/triton",       "nvidia_eng_1",   "Nvidia",      15),
        ("openai/triton",       "msft_dev_1",     "Microsoft",   12),
        ("openai/triton",       "google_ml_1",    "Google",      12),
        ("openai/triton",       "amazon_1",       "Amazon",      12),
        ("openai/triton",       "meta_research",  "Meta",        12),
        ("openai/triton",       "amd_arch",       "Amd",          6),
        ("vllm-project/vllm",   "nvidia_gpu_2",   "Nvidia",      15),
        ("vllm-project/vllm",   "anthropic_1",    "Anthropic",   15),
        ("vllm-project/vllm",   "openai_inf",     "Openai",      15),
        ("vllm-project/vllm",   "aws_infra",      "Aws",         12),
        ("vllm-project/vllm",   "google_tpu",     "Google",      12),
        ("vllm-project/vllm",   "databricks_ml",  "Databricks",  10),
        ("vllm-project/vllm",   "msft_azure",     "Microsoft",   12),
        ("grafana/alloy",       "cloudflare_sre", "Cloudflare",  10),
        ("grafana/alloy",       "stripe_eng",     "Stripe",      10),
        ("grafana/alloy",       "netflix_obs",    "Netflix",     10),
        ("cube-js/cube",        "salesforce_bi",  "Salesforce",   6),
        ("cube-js/cube",        "snowflake_data", "Snowflake",   10),
    ]

    repo_map = {r["full_name"]: r["id"]
                for r in db.table("repositories").select("id,full_name").execute().data}

    for full_name, login, company, score in demo_signals:
        repo_id = repo_map.get(full_name)
        if not repo_id:
            continue
        sg = db.table("stargazers").upsert({
            "repo_id": repo_id, "github_login": login,
            "profile_url": f"https://github.com/{login}",
            "company": company, "company_score": score, "enriched": True,
        }, on_conflict="repo_id,github_login").execute()
        sg_id = sg.data[0]["id"] if sg.data else None
        if sg_id:
            db.table("corporate_signals").upsert({
                "repo_id": repo_id, "stargazer_id": sg_id,
                "github_login": login, "company": company, "signal_score": score,
            }, on_conflict="repo_id,stargazer_id").execute()

    # Seed score_history so the sparkline charts have data
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    for repo in demo_repos:
        rid = repo_map.get(repo["full_name"])
        if not rid:
            continue
        base = repo["corporate_score"]
        for days_ago in range(6, -1, -1):
            jitter = (hash(repo["full_name"] + str(days_ago)) % 20) - 10
            db.table("score_history").insert({
                "repo_id": rid, "full_name": repo["full_name"],
                "corporate_score": max(0, base - days_ago * 8 + jitter),
                "ai_hype_score": repo["ai_hype_score"],
                "stars_count": repo["stars_count"],
                "recorded_at": (now - timedelta(days=days_ago)).isoformat(),
            }).execute()

    # Seed a pipeline run so the monitor tab has content
    for phase in ["harvest", "enrich", "analyze", "alert"]:
        db.table("pipeline_runs").insert({
            "phase": phase, "status": "success",
            "repos_processed": len(demo_repos),
            "stargazers_processed": len(demo_signals),
            "signals_detected": len(demo_signals),
            "alerts_fired": 2 if phase == "alert" else 0,
            "duration_seconds": 42.3,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    # Seed two alert log rows
    for full_name in ["vllm-project/vllm", "openai/triton"]:
        rid = repo_map.get(full_name)
        db.table("alert_log").insert({
            "repo_full_name": full_name,
            "repo_id": rid,
            "corporate_score_at_alert": repo_map.get(full_name) and 100,
            "rating": "BUY",
            "channels_notified": 2,
            "alerted_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    log.info(
        f"Demo seed complete — {len(demo_repos)} repos, "
        f"{len(demo_signals)} signals, score history + pipeline runs."
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def run_full_pipeline(db: Client) -> None:
    start = datetime.now(timezone.utc)
    log.info("═" * 65)
    log.info("  REPOALPHA — Enterprise Intelligence Pipeline")
    log.info(f"  {start.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log.info("═" * 65)

    phases = [
        ("Phase 1 — Harvest",   lambda: phase_harvest(db)),
        ("Phase 2 — Enrich",    lambda: phase_enrich(db, batch=50)),
        ("Phase 3 — Analyse",   lambda: phase_analyse(db, batch=20)),
        ("Phase 4 — Alert",     lambda: phase_alert(db)),
        ("Phase 5 — Vectorise", lambda: phase_vectorise(db)),
        ("Snapshot scores",     lambda: snapshot_scores(db)),
    ]

    for name, fn in phases:
        try:
            fn()
        except Exception as e:
            log.error(f"{name} failed — skipping and continuing. Error: {e}")

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    log.success(f"Full pipeline complete in {elapsed:.0f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RepoAlpha Pipeline Orchestrator")
    parser.add_argument("--loop",  action="store_true", help="Run every hour indefinitely")
    parser.add_argument("--demo",  action="store_true", help="Seed mock data (no API keys needed)")
    parser.add_argument("--phase", type=int, choices=[1,2,3,4,5],
                        help="Run a single phase only (1=harvest 2=enrich 3=analyse 4=alert 5=vector)")
    args = parser.parse_args()

    db = make_db()

    if args.demo:
        seed_demo(db)
        sys.exit(0)

    phase_fns = {
        1: lambda: phase_harvest(db),
        2: lambda: phase_enrich(db),
        3: lambda: phase_analyse(db),
        4: lambda: phase_alert(db),
        5: lambda: phase_vectorise(db),
    }

    if args.phase:
        phase_fns[args.phase]()
        sys.exit(0)

    if args.loop:
        while True:
            run_full_pipeline(db)
            log.info("Sleeping 3600s until next cycle...")
            time.sleep(3600)
    else:
        run_full_pipeline(db)
