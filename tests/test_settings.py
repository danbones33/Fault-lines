"""Settings panel backend: .env writing, validation, masking. A temp .env, no network. Run: python -I tests/test_settings.py"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import settings  # noqa: E402

KEYS = [f["key"] for f in settings.FIELDS]


def fresh_env(text=None):
    d = tempfile.mkdtemp()
    settings.ENV_PATH = os.path.join(d, ".env")
    if text is not None:
        open(settings.ENV_PATH, "w", encoding="utf-8").write(text)
    for k in KEYS:
        os.environ.pop(k, None)
    return settings.ENV_PATH


def test_save_replace_remove_keeps_other_lines():
    p = fresh_env("# my notes\nMIROFISH_BACKEND=D:/mf/backend\nCARTESIA_API_KEY=old-cartesia-key-0000\n")
    os.environ["CARTESIA_API_KEY"] = "old-cartesia-key-0000"
    changed = settings.apply({"OPENROUTER_API_KEY": "sk-or-v1-abcdefgh12345678", "CARTESIA_API_KEY": "new-cartesia-key-9999", "RUN_BUDGET_USD": "2.50"})
    assert changed == ["CARTESIA_API_KEY", "OPENROUTER_API_KEY", "RUN_BUDGET_USD"], changed
    text = open(p, encoding="utf-8").read()
    assert "# my notes" in text and "MIROFISH_BACKEND=D:/mf/backend" in text           # hand-written lines survive
    assert "CARTESIA_API_KEY=new-cartesia-key-9999" in text and "old-cartesia" not in text   # replaced in place
    assert "RUN_BUDGET_USD=2.5" in text and os.environ["OPENROUTER_API_KEY"].endswith("5678")
    assert settings.apply({"OPENROUTER_API_KEY": ""}) == []                             # a blank secret keeps the saved one
    assert settings.apply({}, clear=["OPENROUTER_API_KEY"]) == ["OPENROUTER_API_KEY"]
    assert "OPENROUTER_API_KEY" not in open(p, encoding="utf-8").read() and "OPENROUTER_API_KEY" not in os.environ
    assert settings.apply({"RUN_BUDGET_USD": ""}) == ["RUN_BUDGET_USD"]                 # a blank plain value removes it


def test_creates_env_when_missing():
    p = fresh_env()
    settings.apply({"FL_FALLBACK_VOICE": "browser"})
    text = open(p, encoding="utf-8").read()
    assert text.startswith("# FAULT LINES settings") and "FL_FALLBACK_VOICE=browser" in text


def test_validation_rejects_junk():
    fresh_env()
    for values in ({"OPENROUTER_API_KEY": "my key is here"}, {"OPENROUTER_API_KEY": "short"},
                   {"CARTESIA_API_KEY": "abcdefghij\nEVIL=1"}, {"FL_FALLBACK_VOICE": "loud"},
                   {"RUN_BUDGET_USD": "lots"}, {"RUN_BUDGET_USD": "-1"}, {"LOCAL_MODEL": "qwen2.5:7b"}, {"FAL_KEY": "abcdefgh12345678"},
                   {"MIROFISH_BACKEND": "C:/x"}, {"PATH": "x"}):
        try:
            settings.apply(values)
        except ValueError:
            continue
        raise AssertionError(f"accepted {values}")
    assert not os.path.exists(settings.ENV_PATH)                                          # nothing written on a rejection
    try:
        settings.apply({}, clear=["PATH"])
        raise AssertionError("cleared an unknown key")
    except ValueError:
        pass


def test_view_never_shows_a_secret():
    fresh_env()
    settings.apply({"FISH_AUDIO_API_KEY": "abcdef0123456789secretTAIL", "FL_FALLBACK_VOICE": "browser"})
    v = {f["key"]: f for f in settings.view()}
    assert v["FISH_AUDIO_API_KEY"]["set"] is True and v["FISH_AUDIO_API_KEY"]["hint"] == "•••• TAIL"
    assert "value" not in v["FISH_AUDIO_API_KEY"] and "0123456789" not in str(settings.view())
    assert v["FL_FALLBACK_VOICE"]["value"] == "browser" and v["OPENROUTER_API_KEY"]["set"] is False
    assert "LOCAL_MODEL" not in v and "FAL_KEY" not in v and "VIDEO_PROVIDER" not in v      # not offered in the release


def test_key_test_needs_a_key():
    fresh_env()
    assert settings.test("openrouter")["ok"] is False                                    # nothing saved, nothing pasted: no call
    assert settings.test("openrouter", "not a key")["ok"] is False
    assert settings.test("bogus", "abcdefgh12345678")["ok"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok ", fn.__name__)
    print(f"{len(fns)} passed")
