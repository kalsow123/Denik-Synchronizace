"""Counter-only path when wave_position_enabled=False."""
from __future__ import annotations

from unittest.mock import patch

from config.bot_config import BotConfig
from runtime import live_loop as ll


def test_counter_only_skips_primary_wave_send():
    cfg = BotConfig(
        wave_position_enabled=False,
        wave_counter_two_sided_enabled=True,
        tp_mode="wave_target_n",
        tp_target_wave_index=4,
    )
    sent: set[str] = set()
    wave = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.30,
    }
    sig_key = "k1"
    with patch.object(ll, "_maybe_place_live_counter_from_tp") as mock_counter:
        assert ll._try_live_counter_only_on_wave(
            cfg=cfg,
            wave=wave,
            seq_info={},
            all_waves=[],
            entries_allowed=True,
            sent_signals=sent,
            sig_key=sig_key,
        )
        mock_counter.assert_called_once()
    assert sig_key in sent


def test_counter_only_no_counter_flag_consumes_signal():
    cfg = BotConfig(wave_position_enabled=False, wave_counter_two_sided_enabled=False)
    sent: set[str] = set()
    sig_key = "k2"
    with patch.object(ll, "_maybe_place_live_counter_from_tp") as mock_counter:
        assert ll._try_live_counter_only_on_wave(
            cfg=cfg,
            wave={"wave_time": "202601011000", "dir": 1, "fib50": 1.1, "sl": 1.09},
            seq_info={},
            all_waves=[],
            entries_allowed=True,
            sent_signals=sent,
            sig_key=sig_key,
        )
        mock_counter.assert_not_called()
    assert sig_key in sent
