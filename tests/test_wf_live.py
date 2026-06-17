"""WF live runtime — parita s engine bar-by-bar vs evaluate_wf_from_df."""
from __future__ import annotations

from config.bot_config import BotConfig
from runtime.wf_live import WfLiveRuntime
from strategy.wave_detection import detect_waves
from strategy.wf_wave_list import prepare_waves_after_wf_eval
from strategy.wick_fakeout import evaluate_wf_from_df


def _sample_cfg(**kw) -> BotConfig:
    base = dict(
        wf_enabled=True,
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=False,
    )
    base.update(kw)
    return BotConfig(**base)


def _make_df():
    import pandas as pd

    times = pd.date_range("2025-06-01 00:00", periods=40, freq="30min")
    close = [1.1000 + i * 0.0001 for i in range(40)]
    close[31] = 1.1012
    close[33] = 1.0995
    high = [c + 0.0003 for c in close]
    low = [c - 0.0003 for c in close]
    high[31] = 1.1015
    low[33] = 1.0990
    return pd.DataFrame(
        {"time": times, "open": close, "high": high, "low": low, "close": close}
    )


def test_wf_live_first_sync_no_activation():
    df = _make_df()
    cfg = _sample_cfg()
    waves = detect_waves(df, cfg)
    rt = WfLiveRuntime()
    prep = rt.process(df, cfg, waves)
    assert prep.wf_wave is None


def test_wf_live_incremental_matches_batch_on_new_bar():
    df1 = _make_df().iloc[:-1].copy()
    df2 = _make_df().copy()
    cfg = _sample_cfg()
    waves1 = detect_waves(df1, cfg)
    rt = WfLiveRuntime()
    assert rt.process(df1, cfg, waves1).wf_wave is None

    waves2 = detect_waves(df2, cfg)
    live_prep = rt.process(df2, cfg, waves2)
    legacy = prepare_waves_after_wf_eval(df2, cfg, list(waves2))

    if legacy.wf_wave is None:
        assert live_prep.wf_wave is None
    else:
        assert live_prep.wf_wave is not None
        assert str(live_prep.wf_wave.get("wave_time")) == str(
            legacy.wf_wave.get("wave_time")
        )


def test_wf_live_tracker_same_as_evaluate_wf_from_df():
    df = _make_df()
    cfg = _sample_cfg()
    waves = detect_waves(df, cfg)
    if not waves:
        return
    batch = evaluate_wf_from_df(df, waves[-1], cfg)
    rt = WfLiveRuntime()
    rt.process(df.iloc[:-1].copy(), cfg, detect_waves(df.iloc[:-1].copy(), cfg))
    live = rt.process(df, cfg, waves)
    if batch is None:
        assert live.wf_wave is None
    elif batch.get("status") == "activate":
        assert live.wf_wave is not None
