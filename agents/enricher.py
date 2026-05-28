"""
enricher.py — Phase 2: The Corporate Signal Engine
RepoAlpha | Bright Data AI Agents Hackathon 2026

Uses Bright Data Web Unlocker + LangChain to:
  1. Scrape each stargazer's public GitHub profile
  2. Extract company/bio from the profile page
  3. Score each company against a Fortune 500 / Big Tech weight map
  4. Aggregate scores per repo and write `corporate_signals` rows

This is the core competitive moat of RepoAlpha:
  "A Software Engineer at Nvidia starring your repo is a 10x stronger
   signal than an anonymous user."
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

from langchain.tools import tool

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ENRICHER] %(message)s")
log = logging.getLogger(__name__)

# ─── Clients ────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BRIGHTDATA_API_TOKEN = os.environ["BRIGHTDATA_API_TOKEN"]
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "web_unlocker1")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Corporate Scoring Map ───────────────────────────────────────────────────
# Tier 1 (15 pts): Hyperscalers & AI leaders — highest M&A signal
# Tier 2 (10 pts): Major tech incumbents
# Tier 3 (6 pts): Enterprise software / notable unicorns
# Tier 4 (3 pts): Any Fortune 500 / known company

COMPANY_SCORES: dict[str, int] = {
    # Tier 1 — Maximum signal
    "nvidia": 15, "openai": 15, "anthropic": 15, "google": 12, "deepmind": 15,
    "meta": 12, "microsoft": 12, "apple": 12, "amazon": 12, "aws": 12,
    "tesla": 12, "spacex": 12,

    # Tier 2 — High signal
    "netflix": 10, "uber": 10, "airbnb": 10, "stripe": 10, "databricks": 10,
    "snowflake": 10, "palantir": 10, "cloudflare": 10, "gitlab": 10,
    "hashicorp": 10, "hugging face": 10, "mistral": 12, "cohere": 10,
    "together.ai": 10, "replicate": 10, "inflection": 10,

    # Tier 3 — Notable
    "salesforce": 6, "oracle": 6, "sap": 6, "ibm": 6, "intel": 6,
    "amd": 6, "qualcomm": 6, "arm": 6, "redis": 6, "mongodb": 6,
    "elastic": 6, "confluent": 6, "dbt labs": 6, "fivetran": 6,

    # Tier 4 — Fortune 500 / enterprise catch-all (generic terms below)
}

FORTUNE_500_PATTERNS = [
    r"\bjp\s?morgan\b", r"\bgoldman\b", r"\bblackrock\b", r"\bciti\b",
    r"\bboeing\b", r"\blockheed\b", r"\braytheon\b", r"\bsiemens\b",
    r"\bsony\b", r"\bsamsung\b", r"\btsmc\b",
]


def score_company(raw_text: str) -> tuple[str, int]:
    """
    Given a raw bio/company string from GitHub, return (normalized_company, score).
    Uses both the explicit map and regex patterns for Fortune 500 catchall.
    """
    if not raw_text:
        return "", 0

    text_lower = raw_text.lower()

    # Check explicit tier map
    for company, score in COMPANY_SCORES.items():
        if company in text_lower:
            return company.title(), score

    # Fortune 500 regex catch-all → 3 pts
    for pattern in FORTUNE_500_PATTERNS:
        if re.search(pattern, text_lower):
            match = re.search(pattern, text_lower)
            return match.group().strip().title(), 3

    # Any string mentioning known signals
    if any(kw in text_lower for kw in ["engineer", "developer", "scientist", "researcher", "architect"]):
        # Generic known-employee signal — 1 pt
        return raw_text[:60], 1

    return raw_text[:60], 0


# ─── Bright Data Web Unlocker Tool ──────────────────────────────────────────

def scrape_github_profile_via_brightdata(profile_url: str) -> Optional[str]:
    """
    Uses Bright Data's Web Unlocker proxy to fetch a GitHub profile page,
    bypassing anti-scraping measures and rate limits.

    Returns raw HTML or None on failure.
    """
    proxy_host = f"brd.superproxy.io:22225"
    proxy_user = f"brd-customer-{os.environ['BRIGHTDATA_CUSTOMER_ID']}-zone-{BRIGHTDATA_ZONE}"
    proxy_pass = os.environ["BRIGHTDATA_ZONE_PASSWORD"]

    proxies = {
        "http": f"http://{proxy_user}:{proxy_pass}@{proxy_host}",
        "https": f"http://{proxy_user}:{proxy_pass}@{proxy_host}",
    }

    try:
        resp = requests.get(
            profile_url,
            proxies=proxies,
            verify=False,  # Bright Data's SSL cert
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RepoAlpha/1.0)"},
        )
        if resp.status_code == 200:
            return resp.text
        log.warning(f"Profile fetch HTTP {resp.status_code}: {profile_url}")
        return None
    except Exception as e:
        log.error(f"Web Unlocker error for {profile_url}: {e}")
        return None


def parse_github_profile(html: str) -> dict:
    """
    Parses company, bio, name, and location from GitHub profile HTML.
    GitHub's public profile renders these in <span> / <div> with known classes.
    """
    soup = BeautifulSoup(html, "html.parser")

    def safe_text(selector: str, attr: str = None) -> str:
        el = soup.select_one(selector)
        if not el:
            return ""
        return (el.get(attr) or el.get_text()).strip() if attr else el.get_text().strip()

    company = safe_text(".p-org")          # e.g. "NVIDIA"
    bio = safe_text(".p-note")             # free-text bio
    name = safe_text(".p-name")
    location = safe_text(".p-label")
    email = safe_text("a[href^='mailto:']", "href")

    # Combine company + bio for scoring (bio often has "@ Google" style hints)
    combined = f"{company} {bio}"

    return {
        "name": name,
        "company_raw": company,
        "bio": bio,
        "location": location,
        "email": email.replace("mailto:", "") if email else "",
        "combined_text": combined,
    }


# ─── LangChain Tool ─────────────────────────────────────────────────────────

@tool
def enrich_stargazer_profile(profile_url: str) -> str:
    """
    LangChain Tool: Given a GitHub profile URL, scrape it via Bright Data,
    extract company/bio, compute a corporate score, and return a JSON string
    with keys: login, company, bio, company_score, email.
    """
    html = scrape_github_profile_via_brightdata(profile_url)
    if not html:
        return json.dumps({"error": "failed to fetch", "profile_url": profile_url})

    profile = parse_github_profile(html)
    company, score = score_company(profile["combined_text"])

    return json.dumps({
        "profile_url": profile_url,
        "name": profile["name"],
        "company": company,
        "bio": profile["bio"],
        "email": profile["email"],
        "location": profile["location"],
        "company_score": score,
    })


# ─── Main Enrichment Loop ───────────────────────────────────────────────────

def enrich_pending_stargazers(batch_size: int = 50) -> None:
    """
    Fetches un-enriched stargazers from Supabase, runs them through the
    Bright Data → parse → score pipeline, and writes results back.

    Processes `batch_size` at a time to avoid overwhelming Bright Data
    within the $250 credit budget.
    """
    log.info("Fetching unenriched stargazers...")

    result = (
        supabase.table("stargazers")
        .select("id, repo_id, github_login, profile_url")
        .eq("enriched", False)
        .limit(batch_size)
        .execute()
    )
    rows = result.data
    log.info(f"Found {len(rows)} stargazers to enrich.")

    for row in rows:
        profile_url = row.get("profile_url")
        if not profile_url:
            continue

        log.info(f"Enriching: {row['github_login']} ({profile_url})")

        html = scrape_github_profile_via_brightdata(profile_url)
        if not html:
            # Mark as attempted so we don't retry endlessly
            supabase.table("stargazers").update({"enriched": True, "company_score": 0}).eq("id", row["id"]).execute()
            continue

        profile = parse_github_profile(html)
        company, score = score_company(profile["combined_text"])

        # Update stargazer record
        supabase.table("stargazers").update({
            "company": company,
            "bio": profile["bio"],
            "email": profile["email"],
            "location": profile["location"],
            "company_score": score,
            "enriched": True,
            "enriched_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()

        # Write corporate signal record for any meaningful signal
        if score > 0:
            supabase.table("corporate_signals").upsert({
                "repo_id": row["repo_id"],
                "stargazer_id": row["id"],
                "github_login": row["github_login"],
                "company": company,
                "signal_score": score,
                "raw_bio": profile["bio"],
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="repo_id,stargazer_id").execute()
            log.info(f"  → Signal: {company} (+{score} pts) for user {row['github_login']}")
        else:
            log.info(f"  → No corporate signal for {row['github_login']}")

        # Polite delay to respect Bright Data credits
        time.sleep(1.5)


def recalculate_repo_scores() -> None:
    """
    After enrichment, recalculates the aggregate `corporate_score`
    for each repository by summing its stargazers' company_scores.
    """
    log.info("Recalculating aggregate corporate scores...")

    result = supabase.table("repositories").select("id, full_name").execute()
    for repo in result.data:
        signals = (
            supabase.table("corporate_signals")
            .select("signal_score")
            .eq("repo_id", repo["id"])
            .execute()
        )
        total = sum(s["signal_score"] for s in signals.data)
        supabase.table("repositories").update({"corporate_score": total}).eq("id", repo["id"]).execute()
        log.info(f"  {repo['full_name']} → Corporate Score: {total}")


if __name__ == "__main__":
    while True:
        enrich_pending_stargazers(batch_size=50)
        recalculate_repo_scores()
        log.info("Enrichment cycle done. Sleeping 600s...")
        time.sleep(600)
