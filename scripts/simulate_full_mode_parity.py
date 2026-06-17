"""
Simulace FULL režimu: všechny druhy pozic ON, wave isolation OFF.
Porovnání live MT5 vs backtest engine.
"""
from __future__ import annotations

from dataclasses import fields, replace

from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    filter_wave_only_pending_snapshots,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    live_wave_isolation_mt5_active,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)

# Všechny moduly zapnuté, isolation vypnutá → full engine
FULL_ALL_POSITIONS = replace(
    LIVE_BOT_CONFIG,
    wave_positions_only=False,
    wave_isolation_study=False,
    wave_position_enabled=True,
    wave_counter_two_sided_enabled=True,
    counter_position_enabled=True,
    two_sided_entry_enabled=True,
    pp_enabled=True,
    bos_entry_enable=True,
    bos_reentry_enabled=True,
    ext_enabled=True,
    ext_secondary_enabled=True,
    ext_counter_enabled=True,
)

POSITION_FLAGS = (
    "wave_position_enabled",
    "wave_positions_only",
    "wave_isolation_study",
    "counter_position_enabled",
    "wave_counter_two_sided_enabled",
    "two_sided_entry_enabled",
    "pp_enabled",
    "bos_entry_enable",
    "bos_reentry_enabled",
    "bos_entry_in_rrr_fixed",
    "ext_enabled",
    "ext_secondary_enabled",
    "ext_counter_enabled",
    "live_mt5_wave_slice_only",
)

ENTRY_KINDS = (
    "WAVE",
    "COUNTER",
    "EXT_COUNTER",
    "TWO_SIDED",
    "PP",
    "BOS",
    "EXT_SECONDARY",
)

MT5_COMMENTS = (
    "W202601011030",
    "CNTR_202601011030@G4",
    "TS2_202601011030",
    "PP_202601011030",
    "BOS_202601011030",
    "E23_202601011030",
    "E2S_202601011030",
    "ECT_202601011030",
    "ECB_202601011030",
)


def plain_wave(**kw) -> dict:
    base = {
        "wave_time": "202601011030",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.35,
    }
    base.update(kw)
    return base


def ext_wave() -> dict:
    return plain_wave(move_pct=0.80, is_ext=True)


def mt5_send(cfg, entry_kind: str) -> bool:
    return not skip_live_non_wave_entry(cfg, entry_kind)


def guard_send(cfg, name: str) -> bool:
    if name == "plain WAVE":
        return not guard_live_send_order(cfg, plain_wave())
    if name == "EXT primary":
        return not guard_live_send_order(cfg, ext_wave())
    if name == "BOS retro":
        return not guard_live_send_order(
            cfg, plain_wave(), bypass_trend_filter=True,
        )
    raise ValueError(name)


def main() -> None:
    raw = FULL_ALL_POSITIONS
    engine = resolve_grid_engine_config(raw)
    live = resolve_live_execution_config(raw)

    print("=" * 80)
    print("FULL MODE: vsechny pozice ON, wave_isolation_study=OFF, wave_positions_only=OFF")
    print("=" * 80)
    print()
    print(f"live mode: {classify_live_execution_mode(live)}")
    print(f"isolation MT5 active: {live_wave_isolation_mt5_active(live)}")
    print()

    # --- Tabulka 1: config flags ---
    print("TABULKA 1 - Config flags (live MT5 vs backtest engine)")
    print("-" * 80)
    print(f"{'Flag':<32} {'Live':<8} {'Engine':<8} {'Shoda':<6}")
    print("-" * 80)
    config_ok = True
    for k in POSITION_FLAGS:
        lv = getattr(live, k, None)
        ev = getattr(engine, k, None)
        match = lv == ev
        if not match:
            config_ok = False
        print(f"{k:<32} {str(lv):<8} {str(ev):<8} {'OK' if match else 'FAIL':<6}")
    print("-" * 80)
    print(f"Config parita celkem: {'ANO' if config_ok else 'NE'}")
    print()

    # --- Tabulka 2: entry kinds ---
    print("TABULKA 2 - MT5 send (skip_live_non_wave_entry): live vs engine")
    print("-" * 80)
    print(f"{'Typ pozice':<18} {'Live MT5':<10} {'Engine*':<10} {'Shoda':<6}")
    print("-" * 80)
    entry_ok = True
    for kind in ENTRY_KINDS:
        ls = mt5_send(live, kind)
        es = mt5_send(engine, kind)
        match = ls == es
        if not match:
            entry_ok = False
        print(
            f"{kind:<18} {'SEND' if ls else 'BLOCK':<10} "
            f"{'SEND' if es else 'BLOCK':<10} {'OK' if match else 'FAIL':<6}"
        )
    print("-" * 80)
    print("* Engine: guards neaktivni -> vse SEND; engine simuluje v backtestu")
    print(f"Entry parita: {'ANO' if entry_ok else 'NE'}")
    print()

    # --- Tabulka 3: guards ---
    print("TABULKA 3 - guard_live_send_order: live vs engine")
    print("-" * 80)
    guards = ("plain WAVE", "EXT primary", "BOS retro")
    guard_ok = True
    print(f"{'Scénář':<18} {'Live MT5':<10} {'Engine':<10} {'Shoda':<6}")
    print("-" * 80)
    for g in guards:
        ls = guard_send(live, g)
        es = guard_send(engine, g)
        match = ls == es
        if not match:
            guard_ok = False
        print(
            f"{g:<18} {'SEND' if ls else 'BLOCK':<10} "
            f"{'SEND' if es else 'BLOCK':<10} {'OK' if match else 'FAIL':<6}"
        )
    print("-" * 80)
    print(f"Guard parita: {'ANO' if guard_ok else 'NE'}")
    print()

    # --- Tabulka 4: pending comments ---
    from infra.pending_snapshot import PendingOrderSnapshot

    snaps = [
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, c, None) for c in MT5_COMMENTS
    ]
    live_snaps = filter_wave_only_pending_snapshots(live, snaps)
    eng_snaps = filter_wave_only_pending_snapshots(engine, snaps)

    print("TABULKA 4 - Pending restore (snapshot filter)")
    print("-" * 80)
    print(f"{'Comment':<22} {'Live':<8} {'Engine':<8} {'Shoda':<6}")
    print("-" * 80)
    snap_ok = True
    for c in MT5_COMMENTS:
        lk = c in {s.comment for s in live_snaps}
        ek = c in {s.comment for s in eng_snaps}
        match = lk == ek
        if not match:
            snap_ok = False
        print(
            f"{c:<22} {'KEEP' if lk else 'DROP':<8} "
            f"{'KEEP' if ek else 'DROP':<8} {'OK' if match else 'FAIL':<6}"
        )
    print("-" * 80)
    print(f"Snapshot parita: {'ANO' if snap_ok else 'NE'}")
    print()

    # --- Souhrn ---
    all_ok = config_ok and entry_ok and guard_ok and snap_ok
    print("=" * 80)
    print("SOUHRN FULL MODE - live MT5 vs backtest engine")
    print("=" * 80)
    print(f"  Config flags:     {'STEJNE' if config_ok else 'ROZDIL'}")
    print(f"  Entry typy:       {'STEJNE' if entry_ok else 'ROZDIL'}")
    print(f"  Send guards:      {'STEJNE' if guard_ok else 'ROZDIL'}")
    print(f"  Pending restore:  {'STEJNE' if snap_ok else 'ROZDIL'}")
    print(f"  CELKEM:           {'PARITA ANO — vsechny pozice stejne' if all_ok else 'PARITA NE'}")
    print()

    # Rozdily mimo position flags (informativne)
    diffs = []
    for f in fields(engine):
        if f.name in POSITION_FLAGS:
            continue
        lv, ev = getattr(live, f.name), getattr(engine, f.name)
        if lv != ev:
            diffs.append((f.name, lv, ev))
    if diffs:
        print("Poznamka: jine fieldy live vs engine (live-only metadata, ne execution):")
        for name, lv, ev in diffs[:15]:
            print(f"  {name}: live={lv!r} engine={ev!r}")
        if len(diffs) > 15:
            print(f"  ... +{len(diffs) - 15} dalsich")


if __name__ == "__main__":
    main()
