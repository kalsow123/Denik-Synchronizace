"""Testy pojistky MT5 session (bez live terminalu)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import infra.mt5_client as mt5_client
from mt5_credentials import MT5_LOGIN, MT5_SERVER, MT5_PATH


def _terminal_info(path: Path):
    return SimpleNamespace(path=str(path))


def _account_info(login: int, server: str):
    return SimpleNamespace(login=login, server=server)


def test_verify_mt5_session_ok(monkeypatch):
    expected_dir = mt5_client._normalize_terminal_dir(MT5_PATH)
    monkeypatch.setattr(
        mt5_client.mt5,
        "terminal_info",
        lambda: _terminal_info(expected_dir / "terminal64.exe"),
    )
    monkeypatch.setattr(
        mt5_client.mt5,
        "account_info",
        lambda: _account_info(MT5_LOGIN, MT5_SERVER),
    )

    ok, reason, details = mt5_client.verify_mt5_session()

    assert ok is True
    assert reason == ""
    assert details["actual_login"] == MT5_LOGIN


def test_verify_mt5_session_wrong_terminal(monkeypatch):
    monkeypatch.setattr(
        mt5_client.mt5,
        "terminal_info",
        lambda: _terminal_info(r"C:\Other\Bot\MT5\terminal64.exe"),
    )
    monkeypatch.setattr(
        mt5_client.mt5,
        "account_info",
        lambda: _account_info(MT5_LOGIN, MT5_SERVER),
    )

    ok, reason, _ = mt5_client.verify_mt5_session()

    assert ok is False
    assert "Spatny MT5 terminal" in reason


def test_verify_mt5_session_wrong_account(monkeypatch):
    expected_dir = mt5_client._normalize_terminal_dir(MT5_PATH)
    monkeypatch.setattr(
        mt5_client.mt5,
        "terminal_info",
        lambda: _terminal_info(expected_dir / "terminal64.exe"),
    )
    monkeypatch.setattr(
        mt5_client.mt5,
        "account_info",
        lambda: _account_info(1111111, MT5_SERVER),
    )

    ok, reason, _ = mt5_client.verify_mt5_session()

    assert ok is False
    assert "Spatny MT5 ucet" in reason


def test_enforce_mt5_session_exits_on_mismatch(monkeypatch):
    monkeypatch.setattr(
        mt5_client,
        "verify_mt5_session",
        lambda: (False, "Spatny MT5 ucet", {"expected_login": 1, "actual_login": 2}),
    )
    monkeypatch.setattr(mt5_client.mt5, "shutdown", lambda: None)

    with pytest.raises(SystemExit) as exc:
        from config.bot_config import LIVE_BOT_CONFIG

        mt5_client.enforce_mt5_session(LIVE_BOT_CONFIG)

    assert exc.value.code == 3
