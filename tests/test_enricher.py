"""
tests/test_enricher.py
Unit tests for the corporate scoring engine — no Bright Data calls needed.
"""
from agents.enricher import score_company, parse_github_profile


# ── score_company ─────────────────────────────────────────────────────────────
def test_nvidia_scores_15():
    company, score = score_company("Senior Engineer at NVIDIA")
    assert score == 15
    assert "Nvidia" in company or "nvidia" in company.lower()

def test_google_scores_12():
    _, score = score_company("SWE @Google Brain")
    assert score == 12

def test_netflix_scores_10():
    _, score = score_company("Staff Engineer, Netflix")
    assert score == 10

def test_fortune500_regex_jpmorgan():
    _, score = score_company("Quant at JPMorgan Chase")
    assert score == 3

def test_generic_engineer_scores_1():
    _, score = score_company("Software Engineer at tiny startup")
    assert score == 1

def test_empty_bio_scores_zero():
    company, score = score_company("")
    assert score == 0
    assert company == ""

def test_no_company_hint_scores_zero():
    _, score = score_company("I love open source and coffee ☕")
    assert score == 0

def test_case_insensitive():
    _, score = score_company("machine learning researcher at ANTHROPIC")
    assert score == 15


# ── parse_github_profile ──────────────────────────────────────────────────────
FAKE_HTML = """
<html><body>
  <span class="p-name">Ada Lovelace</span>
  <span class="p-org">Nvidia Corporation</span>
  <div class="p-note">GPU Engineer building the future of AI</div>
  <span class="p-label">Santa Clara, CA</span>
</body></html>
"""

def test_parse_name():
    p = parse_github_profile(FAKE_HTML)
    assert p["name"] == "Ada Lovelace"

def test_parse_company():
    p = parse_github_profile(FAKE_HTML)
    assert "Nvidia" in p["company_raw"]

def test_parse_bio():
    p = parse_github_profile(FAKE_HTML)
    assert "GPU Engineer" in p["bio"]

def test_parse_empty_html():
    p = parse_github_profile("")
    assert p["name"] == ""
    assert p["company_raw"] == ""
