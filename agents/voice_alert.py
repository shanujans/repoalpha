"""
agents/voice_alert.py — Speechmatics Voice Alert Narration
Narrates BUY signals aloud using Speechmatics real-time TTS.
Called from alerter.py when a high-score repo is detected.
$200 free credit from hackathon partner.
"""
import os
import requests

SPEECHMATICS_API_KEY = os.environ.get("SPEECHMATICS_API_KEY", "")

def narrate_signal(repo_full_name: str, corporate_score: int, top_company: str) -> bool:
    """
    Sends a voice narration request to Speechmatics TTS API.
    Plays a spoken alert when a BUY signal is detected.
    Returns True if successful.
    """
    if not SPEECHMATICS_API_KEY:
        return False

    text = (
        f"RepoAlpha BUY signal detected. {repo_full_name.replace('/', ' by ')}. "
        f"Corporate score: {corporate_score}. "
        f"Top adopter: {top_company}. "
        f"Recommend immediate review."
    )

    try:
        resp = requests.post(
            "https://mp.speechmatics.com/v1/api_keys",   # auth check
            headers={
                "Authorization": f"Bearer {SPEECHMATICS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"ttl": 60},
            timeout=10,
        )
        if resp.status_code != 201:
            return False

        temp_key = resp.json().get("key_value")

        tts_resp = requests.post(
            "https://mp.speechmatics.com/v1/speech:synthesize",
            headers={
                "Authorization": f"Bearer {temp_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": {"text": text},
                "audio_format": {"type": "mp3"},
                "voice": {"language": "en", "name": "aria"},
            },
            timeout=20,
        )

        if tts_resp.status_code == 200:
            # Save audio file for dashboard playback
            os.makedirs("assets", exist_ok=True)
            with open(f"assets/alert_{repo_full_name.replace('/','_')}.mp3", "wb") as f:
                f.write(tts_resp.content)
            return True

    except Exception as e:
        print(f"Speechmatics error: {e}")

    return False