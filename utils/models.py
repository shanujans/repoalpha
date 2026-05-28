"""
utils/models.py — Enterprise Data Models
RepoAlpha | Pydantic v2 for full type safety across all agents.

Every piece of data flowing through the pipeline is validated here.
This catches bad API responses early instead of letting silent bugs
corrupt the database hours later.
"""

from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Enums ──────────────────────────────────────────────────────────────────

Rating = Literal["BUY", "HOLD", "SELL"]
LicenseColor = Literal["green", "yellow", "red"]
AcquiRating = Literal["High", "Medium", "Low", "Unknown"]
MarketCategory = Literal[
    "AI/ML", "DevTools", "Security", "Data/Analytics",
    "Infrastructure", "Web3", "Robotics", "Other"
]


# ─── Stargazer Models ────────────────────────────────────────────────────────

class RawStargazer(BaseModel):
    """Output from GitHub API before enrichment."""
    login: str
    avatar_url: Optional[str] = None
    profile_url: Optional[str] = None
    starred_at: Optional[datetime] = None

    @field_validator("login")
    @classmethod
    def login_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("github login cannot be empty")
        return v.strip()


class EnrichedStargazer(RawStargazer):
    """After Bright Data Web Unlocker enrichment."""
    company: Optional[str] = None
    bio: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    company_score: int = Field(default=0, ge=0, le=20)
    enriched: bool = True
    enriched_at: Optional[datetime] = None


# ─── Repository Models ───────────────────────────────────────────────────────

class RawRepository(BaseModel):
    """Output from Bright Data GitHub Trending scrape."""
    full_name: str
    name: str
    owner: str
    description: Optional[str] = ""
    language: Optional[str] = ""
    stars_count: int = Field(default=0, ge=0)
    forks_count: int = Field(default=0, ge=0)
    url: Optional[str] = None
    trending_rank: int = Field(default=0, ge=0)

    @field_validator("full_name")
    @classmethod
    def must_have_slash(cls, v: str) -> str:
        if "/" not in v:
            raise ValueError(f"full_name must be 'owner/repo', got: {v}")
        return v

    @model_validator(mode="after")
    def sync_owner_name(self) -> "RawRepository":
        owner, _, name = self.full_name.partition("/")
        if not self.owner:
            self.owner = owner
        if not self.name:
            self.name = name
        return self


class AnalyzedRepository(RawRepository):
    """After Groq AI analysis — Phase 3 output."""
    corporate_score: int = Field(default=0, ge=0)
    ai_hype_score: int = Field(default=0, ge=0, le=10)
    commercial_summary: Optional[str] = None
    tech_vibe: Optional[str] = None
    market_category: Optional[MarketCategory] = "Other"
    license_type: Optional[str] = None
    license_label: Optional[str] = None
    license_color: LicenseColor = "yellow"
    hiring_dossier: Optional[dict] = None
    rating: Optional[Rating] = None
    scraped_at: Optional[datetime] = None
    analyzed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def compute_rating(self) -> "AnalyzedRepository":
        if self.rating is None:
            if self.corporate_score >= 60:
                self.rating = "BUY"
            elif self.corporate_score >= 25:
                self.rating = "HOLD"
            else:
                self.rating = "SELL"
        return self


# ─── Corporate Signal Models ─────────────────────────────────────────────────

class CorporateSignal(BaseModel):
    """One corporate employee starring one repo."""
    repo_id: str
    stargazer_id: str
    github_login: str
    company: str
    signal_score: int = Field(ge=1, le=20)
    raw_bio: Optional[str] = None
    detected_at: Optional[datetime] = None

    @field_validator("company")
    @classmethod
    def company_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("company name cannot be empty")
        return v.strip()


# ─── AI Analysis Models ──────────────────────────────────────────────────────

class ReadmeAnalysis(BaseModel):
    """Groq LLM response for README analysis."""
    commercial_summary: str = Field(max_length=500)
    hype_score: int = Field(ge=1, le=10)
    tech_vibe: str = Field(max_length=40)
    market_category: MarketCategory = "Other"

    @field_validator("hype_score", mode="before")
    @classmethod
    def clamp_hype(cls, v) -> int:
        return max(1, min(10, int(v)))


class HiringDossier(BaseModel):
    """Groq LLM response for hiring dossier."""
    acqui_hire_rating: AcquiRating = "Unknown"
    acqui_hire_rationale: str = Field(default="", max_length=300)
    key_talent: str = ""
    red_flags: str = ""
    contributors: list[dict] = Field(default_factory=list)


# ─── Alert Models ────────────────────────────────────────────────────────────

class AlertEvent(BaseModel):
    """Fired when a repo crosses a BUY threshold."""
    repo_full_name: str
    corporate_score: int
    top_companies: list[str]
    hype_score: int
    license_label: str
    rating: Rating
    url: str
    triggered_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Watchlist Models ────────────────────────────────────────────────────────

class WatchlistEntry(BaseModel):
    """User-pinned repository for tracking."""
    user_id: str
    repo_full_name: str
    note: Optional[str] = Field(default=None, max_length=200)
    alert_threshold: int = Field(default=30, ge=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ─── Pipeline Event Models ───────────────────────────────────────────────────

class PipelineRun(BaseModel):
    """Audit log entry for every pipeline execution."""
    phase: Literal["harvest", "enrich", "analyze", "alert"]
    status: Literal["started", "success", "failed", "partial"]
    repos_processed: int = 0
    stargazers_processed: int = 0
    signals_detected: int = 0
    alerts_fired: int = 0
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
