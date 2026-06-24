"""Cold-start replay: MT5 side effects vs state-only sync."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from config.bot_config import LIVE_BOT_CONFIG
from config.enums import PendingCancelMode
from runtime.missed_bar_replay import MissedBarReplayState, replay_missed_closed_bar


def _minimal_df(n: int = 3) -> pd.DataFrame:
    times = pd.date_range("2026-06-20 10:00", periods=n, freq="30min")
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.10] * n,
            "high": [1.11] * n,
            "low": [1.09] * n,
            "close": [1.10, 1.12, 1.08],
        }
    )


def _replay_kw(**overrides):
    cfg = LIVE_BOT_CONFIG
    df = _minimal_df()
    state = MissedBarReplayState(
        last_known_trend_dir="bear",
        prev_cycle_last_bar_time=None,
        processed_tp_wave_times=set(),
        forming_tp_watch=None,
        ext_sl_anchor=None,
        retro_bos_attempted=set(),
        promoted_two_sided_wave_times=set(),
    )
    ext_runtime = MagicMock()
    ext_runtime._wave_birth_by_time = {}
    base = dict(
        cfg=cfg,
        df=df,
        waves=[],
        bar_idx=1,
        state=state,
        bar_trend_states=[None, MagicMock(direction="bull"), MagicMock(direction="bull")],
        seq_info={},
        protected_waves=set(),
        bos_flip_map={},
        bos_wave_times=set(),
        trend_states_per_wave={},
        ext1_per_bar=None,
        ext_runtime=ext_runtime,
        wf_activations=[],
        sent_signals=set(),
        failed_signals={},
        signal_digits=5,
        entries_allowed=True,
        wave_birth_by_time={},
        active_counter_wave_times=set(),
        pcm=PendingCancelMode.TREND,
        place_live_bos_reentry=MagicMock(),
        place_live_counter_from_g_extension=MagicMock(),
        g_extension_hit_closed_positions=MagicMock(return_value=False),
        place_live_counter_position=MagicMock(),
        log_event_fn=MagicMock(),
    )
    base.update(overrides)
    return base


@patch("runtime.missed_bar_replay.find_close_bos_flip_for_target_since")
@patch("runtime.missed_bar_replay.cancel_pendings_by_direction")
def test_cold_start_replay_skips_mt5_cancel(mock_cancel, mock_flip):
    mock_flip.return_value = (pd.Timestamp("2026-06-20 10:30"), "bull_bos", 1)
    replay_missed_closed_bar(**_replay_kw(apply_mt5_effects=False))
    mock_cancel.assert_not_called()


@patch("runtime.missed_bar_replay.find_close_bos_flip_for_target_since")
@patch("runtime.missed_bar_replay.cancel_pendings_by_direction")
def test_outage_replay_applies_mt5_cancel(mock_cancel, mock_flip):
    mock_flip.return_value = (pd.Timestamp("2026-06-20 10:30"), "bull_bos", 1)
    replay_missed_closed_bar(**_replay_kw(apply_mt5_effects=True))
    mock_cancel.assert_called_once()


@patch("runtime.missed_bar_replay.find_close_bos_flip_for_target_since")
def test_state_only_replay_still_updates_trend(mock_flip):
    mock_flip.return_value = (pd.Timestamp("2026-06-20 10:30"), "bull_bos", 1)
    kw = _replay_kw(apply_mt5_effects=False)
    out = replay_missed_closed_bar(**kw)
    assert out.last_known_trend_dir == "bull"
