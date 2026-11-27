"""Detect input language: hindi (Devanagari), hinglish, or english.

Lightweight heuristic — no ML. Used to:
- pick the right TTS voice
- inject 'reply in same language' instruction into the system prompt
"""

import re

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Words that only appear in Hinglish (Roman-script Hindi).
HINGLISH_KEYWORDS = {
    "hai",
    "hain",
    "hoon",
    "hu",
    "ho",
    "tha",
    "thi",
    "kar",
    "kr",
    "kro",
    "kre",
    "karo",
    "karna",
    "karke",
    "kiya",
    "kya",
    "kyu",
    "kyun",
    "kaise",
    "kaisa",
    "kitna",
    "kitne",
    "kab",
    "kaha",
    "kahan",
    "mai",
    "mei",
    "mein",
    "tu",
    "tum",
    "aap",
    "wo",
    "woh",
    "ye",
    "yeh",
    "mujhe",
    "mera",
    "meri",
    "mere",
    "tera",
    "teri",
    "tere",
    "hum",
    "humko",
    "bhai",
    "yaar",
    "bata",
    "batao",
    "bolo",
    "khol",
    "kholo",
    "dekh",
    "dekho",
    "chal",
    "chalo",
    "ruk",
    "ruko",
    "bana",
    "banao",
    "banani",
    "haan",
    "nahi",
    "nahin",
    "nai",
    "abhi",
    "aur",
    "lekin",
    "magar",
    "phir",
    "agar",
    "toh",
    "bhi",
    "sirf",
    "bas",
    "thoda",
    "zyada",
    "accha",
    "achha",
    "acha",
    "theek",
    "thik",
    "sahi",
    "ek",
    "koi",
    "kuch",
    "sab",
    "wala",
    "wali",
    "raha",
    "rahi",
    "gaya",
    "gayi",
    "liye",
    "chahiye",
    "badhiya",
    "kaam",
    "isko",
    "usko",
    "yaha",
    "yahan",
    "waha",
    "wahan",
    "se",
    "ko",
    "ka",
    "ki",
    "ke",
    "pe",
    "kam",
}
# Hinglish words that are also everyday English ("to", "me", "do", "main",
# "bro"…). They count toward density but never decide on their own:
# "have you tool to make image?" is English.
AMBIGUOUS_KEYWORDS = {"to", "me", "do", "de", "main", "bro", "par", "tab", "jab"}


def detect_language(text: str) -> str:
    """Return one of: 'hi' (Devanagari), 'hinglish', 'en'."""
    if not text or not text.strip():
        return "en"
    if DEVANAGARI.search(text):
        return "hi"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "en"
    strong = sum(1 for w in words if w in HINGLISH_KEYWORDS)
    if strong >= 2:
        return "hinglish"
    loose = sum(1 for w in words if w in AMBIGUOUS_KEYWORDS)
    # One clear Hinglish word plus ≥15% Hinglish-ish density → Hinglish.
    if strong == 1 and (strong + loose) / len(words) >= 0.15:
        return "hinglish"
    return "en"


def language_instruction(lang: str) -> str:
    """Return a one-liner to append to the system prompt."""
    # Mirror the user's language for this turn. This instruction overrides
    # the language of the persona prompt.
    if lang == "hi":
        return "The user wrote in Hindi (Devanagari). Reply in Hindi, in Devanagari script."
    if lang == "en":
        return (
            "The user wrote in English. Reply in pure English only — no Hindi or "
            "Hinglish words, even if earlier turns were in Hinglish."
        )
    return (
        "The user wrote in Hinglish (Hindi in Roman script). Reply in casual Hinglish, "
        "in Roman script."
    )


VOICE_FOR_LANG = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
    "hinglish": "en-IN-NeerjaNeural",
}


def voice_for(lang: str) -> str:
    return VOICE_FOR_LANG.get(lang, "en-IN-NeerjaNeural")
