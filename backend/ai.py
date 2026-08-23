"""Gemini-powered Copilot: turns analysis results into human advice.

The API key lives in .env (gitignored); it is never logged or returned.
"""

import os
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


def _load_dotenv() -> None:
    """Minimal .env loader — no extra dependency needed."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class AIUnavailable(Exception):
    """Raised when the key is missing or Gemini cannot be reached."""


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise AIUnavailable("GEMINI_API_KEY is not configured (.env file missing?).")
    return key


def ask_gemini(prompt: str, system: str = "", timeout: float = 120.0) -> str:
    """One-shot text generation via the Gemini REST API."""
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 4096},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    try:
        res = httpx.post(
            f"{API_ROOT}/{model}:generateContent",
            params={"key": _api_key()},
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise AIUnavailable(f"Could not reach Gemini: {exc}") from exc

    if res.status_code == 403:
        raise AIUnavailable("Gemini rejected the API key (403).")
    if res.status_code == 429:
        raise AIUnavailable("Gemini rate limit hit (429) — retry in a moment.")
    if res.status_code >= 400:
        raise AIUnavailable(f"Gemini error {res.status_code}.")

    try:
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise AIUnavailable("Gemini returned an unexpected response.") from exc
