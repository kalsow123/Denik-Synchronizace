"""
DIAG: proc engine NEzavre WAVE 202601201530 (BUY, entry 2445 v bear) na per-bar
BOS, ale drzi do TP_WAVE_N (2552)? Instrumentujeme should_close_trade_on_bos_flip
+ _make_closed pro danou vlnu.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
TARGET = "202601201530"


def main() -> None:
    from backtest.data_loader import filter_by_date_range, load_csv
    import backtest.engine as eng_mod
    from backtest.engine import BacktestEngine
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)

    # wrap should_close_trade_on_bos_flip (jak je naimportovana v engine modulu)
    orig_should = eng_mod.should_close_trade_on_bos_flip
    log = []

    def traced_should(trade, *, broken_dir, flipped, protected_wave_times=None):
        r = orig_should(trade, broken_dir=broken_dir, flipped=flipped,
                        protected_wave_times=protected_wave_times)
        if str(getattr(trade, "wave_time", "")) == TARGET:
            log.append(("should_close", broken_dir, flipped,
                        "prot" if (protected_wave_times and str(getattr(trade,'wave_time','')) in protected_wave_times) else "",
                        r))
        return r

    eng_mod.should_close_trade_on_bos_flip = traced_should

    # wrap _handle_bos_exit_on_bar pro bar idx context
    orig_handle = BacktestEngine._handle_bos_exit_on_bar
    cur_bar = {"i": -1}

    def _snap(self, bar_idx, when):
        opens = [t for t in getattr(self, "open_trades", [])
                 if str(getattr(t, "wave_time", "")) == TARGET]
        pends = [o for o in getattr(self, "pending_orders", [])
                 if str(getattr(o, "wave_time", "")) == TARGET]
        if bar_idx in (2444, 2445, 2446, 2490, 2491, 2492, 2552):
            for t in opens:
                log.append(("OPEN@" + when, bar_idx,
                            f"dir={getattr(t,'dir','?')} is_ext={getattr(t,'is_ext','?')} "
                            f"is_counter={getattr(t,'is_counter','?')} tag={getattr(t,'entry_tag','?')} "
                            f"entry_bar={getattr(t,'entry_bar','?')}"))
            for o in pends:
                log.append(("PEND@" + when, bar_idx,
                            f"dir={getattr(o,'dir','?')} type={getattr(o,'order_type','?')} "
                            f"is_ext={getattr(o,'is_ext','?')} is_counter={getattr(o,'is_counter','?')}"))

    def traced_handle(self, bar_idx, *a, **k):
        cur_bar["i"] = bar_idx
        log.append(("BAR", bar_idx, "close_pos=" + str(k.get("close_positions"))))
        _snap(self, bar_idx, "pre_bos")
        r = orig_handle(self, bar_idx, *a, **k)
        _snap(self, bar_idx, "post_bos")
        return r

    BacktestEngine._handle_bos_exit_on_bar = traced_handle

    # wrap _make_closed
    orig_close = BacktestEngine._make_closed

    def traced_close(self, trade, close_bar, close_price, bar_time, reason, *a, **k):
        if str(getattr(trade, "wave_time", "")) == TARGET:
            log.append(("CLOSED", close_bar, reason, getattr(trade, "entry_bar", "?"),
                        f"dir={getattr(trade,'dir','?')} is_ext={getattr(trade,'is_ext','?')} "
                        f"is_counter={getattr(trade,'is_counter','?')} "
                        f"is_two_sided={getattr(trade,'is_two_sided_mirror','?')} "
                        f"is_bos_reentry={getattr(trade,'is_bos_reentry','?')} "
                        f"tag={getattr(trade,'entry_tag','?')}"))
        return orig_close(self, trade, close_bar, close_price, bar_time, reason, *a, **k)

    BacktestEngine._make_closed = traced_close

    BacktestEngine(cfg).run(df, retain_wave_snapshot=False)

    # vypis jen kolem zajimavych baru (2444-2553)
    print(f"TARGET={TARGET}  zaznamy (bar 2443-2553):")
    cur = None
    for e in log:
        if e[0] == "BAR":
            if 2443 <= e[1] <= 2553:
                cur = e[1]
                print(f"\n  --- BAR {e[1]} ({e[2]}) ---")
            else:
                cur = None
        elif cur is not None:
            print(f"      {e}")
        elif e[0] == "CLOSED":
            print(f"  >>> CLOSED bar={e[1]} reason={e[2]} entry_bar={e[3]}  {e[4] if len(e)>4 else ''}")
        elif e[0].startswith("OPEN@") or e[0].startswith("PEND@"):
            print(f"  [{e[0]} bar={e[1]}] {e[2]}")


if __name__ == "__main__":
    main()
