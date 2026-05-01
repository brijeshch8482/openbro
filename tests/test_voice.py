"""Tests for voice layer (mocked - no real audio I/O in CI)."""

from unittest.mock import patch

from openbro.voice.listener import DEFAULT_WAKE_WORDS, VoiceListener
from openbro.voice.stt import VOICE_DEPS_HINT, SpeechToText
from openbro.voice.tts import DEFAULT_VOICE, TextToSpeech


def test_stt_init_does_not_load_model():
    stt = SpeechToText(model_size="tiny")
    assert stt._model is None
    assert stt.model_size == "tiny"


def test_stt_missing_deps_message():
    assert "openbro[voice]" in VOICE_DEPS_HINT


def test_tts_default_voice():
    tts = TextToSpeech()
    assert tts.voice == DEFAULT_VOICE
    assert "en-IN" in DEFAULT_VOICE  # Indian English by default


def test_tts_speak_empty_noop():
    tts = TextToSpeech()
    # Should not raise on empty text
    tts.speak("")
    tts.speak("   ")


def test_listener_wake_word_detect():
    listener = VoiceListener.__new__(VoiceListener)
    listener.wake_words = [w.lower() for w in DEFAULT_WAKE_WORDS]
    assert listener.is_wake_word("Hey bro, what's up") is True
    assert listener.is_wake_word("HI BRO") is True
    assert listener.is_wake_word("just talking to myself") is False


def test_listener_strip_wake_word():
    out = VoiceListener.strip_wake_word("Hey bro, open chrome", ["hey bro"])
    assert out == "open chrome"
    out2 = VoiceListener.strip_wake_word("ok bro suno", ["ok bro"])
    assert out2 == "suno"


def test_listener_strip_wake_word_no_match():
    out = VoiceListener.strip_wake_word("just chatting", ["hey bro"])
    assert out == "just chatting"


def test_listener_default_wake_words():
    assert "hey bro" in DEFAULT_WAKE_WORDS
    assert "ok bro" in DEFAULT_WAKE_WORDS


def test_listener_custom_wake_words():
    listener = VoiceListener.__new__(VoiceListener)
    listener.wake_words = ["yo bro"]
    assert listener.is_wake_word("yo bro hello") is True
    assert listener.is_wake_word("hey bro hello") is False


def test_tts_speak_falls_back_on_edge_failure():
    tts = TextToSpeech()
    with patch.object(tts, "_speak_edge", side_effect=RuntimeError("no edge")):
        with patch.object(tts, "_speak_pyttsx3") as fallback:
            tts.speak("hello")
            fallback.assert_called_once_with("hello")


def test_voice_mode_handles_missing_deps():
    """run_voice_mode should print friendly error when voice deps missing."""
    from openbro.cli import voice_mode

    with patch.object(voice_mode, "load_config", return_value={"voice": {"enabled": True}}):
        with patch.object(voice_mode, "Agent"):
            with patch(
                "openbro.cli.voice_mode.VoiceListener",
                create=True,
                side_effect=ImportError("no sounddevice"),
            ):
                # Should not raise; init failure is caught
                voice_mode.run_voice_mode()


def test_voice_disabled_in_config():
    from openbro.cli import voice_mode

    with patch.object(voice_mode, "load_config", return_value={"voice": {"enabled": False}}):
        # Agent should not be constructed
        with patch.object(voice_mode, "Agent") as agent_cls:
            voice_mode.run_voice_mode()
            agent_cls.assert_not_called()
