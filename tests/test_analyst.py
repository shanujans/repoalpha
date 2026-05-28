"""
tests/test_analyst.py
Tests license classification — no Groq calls.
"""
from agents.analyst import classify_license


def test_mit_is_green():
    label, emoji, color = classify_license("MIT")
    assert color == "green"
    assert "Enterprise" in label

def test_apache_is_green():
    _, _, color = classify_license("Apache-2.0")
    assert color == "green"

def test_agpl_is_red():
    label, emoji, color = classify_license("AGPL-3.0")
    assert color == "red"
    assert "Minefield" in label or "Risk" in label

def test_gpl3_is_red():
    _, _, color = classify_license("GPL-3.0")
    assert color == "red"

def test_lgpl_is_yellow():
    _, _, color = classify_license("LGPL-2.1")
    assert color == "yellow"

def test_empty_license_is_red():
    label, _, color = classify_license("")
    assert color == "red"
    assert "Reserved" in label or "No License" in label

def test_unknown_license_is_yellow():
    _, _, color = classify_license("Beerware-42")
    assert color == "yellow"

def test_case_insensitive_mit():
    _, _, color = classify_license("mit")
    assert color == "green"
