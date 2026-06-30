"""CESTA D: EXT1 → W2 HH nad EXT1 → konec EXT → WAVE_BOS pod W2."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.translator import grid_dict_to_bot_config


def _testing_combo():
    combos = generate_combinations(get_profile("testing"))
    return grid_dict_to_bot_config(combos[0])


def _find_ext1_wave2_scenario(eng: BacktestEngine) -> tuple[str, str] | None:
    """Najde EXT1 UP s navazujici W2 UP s HH nad EXT1 v simulaci."""
    for w in eng.last_waves:
        if not w.get("is_ext"):
            continue
        wt_ext = str(w["wave_time"])
        info_ext = eng.wave_sequence_info.get(wt_ext)
        if info_ext is None or info_ext.index_in_trend != 1:
            continue
        if int(w.get("dir", 0)) != 1:
            continue
        ext_top = float(w.get("box_top"))
        ext_dr = int(w.get("draw_right", 0))
        for w2 in eng.last_waves:
            wt2 = str(w2["wave_time"])
            if wt2 == wt_ext:
                continue
            if int(w2.get("dir", 0)) != 1:
                continue
            try:
                dr2 = int(w2.get("draw_right"))
            except (TypeError, ValueError):
                continue
            if dr2 <= ext_dr:
                continue
            if float(w2.get("box_top")) <= ext_top:
                continue
            info2 = eng.wave_sequence_info.get(wt2)
            if info2 is None or info2.index_in_trend != 2:
                continue
            return wt_ext, wt2
    return None


def test_ext1_wave2_hh_ends_ext_and_allows_wave_bos():
    cfg = _testing_combo()
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    df = df[(df["time"] >= "2025-01-01") & (df["time"] <= "2025-12-31")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df.copy(), retain_wave_snapshot=True)

    pair = _find_ext1_wave2_scenario(eng)
    if pair is None:
        import pytest

        pytest.skip("no EXT1→W2 HH scenario in dataset slice")
    wt_ext, wt_w2 = pair

    w2 = eng.waves_by_wave_time[wt_w2]
    assert not w2.get("in_ext_range", False), "EXT range must end on W2 with HH"

    flips = eng._close_bos_flip_bar_indices or set()
    w2_dr = int(w2.get("draw_right", 0))
    post_w2_flips = [i for i in sorted(flips) if i > w2_dr]
    assert post_w2_flips, "expected WAVE_BOS flip after W2 that ended EXT1"
