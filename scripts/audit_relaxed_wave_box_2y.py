"""FÁZE 3C-b — audit relaxed_wave_box_enabled (profil B) na 2letém okně.

ÚČEL: Změřit a NEROZHODNOUT. Spustí referenční 2letý incremental_causal backtest
(stejný setup jako `backtest/tests/test_incremental_reference_snapshot.py` —
`resolve_grid_engine_config`, `wave_detection_mode=INCREMENTAL_CAUSAL`) dvakrát:

  A) STRICT   — relaxed_wave_box_enabled=False (default, clamp_wave_box_to_bar=True)
  B) profil B — relaxed_wave_box_enabled=True  (clamp_wave_box_to_bar=False)

Vytiskne srovnávací tabulku (trades, net PnL, max DD %, max DD USD, Δ vs A) a ověří,
že retro-BOS blok (`block_retro_before_birth`) funguje identicky v obou profilech
(nebyl obejit zapnutím relaxed_wave_box_enabled).

NEGARANTUJE žádné konkrétní číslo — jen měří. Rozhodnutí o zapnutí je na uživateli;
tento skript NEMĚNÍ `config/bot_config.py` ani `LIVE_BOT_CONFIG`.

Použití:
    python scripts/audit_relaxed_wave_box_2y.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows konzole/redirect casto pouziva cp1250 (bez Δ apod.) — vynutit UTF-8 pro stdout,
# aby print() s Unicode znaky (Δ, —, á…) nespadl na UnicodeEncodeError pri redirectu do souboru.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backtest.causal_policy import causal_debug_summary
from backtest.data_loader import default_backtest_date_range, load_csv
from backtest.engine import BacktestEngine
from backtest.grid.data_cache import clear_cache, csv_path_for, load_data
from backtest.stats import compute_stats, trades_to_df
from backtest.wave_sim_cache import clear_pine_sim_cache
from config.bot_config import LIVE_BOT_CONFIG
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config

RETRO_KEYS = ("causal_retro_blocked_birth_none", "causal_retro_blocked_birth_ge_flip")


def _window() -> tuple[str | None, str | None]:
    df_full = load_csv(csv_path_for(LIVE_BOT_CONFIG.symbol, LIVE_BOT_CONFIG.timeframe_label))
    return default_backtest_date_range(df_full)


def _cfg_for(relaxed: bool, date_from: str | None, date_to: str | None):
    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG, date_from=date_from, date_to=date_to)
    cfg = replace(
        cfg,
        wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL,
        relaxed_wave_box_enabled=relaxed,
    )
    assert cfg.causal_mode is True
    assert cfg.wave_detection_mode == WaveDetectionMode.INCREMENTAL_CAUSAL
    assert cfg.relaxed_wave_box_enabled is relaxed
    return cfg


def _run(label: str, relaxed: bool, date_from: str | None, date_to: str | None) -> dict:
    clear_cache()
    clear_pine_sim_cache()
    cfg = _cfg_for(relaxed, date_from, date_to)
    df = load_data(cfg.symbol, cfg.timeframe_label, date_from, date_to)
    if df.empty:
        raise RuntimeError(f"[{label}] prazdna data pro okno {date_from}..{date_to}")

    eng = BacktestEngine(cfg)
    trades = eng.run(df)
    stats = compute_stats(trades_to_df(trades), track_concurrent=True)
    info = eng.get_run_info()

    policy = eng._causal_policy  # noqa: SLF001 — diagnostika, jen pro audit report
    assert policy.enabled is True, f"[{label}] causal policy neni enabled — spatny setup"
    assert policy.block_retro_before_birth is True, (
        f"[{label}] block_retro_before_birth neni True — hard lock poruseny!"
    )
    assert policy.filter_flip_map_by_birth is True, (
        f"[{label}] filter_flip_map_by_birth neni True — hard lock poruseny!"
    )
    assert policy.clamp_wave_box_to_bar is (not relaxed), (
        f"[{label}] clamp_wave_box_to_bar={policy.clamp_wave_box_to_bar} "
        f"neodpovida relaxed_wave_box_enabled={relaxed}"
    )

    return {
        "label": label,
        "trades": int(stats.get("trades_wave", 0)),
        "net_pnl": float(stats.get("net_pnl_wave_usd", 0.0)),
        "max_dd_pct": float(stats.get("max_drawdown_pct_wave", 0.0)),
        "max_dd_usd": float(stats.get("max_drawdown_usd", 0.0)),
        "causal_debug": causal_debug_summary(policy),
        "retro_blocked_total": sum(int(info.get(k, 0)) for k in RETRO_KEYS),
        "retro_keys_present": {k: int(info.get(k, 0)) for k in RETRO_KEYS},
        "box_clamped": int(info.get("causal_box_clamped", 0)),
    }


def _print_table(a: dict, b: dict) -> None:
    d_pnl = b["net_pnl"] - a["net_pnl"]
    d_dd = b["max_dd_pct"] - a["max_dd_pct"]  # obe cisla jsou zaporna/nulova; pp rozdil
    print("\n=== VÝSLEDKOVÁ TABULKA (2leté okno, incremental_causal) ===")
    header = f"| {'profil':10} | {'trades':7} | {'net PnL':>14} | {'max DD %':>9} | {'Δ PnL vs A':>12} | {'Δ DD pp vs A':>12} |"
    print(header)
    print("-" * len(header))
    print(
        f"| {a['label']:10} | {a['trades']:7} | {a['net_pnl']:14,.2f} | {a['max_dd_pct']:9.2f} | "
        f"{'—':>12} | {'—':>12} |"
    )
    print(
        f"| {b['label']:10} | {b['trades']:7} | {b['net_pnl']:14,.2f} | {b['max_dd_pct']:9.2f} | "
        f"{d_pnl:+12,.2f} | {d_dd:+12.2f} |"
    )
    print(f"\n(max DD USD)  A={a['max_dd_usd']:+,.2f}   B={b['max_dd_usd']:+,.2f}")


def _print_retro_audit(a: dict, b: dict) -> None:
    print("\n=== RETRO-BOS BLOK OVĚŘENÍ (block_retro_before_birth) ===")
    for r in (a, b):
        print(f"  [{r['label']}] causal_debug_summary keys: {r['causal_debug']}")
    for r in (a, b):
        print(
            f"  [{r['label']}] retro_blocked_total={r['retro_blocked_total']}  "
            f"detail={r['retro_keys_present']}  box_clamped={r['box_clamped']}"
        )
    same_mechanism = (a["retro_keys_present"].keys() == b["retro_keys_present"].keys())
    print(
        f"\n  Retro blok existuje/funguje v obou profilech stejnym mechanismem: "
        f"{'ANO' if same_mechanism else 'NE — ZKONTROLUJ!'}"
    )
    print(
        f"  Profil B (relaxed) NEobsazuje causal_box_clamped bumpy (clamp OFF): "
        f"box_clamped={b['box_clamped']} (ocekavano 0)"
    )
    print(
        f"  Profil A (STRICT) POUZIVA clamp (causal_box_clamped>0 ocekavano): "
        f"box_clamped={a['box_clamped']}"
    )


def _print_adoption_rules(a: dict, b: dict) -> None:
    d_pnl = b["net_pnl"] - a["net_pnl"]
    d_dd = b["max_dd_pct"] - a["max_dd_pct"]
    rule1 = d_pnl > 0
    rule2 = b["max_dd_pct"] >= a["max_dd_pct"] - 1.0  # DD je zaporne cislo; "hlubsi" DD = mensi (vic zaporne)
    rule3 = (
        b["retro_keys_present"].keys() == a["retro_keys_present"].keys()
        and b["retro_blocked_total"] >= 0
    )
    print("\n=== PRAVIDLA ADOPCE (jen vyhodnoceni — NEROZHODUJI) ===")
    print(f"  (1) Δ net PnL > 0                              : {'SPLNENO' if rule1 else 'NESPLNENO'}  (Δ={d_pnl:+,.2f})")
    print(
        f"  (2) max DD profilu B <= max DD STRICT + 1.0pp  : {'SPLNENO' if rule2 else 'NESPLNENO'}  "
        f"(A={a['max_dd_pct']:.2f}%, B={b['max_dd_pct']:.2f}%, Δ={d_dd:+.2f}pp)"
    )
    print(f"  (3) retro blok overen (mechanismus stejny)     : {'SPLNENO' if rule3 else 'NESPLNENO'}")
    print(
        "  (4) explicitni schvaleni uzivatele v chatu     : NA TOMTO SKRIPTU NEZAVISI — "
        "cekej na uzivatele."
    )
    print(
        "\n  POZOR: toto je jen vyhodnoceni pravidel, NE rozhodnuti. "
        "LIVE_BOT_CONFIG.relaxed_wave_box_enabled zustava False, dokud to uzivatel "
        "vyslovne neschvali v chatu."
    )


def main() -> None:
    date_from, date_to = _window()
    print(f"Okno (BACKTEST_WINDOW_YEARS): {date_from} .. {date_to}")

    print("\n--- Profil A: STRICT (relaxed_wave_box_enabled=False) ---")
    a = _run("A_STRICT", False, date_from, date_to)
    print(f"  trades={a['trades']}  net_pnl={a['net_pnl']:+,.2f}  max_dd_pct={a['max_dd_pct']:.2f}%")

    print("\n--- Profil B: relaxed_wave_box_enabled=True ---")
    b = _run("B_RELAXED", True, date_from, date_to)
    print(f"  trades={b['trades']}  net_pnl={b['net_pnl']:+,.2f}  max_dd_pct={b['max_dd_pct']:.2f}%")

    _print_table(a, b)
    _print_retro_audit(a, b)
    _print_adoption_rules(a, b)

    print(
        "\nPOZN.: referenční číslo test_incremental_reference_snapshot (STRICT, 2leté okno) = "
        "640 obchodů / +124 461.68 USD / max DD −10.34 %. Pokud se profil A výše výrazně liší, "
        "zkontroluj shodu setupu (resolve_grid_engine_config, INCREMENTAL_CAUSAL, stejné okno)."
    )


if __name__ == "__main__":
    main()
