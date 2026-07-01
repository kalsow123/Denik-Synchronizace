"""3B — E2E legacy vs BACKTEST WAVE (2 roky): běh bez chyby + smysluplná čísla.

E2E používá legacy engine (live_loop_legacy + missed_bar_replay), ne nový
LiveEngineSession. Rozdíl vůči incremental_causal backtesteru je OČEKÁVANÝ —
test neassertuje striktní shodu, jen že obě větve doběhnou a vrátí >0 obchodů.

Referenční čísla z běhu 2026-07-01 (2leté okno, default_backtest_date_range):
  date_from=2024-05-18, date_to=None, 24 814 barů EURUSD M30
  BACKTEST WAVE (legacy engine v e2e skriptu): 751 obchodů / +279 156.24 USD / max DD −10.14 %
    exit: TP_WAVE_N 142, SL 366, BOS_EXIT_WAVE_TARGET 201, EXT_BOS_CLOSE 38, TP 4
  LIVE E2E legacy: 822 obchodů / +157 455.29 USD / max DD −10.66 %
    exit (agreg.): SL 405, TP 5, TP_WAVE_N ~142, BOS_EXIT_* ~270 (live reason obsahuje wave suffix)
  delta (LIVE − BACKTEST v e2e): +71 obchodů / −121 700.95 USD
  incremental STRICT (test_incremental_reference_snapshot, jiný engine/metrika): 640 / +124 461.68 / DD −10.34 %
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_loader import default_backtest_date_range, load_csv
from backtest.grid.data_cache import clear_cache
from backtest.wave_sim_cache import clear_pine_sim_cache

CSV = ROOT / "data" / "EURUSD_M30.csv"

pytestmark = pytest.mark.slow

# Regresní kotvy z běhu 2026-07-01 (2 roky) — signál pro prošetření, ne tvrdý assert.
REF_DATE_FROM = "2024-05-18"
REF_BACKTEST_TRADES = 751
REF_BACKTEST_NET_PNL_USD = 279156.24
REF_LIVE_TRADES = 822
REF_LIVE_NET_PNL_USD = 157455.29


def _window() -> tuple[str | None, str | None]:
    df_full = load_csv(str(CSV))
    return default_backtest_date_range(df_full)


@pytest.fixture(autouse=True)
def _reset_caches():
    clear_cache()
    clear_pine_sim_cache()
    yield
    clear_cache()
    clear_pine_sim_cache()
    if "MetaTrader5" in sys.modules:
        mod = sys.modules["MetaTrader5"]
        if getattr(mod, "__class__", None).__name__ == "FakeMt5" or hasattr(mod, "_closed"):
            del sys.modules["MetaTrader5"]


@pytest.mark.skipif(not CSV.exists(), reason="data/EURUSD_M30.csv missing")
def test_e2e_legacy_vs_backtest_wave_runs_on_2y_window():
    """E2E legacy doběhne na 2letém okně a vrátí kladný počet obchodů oběma větvemi."""
    from scripts import e2e_live_broker_sim as e2e

    date_from, date_to = _window()
    result = e2e.main(date_from=date_from, date_to=date_to)

    assert result["bars"] > 20_000, f"expected ~2y M30 bars, got {result['bars']}"
    assert result["backtest_trades"] > 0
    assert result["live_trades"] > 0
    assert result["backtest_net_pnl_usd"] != 0.0 or result["live_net_pnl_usd"] != 0.0

    # Dokumentovaný rozdíl je OK — jen log pro regresní kontrolu v CI logu.
    delta_trades = result["live_trades"] - result["backtest_trades"]
    delta_pnl = result["live_net_pnl_usd"] - result["backtest_net_pnl_usd"]
    print(
        f"E2E 2y: BT {result['backtest_trades']} / {result['backtest_net_pnl_usd']:.2f} USD | "
        f"LIVE {result['live_trades']} / {result['live_net_pnl_usd']:.2f} USD | "
        f"delta trades {delta_trades:+d}, PnL {delta_pnl:+.2f} USD"
    )
