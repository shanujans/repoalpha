"""
api/main.py — RepoAlpha REST API
Enterprise-grade programmatic access layer

Exposes the RepoAlpha intelligence database via a clean REST API,
enabling integration with:
  - Internal VC portfolio management tools
  - Zapier/Make.com automations
  - Custom analyst dashboards
  - Webhook-based alert consumers

Run locally: uvicorn api.main:app --reload --port 8000
Deploy:      Connect GitHub repo to render.com → New Web Service
"""

import os
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import io
import csv

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="RepoAlpha API",
    description="Open Source M&A Intelligence — Corporate Signal Engine",
    version="1.0.0",
    docs_url="/docs",       # Swagger UI at /docs
    redoc_url="/redoc",     # ReDoc at /redoc
)

@app.get("/")
def root():
    return {"message": "RepoAlpha API is running"}

# Allow Streamlit dashboard to call this API cross-origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── Supabase Dependency ─────────────────────────────────────────────────────

def get_db() -> Client:
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )


# ─── Response Models ─────────────────────────────────────────────────────────

class RepoSummary(BaseModel):
    full_name: str
    description: Optional[str]
    language: Optional[str]
    stars_count: int
    corporate_score: int
    ai_hype_score: int
    rating: Optional[str]
    license_label: Optional[str]
    market_category: Optional[str]
    url: Optional[str]


class WatchlistRequest(BaseModel):
    repo_full_name: str
    alert_threshold: int = 30
    note: Optional[str] = None


# ─── Health Check ────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health():
    """Ping endpoint for uptime monitoring (e.g. UptimeRobot free tier)."""
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


# ─── Repository Endpoints ────────────────────────────────────────────────────

@app.get("/repos", response_model=list[RepoSummary], tags=["Intelligence"])
async def list_repos(
    rating: Optional[Literal["BUY", "HOLD", "SELL"]] = None,
    category: Optional[str] = None,
    min_score: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort_by: Literal["corporate_score", "ai_hype_score", "stars_count"] = "corporate_score",
    db: Client = Depends(get_db),
):
    """
    List repositories ranked by corporate signal score.

    - **rating**: Filter by BUY / HOLD / SELL
    - **category**: Filter by market category (AI/ML, DevTools, etc.)
    - **min_score**: Minimum corporate score threshold
    - **limit**: Max results (1–100)
    - **sort_by**: Sort field
    """
    query = (
        db.table("repositories")
        .select("*")
        .gte("corporate_score", min_score)
        .order(sort_by, desc=True)
        .limit(limit)
    )
    if rating:
        query = query.eq("rating", rating)
    if category:
        query = query.eq("market_category", category)

    result = query.execute()
    return result.data


@app.get("/repos/{owner}/{repo}", tags=["Intelligence"])
async def get_repo(owner: str, repo: str, db: Client = Depends(get_db)):
    """Full intelligence report for a specific repository."""
    full_name = f"{owner}/{repo}"
    result = db.table("repositories").select("*").eq("full_name", full_name).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Repo {full_name} not found in RepoAlpha database")

    repo_data = result.data[0]

    # Fetch signals
    signals = (
        db.table("corporate_signals")
        .select("company, signal_score, github_login")
        .eq("repo_id", repo_data["id"])
        .order("signal_score", desc=True)
        .limit(20)
        .execute()
    )
    repo_data["corporate_signals"] = signals.data
    return repo_data


@app.get("/repos/{owner}/{repo}/stargazers", tags=["Intelligence"])
async def get_stargazers(
    owner: str,
    repo: str,
    enriched_only: bool = True,
    limit: int = Query(50, ge=1, le=200),
    db: Client = Depends(get_db),
):
    """List enriched stargazers for a repo, sorted by company score."""
    full_name = f"{owner}/{repo}"
    repo_result = db.table("repositories").select("id").eq("full_name", full_name).limit(1).execute()
    if not repo_result.data:
        raise HTTPException(status_code=404, detail="Repo not found")

    query = (
        db.table("stargazers")
        .select("github_login, company, bio, company_score, profile_url, email")
        .eq("repo_id", repo_result.data[0]["id"])
        .order("company_score", desc=True)
        .limit(limit)
    )
    if enriched_only:
        query = query.eq("enriched", True)

    return query.execute().data


# ─── Signal Intelligence Endpoints ───────────────────────────────────────────

@app.get("/signals/top-companies", tags=["Intelligence"])
async def top_companies(
    limit: int = Query(20, ge=1, le=50),
    db: Client = Depends(get_db),
):
    """Companies with the highest aggregate signal across all repos."""
    result = (
        db.table("corporate_signals")
        .select("company, signal_score")
        .order("signal_score", desc=True)
        .limit(500)
        .execute()
    )

    # Aggregate client-side (Supabase free tier has limited GROUP BY support)
    from collections import defaultdict
    agg: dict[str, int] = defaultdict(int)
    for row in result.data:
        if row.get("company"):
            agg[row["company"]] += row["signal_score"]

    sorted_companies = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"company": c, "total_score": s} for c, s in sorted_companies]


@app.get("/signals/trending", tags=["Intelligence"])
async def trending_signals(
    hours: int = Query(24, ge=1, le=168),
    db: Client = Depends(get_db),
):
    """Repos gaining the most corporate signals in the last N hours."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    result = (
        db.table("corporate_signals")
        .select("repo_id, signal_score, detected_at, repositories(full_name, url, rating)")
        .gte("detected_at", cutoff)
        .order("signal_score", desc=True)
        .limit(100)
        .execute()
    )

    from collections import defaultdict
    agg: dict[str, dict] = {}
    for row in result.data:
        repo = row.get("repositories", {}) or {}
        name = repo.get("full_name", row["repo_id"])
        if name not in agg:
            agg[name] = {
                "full_name": name,
                "url": repo.get("url"),
                "rating": repo.get("rating"),
                "recent_score_gain": 0,
                "signal_count": 0,
            }
        agg[name]["recent_score_gain"] += row["signal_score"]
        agg[name]["signal_count"] += 1

    return sorted(agg.values(), key=lambda x: x["recent_score_gain"], reverse=True)


# ─── Watchlist Endpoints ─────────────────────────────────────────────────────

@app.post("/watchlist/{user_id}", tags=["Watchlist"])
async def add_to_watchlist(
    user_id: str,
    body: WatchlistRequest,
    db: Client = Depends(get_db),
):
    """Add a repository to a user's watchlist."""
    result = db.table("watchlist").upsert({
        "user_id": user_id,
        "repo_full_name": body.repo_full_name,
        "alert_threshold": body.alert_threshold,
        "note": body.note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id,repo_full_name").execute()
    return {"status": "added", "entry": result.data[0] if result.data else {}}


@app.get("/watchlist/{user_id}", tags=["Watchlist"])
async def get_watchlist(user_id: str, db: Client = Depends(get_db)):
    """Get all watchlisted repos for a user with their current intelligence data."""
    result = (
        db.table("watchlist")
        .select("repo_full_name, alert_threshold, note, created_at")
        .eq("user_id", user_id)
        .execute()
    )
    enriched = []
    for entry in result.data:
        repo_result = (
            db.table("repositories")
            .select("corporate_score, ai_hype_score, rating, license_label")
            .eq("full_name", entry["repo_full_name"])
            .limit(1)
            .execute()
        )
        entry["intelligence"] = repo_result.data[0] if repo_result.data else {}
        enriched.append(entry)
    return enriched


# ─── Export Endpoints ────────────────────────────────────────────────────────

@app.get("/export/csv", tags=["Export"])
async def export_csv(
    min_score: int = 0,
    rating: Optional[str] = None,
    db: Client = Depends(get_db),
):
    """
    Export the full repo intelligence database as a CSV file.
    Perfect for importing into Excel or Google Sheets.
    """
    query = (
        db.table("repositories")
        .select("full_name,description,language,stars_count,corporate_score,ai_hype_score,rating,license_label,market_category,tech_vibe,commercial_summary,url")
        .gte("corporate_score", min_score)
        .order("corporate_score", desc=True)
        .limit(1000)
    )
    if rating:
        query = query.eq("rating", rating)

    result = query.execute()
    rows = result.data

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=repoalpha_export.csv"},
    )


@app.get("/export/json", tags=["Export"])
async def export_json(
    rating: Optional[str] = None,
    db: Client = Depends(get_db),
):
    """Export as structured JSON — useful for downstream automation."""
    query = (
        db.table("repositories")
        .select("*")
        .order("corporate_score", desc=True)
        .limit(500)
    )
    if rating:
        query = query.eq("rating", rating)
    return query.execute().data


# ─── Pipeline Status ─────────────────────────────────────────────────────────

@app.get("/pipeline/status", tags=["System"])
async def pipeline_status(
    limit: int = Query(20, ge=1, le=100),
    db: Client = Depends(get_db),
):
    """Recent pipeline run audit log."""
    result = (
        db.table("pipeline_runs")
        .select("*")
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
