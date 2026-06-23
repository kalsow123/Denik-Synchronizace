"""WF merge + ghost gate: index_in_trend musí sedět s HTML chartem."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from backtest.visual_wave_filter import wave_passes_visual_filter
from backtest.visual_waves import build_wave_visual_bundle
from backtest.waves_plotly_figure import _wave_visible_in_html_plot
from config.bot_config import BotConfig
from strategy.wave_sequence import compute_wave_sequence_info_per_wave

EXT_BEAR = "202505142300"
GHOST_BEAR = "202505150830"
VISIBLE_BEAR = "202505152030"


def _testing_cfg() -> tuple[dict, BotConfig]:
    """Stabilní kombinace z profilu testing (wf + thhl + wave_target_n)."""
    combos = generate_combinations(get_profile("testing"))
    for combo in combos:
        if (
            combo.get("wf_enabled")
            and combo.get("trend_hh_hl_filter_enabled")
            and combo.get("tp_mode") == "wave_target_n"
            and combo.get("wave_counter_two_sided_enabled") is False
            and combo.get("pp_enabled") is False
        ):
            return combo, grid_dict_to_bot_config(combo)
    raise RuntimeError("testing profil: nenalezena vhodná kombinace")


def _segment_df() -> pd.DataFrame:
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[
        (df["time"] >= "2025-05-10") & (df["time"] <= "2025-05-20")
    ].reset_index(drop=True)


def _run_testing_engine() -> BacktestEngine:
    combo, cfg = _testing_cfg()
    assert combo.get("wf_enabled") is True
    assert combo.get("trend_hh_hl_filter_enabled") is True
    assert cfg.wf_enabled is True
    assert cfg.trend_hh_hl_filter_enabled is True
    eng = BacktestEngine(cfg)
    eng.run(_segment_df(), retain_wave_snapshot=True)
    return eng


def test_wf_merge_recomputes_bear_wave_index_after_ext():
    """Regrese EURUSD M30 2025-05-10–20: brown idx=2, ghost pryč z _all_waves i last_waves."""
    eng = _run_testing_engine()
    cfg = eng.cfg

    by_wt = {str(w["wave_time"]): w for w in eng._all_waves}
    last_wts = {str(w["wave_time"]) for w in eng.last_waves}
    vis_wts = {str(w["wave_time"]) for w in eng.last_waves_for_visual}

    assert GHOST_BEAR not in by_wt
    assert GHOST_BEAR not in last_wts
    assert GHOST_BEAR not in vis_wts

    ext_info = eng.wave_sequence_info[EXT_BEAR]
    vis_info = eng.wave_sequence_info[VISIBLE_BEAR]
    vis = by_wt[VISIBLE_BEAR]

    assert ext_info.index_in_trend == 1
    assert vis_info.index_in_trend == 2
    assert vis_info.prev_same_dir_in_trend_wave_time == EXT_BEAR
    assert vis["index_in_trend"] == vis_info.index_in_trend

    bos = set(getattr(eng, "_visual_bos_wave_times", set()) or set())
    assert _wave_visible_in_html_plot(vis, cfg, bos_wave_times=bos)
    assert int(vis["index_in_trend"]) == 2


def test_visual_bundle_index_matches_engine_after_wf_merge():
    """Bundle index_in_trend == engine.wave_sequence_info == propagate ve wave dict."""
    eng = _run_testing_engine()
    by_wt = {str(w["wave_time"]): w for w in eng._all_waves}
    vis = by_wt[VISIBLE_BEAR]
    engine_idx = eng.wave_sequence_info[VISIBLE_BEAR].index_in_trend

    waves_src = eng.last_waves_for_visual or eng.last_waves
    bundle = build_wave_visual_bundle(
        eng._run_df,
        list(waves_src),
        eng.wave_birth_by_time,
        eng.closed_trades,
        full_span=True,
        wave_seq_by_time=eng.wave_sequence_info,
    )
    assert bundle is not None
    bundle_by = {str(w["wave_time"]): w for w in bundle.waves}
    assert VISIBLE_BEAR in bundle_by

    bundle_idx = bundle_by[VISIBLE_BEAR]["index_in_trend"]
    assert bundle_idx == 2
    assert bundle_idx == engine_idx
    assert bundle_idx == vis["index_in_trend"]


def test_ghost_hh_hl_fail_skips_index_while_in_wave_list():
    """Ghost gate: hh_hl_pass=False vlna v seznamu → idx None, další viditelná idx 2."""
    bars = [
        (1.20, 1.20, 1.10, 1.10),
        (1.10, 1.12, 1.08, 1.09),
        (1.09, 1.10, 1.05, 1.06),
        (1.06, 1.07, 1.03, 1.04),
        (1.04, 1.05, 1.03, 1.05),
        (1.05, 1.06, 1.04, 1.04),
        (1.04, 1.05, 1.01, 1.02),
        (1.02, 1.03, 1.02, 1.03),
        (1.03, 1.05, 1.02, 1.04),
        (1.04, 1.10, 1.04, 1.09),
        (1.09, 1.20, 1.08, 1.18),
        (1.18, 1.19, 1.16, 1.17),
        (1.17, 1.175, 1.15, 1.16),
        (1.16, 1.18, 1.15, 1.17),
    ]
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"])

    def _w(wt, d, dl, dr, top, bot, **kw):
        return {
            "wave_time": wt,
            "dir": d,
            "draw_left": dl,
            "draw_right": dr,
            "box_top": top,
            "box_bottom": bot,
            "is_ext": kw.get("is_ext", False),
            "hh_hl_pass": kw.get("hh_hl_pass", True),
        }

    waves = [
        _w("DOWN1", -1, 0, 3, 1.20, 1.03),
        _w("DOWN2", -1, 4, 6, 1.05, 1.01),
        _w("EXT_UP", 1, 7, 10, 1.20, 1.02, is_ext=True),
        _w("GHOST_UP", 1, 11, 12, 1.175, 1.15, hh_hl_pass=False),
        _w("UP2", 1, 13, 13, 1.18, 1.15),
    ]

    cfg = BotConfig(trend_hh_hl_filter_enabled=True, trend_filter_enabled=True)
    result = compute_wave_sequence_info_per_wave(df, waves, cfg)

    assert result["GHOST_UP"].index_in_trend is None
    assert not wave_passes_visual_filter(waves[3], cfg, check_bos=False)
    assert result["UP2"].index_in_trend == 2
    assert result["UP2"].prev_same_dir_in_trend_wave_time == "EXT_UP"
