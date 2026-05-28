"""
tests/test_models.py
Validates Pydantic models catch bad data before it hits Supabase.
Run: pytest tests/ -v
"""
import pytest
from pydantic import ValidationError
from utils.models import (
    RawStargazer, RawRepository, AnalyzedRepository,
    ReadmeAnalysis, CorporateSignal,
)


# ── RawStargazer ─────────────────────────────────────────────────────────────
def test_stargazer_valid():
    s = RawStargazer(login="torvalds", profile_url="https://github.com/torvalds")
    assert s.login == "torvalds"

def test_stargazer_empty_login_rejected():
    with pytest.raises(ValidationError):
        RawStargazer(login="  ")

# ── RawRepository ────────────────────────────────────────────────────────────
def test_repo_valid():
    r = RawRepository(full_name="openai/triton", name="triton", owner="openai",
                      stars_count=13000, forks_count=1200)
    assert r.owner == "openai"

def test_repo_missing_slash_rejected():
    with pytest.raises(ValidationError):
        RawRepository(full_name="noslash", name="x", owner="x")

def test_repo_negative_stars_rejected():
    with pytest.raises(ValidationError):
        RawRepository(full_name="a/b", name="b", owner="a", stars_count=-1)

def test_repo_syncs_owner_from_full_name():
    r = RawRepository(full_name="huggingface/transformers", name="", owner="")
    assert r.owner == "huggingface"
    assert r.name  == "transformers"

# ── AnalyzedRepository ───────────────────────────────────────────────────────
def test_rating_computed_buy():
    r = AnalyzedRepository(full_name="a/b", name="b", owner="a", corporate_score=80)
    assert r.rating == "BUY"

def test_rating_computed_hold():
    r = AnalyzedRepository(full_name="a/b", name="b", owner="a", corporate_score=40)
    assert r.rating == "HOLD"

def test_rating_computed_sell():
    r = AnalyzedRepository(full_name="a/b", name="b", owner="a", corporate_score=10)
    assert r.rating == "SELL"

# ── ReadmeAnalysis ────────────────────────────────────────────────────────────
def test_hype_clamped_above_ten():
    a = ReadmeAnalysis(commercial_summary="good", hype_score=999,
                       tech_vibe="AI", market_category="AI/ML")
    assert a.hype_score == 10

def test_hype_clamped_below_one():
    a = ReadmeAnalysis(commercial_summary="ok", hype_score=0,
                       tech_vibe="meh", market_category="Other")
    assert a.hype_score == 1

# ── CorporateSignal ───────────────────────────────────────────────────────────
def test_signal_empty_company_rejected():
    with pytest.raises(ValidationError):
        CorporateSignal(repo_id="abc", stargazer_id="def",
                        github_login="x", company="  ", signal_score=10)

def test_signal_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        CorporateSignal(repo_id="abc", stargazer_id="def",
                        github_login="x", company="Nvidia", signal_score=999)
