"""Cílený look-ahead test: jsou E2E-obchodované vlny detekovatelné KAUZÁLNĚ?

Pro každou vlnu, kterou E2E reálně obchoduje, porovná detekci nad CELÝM df
(jak to dělá run_e2e — riziko look-ahead) vs nad KAUZÁLNÍM 1440-oknem končícím
na baru, kde ji bot poprvé vidí (birth nad celým df). Pine sim běží zleva-doprava,
takže tohle přímo ukáže, jestli full-df předpočet dává vstupní výhodu.

Spuštění: $env:PYTHONIOENCODING="utf-8"; .venv/Scripts/python.exe scripts/_diag_lookahead_check.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"
WINDOW = 1440


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import install_fake, run_e2e, _clean_wave_time

    engine_cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(engine_cfg.symbol, engine_cfg.contract_size)

    from backtest.data_loader import filter_by_date_range, load_csv
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)

    # full-df detekce (to, co run_e2e předpočítá)
    full_waves = detect_waves(df, cfg)
    full_birth = compute_wave_birth_bars_pine(df, cfg)
    full_by_wt = {str(w["wave_time"]): w for w in full_waves}

    # absolutní čas -> index (pro mapování birth window->abs)
    t2i = {int(__import__("pandas").Timestamp(t).value): i for i, t in enumerate(df["time"])}
    import pandas as pd

    # E2E obchodované vlny (bez flagu = produkční režim)
    closed = run_e2e(df, cfg, fake)
    traded = sorted({_clean_wave_time(getattr(t, "comment", "")) for t in closed})
    traded = [wt for wt in traded if wt]
    print(f"E2E obchodovaných unikátních vln: {len(traded)}\n", flush=True)

    ok_present = 0
    ok_birth = 0
    missing = []
    birth_diff = []
    no_full_birth = []

    for wt in traded:
        eb = full_birth.get(wt)
        if eb is None:
            no_full_birth.append(wt)
            continue
        lo = max(0, eb + 1 - WINDOW)
        prefix = df.iloc[lo:eb + 1].reset_index(drop=True)
        pw = detect_waves(prefix, cfg)
        pbirth = compute_wave_birth_bars_pine(prefix, cfg)
        present = wt in {str(w["wave_time"]) for w in pw}
        if not present:
            missing.append((wt, eb))
            continue
        ok_present += 1
        pb = pbirth.get(wt)
        causal_abs = (lo + pb) if pb is not None else None
        if causal_abs == eb:
            ok_birth += 1
        else:
            birth_diff.append((wt, eb, causal_abs))

    n = len(traded)
    print(f"PŘÍTOMNOST v kauzálním okně:  {ok_present}/{n}")
    print(f"BIRTH shoda (kauzál == full): {ok_birth}/{n}")
    print(f"  bez full-birth: {len(no_full_birth)}  {no_full_birth[:20]}")
    print(f"\nCHYBÍ v kauzálním okně ({len(missing)}):")
    for wt, eb in missing[:40]:
        w = full_by_wt.get(wt, {})
        print(f"  {wt}  eb={eb}  dl={w.get('draw_left')} dr={w.get('draw_right')} "
              f"win_lo={max(0, eb + 1 - WINDOW)}  (dl<win_lo={int(w.get('draw_left', 0)) < max(0, eb + 1 - WINDOW)})")
    print(f"\nBIRTH posun (přítomna, jiný birth) ({len(birth_diff)}):")
    for wt, eb, cb in birth_diff[:40]:
        print(f"  {wt}  full_birth={eb}  causal_birth={cb}  diff={None if cb is None else cb - eb}")


if __name__ == "__main__":
    main()
