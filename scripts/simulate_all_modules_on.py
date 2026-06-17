"""Simulace: PP/BOS/EXT sec/EXT primary/BOS retro povolene v raw configu."""
from __future__ import annotations

from dataclasses import replace

from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    live_wave_isolation_mt5_active,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)

ALL_ON = replace(
    LIVE_BOT_CONFIG,
    pp_enabled=True,
    bos_entry_enable=True,
    bos_reentry_enabled=True,
    ext_secondary_enabled=True,
    ext_enabled=True,
    ext_counter_enabled=True,
)

FULL_MODE = replace(
    ALL_ON,
    wave_isolation_study=False,
    wave_positions_only=False,
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


def print_scenario(label: str, cfg) -> None:
    mode = classify_live_execution_mode(cfg) if label != "RAW (ALL ON, combo2)" else "raw"
    iso = live_wave_isolation_mt5_active(cfg)
    if label == "RAW (ALL ON, combo2)":
        mode = "raw"
        iso = bool(cfg.wave_positions_only and cfg.wave_isolation_study)

    print()
    print(f"--- {label} ---")
    print(
        f"  mode={mode}  isolation_mt5_active={iso}  "
        f"slice_flag={getattr(cfg, 'live_mt5_wave_slice_only', False)}"
    )
    print(
        f"  config: pp={cfg.pp_enabled} bos={cfg.bos_entry_enable} "
        f"ext_sec={cfg.ext_secondary_enabled} ext={cfg.ext_enabled} "
        f"counter={cfg.counter_position_enabled}"
    )

    entries = [
        ("WAVE", "WAVE"),
        ("COUNTER", "COUNTER"),
        ("EXT_COUNTER", "EXT_COUNTER"),
        ("PP", "PP"),
        ("BOS", "BOS"),
        ("EXT_SECONDARY", "EXT_SECONDARY"),
    ]
    print("  skip_live_non_wave_entry (MT5 SEND = projde):")
    for name, kind in entries:
        send = not skip_live_non_wave_entry(cfg, kind)
        print(f"    {'MT5 SEND' if send else 'BLOCKED':9} {name}")

    guards = [
        ("plain WAVE", guard_live_send_order(cfg, plain_wave())),
        ("EXT primary wave", guard_live_send_order(cfg, ext_wave())),
        ("BOS retro", guard_live_send_order(
            cfg, plain_wave(), bypass_trend_filter=True,
        )),
    ]
    print("  guard_live_send_order (MT5 SEND = projde):")
    for name, blocked in guards:
        print(f"    {'MT5 SEND' if not blocked else 'BLOCKED':9} {name}")


def main() -> None:
    print("=" * 72)
    print("SIMULACE: PP / BOS / EXT sec / EXT primary / BOS retro = ON v raw configu")
    print("=" * 72)

    print_scenario("RAW (ALL ON, combo2)", ALL_ON)
    print_scenario("BACKTEST ENGINE", resolve_grid_engine_config(ALL_ON))
    print_scenario("LIVE MT5 combo2", resolve_live_execution_config(ALL_ON))
    print_scenario("FULL MODE (bez isolation)", resolve_live_execution_config(FULL_MODE))

    live = resolve_live_execution_config(ALL_ON)
    print()
    print("--- MT5 pending allowlist (combo2 live, i kdyz raw ma PP/BOS ON) ---")
    for c in (
        "W202601011030",
        "PP_202601011030",
        "BOS_202601011030",
        "E23_202601011030",
        "ECT_202601011030",
    ):
        ok = is_isolation_study_allowed_mt5_comment(c)
        print(f"  {'KEEP' if ok else 'DROP':4} {c}")


if __name__ == "__main__":
    main()
