"""
Hlavni vstupni bod pro backtest.

ZAKLADNI POUZITI
================

  # 1) Otestovat aktualni LIVE config (jak ho ma bot ted nastaveny)
  python -m backtest.run_backtest --profile live_match

  # 2) Pustit grid search (tisice kombinaci)
  python -m backtest.run_backtest --profile grid --grid-profile full_grid
  python -m backtest.run_backtest --profile grid --grid-profile bot_optimalisation
  python -m backtest.run_backtest --profile grid --grid-profile positions_setting

  # Heatmap wave_min_pct × rrr z uloženého grid_report.xlsx
  python scripts/plot_grid_heatmap.py results/EURUSD.x/grid_<profil>_M15_<datum>_<NNN>/grid_report.xlsx -o results/heatmap.png

  Po přenosu složky z jiného PC: z kořene repa spusťte .\\scripts\\setup_venv.ps1 (nový .venv + pip),
  případně viz PowerShell sekce v backtest/grid/backtest_conf.py.

VOLBY
=====

  --csv PATH            CSV soubor pro live_match a compare. Default: data/{symbol}_{tf}.csv
  --date-from DATE      Filtr od data (YYYY-MM-DD); u --profile grid prepise datum u kazde kombinace
  --date-to DATE        Filtr do data (YYYY-MM-DD); u gridu stejne
  --workers N           Pocet paralelnich procesu pro grid (default: cpu_count - 1)
  --sequential          Spustit grid sekvencne (snazsi debugging)
  --print-grid-rankings Vypis TOP/BOTTOM do terminalu (default: vypnuto; vysledky v grid_report.xlsx)
  --top N               Pocet TOP/BOTTOM radku pri --print-grid-rankings (default: 10)
  --output DIR          Koren vystupu (default: results/). live_match:
                        results/{PÁR}/grid_{CONFIG}_{TF}_{od}_{do}_{NNN}/.
                        Grid: results/{PÁR}/grid_{profil}_{TF}_{od}_{do}_{NNN}/.
  --visual-waves        Export struktury vln + obchodu jako Plotly HTML (vizual_waves_plotly_html v profilu jen pro visual_waves_enabled bez CLI).
  --visual-last-n N     Poslednich N vln v orezu (prepise visual_last_n_waves).
  --visual-bars K       Max. pocet baru v orezu (prepise visual_waves_max_bars).
  --visual-full-span    Vynuti cele obdobi + vsechny eligible vlny (i kdyz je v profilu vypnuto).
  --visual-clip         Orez vizualu: poslednich N vln a max. M baru (viz --visual-last-n / --visual-bars / base).
  --visual-html         Alias pro HTML export vln (od --visual-waves uz neni potreba; zachovano pro zpetnou kompatibilitu).
  --plots-html-only     U souhrnneho equity gridu (--plot) nevytvarej PNG, jen Plotly HTML.
  --plot-equity-html    S --plot uloz i Plotly HTML (kumul. PnL v case + periodicke PnL).
  --plot-monthly-kind-html  Měsíční PnL + max DD %% vs initial (ALL / WAVE / PP / BOS) do jednoho Plotly HTML.
  --plot-scroll-combined-html  Jeden HTML (scroll): equity (2) + měsíční druhy (4) + vlny (5), soubor *_equity_monthly_waves_scroll.html.
                          live_match: do results/{PÁR}/. compare: do --output s dennim inkrementem.
                          Grid: do <grid_dir>/plots_scroll_combined/ (stejne --grid-export-top-n).
  --plot-interactive    Grid s --plot: vsechny uspesne kombinace v jednom grafu (vychozi bez nej = TOP 5; stejne soubory jako --plot-all).
  --grid-export-top-n N  Grid: druhy pruchod (--plot-trades, --plot-monthly-kind-html, --plot-scroll-combined-html, --visual-waves)
                          omez na top N podle projected_net_pnl_at_max_risk_usd (vsechny radky v xlsx).
  --grid-export-split    Grid: druhy pruchod/exporty rozdel po obdobich (none/yearly/halfyear), aby velke HTML nebylo pres cele roky najednou.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


import pandas as pd

# Pridame trading_bot root do sys.path, aby fungovaly importy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.io.csv_export import export_csv, set_global_csv_format  # noqa: F401 — ensure_csv_export_defaults() při importu
from backtest.plotting import (
    plot_equity_curve,
    plot_top_n_grid,
    plot_price_with_trades,
    plot_waves_structure,
)
from backtest.visual_waves import (
    append_visual_waves_index,
    build_wave_visual_bundle,
    default_visual_output_path,
    supplement_visual_waves_for_trades,
    visual_enabled_from_combo,
    visual_params_from_combo_and_args,
)
from config.bot_config import BotConfig
from backtest.engine import BacktestEngine
from strategy.wave_sequence import (
    compute_wave_sequence_info_per_wave,
    propagate_seq_info_to_waves,
)
from backtest.sim_params import sim_params_from_grid_combo
from backtest.data_loader import load_csv, filter_by_date_range
from backtest.stats import compute_stats, trades_to_df
from backtest.file_stems import export_path_stem, prefixed_export_stem
from backtest.report import (
    append_grid_plot_trades_index,
    compare_configs,
    print_last_trades,
    print_summary,
    save_trades_csv,
)
from backtest.monthly_kind_html import write_monthly_kind_summary_html
from backtest.profile_resolver import resolve_live_match, resolve_compare
from backtest.grid.data_cache import csv_path_for, get_data_dir
from backtest.output_paths import (
    live_match_output_dir,
    next_daily_output_dir,
    output_symbol_for_config,
    output_symbol_for_configs,
    run_name_compare,
)


# ---------------------------------------------------------------------------
# Helpery
# ---------------------------------------------------------------------------

def _prep_grid_combos_for_paths(
    profile_name: str,
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    """Stejné kombinace jako run_grid (pro symbol/TF ve jménu výstupní složky)."""
    from backtest.grid.backtest_conf import generate_combinations, get_profile, finalize_grid_combo_bot_name

    combos = generate_combinations(get_profile(profile_name))
    if date_from is not None or date_to is not None:
        for c in combos:
            if date_from is not None:
                c["date_from"] = date_from
            if date_to is not None:
                c["date_to"] = date_to
            finalize_grid_combo_bot_name(c)
    return combos

def _load_data_for_config(cfg: BotConfig, csv_path: str | None,
                          date_from: str | None,
                          date_to: str | None) -> pd.DataFrame:
    """Nacte data bud z explicitne zadaneho CSV, nebo z konvence data/{symbol}_{tf}.csv."""
    if csv_path:
        df = load_csv(csv_path)
    else:
        tf_label = cfg.timeframe_label
        path = csv_path_for(cfg.symbol, tf_label)
        if not path.exists():
            raise FileNotFoundError(
                f"CSV nenalezeno: {path}\n"
                f"Bud zadej --csv PATH, nebo nahraj CSV do: {get_data_dir()}/\n"
                f"Pojmenovani: {cfg.symbol}_{tf_label}.csv"
            )
        df = load_csv(path)

    df = filter_by_date_range(df, date_from, date_to)
    if df.empty:
        raise ValueError(
            f"Prazdny DataFrame po filtraci. date_from={date_from} date_to={date_to}"
        )
    return df


def _grid_export_periods(combo: dict, split_mode: str | None) -> list[tuple[str | None, str | None, str | None]]:
    """
    Vrati exportni okna pro detailni grid exporty.

    split_mode="none" zachova puvodni chovani: jeden export pres cele obdobi.
    split_mode="yearly"/"halfyear" rozdeli jen druhy pruchod s HTML/CSV exporty,
    hlavni grid ranking zustava vypocitany pres cele obdobi.
    """
    date_from = combo.get("date_from")
    date_to = combo.get("date_to")
    mode = (split_mode or "none").lower()
    if mode == "none" or not date_from or not date_to:
        return [(None, date_from, date_to)]

    months_by_mode = {
        "yearly": 12,
        "halfyear": 6,
    }
    months = months_by_mode.get(mode)
    if months is None:
        return [(None, date_from, date_to)]

    start = pd.Timestamp(date_from).normalize()
    final_end = pd.Timestamp(date_to).normalize()
    if start > final_end:
        return [(None, date_from, date_to)]

    periods: list[tuple[str | None, str | None, str | None]] = []
    cur = start
    while cur <= final_end:
        next_start = cur + pd.DateOffset(months=months)
        end = min(next_start - pd.Timedelta(days=1), final_end)
        start_s = cur.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        periods.append((f"{start_s}__{end_s}", start_s, end_s))
        cur = end + pd.Timedelta(days=1)

    return periods or [(None, date_from, date_to)]


def _bos_flip_events_in_window(df_win: pd.DataFrame, events) -> list:
    """Ořez BOS flipů na časové okno vizuálu (stejná myšlenka jako u pending_vis)."""
    if df_win is None or df_win.empty or not events:
        return []
    t0 = pd.Timestamp(df_win["time"].iloc[0])
    t1 = pd.Timestamp(df_win["time"].iloc[-1])
    out = []
    for ev in events:
        if not ev or len(ev) < 2:
            continue
        tb = pd.Timestamp(ev[0])
        if t0 <= tb <= t1:
            out.append(tuple(ev))
    return out


def _tp_mode_label_for_export(combo: dict | None, engine: BacktestEngine) -> str | None:
    if combo is not None:
        raw = combo.get("tp_mode")
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    raw = getattr(getattr(engine, "cfg", None), "tp_mode", None)
    if raw is None:
        return None
    return raw.value if hasattr(raw, "value") else str(raw)


def _export_visual_waves(
    *,
    engine: BacktestEngine,
    df: pd.DataFrame,
    trades,
    bot_name: str,
    output_dir,
    combo: dict | None,
    args,
    test_pozice: int | None = None,
) -> None:
    """Po behu engine s retain_wave_snapshot vytvori PNG/HTML struktury vln."""
    cli_visual = bool(args and getattr(args, "visual_waves", False))
    if not visual_enabled_from_combo(combo, cli_visual=cli_visual):
        return
    cli_plotly = (
        True
        if (args and getattr(args, "visual_html", False))
        else None
    )
    last_n, max_bars, guess, use_html, full_span = visual_params_from_combo_and_args(
        combo,
        cli_last_n=getattr(args, "visual_last_n", None) if args else None,
        cli_max_bars=getattr(args, "visual_bars", None) if args else None,
        cli_plotly=cli_plotly,
        cli_visual_waves=cli_visual,
        cli_full_span=bool(args and getattr(args, "visual_full_span", False)),
        cli_visual_clip=bool(args and getattr(args, "visual_clip", False)),
    )
    waves_src = getattr(engine, "last_waves_for_visual", None) or engine.last_waves

    # Cisla vln v grafu MUSI odpovidat tomu, na cem engine REALNE obchodoval
    # (engine.wave_sequence_info). Recompute na finalnich vlnach dava JINA cisla,
    # protoze in_ext_range se po behu re-taguje pro vizual (reapply_ext_range_tags)
    # — to posouva cislovani v HTML oproti enginu. Preferuj engine snapshot;
    # recompute jen jako fallback.
    wave_seq_by_time = getattr(engine, "wave_sequence_info", None)
    if not wave_seq_by_time:
        waves_all = list(getattr(engine, "last_waves", None) or [])
        wave_seq_by_time = compute_wave_sequence_info_per_wave(
            df, waves_all, engine.cfg
        )
        propagate_seq_info_to_waves(waves_all, wave_seq_by_time)
    bundle = build_wave_visual_bundle(
        df,
        list(waves_src or []),
        engine.wave_birth_by_time,
        trades,
        last_n=last_n,
        max_bars=max_bars,
        bars_per_wave_guess=guess,
        pending_vis=getattr(engine, "pending_vis", None),
        full_span=full_span,
        wave_seq_by_time=wave_seq_by_time,
    )
    if bundle is not None and not bundle.df.empty:
        supplement_visual_waves_for_trades(
            bundle,
            last_waves=list(getattr(engine, "last_waves", None) or []),
            all_waves=list(getattr(engine, "_all_waves", None) or []),
            wave_birth=engine.wave_birth_by_time,
            wave_seq_by_time=wave_seq_by_time,
            pending_vis=getattr(engine, "pending_vis", None),
            df_full=df,
        )
    if bundle is None or bundle.df.empty:
        print(f"[visual-waves] {bot_name}: nelze sestavit bundle (prazdna data)")
        return
    tp = test_pozice
    if tp is None and combo:
        v = combo.get("_grid_test_pozice")
        if v is not None:
            try:
                tp = int(v)
            except (TypeError, ValueError):
                tp = None
    bos_pts = _bos_flip_events_in_window(
        bundle.df, getattr(engine, "bos_flip_events", None) or []
    )
    results_dir = Path(output_dir) if output_dir else Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    if not use_html:
        print(
            f"[visual-waves] {bot_name}: preskoceno — export vln je jen HTML; "
            "zapni --visual-waves nebo visual_waves_enabled + visual_waves_plotly_html v grid profilu."
        )
        return
    html_path = default_visual_output_path(
        results_dir,
        bot_name,
        suffix="html",
        test_pozice=tp,
        tp_mode=_tp_mode_label_for_export(combo, engine),
    )
    hh_note = (
        " | pozadí: HH/HL strukturální vlny + EXT modře (vždy)"
        if getattr(engine.cfg, "trend_hh_hl_filter_enabled", False)
        else " | EXT vlny: modré pozadí"
    )
    wf_note = " | WF continuation: černé pozadí"
    cap = (
        f"Úrovně ve vlně: vstup fib {engine.cfg.entry_fib_level:g} (fib50) | "
        f"SL fib {engine.cfg.sl_fib_level:g} | RRR {engine.cfg.rrr:g} — "
        f"obrys vstupu = barva vlny (wave_time); "
        f"○ nový pending, × expirace, ■ prune{hh_note}{wf_note}"
    )
    plot_waves_structure(
        df_window=bundle.df,
        waves=bundle.waves,
        closed_trades=bundle.trades,
        bot_name=bot_name,
        bos_points=bos_pts or None,
        save_path=None,
        interactive_html_path=html_path,
        show=False,
        fib_levels_caption=cap,
        pending_events=bundle.pending_events,
        entry_fib_ratio=float(engine.cfg.entry_fib_level),
        sl_fib_ratio=float(engine.cfg.sl_fib_level),
        cfg=engine.cfg,
        bos_wave_times=set(
            getattr(engine, "_visual_bos_wave_times", None)
            or getattr(engine, "_bos_wave_times", None)
            or set()
        ),
    )
    html_ok = bool(html_path) and Path(html_path).is_file()
    if html_ok:
        append_visual_waves_index(
            results_dir,
            bot_name,
            None,
            Path(html_path) if html_ok else None,
            test_pozice=tp,
        )


def _export_scroll_combined_html(
    *,
    engine: BacktestEngine,
    df: pd.DataFrame,
    trades,
    trades_df: pd.DataFrame,
    cfg: BotConfig,
    output_dir,
    combo_for_visual: dict | None = None,
    args=None,
    test_pozice: int | None = None,
    prop_metrics: dict | None = None,
    prop_metrics_by_broker: dict | None = None,
    preset_names: list[str] | None = None,
) -> None:
    """Jeden Plotly HTML: equity + měsíční druhy + vlny + souhrn BotConfig."""
    if args is None or not getattr(args, "plot_scroll_combined_html", False):
        return
    if trades_df.empty:
        print("[scroll-combined] Přeskočeno — žádné uzavřené obchody v trades_df.")
        return

    from backtest.plotting import (
        equity_scroll_plotly_figure,
        _median_bar_timedelta,
        _wave_color_by_dir,
    )
    from backtest.monthly_kind_html import (
        build_monthly_kind_figure,
        build_monthly_kind_multi_broker_projected_figure,
    )
    from backtest.combined_scroll_html import write_scroll_combined_plotly_html
    from backtest.waves_plotly_figure import build_waves_structure_plotly_figure
    from backtest.prop_firm.report_keys import scale_trades_df_by_headroom
    from backtest.bot_config_summary_html import build_bot_config_summary_html

    pm = prop_metrics or {}
    brokers_pm = prop_metrics_by_broker or {}

    preset_order = [str(p) for p in (preset_names or []) if p in brokers_pm]
    if not preset_order and brokers_pm:
        preset_order = sorted(brokers_pm.keys())

    proj_list = [
        (b, float((brokers_pm.get(b) or {}).get("headroom_scale", 1.0) or 1.0))
        for b in preset_order
    ]

    pm_ftmo: dict = {}
    if brokers_pm and "FTMO" in brokers_pm:
        pm_ftmo = dict(brokers_pm["FTMO"])
    elif pm:
        pm_ftmo = dict(pm)

    preset = str(pm.get("prop_firm_preset", "") or "")
    meta_display_name = (
        "FTMO" if (brokers_pm and "FTMO" in brokers_pm) else (preset or "primární preset")
    )

    ftmo_meta_parts = []
    if pm_ftmo:
        if pm_ftmo.get("headroom_scale") is not None:
            hs = pm_ftmo.get("headroom_scale")
            ftmo_meta_parts.append(
                f"{meta_display_name}: headroom_scale={float(hs):.4g}"
            )
        if pm_ftmo.get("max_risk_per_trade_usd") is not None:
            mr = pm_ftmo.get("max_risk_per_trade_usd")
            ftmo_meta_parts.append(f"max_risk={float(mr):,.0f} USD")
        pj = pm_ftmo.get("projected_net_pnl_at_max_risk_usd")
        if pj is not None:
            ftmo_meta_parts.append(f"projected_net_pnl={float(pj):+,.0f} USD")
    title_supplement = " | ".join(ftmo_meta_parts) if ftmo_meta_parts else None

    headroom_ftmo = float(pm_ftmo.get("headroom_scale", 1.0) or 1.0)
    trades_df_proj_ftmo = scale_trades_df_by_headroom(trades_df, headroom_ftmo)

    broker_bits: list[str] = []
    for bname in preset_order or sorted(brokers_pm.keys()):
        bm = brokers_pm.get(bname, {})
        broker_bits.append(
            f"<b>{bname}</b>: headroom={float(bm.get('headroom_scale', 1) or 1):.4g}, "
            f"max_risk={bm.get('max_risk_per_trade_usd', '—')}, "
            f"projected_pnl={bm.get('projected_net_pnl_at_max_risk_usd', '—')}"
        )
    brokers_line = " | ".join(broker_bits) if broker_bits else (
        f"Prop preset: <b>{preset or '—'}</b> | headroom_scale=<b>{headroom_ftmo:.4g}</b> | "
        f"max_risk=<b>{pm_ftmo.get('max_risk_per_trade_usd', '—')}</b> | "
        f"projected_net_pnl=<b>{pm_ftmo.get('projected_net_pnl_at_max_risk_usd', '—')}</b>."
    )

    results_dir = Path(output_dir) if output_dir else Path("results")
    stem = prefixed_export_stem(export_path_stem(cfg.bot_name), test_pozice)
    out_combo = results_dir / f"{stem}_equity_monthly_waves_scroll.html"

    gran = getattr(args, "plot_granularity", "monthly")
    from backtest.plotting_adx14 import adx14_plot_kwargs_from_engine

    adx14_kw = adx14_plot_kwargs_from_engine(
        engine,
        force_plot=bool(getattr(args, "plot_adx14", False)),
        df=df,
    )
    fig_eq = equity_scroll_plotly_figure(
        trades,
        cfg.bot_name,
        headroom_scale=headroom_ftmo,
        broker_projections=proj_list if proj_list else None,
        initial_balance=100_000.0,
        granularity=gran,
        projected_pnl_usd=pm_ftmo.get("projected_net_pnl_at_max_risk_usd"),
        max_risk_per_trade_usd=pm_ftmo.get("max_risk_per_trade_usd"),
        title_supplement=title_supplement,
        **adx14_kw,
    )
    fig_m_base = build_monthly_kind_figure(
        trades_df,
        symbol=cfg.symbol,
        bot_name=cfg.bot_name,
        initial_balance=100_000.0,
        pnl_variant_label="základní PnL",
    )
    if len(preset_order) > 0:
        broker_scaled = {
            b: scale_trades_df_by_headroom(
                trades_df, float((brokers_pm.get(b) or {}).get("headroom_scale", 1.0) or 1.0)
            )
            for b in preset_order
        }
        fig_m_proj = build_monthly_kind_multi_broker_projected_figure(
            trades_df,
            broker_scaled_trades=broker_scaled,
            broker_order=preset_order,
            symbol=cfg.symbol,
            bot_name=cfg.bot_name,
            initial_balance=100_000.0,
            ftmo_caption=title_supplement or "",
        )
    else:
        fig_m_proj = build_monthly_kind_figure(
            trades_df_proj_ftmo,
            symbol=cfg.symbol,
            bot_name=cfg.bot_name,
            initial_balance=100_000.0,
            pnl_variant_label=f"projected @ max risk ({preset})" if preset else "projected @ max risk",
        )
    fig_waves = None
    lw = getattr(engine, "last_waves", None)
    if lw:
        last_n, max_bars, guess, _use_html, full_span = visual_params_from_combo_and_args(
            combo_for_visual,
            cli_last_n=getattr(args, "visual_last_n", None) if args else None,
            cli_max_bars=getattr(args, "visual_bars", None) if args else None,
            cli_plotly=True,
            cli_full_span=bool(args and getattr(args, "visual_full_span", False)),
            cli_visual_clip=bool(args and getattr(args, "visual_clip", False)),
        )
        waves_src = getattr(engine, "last_waves_for_visual", None) or engine.last_waves

        # Viz komentar vyse: cisla v grafu = engine.wave_sequence_info (na cem
        # engine obchodoval), ne recompute na re-tagovanych vlnach.
        wave_seq_by_time = getattr(engine, "wave_sequence_info", None)
        if not wave_seq_by_time:
            waves_all = list(getattr(engine, "last_waves", None) or [])
            wave_seq_by_time = compute_wave_sequence_info_per_wave(
                df, waves_all, engine.cfg
            )
            propagate_seq_info_to_waves(waves_all, wave_seq_by_time)
        bundle = build_wave_visual_bundle(
            df,
            list(waves_src or []),
            engine.wave_birth_by_time,
            trades,
            last_n=last_n,
            max_bars=max_bars,
            bars_per_wave_guess=guess,
            pending_vis=getattr(engine, "pending_vis", None),
            full_span=full_span,
            wave_seq_by_time=wave_seq_by_time,
        )
        if bundle is not None and not bundle.df.empty:
            supplement_visual_waves_for_trades(
                bundle,
                last_waves=list(getattr(engine, "last_waves", None) or []),
                all_waves=list(getattr(engine, "_all_waves", None) or []),
                wave_birth=engine.wave_birth_by_time,
                wave_seq_by_time=wave_seq_by_time,
                pending_vis=getattr(engine, "pending_vis", None),
                df_full=df,
            )
        if bundle is not None and not bundle.df.empty:
            nb = len(bundle.df)
            bar_td_b = _median_bar_timedelta(bundle.df["time"])
            wcb: dict[str, str] = {}
            for iw, w in enumerate(bundle.waves):
                wtk = w.get("wave_time")
                if wtk is not None:
                    wcb[str(wtk)] = _wave_color_by_dir(w, iw, engine.cfg)
            bos_pts = _bos_flip_events_in_window(
                bundle.df, getattr(engine, "bos_flip_events", None) or []
            )
            hh_note = (
                " | pozadí: HH/HL strukturální vlny + EXT modře (vždy)"
                if getattr(engine.cfg, "trend_hh_hl_filter_enabled", False)
                else " | EXT vlny: modré pozadí"
            )
            wf_note = " | WF continuation: černé pozadí"
            cap = (
                f"Úrovně ve vlně: vstup fib {engine.cfg.entry_fib_level:g} (fib50) | "
                f"SL fib {engine.cfg.sl_fib_level:g} | RRR {engine.cfg.rrr:g} — "
                f"obrys vstupu = barva vlny (wave_time); "
                f"○ nový pending, × expirace, ■ prune{hh_note}{wf_note}"
            )
            fig_waves = build_waves_structure_plotly_figure(
                df=bundle.df,
                n=nb,
                bar_td=bar_td_b,
                waves=bundle.waves,
                closed_trades=bundle.trades,
                bot_name=cfg.bot_name,
                wave_color_by_time=wcb,
                pending_list=bundle.pending_events or [],
                bos_points=bos_pts or None,
                fib_levels_caption=cap,
                entry_fib_ratio=float(engine.cfg.entry_fib_level),
                sl_fib_ratio=float(engine.cfg.sl_fib_level),
                cfg=engine.cfg,
                bos_wave_times=set(
            getattr(engine, "_visual_bos_wave_times", None)
            or getattr(engine, "_bos_wave_times", None)
            or set()
        ),
            )

    intro = (
        "Svislý přehled — pořadí: "
        "(2) kumulativní PnL: zeleně základní, projected <b>každá prop-firma jinou barvou</b>; "
        "(4a) měsíční PnL/DD základní; "
        "(4b) měsíční PnL/DD — projected zvlášť pro každého brokera (stejná paleta jako (2)); "
        "(5) struktura vln; (6) souhrn BotConfig. "
        "Číselný doplněk pod nadpisem grafu (2) a text mimo tyto multi-grafy: "
        "<b>FTMO</b> (pokud je v gridu), jinak primární preset. "
        f"{brokers_line}"
    )
    write_scroll_combined_plotly_html(
        out_combo,
        page_title=f"{cfg.bot_name} | Equity + měsíční + vlny + nastavení",
        intro_html=intro,
        sections=[
            ("2) Kumulativní PnL (základní + projected @ max risk)", fig_eq),
            ("4a) Měsíční PnL + max_dd_%_vs_initial — základní PnL", fig_m_base),
            (
                "4b) Měsíční PnL + max_dd_%_vs_initial — projected @ max_risk_per_trade_usd",
                fig_m_proj,
            ),
            ("5) Struktura vln + obchody", fig_waves),
        ],
        footer_html=(
            (
                lambda _wf_html: (
                    _wf_html + "<br/>" if _wf_html else ""
                ) + build_bot_config_summary_html(cfg, brokers=brokers_pm or None)
            )(
                __import__(
                    "backtest.report", fromlist=["wf_origin_breakdown_html"]
                ).wf_origin_breakdown_html(trades_df)
            )
        ),
    )


def _apply_backtest_runtime_flags(cfg: BotConfig, args) -> BotConfig:
    """CLI prepisuje BotConfig causal_mode / run_e2e_parity."""
    if args is None:
        return cfg
    if getattr(args, "causal", False):
        cfg.causal_mode = True
    if getattr(args, "e2e", False):
        cfg.run_e2e_parity = True
    return cfg


def _run_single_config(
    cfg: BotConfig,
    df: pd.DataFrame,
    output_dir: str,
    args=None,
    combo_for_visual: dict | None = None,
    *,
    test_pozice: int | None = None,
) -> dict:
    """Spusti backtest pro jeden config, vytiskne summary, ulozi trades CSV."""
    cfg = _apply_backtest_runtime_flags(cfg, args)
    if cfg.causal_mode:
        print("=== CAUSAL MODE: backtest bez look-ahead (parita live) ===")
    print(f"\n>>> Spoustim backtest: {cfg.bot_name} ({cfg.symbol} {cfg.timeframe_label})")
    # ──────────────────────────────────────────────────────────────────────
    # WICK FAKEOUT RECOVERY (WF) — sekce config printu backtesteru
    # ──────────────────────────────────────────────────────────────────────
    # Co to dělá:
    #   WF řeší situaci, kdy po dokončení vlny ve směru trendu přijde
    #   protisměrový pohyb, který NENÍ validní BOS (jen wick nad/pod
    #   extrémem last wave, žádný close na druhé straně). Pak se trh
    #   vrátí ve směru trendu a udělá close za opačným extrémem last wave.
    #   Engine by tuto situaci jinak nechal bez definice — WF v tomto
    #   momentě vytvoří NOVOU continuation vlnu od fakeout pivotu.
    # ──────────────────────────────────────────────────────────────────────
    _wf_on = bool(getattr(cfg, "wf_enabled", False))
    print(
        "=== Wick Fakeout Recovery (WF) ===\n"
        f"WF_ENABLED: {_wf_on}\n"
        "(Aktivuje se POUZE když v okně po last wave byl wick přes opačný extrém\n"
        " bez close-BOS a aktuální close je za opačným extrémem last wave.\n"
        " Výjimka: WF se neaktivuje, pokud je trh v EXT.)\n"
    )
    spr, slip, track_conc = sim_params_from_grid_combo(combo_for_visual)
    retain_v = visual_enabled_from_combo(
        combo_for_visual,
        cli_visual=bool(args and getattr(args, "visual_waves", False)),
    ) or bool(args and getattr(args, "plot_scroll_combined_html", False))
    _combo = combo_for_visual or {}
    cap_mode, cap_limit = ("off", None)
    if _combo:
        from backtest.grid.translator import grid_backtest_position_cap_settings

        cap_mode, cap_limit = grid_backtest_position_cap_settings(_combo)
    engine = BacktestEngine(
        cfg,
        backtest_position_cap_mode=cap_mode,
        backtest_max_open_positions=cap_limit,
        backtest_spread=spr,
        backtest_slippage=slip,
    )
    trades = engine.run(df, retain_wave_snapshot=retain_v)
    trades_df = trades_to_df(trades)
    from backtest.grid.study_mode import filter_trades_df_for_grid_stats

    trades_df_stats = (
        filter_trades_df_for_grid_stats(trades_df, _combo)
        if _combo
        else trades_df
    )
    stats = compute_stats(
        trades_df_stats,
        track_concurrent=track_conc,
        date_from=_combo.get("date_from"),
        date_to=_combo.get("date_to"),
    )
    if "error" not in stats and _combo and not trades_df_stats.empty:
        from backtest.metrics.robustness import compute_robustness_metrics

        stats.update(
            compute_robustness_metrics(
                trades_df_stats,
                max_dd_pct_vs_peak=stats.get("max_drawdown_pct_vs_peak"),
                max_dd_pct_vs_initial=stats.get("max_drawdown_pct"),
                bot_name=cfg.bot_name,
            )
        )
    stats.update(engine.get_run_info())
    if _combo:
        from backtest.grid.study_mode import apply_wave_isolation_report_stats

        stats = apply_wave_isolation_report_stats(stats, _combo)
        stats["config"] = dict(_combo)
    print_summary(cfg.bot_name, stats, trades_df)

    # WF breakdown (jen pokud jsou WF obchody)
    if not trades_df.empty and "wave_origin" in trades_df.columns:
        if "wf_continuation" in trades_df["wave_origin"].values:
            from backtest.report import wf_origin_breakdown_html as _wf_brkdn
            _wf_n = int((trades_df["wave_origin"] == "wf_continuation").sum())
            _wf_tot = len(trades_df)
            print(
                f"  WF obchody: {_wf_n} / {_wf_tot} "
                f"({_wf_n / _wf_tot * 100:.1f}%) — breakdown viz trades CSV (wave_origin sloupec)"
            )

    if not trades_df.empty:
        print_last_trades(trades_df, n=10)
        save_trades_csv(trades_df, output_dir, cfg.bot_name, test_pozice=test_pozice)

        # Plot equity curve, pokud byl --plot flag
        if args is not None and getattr(args, "plot", False):
            results_dir = Path(output_dir) if output_dir else Path("results")
            save_path_eq = results_dir / f"{cfg.bot_name}_equity.png"
            html_eq = (
                results_dir / f"{cfg.bot_name}_equity.html"
                if getattr(args, "plot_equity_html", False)
                else None
            )
            if getattr(args, "plots_html_only", False) and html_eq:
                save_path_eq = None
            from backtest.plotting_adx14 import adx14_plot_kwargs_from_engine

            plot_equity_curve(
                closed_trades=trades,
                bot_name=cfg.bot_name,
                initial_balance=10000.0,
                granularity=getattr(args, "plot_granularity", "monthly"),
                save_path=save_path_eq,
                interactive_html_path=html_eq,
                show=getattr(args, "plot_show", False),
                **adx14_plot_kwargs_from_engine(
                    engine,
                    force_plot=bool(getattr(args, "plot_adx14", False)),
                    df=df,
                ),
            )

        # Graf ceny + obchodu jen jako Plotly HTML (--plot-trades-html); PNG se negeneruje.
        if args is not None and getattr(args, "plot_trades", False) and getattr(
            args, "plot_trades_html", False
        ):
            results_dir = Path(output_dir) if output_dir else Path("results")
            stem = prefixed_export_stem(export_path_stem(cfg.bot_name), test_pozice)
            html_p = results_dir / f"{stem}_price_trades.html"
            plot_price_with_trades(
                price_df=df,
                closed_trades=trades,
                bot_name=cfg.bot_name,
                save_path=None,
                interactive_html_path=html_p,
                show=getattr(args, "plot_show", False),
            )

        if args is not None and getattr(args, "plot_monthly_kind_html", False):
            results_dir = Path(output_dir) if output_dir else Path("results")
            stem = prefixed_export_stem(export_path_stem(cfg.bot_name), test_pozice)
            html_m = results_dir / f"{stem}_monthly_pnl_dd_by_kind.html"
            write_monthly_kind_summary_html(
                trades_df,
                symbol=cfg.symbol,
                bot_name=cfg.bot_name,
                out_path=html_m,
                initial_balance=100_000.0,
            )

    _export_visual_waves(
        engine=engine,
        df=df,
        trades=trades,
        bot_name=cfg.bot_name,
        output_dir=output_dir,
        combo=combo_for_visual,
        args=args,
        test_pozice=test_pozice,
    )

    _export_scroll_combined_html(
        engine=engine,
        df=df,
        trades=trades,
        trades_df=trades_df,
        cfg=cfg,
        output_dir=output_dir,
        combo_for_visual=combo_for_visual,
        args=args,
        test_pozice=test_pozice,
    )

    if _combo.get("_grid_test_pozice") is not None:
        from backtest.grid.grid_report_io import write_live_match_grid_report

        write_live_match_grid_report(stats, output_dir, args=args)

    if bool(getattr(cfg, "run_e2e_parity", False)):
        from backtest.causal_gate_e2e import (
            print_causal_gate_e2e_report,
            run_e2e_parity_after_backtest,
        )

        e2e_result = run_e2e_parity_after_backtest(
            df, cfg, backtest_trades=trades, backtest_stats=stats,
        )
        print_causal_gate_e2e_report(e2e_result)
        stats["e2e_parity"] = e2e_result.parity

    return stats


# ---------------------------------------------------------------------------
# Single mode (live_match) a Compare mode
# ---------------------------------------------------------------------------

def run_single_mode(args) -> None:
    """Profile = live_match: spusti jeden config."""
    from backtest.profile_resolver import resolve_live_match_pair

    config_name = args.config or "LIVE_BOT_CONFIG"
    cfg, combo = resolve_live_match_pair(
        config_name,
        date_from=args.date_from,
        date_to=args.date_to,
        combo_no=1,
    )
    output_dir = live_match_output_dir(
        args.output,
        output_symbol_for_config(cfg),
        config_name=config_name,
        timeframe_label=cfg.timeframe_label,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    print(f"\nVystupni adresar: {output_dir}\n")
    df = _load_data_for_config(cfg, args.csv, args.date_from, args.date_to)
    print(f"Nacteno {len(df)} baru | {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    _run_single_config(
        cfg,
        df,
        str(output_dir),
        args=args,
        combo_for_visual=combo,
    )


def run_compare_mode(args) -> None:
    """Profile = compare: spusti vice configu vedle sebe."""
    if not args.configs:
        print("CHYBA: --profile compare vyzaduje --configs NAZEV1,NAZEV2,...")
        sys.exit(1)
    names = [n.strip() for n in args.configs.split(",") if n.strip()]
    configs = resolve_compare(names)

    output_dir = next_daily_output_dir(
        args.output,
        output_symbol_for_configs(configs),
        run_name_compare(configs),
    )
    print(f"\nVystupni adresar: {output_dir}\n")

    all_stats = {}
    for cfg in configs:
        df = _load_data_for_config(cfg, args.csv, args.date_from, args.date_to)
        stats = _run_single_config(
            cfg, df, str(output_dir), args=args, combo_for_visual=None, test_pozice=None
        )
        all_stats[cfg.bot_name] = stats

    print("\n" + "=" * 80)
    print("  POROVNANI KONFIGURACI")
    print("=" * 80)
    comparison = compare_configs(all_stats)
    if not comparison.empty:
        print(comparison.to_string())
        comp_path = output_dir / "comparison.csv"
        export_csv(comparison.reset_index(), str(comp_path), index=False)
        print(f"\nPorovnani ulozeno: {comp_path}\n")


# ---------------------------------------------------------------------------
# Grid mode
# ---------------------------------------------------------------------------

def run_grid_mode(args) -> None:
    """Profile = grid: spusti grid search nad PROFILES."""
    from backtest.grid.grid_runner import run_grid
    from backtest.grid.aggregator import (
        collect_errors,
        print_bottom_n,
        print_top_n,
        print_top_n_by_timeframe,
    )
    from backtest.io.excel_export import GRID_REPORT_XLSX
    from backtest.grid.translator import grid_dict_to_bot_config, grid_backtest_position_cap_settings
    from backtest.grid.data_cache import load_data

    from backtest.grid.grid_report_io import write_grid_progress_workbook

    results, grid_output_dir = run_grid(
        profile_name=args.grid_profile,
        max_workers=args.workers,
        sequential=args.sequential,
        date_from=args.date_from,
        date_to=args.date_to,
        base_output=args.output,
        checkpoint_args=args,
    )

    total_combos = len(
        _prep_grid_combos_for_paths(args.grid_profile, args.date_from, args.date_to)
    )

    from backtest.grid.backtest_conf import get_profile, resolve_grid_prop_firms
    from backtest.prop_firm.compliance import print_prop_firm_summary

    profile = get_profile(args.grid_profile)
    pf_opts = resolve_grid_prop_firms(profile, args)
    preset_names = pf_opts["preset_names"]

    # Finální zápis xlsx po dokončení celého gridu.
    df_report, df_long, primary_prop_preset = write_grid_progress_workbook(
        results,
        grid_output_dir,
        args.grid_profile,
        args,
        done=total_combos,
        total=total_combos,
        final=True,
        quiet=True,
    )

    if preset_names and not df_long.empty:
        if getattr(args, "print_grid_rankings", False):
            print_prop_firm_summary(df_long, top_n=5)
        if pf_opts["generate_html"]:
            from backtest.prop_firm.html_report import write_prop_firm_html

            write_prop_firm_html(
                df_long,
                grid_output_dir / "prop_firm_compliance.html",
                preset_names,
            )
            print(f"Prop-firm HTML: {grid_output_dir / 'prop_firm_compliance.html'}")

    df_errors = collect_errors(results)
    if not df_errors.empty:
        from backtest.io.excel_export import GRID_SHEET_CHYBY

        print(
            f"VAROVANI: {len(df_errors)} kombinaci selhalo — list "
            f"'{GRID_SHEET_CHYBY}' v {(grid_output_dir / GRID_REPORT_XLSX).name}"
        )

    if getattr(args, "print_grid_rankings", False):
        _top_sort = (
            f"{primary_prop_preset}__projected_net_pnl_at_max_risk_usd"
            if primary_prop_preset
            and f"{primary_prop_preset}__projected_net_pnl_at_max_risk_usd" in df_report.columns
            else "net_pnl_usd"
        )
        print_top_n(df_report, n=args.top, sort_by=_top_sort)
        print_bottom_n(df_report, n=args.top, sort_by=_top_sort)
        print_top_n_by_timeframe(df_report, n=5, sort_by=_top_sort)
    elif df_report.empty and not df_errors.empty:
        print(
            f"Zadne uspesne kombinace — viz list 'chyby' v "
            f"{grid_output_dir / GRID_REPORT_XLSX}"
        )

    # Plot equity — jeden souhrny graf (PNG + Plotly HTML); pocet krivek podle --plot-all / --plot-top-n / --plot-interactive / default TOP 5.
    # Bez per-kombo equity exportu.
    if getattr(args, "plot", False) and results:
        plots_html_only = getattr(args, "plots_html_only", False)
        plot_all = getattr(args, "plot_all", False)
        plot_top_n = getattr(args, "plot_top_n", None)
        plot_ix = getattr(args, "plot_interactive", False)

        if plot_all:
            save_path = grid_output_dir / "grid_all_equity.png"
            html_path = grid_output_dir / "grid_all_equity_interactive.html"
            n_arg = None
        elif plot_top_n is not None and int(plot_top_n) > 0:
            n_int = int(plot_top_n)
            save_path = grid_output_dir / f"grid_top{n_int}_equity.png"
            html_path = grid_output_dir / f"grid_top{n_int}_equity_interactive.html"
            n_arg = n_int
        elif plot_ix:
            save_path = grid_output_dir / "grid_all_equity.png"
            html_path = grid_output_dir / "grid_all_equity_interactive.html"
            n_arg = None
        else:
            save_path = grid_output_dir / "grid_top5_equity.png"
            html_path = grid_output_dir / "grid_top5_equity_interactive.html"
            n_arg = 5

        eq_png = None if plots_html_only else save_path
        eq_html = html_path
        plot_top_n_grid(
            grid_results=results,
            n=n_arg,
            initial_balance=10000.0,
            save_path=eq_png,
            interactive_html_path=eq_html,
            show=False,
            preferred_bot_order=(
                df_report["bot_name"].tolist() if not df_report.empty else None
            ),
            primary_prop_preset=(primary_prop_preset or None),
            df_prop_long=(df_long if not df_long.empty else None),
            df_report=(df_report if not df_report.empty else None),
            force_plot_adx14=bool(getattr(args, "plot_adx14", False)),
        )

    # Volitelny detailni export pro kombinace (stejny vyrez podle --grid-export-top-n):
    # - --plot-trades: CSV (+ volitelne cenu v HTML --plot-trades-html)
    # - --plot-monthly-kind-html: mesicni PnL + max DD podle druhu (Plotly HTML; muze samostatne bez --plot-trades)
    # - --plot-scroll-combined-html: jeden HTML scroll (equity + mesicni + vlny) do plots_scroll_combined/
    _want_trades = getattr(args, "plot_trades", False)
    _want_monthly = getattr(args, "plot_monthly_kind_html", False)
    _want_scroll = getattr(args, "plot_scroll_combined_html", False)
    if results and (_want_trades or _want_monthly or _want_scroll):
        valid = [
            (name, s)
            for name, s in results.items()
            if "error" not in s and s.get("config") is not None
        ]
        _top_n = getattr(args, "grid_export_top_n", None)
        if _top_n is not None and _top_n > 0 and not df_report.empty:
            from backtest.prop_firm.report_keys import projected_pnl_wide_column

            _pcol = (
                projected_pnl_wide_column(primary_prop_preset)
                if primary_prop_preset
                else "net_pnl_usd"
            )
            if _pcol not in df_report.columns:
                _pcol = "net_pnl_usd"
            _top_df = df_report.sort_values(_pcol, ascending=False, na_position="last")
            _allowed = set(_top_df.head(int(_top_n))["bot_name"])
            valid = [(n, s) for n, s in valid if n in _allowed]

        trades_dir = grid_output_dir / "trades"
        plots_dir = grid_output_dir / "plots_price_trades"
        monthly_dir = grid_output_dir / "plots_monthly_kind"
        scroll_dir = grid_output_dir / "plots_scroll_combined"
        trades_dir.mkdir(parents=True, exist_ok=True)
        plots_dir.mkdir(parents=True, exist_ok=True)
        monthly_dir.mkdir(parents=True, exist_ok=True)
        if _want_scroll:
            scroll_dir.mkdir(parents=True, exist_ok=True)

        _parts: list[str] = []
        if _want_trades and getattr(args, "plot_trades_html", False):
            _parts.append("trades CSV + cena v HTML")
        elif _want_trades:
            _parts.append("jen trades CSV")
        if _want_monthly:
            _parts.append("mesicni PnL/DD podle druhu (HTML)")
        if _want_scroll:
            _parts.append("kombinovany scroll HTML (equity + mesicni + vlny)")
        if _parts:
            print(f"\n[grid] Druhy pruchod ({'; '.join(_parts)}) pro {len(valid)} kombinaci...")
        for idx, (name, s) in enumerate(valid, 1):
            combo = s["config"]
            try:
                cfg = grid_dict_to_bot_config(combo)
                cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
                spr, slip, _ = sim_params_from_grid_combo(combo)

                tp = combo.get("_grid_test_pozice")
                try:
                    tp = int(tp) if tp is not None else None
                except (TypeError, ValueError):
                    tp = None

                stem = prefixed_export_stem(export_path_stem(cfg.bot_name), tp)

                periods = _grid_export_periods(combo, getattr(args, "grid_export_split", "none"))
                if len(periods) > 1:
                    print(
                        f"[grid] [{idx}/{len(valid)}] {cfg.bot_name}: "
                        f"detailni export rozdelen na {len(periods)} obdobi"
                    )

                for period_idx, (period_label, period_from, period_to) in enumerate(periods, 1):
                    period_suffix = (
                        f" ({period_idx}/{len(periods)} {period_from} az {period_to})"
                        if period_label
                        else ""
                    )
                    export_combo = dict(combo)
                    export_combo["date_from"] = period_from
                    export_combo["date_to"] = period_to

                    period_trades_dir = trades_dir / period_label if period_label else trades_dir
                    period_plots_dir = plots_dir / period_label if period_label else plots_dir
                    period_monthly_dir = monthly_dir / period_label if period_label else monthly_dir
                    period_scroll_dir = scroll_dir / period_label if period_label else scroll_dir

                    if _want_trades:
                        period_trades_dir.mkdir(parents=True, exist_ok=True)
                    if _want_trades and getattr(args, "plot_trades_html", False):
                        period_plots_dir.mkdir(parents=True, exist_ok=True)
                    if _want_monthly:
                        period_monthly_dir.mkdir(parents=True, exist_ok=True)
                    if _want_scroll:
                        period_scroll_dir.mkdir(parents=True, exist_ok=True)

                    df = load_data(
                        symbol=combo["symbol"],
                        timeframe_label=combo["timeframe"],
                        date_from=period_from,
                        date_to=period_to,
                    )
                    engine = BacktestEngine(
                        cfg,
                        backtest_position_cap_mode=cap_mode,
                        backtest_max_open_positions=cap_limit,
                        backtest_spread=spr,
                        backtest_slippage=slip,
                    )
                    trades = engine.run(df, retain_wave_snapshot=bool(_want_scroll))
                    trades_df = trades_to_df(trades)

                    csv_path = None
                    if _want_trades:
                        csv_path = save_trades_csv(
                            trades_df, str(period_trades_dir), cfg.bot_name, test_pozice=tp
                        )
                    price_html_path = (
                        period_plots_dir / f"{stem}_price_trades.html"
                        if _want_trades and getattr(args, "plot_trades_html", False)
                        else None
                    )
                    if price_html_path is not None:
                        plot_price_with_trades(
                            price_df=df,
                            closed_trades=trades,
                            bot_name=cfg.bot_name,
                            save_path=None,
                            interactive_html_path=price_html_path,
                            show=False,
                        )
                    if _want_monthly:
                        write_monthly_kind_summary_html(
                            trades_df,
                            symbol=cfg.symbol,
                            bot_name=cfg.bot_name,
                            out_path=period_monthly_dir / f"{stem}_monthly_pnl_dd_by_kind.html",
                            initial_balance=100_000.0,
                        )
                    if _want_scroll:
                        from backtest.prop_firm.report_keys import (
                            lookup_all_prop_metrics,
                            lookup_prop_metrics,
                        )

                        _brokers_pm = (
                            lookup_all_prop_metrics(df_long, cfg.bot_name, preset_names)
                            if preset_names and not df_long.empty
                            else {}
                        )
                        _pm = (
                            _brokers_pm.get(primary_prop_preset)
                            or lookup_prop_metrics(df_long, cfg.bot_name, primary_prop_preset)
                            if primary_prop_preset and not df_long.empty
                            else {}
                        )
                        _export_scroll_combined_html(
                            engine=engine,
                            df=df,
                            trades=trades,
                            trades_df=trades_df,
                            cfg=cfg,
                            output_dir=str(period_scroll_dir),
                            combo_for_visual=export_combo,
                            args=args,
                            test_pozice=tp,
                            prop_metrics=_pm,
                            prop_metrics_by_broker=_brokers_pm,
                            preset_names=preset_names,
                        )
                    if _want_trades and csv_path is not None:
                        trades_ref = (
                            str(Path(period_label) / Path(csv_path).name)
                            if period_label
                            else Path(csv_path).name
                        )
                        price_html_ref = (
                            str(Path(period_label) / price_html_path.name)
                            if period_label and price_html_path and price_html_path.is_file()
                            else (
                                price_html_path.name
                                if price_html_path and price_html_path.is_file()
                                else ""
                            )
                        )
                        append_grid_plot_trades_index(
                            grid_output_dir,
                            cfg.bot_name,
                            trades_ref,
                            "",
                            test_pozice=tp,
                            price_html_basename=price_html_ref,
                        )
                    if period_label:
                        print(f"[grid] [{idx}/{len(valid)}] OK {cfg.bot_name}{period_suffix}")

                print(f"[grid] [{idx}/{len(valid)}] OK {cfg.bot_name}")
            except Exception as e:
                print(f"[grid] [{idx}/{len(valid)}] CHYBA {name}: {e}")

    # Struktura vln (poslednich N) + obchody — podle base visual_waves_* nebo --visual-waves
    force_visual = getattr(args, "visual_waves", False)
    if results:
        v_jobs = [
            (name, s)
            for name, s in results.items()
            if "error" not in s
            and s.get("config") is not None
            and visual_enabled_from_combo(s["config"], cli_visual=force_visual)
        ]
        _vt_n = getattr(args, "grid_export_top_n", None)
        if _vt_n is not None and _vt_n > 0 and not df_report.empty:
            _allowed_v = set(df_report.head(int(_vt_n))["bot_name"])
            v_jobs = [(n, s) for n, s in v_jobs if n in _allowed_v]
        if v_jobs:
            plots_vdir = grid_output_dir / "plots_visual_waves"
            plots_vdir.mkdir(parents=True, exist_ok=True)
            print(
                f"\n[grid] Vizualizace vln (Plotly HTML) pro {len(v_jobs)} kombinaci..."
            )
            for idx, (name, s) in enumerate(v_jobs, 1):
                combo = s["config"]
                try:
                    cfg = grid_dict_to_bot_config(combo)
                    cap_mode, cap_limit = grid_backtest_position_cap_settings(combo)
                    spr, slip, _ = sim_params_from_grid_combo(combo)
                    periods = _grid_export_periods(combo, getattr(args, "grid_export_split", "none"))
                    for period_idx, (period_label, period_from, period_to) in enumerate(periods, 1):
                        period_suffix = (
                            f" ({period_idx}/{len(periods)} {period_from} az {period_to})"
                            if period_label
                            else ""
                        )
                        period_vdir = plots_vdir / period_label if period_label else plots_vdir
                        period_vdir.mkdir(parents=True, exist_ok=True)
                        export_combo = dict(combo)
                        export_combo["date_from"] = period_from
                        export_combo["date_to"] = period_to

                        df_v = load_data(
                            symbol=combo["symbol"],
                            timeframe_label=combo["timeframe"],
                            date_from=period_from,
                            date_to=period_to,
                        )
                        eng_v = BacktestEngine(
                            cfg,
                            backtest_position_cap_mode=cap_mode,
                            backtest_max_open_positions=cap_limit,
                            backtest_spread=spr,
                            backtest_slippage=slip,
                        )
                        tr_v = eng_v.run(df_v, retain_wave_snapshot=True)
                        _export_visual_waves(
                            engine=eng_v,
                            df=df_v,
                            trades=tr_v,
                            bot_name=cfg.bot_name,
                            output_dir=period_vdir,
                            combo=export_combo,
                            args=args,
                        )
                        if period_label:
                            print(f"[grid] [visual {idx}/{len(v_jobs)}] OK {cfg.bot_name}{period_suffix}")
                    print(f"[grid] [visual {idx}/{len(v_jobs)}] OK {cfg.bot_name}")
                except Exception as e:
                    print(f"[grid] [visual {idx}/{len(v_jobs)}] CHYBA {name}: {e}")

    print(f"\nVystupni adresar: {grid_output_dir}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtester pro trading_bot - jednotny vstup pro live_match, compare a grid"
    )
    parser.add_argument("--profile", type=str, default="live_match",
                        choices=["live_match", "compare", "grid"],
                        help="Rezim backtestu (default: live_match)")
    parser.add_argument("--config", type=str, default=None,
                        help="Jmeno configu z CONFIG_REGISTRY (jen pro live_match)")
    parser.add_argument("--configs", type=str, default=None,
                        help="Carkou oddeleny seznam configu (jen pro compare)")
    parser.add_argument("--grid-profile", type=str, default="full_grid",
                        help="Jmeno grid profilu z PROFILES (jen pro grid)")
    parser.add_argument("--csv", type=str, default=None,
                        help="Cesta k CSV. Default: data/{symbol}_{tf}.csv")
    parser.add_argument(
        "--csv-format",
        type=str,
        default="cz",
        choices=["cz", "international"],
        help="Volitelně přepíše výchozí český CSV export (; + desetinná čárka, BOM). "
        "Bez tohoto flagu se při každém generování .csv použije český formát automaticky.",
    )
    parser.add_argument("--date-from", type=str, default=None,
                        help="Filtr od (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=str, default=None,
                        help="Filtr do (YYYY-MM-DD)")
    parser.add_argument(
        "--causal",
        action="store_true",
        help="Zapne causal_mode — backtest bez look-ahead (parita live)",
    )
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="Po backtestu spusti E2E parity (live replay + fake MT5)",
    )
    parser.add_argument("--workers", type=int, default=None,
                        help="Pocet workeru pro grid (default: cpu_count - 1)")
    parser.add_argument("--sequential", action="store_true",
                        help="Spustit grid sekvencne (debugging)")
    parser.add_argument(
        "--print-grid-rankings",
        action="store_true",
        help="Grid: vypis TOP/BOTTOM a prop-firm prehled do terminalu (default vypnuto; data v grid_report.xlsx).",
    )
    parser.add_argument(
        "--verbose-grid-report",
        action="store_true",
        help="Grid: zachovano pro kompatibilitu; standardne se xlsx zapisuje jen finalne po dokonceni gridu.",
    )
    parser.add_argument("--top", type=int, default=10,
                        help="Pocet TOP/BOTTOM radku pri --print-grid-rankings (default: 10)")
    parser.add_argument("--output", type=str, default="results",
                        help="Vystupni adresar (default: results/)")
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Vykresli equity curve a periodic PnL bars (PNG do results/). Grid: souhrny Plotly HTML (equity v case) vzdy spolu s grafem.",
    )
    parser.add_argument(
        "--plot-equity-html",
        action="store_true",
        help="S --plot uloz i Plotly HTML (kumul. PnL v case + periodicke PnL; vyzaduje plotly).",
    )
    parser.add_argument(
        "--plot-show",
        action="store_true",
        help="Otevre interaktivni matplotlib okno (jen pro single mode).",
    )
    parser.add_argument(
        "--plot-all",
        action="store_true",
        help="Vykresli VSECHNY kombinace v gridu (default: jen TOP 5). Pozor: pri >100 kombinaci je graf necitelny a re-run trva dlouho.",
    )
    parser.add_argument(
        "--plot-interactive",
        action="store_true",
        help="Grid: stejny rozsah jako --plot-all (vsechny uspesne kombinace v jednom grafu), pokud neni --plot-top-n / --plot-all. "
        "HTML se uklada i bez tohoto flagu (--plot staci); tento flag prepina rozsah z vychoziho TOP 5 na vsechny.",
    )
    parser.add_argument(
        "--plot-trades",
        action="store_true",
        help="Export trades CSV; graf ceny+obchodu jen s --plot-trades-html (Plotly, bez PNG).",
    )
    parser.add_argument(
        "--plot-trades-html",
        action="store_true",
        help="K --plot-trades uloz Plotly HTML mapy ceny + obchodu (vyzaduje plotly; PNG se negeneruje).",
    )
    parser.add_argument(
        "--plot-monthly-kind-html",
        action="store_true",
        help="Plotly HTML: mesicni PnL a max DD %% vs initial pro ALL + WAVE + PP + BOS (vyzaduje plotly; "
        "stejna initial_balance jako compute_stats: 100000 USD). "
        "Grid: soubory do <grid_output>/plots_monthly_kind/ (lze bez --plot-trades).",
    )
    parser.add_argument(
        "--plot-scroll-combined-html",
        action="store_true",
        help="live_match / compare / grid: jeden Plotly HTML se tremi sekcemi pod sebou (vertikalni scroll): "
        "(2) kumulativni PnL + periodicke PnL jako u --plot-equity-html; "
        "(4) mesicni PnL + max_dd_pct_vs_initial podle ALL/WAVE/PP/BOS; "
        "(5) struktura vln + obchody (stejna logika jako --visual-waves + --visual-html). "
        "Soubor: <stem>_equity_monthly_waves_scroll.html v --output (grid: <grid_dir>/plots_scroll_combined/). "
        "Zapina drzeni snapshotu vln v engine (jako pri --visual-waves); sekce 5 jen pokud existuji vlny.",
    )
    parser.add_argument(
        "--plot-top-n",
        type=int,
        default=None,
        metavar="N",
        help="Grid s --plot: pocet nejlepsich kombinaci v jednom souhrnem equity grafu (vychozi 5; --plot-all ma prioritu).",
    )
    parser.add_argument(
        "--visual-waves",
        action="store_true",
        help="Export struktury vln jako Plotly HTML (PNG vln se negeneruje). "
        "Ve gridu lze misto CLI zapnout visual_waves_enabled v profilu.",
    )
    parser.add_argument(
        "--visual-last-n",
        type=int,
        default=None,
        help="Pocet poslednich vln v orezu (prepis base visual_last_n_waves).",
    )
    parser.add_argument(
        "--visual-bars",
        type=int,
        default=None,
        help="Max. delka orezu v barech (prepis base visual_waves_max_bars).",
    )
    parser.add_argument(
        "--visual-html",
        action="store_true",
        help="Alias: uloz strukturu vln jako Plotly HTML (od --visual-waves uz neni potreba).",
    )
    parser.add_argument(
        "--visual-full-span",
        action="store_true",
        help="Vynuti cele obdobi + vsechny eligible vlny i kdyz je v profilu visual_waves_full_span: False.",
    )
    parser.add_argument(
        "--visual-clip",
        action="store_true",
        help="Orez vizualu vln (poslednich N vln, max. M baru) podle --visual-last-n / --visual-bars nebo base profilu. "
        "Bez tohoto flagu je vychozi cele testovane obdobi (visual_waves_full_span).",
    )
    parser.add_argument(
        "--plots-html-only",
        action="store_true",
        help="Grid/single: u grafu equity (souhrny), vln a ceny+pozic nevytvarej PNG — jen HTML kde je zapnuto.",
    )
    parser.add_argument(
        "--grid-export-top-n",
        type=int,
        default=None,
        metavar="N",
        help="Grid: u --plot-trades, --plot-monthly-kind-html, --plot-scroll-combined-html a/nebo --visual-waves omez druhy pruchod jen na top N "
        "kombinaci podle projected_net_pnl_at_max_risk_usd (primarni preset; None = vsechny uspesne).",
    )
    parser.add_argument(
        "--grid-export-split",
        type=str,
        default="none",
        choices=["none", "yearly", "halfyear"],
        help="Grid: rozdeli jen druhy pruchod/detailni exporty top kombinaci do období. "
        "none = puvodni chovani cele obdobi najednou; yearly = rocni bloky; halfyear = pulrocni bloky.",
    )
    parser.add_argument(
        "--prop-firms",
        type=str,
        default=None,
        help="Prop-firma presety pro grid_report (carkou oddelene; all / none). "
        "Vychozi z profilu: backtest/grid/backtest_conf.py → prop_firms. CLI ma prioritu.",
    )
    parser.add_argument(
        "--prop-firm-config",
        type=str,
        default=None,
        help="JSON vlastnich presetu. Vychozi z profilu prop_firms.config_path; CLI ma prioritu.",
    )
    parser.add_argument(
        "--prop-firm-html",
        action="store_true",
        help="Grid: prop_firm_compliance.html. Zapne take profil prop_firms.generate_html.",
    )
    parser.add_argument(
        "--account-size-override",
        type=float,
        default=None,
        help="Prepise account_size u presetu. Vychozi z profilu prop_firms.account_size_override.",
    )
    parser.add_argument(
        "--plot-adx14",
        action="store_true",
        help="Do equity HTML (--plot / --plot-equity-html / grid TOP N) vygeneruje report PnL + ADX14 zmena "
        "(horni PnL, dolni panel jen ADX14 z adx14_change_indicator.py). "
        "Vyzaduje runtime/adx14_normalizer.json (fit: strategy/adx14_change_indicator.py --fit).",
    )
    args = parser.parse_args()
    set_global_csv_format(args.csv_format)

    if args.profile == "live_match":
        run_single_mode(args)
    elif args.profile == "compare":
        run_compare_mode(args)
    elif args.profile == "grid":
        run_grid_mode(args)


if __name__ == "__main__":
    main()
