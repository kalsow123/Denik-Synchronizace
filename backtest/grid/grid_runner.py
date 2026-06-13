"""
Spousteni grid search - paralelni i sekvencni rezim.

Paralelni: ProcessPoolExecutor (default, rychly na vicejadrovem CPU).
Sekvencni: pomalejsi, ale snadnejsi debugging.

Pouziti:
    from backtest.grid.grid_runner import run_grid
    results, out_dir = run_grid(profile_name="full_grid", max_workers=6,
                              date_from="2024-01-01", date_to="2024-06-30")
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, List

from backtest.engine import BacktestEngine
from backtest.grid.backtest_conf import (
    generate_combinations,
    get_profile,
    finalize_grid_combo_bot_name,
)
from backtest.grid.data_cache import load_data
from backtest.grid.translator import grid_dict_to_bot_config, grid_backtest_position_cap_settings
from backtest.sim_params import sim_params_from_grid_combo
from backtest.metrics.robustness import compute_robustness_metrics
from backtest.stats import compute_stats, trades_to_df

_SHARED_BUNDLE = None
_SHARED_BUNDLE_INFO: dict | None = None
_WORKER_PROP_FIRM_OPTS: dict | None = None


def _grid_pool_initializer(
    bundle_info: dict | None,
    prop_firm_opts: dict | None = None,
) -> None:
    from backtest.grid.shared_data import init_grid_worker

    global _WORKER_PROP_FIRM_OPTS
    init_grid_worker(bundle_info)
    _WORKER_PROP_FIRM_OPTS = prop_firm_opts


def _worker_prop_firm_opts() -> dict | None:
    return _WORKER_PROP_FIRM_OPTS


def _apply_prop_firm_in_worker(bot_name: str, stats: dict) -> None:
    opts = _worker_prop_firm_opts()
    if not opts:
        stats.pop("_prop_trades", None)
        return
    preset_names = opts.get("preset_names") or []
    if not preset_names:
        stats.pop("_prop_trades", None)
        return
    from backtest.prop_firm.compliance import attach_prop_firm_to_stats

    attach_prop_firm_to_stats(
        bot_name,
        stats,
        preset_names,
        custom_config_path=opts.get("config_path"),
        account_size_override=opts.get("account_size_usd"),
    )


def _prepare_shared_grid_data(combos: list[dict]) -> tuple[dict | None, Any | None]:
    """Nacte a sdili OHLC pro grid, kde vsechny kombinace maji stejne (symbol, TF, datum)."""
    if not combos:
        return None, None
    sample = combos[0]
    symbol = sample.get("symbol")
    timeframe = sample.get("timeframe")
    date_from = sample.get("date_from")
    date_to = sample.get("date_to")
    if not symbol or not timeframe:
        return None, None
    for c in combos[1:]:
        if (
            c.get("symbol") != symbol
            or c.get("timeframe") != timeframe
            or c.get("date_from") != date_from
            or c.get("date_to") != date_to
        ):
            return None, None
    from backtest.grid.shared_data import try_create_shared_bundle

    df = load_data(symbol, timeframe, date_from, date_to)
    bundle = try_create_shared_bundle(
        df,
        symbol=str(symbol),
        timeframe_label=str(timeframe),
        date_from=date_from,
        date_to=date_to,
    )
    if bundle is None:
        return None, None
    return bundle.export_info(), bundle


def run_single(combo: dict) -> tuple:
    """
    Spusti backtest pro jednu kombinaci.
    Vraci tuple (bot_name, stats_dict).

    Tato funkce musi byt na top-level kvuli pickling pri multiprocessing.
    """
    bot_name = combo.get("bot_name", "UNKNOWN")
    try:
        cfg = grid_dict_to_bot_config(combo)
        cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
        spr, slip, track_conc = sim_params_from_grid_combo(combo)
        df = load_data(
            symbol=combo["symbol"],
            timeframe_label=combo["timeframe"],
            date_from=combo.get("date_from"),
            date_to=combo.get("date_to"),
        )
        engine = BacktestEngine(
            cfg,
            backtest_position_cap_mode=cap_mode,
            backtest_max_open_positions=cap_limit,
            backtest_spread=spr,
            backtest_slippage=slip,
        )
        trades = engine.run(df)
        df_t = trades_to_df(trades)
        
        from config.position_modes import grid_backtest_isolation_study
        if grid_backtest_isolation_study(combo):
            if "position_kind" in df_t.columns:
                df_t = df_t[df_t["position_kind"] == "WAVE"].copy()
                
        stats = compute_stats(
            df_t,
            track_concurrent=track_conc,
            date_from=combo.get("date_from"),
            date_to=combo.get("date_to"),
        )
        if "error" not in stats:
            stats.update(
                compute_robustness_metrics(
                    df_t,
                    max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
                    max_dd_pct_vs_initial=stats.get("max_drawdown_pct"),
                    bot_name=bot_name,
                )
            )
            if not df_t.empty and _worker_prop_firm_opts() is not None:
                _cols = ["entry_time", "close_time", "entry_price", "sl", "lot", "pnl_usd"]
                _sub = df_t[_cols].copy()
                stats["_prop_trades"] = _sub.to_dict(orient="records")
        stats.update(engine.get_run_info())
        _apply_prop_firm_in_worker(bot_name, stats)
    except Exception as e:
        import traceback
        stats = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
    # Kopie: worker nesmi sdilet referenci s hlavnim procesem (pickle ji stejne oddeli).
    stats["config"] = dict(combo)
    if stats["config"].get("bot_name") != bot_name:
        stats["config"]["bot_name"] = bot_name
    return bot_name, stats


def _store_result(results: dict, name: str, stats: dict) -> str:
    """
    Bezpecne ulozi vysledek i pri kolizi bot_name.
    Pri kolizi vytvori stabilni suffix __dupN, aby se nic neprepsalo.
    """
    if name not in results:
        results[name] = stats
        return name
    i = 2
    while f"{name}__dup{i}" in results:
        i += 1
    unique_name = f"{name}__dup{i}"
    if isinstance(stats, dict):
        cfg = stats.get("config")
        if isinstance(cfg, dict):
            cfg["bot_name_original"] = cfg.get("bot_name", name)
            cfg["bot_name"] = unique_name
    results[unique_name] = stats
    return unique_name


def _maybe_checkpoint_workbook(
    results: dict,
    done: int,
    total: int,
    *,
    output_dir: Path | str | None,
    profile_name: str | None,
    checkpoint_args: Any | None,
    checkpoint_every: int,
) -> None:
    if output_dir is None or profile_name is None or checkpoint_every <= 0:
        return
    if done <= 0:
        return
    if done % checkpoint_every != 0 and done != total:
        return
    from backtest.grid.grid_report_io import write_grid_progress_workbook

    quiet = True
    if checkpoint_args is not None:
        quiet = not getattr(checkpoint_args, "verbose_grid_report", False)

    write_grid_progress_workbook(
        results,
        output_dir,
        profile_name,
        checkpoint_args,
        done=done,
        total=total,
        final=(done == total),
        quiet=quiet,
    )


def run_grid(profile_name: str = "full_grid",
             max_workers: int | None = None,
             sequential: bool = False,
             quiet: bool = False,
             date_from: str | None = None,
             date_to: str | None = None,
             *,
             output_dir: str | Path | None = None,
             base_output: str | Path = "results",
             checkpoint_every: int | None = None,
             checkpoint_args: Any | None = None) -> tuple[dict, Path]:
    """
    Hlavni grid runner.

    Parametry:
        profile_name: jmeno profilu z PROFILES (full_grid, best_candidates, ...)
        max_workers: pocet paralelnich procesu (default: cpu_count - 1)
        sequential: pokud True, spustit sekvencne (snazsi debugging)
        quiet: pokud True, neprintovat per-config progress
        date_from / date_to: prepisuji hodnoty v kazde kombinaci (stejne jako u single backtestu).
        output_dir: vystupni slozka behu; kdyz None, vytvori se automaticky pod base_output.
        base_output: koren vystupu (default results) — results/{SYMBOL}/grid_{profil}_{TF}_{date_from}_{date_to}_{NNN}/.
        checkpoint_every: default 0 = bez prubezneho zapisu, jen finalni workbook na konci.
                          Kladna hodnota zapne checkpointy po N kombinacich.
        checkpoint_args: volitelne CLI args (prop firm); bez nich se pouzije profil z backtest_conf.

    Vraci (results, output_dir).
    """
    from backtest.grid.backtest_conf import resolve_grid_prop_firms
    from backtest.grid.grid_report_io import GRID_CHECKPOINT_EVERY, init_grid_report_workbook
    from backtest.output_paths import grid_run_output_dir

    if checkpoint_every is None:
        checkpoint_every = 0

    profile = get_profile(profile_name)
    pf_resolved = resolve_grid_prop_firms(profile, checkpoint_args)
    global _WORKER_PROP_FIRM_OPTS
    if pf_resolved.get("preset_names"):
        _WORKER_PROP_FIRM_OPTS = {
            "preset_names": pf_resolved["preset_names"],
            "config_path": pf_resolved.get("config_path"),
            "account_size_usd": pf_resolved.get("account_size_usd"),
        }
    else:
        _WORKER_PROP_FIRM_OPTS = None
    combos = generate_combinations(profile)
    if date_from is not None or date_to is not None:
        for c in combos:
            if date_from is not None:
                c["date_from"] = date_from
            if date_to is not None:
                c["date_to"] = date_to
            finalize_grid_combo_bot_name(c)
    # Poradi jako v generatoru — propaguje se do stats["config"] a do vystupnich souboru.
    for i, c in enumerate(combos, 1):
        c["_grid_test_pozice"] = i
    total = len(combos)

    if output_dir is None:
        output_dir = grid_run_output_dir(base_output, profile_name, combos)
        print(f"\nVystupni adresar: {output_dir}\n", flush=True)

    output_dir = Path(output_dir)
    if checkpoint_every > 0:
        init_grid_report_workbook(output_dir, quiet=True)

    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 2) - 1)

    print(f"\nGrid profile: '{profile_name}'  (pid={os.getpid()})", flush=True)
    print(f"Kombinaci celkem: {total}", flush=True)
    print(
        f"Workers: {max_workers if not sequential else 1} ({'sekvencne' if sequential else 'paralelne'})\n",
        flush=True,
    )

    global _SHARED_BUNDLE, _SHARED_BUNDLE_INFO
    _SHARED_BUNDLE = None
    _SHARED_BUNDLE_INFO = None
    if not sequential and max_workers > 1:
        _SHARED_BUNDLE_INFO, _SHARED_BUNDLE = _prepare_shared_grid_data(combos)
        if _SHARED_BUNDLE is not None:
            print(
                "Sdilena OHLC cache: zapnuto (symbol/TF/obdobi shodne pro vsechny kombinace).",
                flush=True,
            )

    results = {}

    if sequential or max_workers == 1:
        for done, combo in enumerate(combos, 1):
            name, stats = run_single(combo)
            name = _store_result(results, name, stats)
            if not quiet:
                _print_progress(done, total, name, stats)
            _maybe_checkpoint_workbook(
                results,
                done,
                total,
                output_dir=output_dir,
                profile_name=profile_name,
                checkpoint_args=checkpoint_args,
                checkpoint_every=checkpoint_every,
            )
    else:
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_grid_pool_initializer,
            initargs=(_SHARED_BUNDLE_INFO, _WORKER_PROP_FIRM_OPTS),
        ) as executor:
            futures = {executor.submit(run_single, c): c for c in combos}
            done = 0
            for future in as_completed(futures):
                name, stats = future.result()
                name = _store_result(results, name, stats)
                done += 1
                if not quiet:
                    _print_progress(done, total, name, stats)
                _maybe_checkpoint_workbook(
                    results,
                    done,
                    total,
                    output_dir=output_dir,
                    profile_name=profile_name,
                    checkpoint_args=checkpoint_args,
                    checkpoint_every=checkpoint_every,
                )

    if _SHARED_BUNDLE is not None:
        _SHARED_BUNDLE.close()
        _SHARED_BUNDLE = None
        _SHARED_BUNDLE_INFO = None

    if checkpoint_every > 0 and total > 0 and total % checkpoint_every != 0:
        _maybe_checkpoint_workbook(
            results,
            total,
            total,
            output_dir=output_dir,
            profile_name=profile_name,
            checkpoint_args=checkpoint_args,
            checkpoint_every=checkpoint_every,
        )

    print(f"\nHotovo. Vysledku: {len(results)}  (pid={os.getpid()})\n", flush=True)
    return results, output_dir


def _sess_short(sessions) -> str:
    """Krátká label pro session ve výpisu."""
    if sessions is None:
        return "NOFILT"
    short = {
        "ASIA": "ASIA",
        "LONDON": "LON",
        "USA": "USA",
        "OVERLAP_LON_USA": "OVL",
    }
    if isinstance(sessions, (list, tuple)):
        return "+".join(short.get(s, s) for s in sessions)
    return str(sessions)


def _fmt_prog_cell(val, width: int) -> str:
    """Retezec pro progress radek; None (napr. wave_max_pct bez limitu) nesmi jit do f'{None:<w}'."""
    s = "-" if val is None else str(val)
    return (s + " " * width)[:width]


def _print_progress(done: int, total: int, name: str, stats: dict) -> None:
    """Vytiskne jednoradkovy progress."""
    if "error" in stats:
        print(
            f"  [{done:>5}/{total}] pid={os.getpid()} {name:<60} CHYBA: {stats['error'][:60]}",
            flush=True,
        )
        return

    cfg = stats.get("config", {})
    tf = cfg.get("timeframe", "?")
    wmp = cfg.get("wave_min_pct", "?")
    opp = cfg.get("min_opp_bars", "?")
    rrr = cfg.get("rrr", "?")
    fib = cfg.get("fib_level", "?")
    mode_short = {"market_fallback": "mkt", "limit_fallback": "lmt", "no_fallback": "nof"}
    mode = mode_short.get(cfg.get("entry_mode", ""), "?")
    exp = cfg.get("order_expiry_days", "?")
    mxw = cfg.get("wave_max_pct", "?")
    sess = _sess_short(cfg.get("wave_allowed_sessions"))
    pcap_raw = str(cfg.get("backtest_position_cap_mode", "off")).lower()
    pcap_s = {
        "off": "off",
        "market_close": "mcl",
        "pending_prune": "ppr",
    }.get(pcap_raw, pcap_raw[:3])
    mp_raw = cfg.get("backtest_max_open_positions")
    mp_s = "-" if mp_raw is None else str(mp_raw)
    cc_hit = int(stats.get("position_cap_market_closed", 0) or 0)
    cp_hit = int(stats.get("position_cap_pending_pruned", 0) or 0)
    cap_act = ""
    if pcap_s in ("mcl", "ppr"):
        cap_act = f" capHit:{cc_hit}/{cp_hit}"

    tr = stats.get("total_trades", "?")
    wr = stats.get("win_rate_pct", "?")
    pnl = stats.get("net_pnl_usd", "?")
    pf = stats.get("profit_factor", "?")
    dd = stats.get("max_drawdown_pct_vs_peak", stats.get("max_drawdown_pct", "?"))
    ddi = stats.get("max_drawdown_pct", "?")
    ddd = stats.get("max_daily_dd_pct", "?")
    sh = stats.get("sharpe_ratio", "?")
    w_ok = stats.get("waves_accepted", 0)
    w_max = stats.get("waves_skipped_wave_max_pct", 0)

    # Concurrent positions (jen pokud zapnuto)
    pos_str = ""
    if "max_concurrent" in stats:
        mc = stats["max_concurrent"]
        mcc = stats["max_concurrent_count"]
        smc = stats["second_max_concurrent"]
        smcc = stats["second_max_concurrent_count"]
        pos_str = f"  Pos:{mc}({mcc}x)/{smc}({smcc}x)"

    name_disp = name if len(name) <= 120 else (name[:117] + "...")
    tag = f"[{done:>5}/{total}] pid={os.getpid()}"
    if cfg.get("bot_name") != name:
        name_disp = f"{name_disp}  (! cfg.bot_name={cfg.get('bot_name')})"

    print(
        f"  {tag} "
        f"TF:{_fmt_prog_cell(tf, 4)} w:{wmp} o:{opp} r:{rrr} f:{_fmt_prog_cell(fib, 6)} {mode:<4} "
        f"exp:{_fmt_prog_cell(exp, 3)} mxw:{_fmt_prog_cell(mxw, 4)} "
        f"cap:{pcap_s:<3} mp:{mp_s:<3}{cap_act} sess:{sess:<10}| "
        f"tr:{tr:>5}  WR:{wr:>5}%  PnL:{pnl:>10}  PF:{pf:>5}  "
        f"DD:{dd:>7}%  DDi:{ddi:>7}%  DDD:{ddd:>7}%  Sh:{sh:>5}  "
        f"waves_ok:{w_ok}  maxW_skip:{w_max}"
        f"{pos_str}  |  {name_disp}",
        flush=True,
    )