"""Tests for language detection."""

from openbro.utils.language import (
    detect_language,
    language_instruction,
    voice_for,
)


def test_detect_devanagari():
    assert detect_language("क्रोम खोल दे") == "hi"
    assert detect_language("मुझे मदद चाहिए") == "hi"


def test_detect_pure_english():
    assert detect_language("open chrome please") == "en"
    assert detect_language("what time is it") == "en"


def test_detect_hinglish():
    assert detect_language("chrome khol de bro") == "hinglish"
    assert detect_language("mujhe ek file banani hai") == "hinglish"
    assert detect_language("kya tum mera kaam kar sakte ho") == "hinglish"


def test_detect_empty():
    assert detect_language("") == "en"
    assert detect_language("   ") == "en"


def test_detect_mixed_script_devanagari_wins():
    # Even one Devanagari char → hi
    assert detect_language("open क्रोम") == "hi"


def test_language_instruction_hi():
    instr = language_instruction("hi")
    assert "Hindi" in instr
    assert "Devanagari" in instr


def test_language_instruction_en():
    instr = language_instruction("en")
    assert "English" in instr
    assert "no Hindi" in instr.lower() or "pure english" in instr.lower()


def test_language_instruction_hinglish():
    instr = language_instruction("hinglish")
    assert "Hinglish" in instr


def test_voice_for_each_lang():
    assert voice_for("hi").startswith("hi-IN")
    assert voice_for("en").startswith("en-IN")
    assert voice_for("hinglish").startswith("en-IN")
    assert voice_for("unknown").startswith("en-IN")  # fallback
