"""keel.livekit.preflight: the provider check before a benchmark run (no network here)."""

from keel.livekit import preflight


def test_no_or_unknown_provider_is_a_usage_error(capsys):
    assert preflight.main([]) == 2
    assert preflight.main(["openai", "nope"]) == 2
    assert "usage" in capsys.readouterr().err


def test_a_refusal_fails_the_check_and_says_which_provider(monkeypatch, capsys):
    def refuse(cfg):
        raise RuntimeError("You have no credits remaining")

    monkeypatch.setitem(preflight.CHECKS, "openai", refuse)
    monkeypatch.setitem(preflight.CHECKS, "gemini", lambda cfg: "Gemini accepts the key (m)")
    assert preflight.main(["gemini", "openai"]) == 1
    out = capsys.readouterr()
    assert "Gemini accepts the key" in out.out
    assert "openai: the provider refused a minimal request" in out.err and "no credits" in out.err


def test_every_check_passing_passes(monkeypatch):
    monkeypatch.setitem(preflight.CHECKS, "openai", lambda cfg: "ok")
    assert preflight.main(["openai"]) == 0
