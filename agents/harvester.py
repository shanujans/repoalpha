"""
harvester.py — Phase 1: The GitHub Trend Scanner
RepoAlpha | Bright Data AI Agents Hackathon 2026

This module discovers trending repositories via the GitHub Search API
and stores discovered repositories + top stargazers into Supabase.

Flow:
  1. Call GitHub Search API → get top 50 trending repos (created last 7 days)
  2. For each repo, call GitHub API (public) → get top 20 stargazers
  3. Persist raw data to Supabase `repositories` and `stargazers` tables
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [HARVESTER] %(message)s")
log = logging.getLogger(__name__)

# ─── Clients ────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Trending Repo Discovery (GitHub Search API) ────────────────────────────

def fetch_trending_repos(limit: int = 50) -> list[dict]:
    """
    Fetches trending repos via GitHub Search API (no Bright Data needed).
    Searches for repos created in the last 7 days sorted by stars.
    Free, no API key required (60 req/hr without token, 5000 with).
    """
    from datetime import datetime, timedelta
    since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    headers = {}
    if gh_token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {gh_token}"

    url = "https://api.github.com/search/repositories"
    params = {
        "q": f"created:>{since}",
        "sort": "stars",
        "order": "desc",
        "per_page": min(limit, 50),
    }

    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("items", [])

    repos = []
    for i, item in enumerate(items):
        repos.append({
            "full_name":    item["full_name"],
            "name":         item["name"],
            "owner":        item["owner"]["login"],
            "description":  item.get("description") or "",
            "language":     item.get("language") or "",
            "stars_count":  item["stargazers_count"],
            "forks_count":  item["forks_count"],
            "url":          item["html_url"],
            "trending_rank": i + 1,
        })
    log.info(f"GitHub Search returned {len(repos)} trending repos.")
    return repos


# ─── GitHub Public API: Get top-N stargazers ────────────────────────────────

def get_top_stargazers(owner: str, repo: str, top_n: int = 20) -> list[dict]:
    """
    Uses GitHub's public REST API (no auth needed, 60 req/hr limit)
    to fetch the most recent stargazers of a repository.

    Returns a list of {login, avatar_url, html_url, profile_url}
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/stargazers"
    headers = {"Accept": "application/vnd.github.v3.star+json"}

    # Optional: use GITHUB_TOKEN for 5,000 req/hr (free)
    if gh_token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {gh_token}"

    resp = requests.get(url, headers=headers, params={"per_page": top_n}, timeout=15)
    if resp.status_code == 403:
        log.warning(f"GitHub rate limit hit for {owner}/{repo}. Sleeping 60s.")
        time.sleep(60)
        return []
    resp.raise_for_status()

    stargazers = []
    for entry in resp.json():
        user = entry.get("user", entry)  # star+json wraps user
        stargazers.append({
            "login": user.get("login"),
            "avatar_url": user.get("avatar_url"),
            "profile_url": user.get("html_url"),
            "starred_at": entry.get("starred_at"),
        })
    return stargazers


# ─── Supabase Persistence ───────────────────────────────────────────────────

def upsert_repository(repo_data: dict) -> str:
    """
    Upserts a repository record. Returns the repo's UUID from Supabase.
    Uses upsert to avoid duplicates across hourly polls.
    """
    record = {
        "full_name": repo_data.get("full_name") or f"{repo_data.get('owner')}/{repo_data.get('name')}",
        "name": repo_data.get("name"),
        "owner": repo_data.get("owner"),
        "description": repo_data.get("description", ""),
        "language": repo_data.get("language", ""),
        "stars_count": repo_data.get("stars_count", 0),
        "forks_count": repo_data.get("forks_count", 0),
        "url": repo_data.get("url") or repo_data.get("html_url"),
        "trending_rank": repo_data.get("trending_rank", 0),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "corporate_score": 0,  # enriched in Phase 2
        "ai_hype_score": 0,    # enriched in Phase 3
        "license_type": "",    # enriched in Phase 3
    }

    result = (
        supabase.table("repositories")
        .upsert(record, on_conflict="full_name")
        .execute()
    )
    repo_id = result.data[0]["id"]
    log.info(f"Upserted repo → {record['full_name']} (ID: {repo_id})")
    return repo_id


def upsert_stargazers(repo_id: str, stargazers: list[dict]) -> None:
    """
    Batch-upserts stargazer records, linking them to a repository.
    Uses upsert to avoid duplicates if the same user stars multiple repos.
    """
    records = [
        {
            "repo_id": repo_id,
            "github_login": sg["login"],
            "avatar_url": sg.get("avatar_url"),
            "profile_url": sg.get("profile_url"),
            "starred_at": sg.get("starred_at"),
            "company": None,       # filled by enricher.py
            "company_score": 0,    # filled by enricher.py
            "enriched": False,
        }
        for sg in stargazers
        if sg.get("login")
    ]

    if records:
        supabase.table("stargazers").upsert(records, on_conflict="repo_id,github_login").execute()
        log.info(f"Upserted {len(records)} stargazers for repo {repo_id}")


# ─── Main Orchestration ─────────────────────────────────────────────────────

def run_harvest():
    """
    Full harvest cycle:
      1. Fetch trending repos from GitHub Search API
      2. For each repo: upsert + fetch stargazers
    """
    log.info("═" * 60)
    log.info("REPOALPHA HARVESTER — Starting cycle")
    log.info("═" * 60)

    repos = fetch_trending_repos(limit=50)

    for i, repo in enumerate(repos):
        log.info(f"Processing [{i+1}/{len(repos)}] → {repo['full_name']}")

        owner = repo["owner"]
        name = repo["name"]

        try:
            repo_id = upsert_repository(repo)
            stargazers = get_top_stargazers(owner, name, top_n=20)
            upsert_stargazers(repo_id, stargazers)
            # Respect GitHub public API rate limit (60 req/hr without token)
            time.sleep(2)
        except Exception as e:
            log.error(f"Error processing {repo['full_name']}: {e}")
            continue

    log.info("Harvest cycle complete.")


if __name__ == "__main__":
    # Run once immediately, then every hour
    while True:
        run_harvest()
        log.info("Sleeping 3600s until next harvest cycle...")
        time.sleep(3600)