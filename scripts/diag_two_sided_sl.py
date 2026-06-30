"""Diag: SL two-sided pozic vs realne low/high counter vlny."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.data_loader import filter_by_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import get_profile
from backtest.grid.translator import grid_dict_to_bot_config
from strategy.two_sided import prepare_two_sided_counter_signal


def main() -> None:
    row = {
        k: (v[0] if isinstance(v, list) else v)
        for k, v in get_profile("EXAMPLE")["grid"][0].items()
    }
    row["bot_name"] = "DIAG"
    row["tp_mode"] = "bos_exit"
    row["symbol"] = "EURUSD"
    row["timeframe"] = "M30"
    cfg = grid_dict_to_bot_config(row)

    df = load_csv("data/EURUSD_M30.csv")
    df = filter_by_date_range(df, "2026-03-17", "2026-03-22")
    eng = BacktestEngine(cfg)
    trades = eng.run(df, retain_wave_snapshot=True)

    ts_trades = [t for t in trades if getattr(t, "is_two_sided_mirror", False)]
    print(f"TWO-SIDED trades Mar 17-22: {len(ts_trades)}")

    wave_meta = {
        str(w.get("wave_time", "")): w for w in eng._all_waves
    }

    for t in ts_trades:
        wt = str(t.wave_time)
        w = wave_meta.get(wt, {})
        sig = prepare_two_sided_counter_signal(dict(w), cfg)
        print(f"  sig for {wt}: entry={sig.get('fib50'):.5f} sl={sig.get('sl'):.5f} tp={sig.get('tp'):.5f}")
        print(
            f"  fill={t.entry_time}->{t.close_time} entry={t.entry_price:.5f}"
            f" close={getattr(t, 'close_price', float('nan')):.5f}"
            f" reason={t.close_reason} pnl={getattr(t, 'pnl_usd', 0):.2f}"
        )
        d = int(w.get("dir", 0))
        top = float(w.get("box_top", 0.0))
        bot = float(w.get("box_bottom", 0.0))
        dl = int(w.get("draw_left", -1))
        dr = int(w.get("draw_right", -1))
        expected = bot if int(t.dir) == 1 else top

        actual_high = actual_low = None
        if dl >= 0 and dr >= 0:
            sub = df.iloc[dl : dr + 1]
            actual_high = float(sub["high"].max())
            actual_low = float(sub["low"].min())

        al = actual_low if actual_low is not None else 0.0
        ah = actual_high if actual_high is not None else 0.0
        print(
            f"  wt={wt} dir={t.dir} entry={t.entry_price:.5f} SL={t.sl:.5f}"
            f" expect_extreme={expected:.5f}"
            f" actual_low={al:.5f}"
            f" actual_high={ah:.5f}"
            f" box_bot={bot:.5f} box_top={top:.5f} draw=[{dl}..{dr}]"
        )


if __name__ == "__main__":
    main()
