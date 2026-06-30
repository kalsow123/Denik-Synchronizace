"""combo_no — první sloupec a párování s názvy souborů."""
from __future__ import annotations

import pandas as pd

from backtest.file_stems import prefixed_export_stem, visual_waves_export_stem
from backtest.grid.combo_columns import finalize_export_column_order


def test_finalize_column_order():
    df = pd.DataFrame({
        "bot_name": ["b_long_name"],
        "test_pozice": [3],
        "net_pnl_usd": [100.0],
    })
    out = finalize_export_column_order(df)
    assert list(out.columns[:2]) == ["combo_no", "bot_name"]
    assert "test_pozice" not in out.columns
    assert int(out.loc[0, "combo_no"]) == 3


def test_file_prefix_matches_combo_no():
    stem = prefixed_export_stem("hash_short", 42)
    assert stem.startswith("00042_")


def test_visual_waves_stem_includes_tp_mode_after_combo_no():
    bot = "M30_w0.26_o3_r2.0_f0.5_mkt_symbolEURUSD_sf0.8_afxshift_sl_wpTrue_exp3"
    stem = visual_waves_export_stem(bot, tp_mode="bos_exit", test_pozice=16)
    assert stem.startswith("00016_bos_exit_")
    assert "4598d7855c959d" not in stem
