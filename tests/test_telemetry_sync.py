"""Testy autostart telemetry sync."""
from __future__ import annotations

from dataclasses import replace

from config.bot_config import LIVE_BOT_CONFIG
from infra.telemetry_sync import update_env_sync_from_config


def test_update_env_sync_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    example = tmp_path / ".env.sync.example"
    example.write_text(
        "TELEMETRY_REPO_PATH=C:\\telemetry\n"
        "BOT_ID=OLD_BOT\n"
        "SOURCE_JSONL_PATH=C:\\old\\live.jsonl\n"
        "TARGET_BRANCH=main\n",
        encoding="utf-8",
    )
    cfg = replace(LIVE_BOT_CONFIG, bot_name="EURUSD_FXIFY_1_n=4")
    env_path = update_env_sync_from_config(cfg, tmp_path)
    assert env_path is not None
    text = env_path.read_text(encoding="utf-8")
    assert "TELEMETRY_REPO_PATH=C:\\telemetry" in text
    assert f"SOURCE_JSONL_PATH={tmp_path / 'EURUSD_FXIFY_1_n=4.jsonl'}" in text.replace("/", "\\")
    assert "BOT_ID=EURUSD_FXIFY_1_n=4" in text
