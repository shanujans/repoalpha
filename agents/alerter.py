"""
agents/alerter.py — Enterprise Alert Engine
RepoAlpha

Fires real-time alerts when the pipeline detects high-value signals:
  - New BUY-rated repo detected
  - AGPL/Viral Risk license on a high-hype repo
  - Tier-1 company (Nvidia, Anthropic, OpenAI) stars a repo

Supported channels (all free):
  ① Slack Incoming Webhooks (free workspace)
  ② Discord Webhooks (free server)
  ③ Resend Email API (100 emails/day free tier)

Zero cost: all three channels have perpetually free tiers.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from utils.models import AlertEvent, Rating
from utils.logger import get_logger
from utils.retry import retry_on_network

load_dotenv()
log = get_logger("alerter")


# ─── Alert Config ────────────────────────────────────────────────────────────

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "alerts@repoalpha.dev")

# Only alert repos that cross these thresholds to avoid noise
BUY_SCORE_THRESHOLD = int(os.environ.get("ALERT_BUY_THRESHOLD", "60"))
TIER1_COMPANIES = {"nvidia", "openai", "anthropic", "deepmind", "mistral"}


# ─── Formatters ─────────────────────────────────────────────────────────────

def _rating_emoji(rating: Rating) -> str:
    return {"BUY": "🟢", "HOLD": "🟡", "SELL": "🔴"}.get(rating, "⚪")


def _format_slack_payload(event: AlertEvent) -> dict:
    """Slack Block Kit message for rich formatting."""
    companies_str = " · ".join(event.top_companies[:5]) or "N/A"
    return {
        "username": "RepoAlpha Signal",
        "icon_emoji": ":satellite:",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{_rating_emoji(event.rating)} {event.rating} SIGNAL DETECTED",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*<{event.url}|{event.repo_full_name}>*\n"
                        f"Corporate Score: `{event.corporate_score}` | "
                        f"AI Hype: `{event.hype_score}/10`"
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Top Adopters*\n{companies_str}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*License*\n{event.license_label}",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Detected at {event.triggered_at.strftime('%Y-%m-%d %H:%M UTC')} · RepoAlpha",
                    }
                ],
            },
        ],
    }


def _format_discord_payload(event: AlertEvent) -> dict:
    """Discord embed message."""
    companies_str = ", ".join(event.top_companies[:5]) or "N/A"
    color = {"BUY": 0x00FFAA, "HOLD": 0xFFD700, "SELL": 0xFF3333}.get(event.rating, 0x888888)
    return {
        "username": "RepoAlpha",
        "avatar_url": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
        "embeds": [
            {
                "title": f"{_rating_emoji(event.rating)} {event.rating} — {event.repo_full_name}",
                "url": event.url,
                "color": color,
                "fields": [
                    {"name": "Corporate Score", "value": str(event.corporate_score), "inline": True},
                    {"name": "AI Hype Score", "value": f"{event.hype_score}/10", "inline": True},
                    {"name": "License", "value": event.license_label, "inline": True},
                    {"name": "Top Adopters", "value": companies_str, "inline": False},
                ],
                "footer": {"text": "RepoAlpha · Open Source M&A Intelligence"},
                "timestamp": event.triggered_at.isoformat(),
            }
        ],
    }


# ─── Channel Senders ─────────────────────────────────────────────────────────

@retry_on_network(max_attempts=3, min_wait=2.0)
def _send_slack(event: AlertEvent) -> bool:
    if not SLACK_WEBHOOK_URL:
        return False
    resp = requests.post(
        SLACK_WEBHOOK_URL,
        json=_format_slack_payload(event),
        timeout=10,
    )
    resp.raise_for_status()
    log.info(f"Slack alert sent for {event.repo_full_name}")
    return True


@retry_on_network(max_attempts=3, min_wait=2.0)
def _send_discord(event: AlertEvent) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        json=_format_discord_payload(event),
        timeout=10,
    )
    resp.raise_for_status()
    log.info(f"Discord alert sent for {event.repo_full_name}")
    return True


@retry_on_network(max_attempts=3, min_wait=5.0)
def _send_email(event: AlertEvent) -> bool:
    """Uses Resend free tier — 100 emails/day."""
    if not RESEND_API_KEY or not ALERT_EMAIL_TO:
        return False

    companies_str = ", ".join(event.top_companies[:5]) or "N/A"
    html_body = f"""
    <div style="font-family:monospace;background:#0E1117;color:#E2E8F0;padding:24px;border-radius:8px;">
      <h2 style="color:#00FFAA;margin:0 0 8px">{_rating_emoji(event.rating)} {event.rating} Signal — {event.repo_full_name}</h2>
      <p style="color:#64748B;margin:0 0 16px">RepoAlpha Corporate Signal Engine · {event.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
      <table style="border-collapse:collapse;width:100%">
        <tr><td style="padding:8px;color:#94A3B8">Corporate Score</td><td style="color:#00FFAA;font-weight:bold">{event.corporate_score}</td></tr>
        <tr><td style="padding:8px;color:#94A3B8">AI Hype Score</td><td style="color:#E2E8F0">{event.hype_score}/10</td></tr>
        <tr><td style="padding:8px;color:#94A3B8">License</td><td style="color:#E2E8F0">{event.license_label}</td></tr>
        <tr><td style="padding:8px;color:#94A3B8">Top Adopters</td><td style="color:#E2E8F0">{companies_str}</td></tr>
      </table>
      <a href="{event.url}" style="display:inline-block;margin-top:16px;background:#00FFAA;color:#0E1117;padding:8px 16px;border-radius:4px;text-decoration:none;font-weight:bold;">View on GitHub →</a>
    </div>
    """

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": ALERT_EMAIL_FROM,
            "to": [ALERT_EMAIL_TO],
            "subject": f"[RepoAlpha] {event.rating} Signal: {event.repo_full_name} (score: {event.corporate_score})",
            "html": html_body,
        },
        timeout=15,
    )
    resp.raise_for_status()
    log.info(f"Email alert sent for {event.repo_full_name} to {ALERT_EMAIL_TO}")
    return True


# ─── Main Alert Dispatcher ───────────────────────────────────────────────────

def fire_alert(event: AlertEvent) -> int:
    """
    Dispatches an alert to all configured channels.
    Returns the number of channels successfully notified.
    """
    log.info(
        f"Firing alert: {event.repo_full_name} | "
        f"{event.rating} | score={event.corporate_score}"
    )
    sent = 0
    for fn in [_send_slack, _send_discord, _send_email]:
        try:
            if fn(event):
                sent += 1
        except Exception as e:
            log.warning(f"Alert channel {fn.__name__} failed: {e}")
    return sent


def check_and_alert(supabase_client, repo: dict) -> bool:
    """
    Called after enrichment for each repo. Builds an AlertEvent
    and fires if the repo crosses BUY_SCORE_THRESHOLD.

    Returns True if an alert was fired.
    """
    score = repo.get("corporate_score", 0)
    rating = repo.get("rating", "SELL")
    hype = repo.get("ai_hype_score", 0)

    if score < BUY_SCORE_THRESHOLD:
        return False

    # Check we haven't already alerted this repo at this score level
    existing = (
        supabase_client.table("alert_log")
        .select("id")
        .eq("repo_full_name", repo["full_name"])
        .gte("corporate_score_at_alert", score - 5)
        .execute()
    )
    if existing.data:
        return False  # Already alerted for similar score

    # Get top companies for this repo
    signals = (
        supabase_client.table("corporate_signals")
        .select("company, signal_score")
        .eq("repo_id", repo["id"])
        .order("signal_score", desc=True)
        .limit(5)
        .execute()
    )
    top_companies = [s["company"] for s in signals.data if s.get("company")]

    event = AlertEvent(
        repo_full_name=repo["full_name"],
        corporate_score=score,
        top_companies=top_companies,
        hype_score=hype,
        license_label=repo.get("license_label", "Unknown"),
        rating=rating,
        url=repo.get("url") or f"https://github.com/{repo['full_name']}",
    )

    channels_notified = fire_alert(event)

    # Log the alert to prevent duplicate fires
    if channels_notified > 0:
        supabase_client.table("alert_log").insert({
            "repo_full_name": repo["full_name"],
            "repo_id": repo["id"],
            "corporate_score_at_alert": score,
            "rating": rating,
            "channels_notified": channels_notified,
            "alerted_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    return channels_notified > 0
