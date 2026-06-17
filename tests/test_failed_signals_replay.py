"""Failed signals replay — birth bar gate + recovery clear."""
from __future__ import annotations

from config.bot_config import BotConfig
from runtime.failed_signals_replay import (
    abandon_failed_signal,
    clear_failed_signals_on_recovery,
    failed_signal_replay_eligible,
)


def test_replay_eligible_on_birth_bar():
    birth = {"202601011000": 42}
    assert failed_signal_replay_eligible(
        "202601011000",
        wave_birth_by_time=birth,
        last_bar_idx=42,
    )
    assert not failed_signal_replay_eligible(
        "202601011000",
        wave_birth_by_time=birth,
        last_bar_idx=43,
    )


def test_replay_eligible_on_missed_bar_batch():
    birth = {"202601011000": 40}
    assert failed_signal_replay_eligible(
        "202601011000",
        wave_birth_by_time=birth,
        last_bar_idx=42,
        new_bar_indices=[40, 41, 42],
    )


def test_abandon_logs_and_marks_sent():
    cfg = BotConfig()
    sent: set[str] = set()
    failed = {"k1": {"wave": {"wave_time": "202601011000"}, "attempts": 1}}
    abandon_failed_signal(
        cfg=cfg,
        sig_key="k1",
        wave_time="202601011000",
        sent_signals=sent,
        failed_signals=failed,
        reason="birth_bar_passed",
    )
    assert "k1" in sent
    assert "k1" not in failed


def test_clear_on_recovery():
    cfg = BotConfig()
    failed = {"k1": {"wave": {}, "attempts": 1}}
    n = clear_failed_signals_on_recovery(failed, cfg=cfg, reason="startup")
    assert n == 1
    assert failed == {}
