"""
analyst.py — Phase 3: The AI Analyst
RepoAlpha | Bright Data AI Agents Hackathon 2026

Uses Groq (Llama 3 70B) to perform:
  1. README commercial analysis + hype scoring (1-10)
  2. LICENSE risk audit → "Enterprise Ready" vs "Viral Risk"
  3. Top contributor "Hiring Dossier" generation

All within Groq's free tier:
  - llama3-70b-8192: 6,000 tokens/min, 500 req/day (free)
  - llama3-8b-8192: 30,000 tokens/min, 14,400 req/day (free)
  → We use 70B for quality analysis, 8B for bulk contributor parsing.
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

from supabase import create_client, Client
from openai import OpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ANALYST] %(message)s")
log = logging.getLogger(__name__)

# ─── Clients ────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
aiml_client = OpenAI(
    api_key=os.environ.get("AIML_API_KEY") or os.environ.get("GROQ_API_KEY"),
    base_url="https://api.aimlapi.com/v1",
)

# ─── License Classification ──────────────────────────────────────────────────

LICENSE_TAGS = {
    # Green: Enterprise-safe licenses
    "mit": ("Enterprise Ready", "✅", "green"),
    "apache-2.0": ("Enterprise Ready", "✅", "green"),
    "apache 2.0": ("Enterprise Ready", "✅", "green"),
    "bsd-2-clause": ("Enterprise Ready", "✅", "green"),
    "bsd-3-clause": ("Enterprise Ready", "✅", "green"),
    "isc": ("Enterprise Ready", "✅", "green"),
    "cc0": ("Enterprise Ready", "✅", "green"),

    # Yellow: Weak copyleft — use with care
    "lgpl": ("Weak Copyleft", "⚠️", "yellow"),
    "mpl-2.0": ("Weak Copyleft", "⚠️", "yellow"),
    "cddl": ("Weak Copyleft", "⚠️", "yellow"),

    # Red: Strong copyleft / viral licenses
    "gpl-2.0": ("Viral Risk 🚨", "🚨", "red"),
    "gpl-3.0": ("Viral Risk 🚨", "🚨", "red"),
    "agpl-3.0": ("Legal Minefield 💀", "💀", "red"),
    "agpl": ("Legal Minefield 💀", "💀", "red"),
    "sspl": ("Legal Minefield 💀", "💀", "red"),
    "commons clause": ("Commercial Restriction", "🔴", "red"),
    "busl": ("Commercial Restriction", "🔴", "red"),  # Business Source License
}

UNLICENSED_TAG = ("No License (All Rights Reserved)", "⛔", "red")


def classify_license(license_str: str) -> tuple[str, str, str]:
    """
    Returns (label, emoji, color) for a license string.
    Handles common variations in license naming.
    """
    if not license_str:
        return UNLICENSED_TAG

    lower = license_str.lower().strip()
    for key, tag in LICENSE_TAGS.items():
        if key in lower:
            return tag

    return ("Unknown License", "❓", "yellow")


# ─── GitHub Data Fetchers ────────────────────────────────────────────────────

def fetch_readme(owner: str, repo: str) -> str:
    """Fetches raw README content via GitHub API (public, no auth)."""
    headers = {}
    if gh_token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {gh_token}"

    # Try README.md first, then README
    for filename in ["README.md", "readme.md", "README.rst", "README"]:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filename}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            import base64
            content = resp.json().get("content", "")
            return base64.b64decode(content).decode("utf-8", errors="ignore")[:8000]  # cap tokens

    return ""


def fetch_license(owner: str, repo: str) -> str:
    """Fetches license info from GitHub API."""
    headers = {}
    if gh_token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {gh_token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/license"
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("license", {}).get("spdx_id", "")
    return ""


def fetch_top_contributors(owner: str, repo: str, top_n: int = 5) -> list[dict]:
    """Fetches top contributors via GitHub API."""
    headers = {}
    if gh_token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {gh_token}"

    url = f"https://api.github.com/repos/{owner}/{repo}/contributors"
    resp = requests.get(url, headers=headers, params={"per_page": top_n}, timeout=15)
    if resp.status_code == 200:
        return [
            {
                "login": c["login"],
                "contributions": c["contributions"],
                "profile_url": c["html_url"],
                "avatar_url": c["avatar_url"],
            }
            for c in resp.json()[:top_n]
        ]
    return []


# ─── Groq LLM Analysis ──────────────────────────────────────────────────────

def analyze_readme_with_groq(readme: str, repo_name: str) -> dict:
    """
    Uses Groq llama3-70b to analyze README for:
      - commercial_summary (1 sentence)
      - hype_score (1-10 integer)
      - tech_vibe (2-3 word trend label)
      - market_category (AI/DevTools/Security/Data/etc.)

    Returns a structured dict. Falls back to defaults on parse failure.
    """
    if not readme:
        return {
            "commercial_summary": "No README available.",
            "hype_score": 0,
            "tech_vibe": "Unknown",
            "market_category": "Unknown",
        }

    # Truncate to ~2000 chars to stay within token limits
    readme_excerpt = readme[:2500]

    prompt = f"""You are a senior technology analyst at a top-tier VC firm.
Analyze this GitHub repository README for "{repo_name}" and respond ONLY with a valid JSON object. No markdown. No explanation.

README:
{readme_excerpt}

Respond with exactly this JSON structure:
{{
  "commercial_summary": "One sentence describing the project's commercial value and potential.",
  "hype_score": <integer 1-10 where 1=niche/academic and 10=viral/industry-transforming>,
  "tech_vibe": "<2-3 word trend label like 'AI Inference', 'DevSecOps', 'Edge ML'>",
  "market_category": "<one of: AI/ML, DevTools, Security, Data/Analytics, Infrastructure, Web3, Robotics, Other>"
}}"""

    try:
        response = aiml_client.chat.completions.create(
            model="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()

        # Strip any markdown fences if model ignores instructions
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        result = json.loads(raw)

        # Validate hype_score range
        result["hype_score"] = max(1, min(10, int(result.get("hype_score", 5))))
        return result

    except (json.JSONDecodeError, KeyError, Exception) as e:
        log.warning(f"Groq parse error for {repo_name}: {e}")
        return {
            "commercial_summary": "Analysis unavailable.",
            "hype_score": 5,
            "tech_vibe": "Tech",
            "market_category": "Other",
        }


def build_hiring_dossier_with_groq(contributors: list[dict], repo_name: str) -> dict:
    """
    Uses Groq llama3-8b (higher rate limits) to generate a structured
    'Hiring Dossier' for the top contributors.

    Returns a dict with key contributor insights.
    """
    if not contributors:
        return {"contributors": [], "acqui_hire_note": "No contributor data."}

    contrib_text = "\n".join([
        f"- {c['login']}: {c['contributions']} commits, profile: {c['profile_url']}"
        for c in contributors
    ])

    prompt = f"""You are an M&A talent analyst. Based on these top contributors to the GitHub repo "{repo_name}", write a brief acqui-hire opportunity assessment.

Contributors:
{contrib_text}

Respond ONLY with a valid JSON object:
{{
  "acqui_hire_rating": "<one of: High, Medium, Low>",
  "acqui_hire_rationale": "<1-2 sentences on team quality signals>",
  "key_talent": "<name of most valuable contributor based on commits>",
  "red_flags": "<any concerns like single contributor, no recent commits, etc.>"
}}"""

    try:
        response = aiml_client.chat.completions.create(
            model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.4,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        dossier = json.loads(raw)
        dossier["contributors"] = contributors
        return dossier

    except Exception as e:
        log.warning(f"Hiring dossier parse error for {repo_name}: {e}")
        return {
            "contributors": contributors,
            "acqui_hire_rating": "Unknown",
            "acqui_hire_rationale": "Analysis unavailable.",
            "key_talent": contributors[0]["login"] if contributors else "N/A",
            "red_flags": "N/A",
        }


# ─── Main Analyst Loop ──────────────────────────────────────────────────────

def analyze_repository(repo: dict) -> None:
    """
    Full analysis pipeline for a single repository:
      1. Fetch README → Groq analysis → hype score + summary
      2. Fetch LICENSE → classify risk
      3. Fetch contributors → build hiring dossier
      4. Persist all results to Supabase
    """
    full_name = repo["full_name"]
    owner, _, name = full_name.partition("/")

    log.info(f"Analyzing: {full_name}")

    # Step 1: README analysis
    readme = fetch_readme(owner, name)
    analysis = analyze_readme_with_groq(readme, full_name)
    time.sleep(1)  # Respect Groq token/min rate limit

    # Step 2: License audit
    license_str = fetch_license(owner, name) or repo.get("license_type", "")
    label, emoji, color = classify_license(license_str)

    # Step 3: Contributor hiring dossier
    contributors = fetch_top_contributors(owner, name)
    dossier = build_hiring_dossier_with_groq(contributors, full_name)
    time.sleep(1)

    # Step 4: Persist to Supabase
    update_data = {
        "ai_hype_score": analysis["hype_score"],
        "commercial_summary": analysis["commercial_summary"],
        "tech_vibe": analysis["tech_vibe"],
        "market_category": analysis["market_category"],
        "license_type": license_str,
        "license_label": label,
        "license_color": color,
        "hiring_dossier": json.dumps(dossier),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    supabase.table("repositories").update(update_data).eq("id", repo["id"]).execute()
    log.info(
        f"  → Hype: {analysis['hype_score']}/10 | "
        f"License: {emoji} {label} | "
        f"Vibe: {analysis['tech_vibe']} | "
        f"Acqui-hire: {dossier.get('acqui_hire_rating', 'N/A')}"
    )


def run_analyst(batch_size: int = 20) -> None:
    """
    Fetches repositories that haven't been analyzed yet (or need refresh)
    and runs the full analysis pipeline.

    Processes `batch_size` repos per run to stay within Groq free tier.
    500 req/day / 2 calls per repo = 250 repos/day max on llama3-70b.
    """
    log.info("═" * 60)
    log.info("REPOALPHA ANALYST — Starting analysis cycle")
    log.info("═" * 60)

    # Prioritize repos with high corporate scores (most valuable to analyze first)
    result = (
        supabase.table("repositories")
        .select("*")
        .is_("analyzed_at", "null")
        .order("corporate_score", desc=True)
        .limit(batch_size)
        .execute()
    )
    repos = result.data
    log.info(f"Found {len(repos)} repos awaiting analysis.")

    for repo in repos:
        try:
            analyze_repository(repo)
            time.sleep(2)  # Conservative pacing for Groq free tier
        except Exception as e:
            log.error(f"Analysis failed for {repo.get('full_name')}: {e}")
            continue

    log.info("Analysis cycle complete.")


if __name__ == "__main__":
    while True:
        run_analyst(batch_size=20)
        log.info("Analyst sleeping 1800s (30 min)...")
        time.sleep(1800)
