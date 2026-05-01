"""Detect input language: hindi (Devanagari), hinglish, or english.

Lightweight heuristic — no ML. Used to:
- pick the right TTS voice
- inject 'reply in same language' instruction into the system prompt
"""

import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
HINGLISH_KEYWORDS = {
    "hai",
    "hain",
    "kar",
    "kr",
    "kro",
    "kre",
    "kya",
    "kyu",
    "kyun",
    "mai",
    "mei",
    "main",
    "tu",
    "tum",
    "wo",
    "ye",
    "yeh",
    "bhai",
    "bro",
    "bata",
    "batao",
    "khol",
    "kholo",
    "de",
    "do",
    "haan",
    "nahi",
    "nai",
    "abhi",
    "se",
    "ko",
    "ka",
    "ki",
    "ke",
    "me",
    "mein",
    "par",
    "pe",
    "aur",
    "lekin",
    "magar",
    "phir",
    "jab",
    "tab",
    "agar",
    "to",
    "thoda",
    "zyada",
    "kam",
    "accha",
    "theek",
    "sahi",
}


def detect_language(text: str) -> str:
    """Return one of: 'hi' (Devanagari), 'hinglish', 'en'."""
    if not text or not text.strip():
        return "en"
    if DEVANAGARI.search(text):
        return "hi"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "en"
    matches = sum(1 for w in words if w in HINGLISH_KEYWORDS)
    # ≥15% Hinglish keyword density → call it Hinglish
    if matches / len(words) >= 0.15:
        return "hinglish"
    return "en"


def language_instruction(lang: str) -> str:
    """Return a one-liner to append to the system prompt."""
    if lang == "hi":
        return (
            "USER ne Hindi (Devanagari) me likha hai. Tu bhi pure Hindi (Devanagari) me reply de."
        )
    if lang == "en":
        return "USER wrote in English. Reply in pure English only — no Hindi words."
    return (
        "USER ne Hinglish me likha hai (Roman script). "
        "Tu bhi casual Hinglish me reply de — Hindi words Roman script me."
    )


VOICE_FOR_LANG = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
    "hinglish": "en-IN-NeerjaNeural",
}


def voice_for(lang: str) -> str:
    return VOICE_FOR_LANG.get(lang, "en-IN-NeerjaNeural")
