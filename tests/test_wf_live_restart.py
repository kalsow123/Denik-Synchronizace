"""WF live runtime — catch-up po restartu (forward replay, ne jen state-only)."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from runtime.wf_live import WfLiveRuntime


def _minimal_df(n: int = 5) -> pd.DataFrame:
    times = pd.date_range("2026-01-01 10:00", periods=n, freq="30min")
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.0] * n,
            "high": [1.01] * n,
            "low": [0.99] * n,
            "close": [1.0 + i * 0.001 for i in range(n)],
        },
    )


def test_wf_live_first_run_advances_last_processed_without_state_only_skip():
    cfg = BotConfig(wf_enabled=True)
    rt = WfLiveRuntime()
    df = _minimal_df()
    waves = [
        {
            "wave_time": "W1",
            "dir": 1,
            "box_top": 1.02,
            "box_bottom": 0.98,
            "draw_right": 1,
        },
    ]
    rt.process(df, cfg, waves)
    assert rt._last_processed_bar_time == pd.Timestamp(df["time"].iloc[-1])
