import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

SPEECHMATICS_API_KEY = ""  # loaded inside function

def narrate_signal(repo_full_name: str, corporate_score: int, top_company: str) -> bool:
    """
    Sends a voice narration request to Speechmatics TTS API.
    Plays a spoken alert when a BUY signal is detected.
    Returns True if successful.
    """
    
    # Read directly from .env file every call — bypasses os.environ caching
    key = ""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SPEECHMATICS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass
    # Also try streamlit secrets as fallback
    if not key:
        try:
            from streamlit import secrets
            key = secrets.get("SPEECHMATICS_API_KEY", "")
        except Exception:
            pass
    if not key:
        return False
    SPEECHMATICS_API_KEY = key

    text = (
        f"RepoAlpha BUY signal detected. {repo_full_name.replace('/', ' by ')}. "
        f"Corporate score: {corporate_score}. "
        f"Top adopter: {top_company}. "
        f"Recommend immediate review."
    )

    try:
        tts_resp = requests.post(
            "https://preview.tts.speechmatics.com/generate/sarah",
            headers={
                "Authorization": f"Bearer {SPEECHMATICS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"text": text},
            timeout=30,
        )

        if tts_resp.status_code == 200:
            os.makedirs("assets", exist_ok=True)
            out_path = f"assets/alert_{repo_full_name.replace('/','_')}.wav"
            with open(out_path, "wb") as f:
                f.write(tts_resp.content)
            return True
        else:
            return f"TTS failed: {tts_resp.status_code} — {tts_resp.text[:200]}"

    except Exception as e:
        print(f"Speechmatics error: {e}")
        return str(e)