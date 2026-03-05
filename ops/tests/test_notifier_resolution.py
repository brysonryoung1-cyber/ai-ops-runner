from __future__ import annotations

from pathlib import Path

from ops.lib import notifier


def _clear_webhook_env(monkeypatch) -> None:
    for env_name in notifier.DISCORD_WEBHOOK_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)


def test_resolve_discord_webhook_url_accepts_discord_webhook_url_alias(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _clear_webhook_env(monkeypatch)
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_SECRET_FILE", tmp_path / "missing_secret")
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_CONFIG_FILE", tmp_path / "missing_config")
    webhook_url = "https://discord.example/alias-webhook"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", webhook_url)

    resolved, source = notifier.resolve_discord_webhook_url()

    assert resolved == webhook_url
    assert source == "env"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_resolve_discord_webhook_url_accepts_openclaw_env_alias(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_webhook_env(monkeypatch)
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_SECRET_FILE", tmp_path / "missing_secret")
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_CONFIG_FILE", tmp_path / "missing_config")
    webhook_url = "https://discord.example/openclaw-webhook"
    monkeypatch.setenv("OPENCLAW_DISCORD_WEBHOOK_URL", webhook_url)

    resolved, source = notifier.resolve_discord_webhook_url()

    assert resolved == webhook_url
    assert source == "env"


def test_resolve_discord_webhook_url_uses_config_file_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_webhook_env(monkeypatch)
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_SECRET_FILE", tmp_path / "missing_secret")
    config_path = tmp_path / "discord_webhook_url"
    config_path.write_text("https://discord.example/config-webhook\n", encoding="utf-8")
    monkeypatch.setattr(notifier, "DISCORD_WEBHOOK_CONFIG_FILE", config_path)

    resolved, source = notifier.resolve_discord_webhook_url()

    assert resolved == "https://discord.example/config-webhook"
    assert source == "file"
