"""
Orez dat pro vizualni backtest vln (bez drzeni milionu drawable objektu v RAM).

Vychozi chovani: poslednich N vln + orez OHLC na max. M baru od konce (viz
visual_last_n_waves, visual_waves_max_bars — CLI nebo DEFAULT_* v tomto modulu).

Pro orez (poslednich N vln, max. M baru) pouzij CLI --visual-clip.
Cele obdobi je vychozi; --visual-full-span vynuti plne okno.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from backtest.file_stems import visual_waves_export_stem
from backtest.io.csv_export import append_csv_row

from backtest.engine import ClosedTrade

# Vychozi hodnoty orezu (profil base je nepouziva; jen CLI / tyto konstanty)
DEFAULT_VISUAL_LAST_N_WAVES = 40
DEFAULT_VISUAL_WAVES_MAX_BARS = 500
DEFAULT_VISUAL_BARS_PER_WAVE_GUESS = 48


@dataclass
class WaveVisualBundle:
    df: pd.DataFrame
    waves: List[dict]
    trades: List[ClosedTrade]
    window_start_bar: int
    window_end_bar: int
    pending_events: List[dict]


def build_wave_visual_bundle(
    df: pd.DataFrame,
    waves: List[dict],
    wave_birth: dict[str, int],
    closed_trades: List[ClosedTrade],
    *,
    last_n: int = DEFAULT_VISUAL_LAST_N_WAVES,
    max_bars: int = DEFAULT_VISUAL_WAVES_MAX_BARS,
    bars_per_wave_guess: int = DEFAULT_VISUAL_BARS_PER_WAVE_GUESS,
    pending_vis: Sequence[dict] | None = None,
    full_span: bool = False,
    wave_seq_by_time: Optional[Dict[str, Any]] = None,
) -> Optional[WaveVisualBundle]:
    """
    Vytvori orezany DataFrame a seznam vln s indexy relativnimi k oknu (+ birth bar v okne).

    full_span=True: vsechny eligible vlny, casove okno od nejlevejsiho okraje vln po konec
    dat (ignoruje max_bars — pozor na delku serie a velikost HTML/PNG).
    """
    if df is None or df.empty:
        return None

    df_full = df.reset_index(drop=True)
    n = len(df_full)
    end_idx = n - 1

    eligible: List[dict] = []
    for w in waves:
        if not isinstance(w, dict):
            continue
        if w.get("wave_time") not in wave_birth:
            continue
        dr = w.get("draw_right")
        dl = w.get("draw_left")
        if dr is None or dl is None:
            continue
        eligible.append(w)

    eligible.sort(key=lambda x: int(x["draw_right"]), reverse=True)

    if full_span and eligible:
        selected = eligible
        need_left = min(int(w["draw_left"]) for w in selected)
        births = [
            int(wave_birth[w["wave_time"]])
            for w in selected
            if w.get("wave_time") in wave_birth
        ]
        if births:
            need_left = min(need_left, min(births))
        start_idx = max(0, need_left)
        end_idx = n - 1
    else:
        ln = max(1, last_n)
        selected = eligible[:ln]

        target_len = min(n, max(int(max_bars), last_n * int(bars_per_wave_guess)))
        start_idx = max(0, end_idx - target_len + 1)

        if selected:
            need_left = min(int(w["draw_left"]) for w in selected)
            births = [
                int(wave_birth[w["wave_time"]])
                for w in selected
                if w.get("wave_time") in wave_birth
            ]
            if births:
                need_left = min(need_left, min(births))
            start_idx = min(start_idx, max(0, need_left))

        # Tvrdy strop na max_bars (priorita: pravý okraj = konec dat).
        if end_idx - start_idx + 1 > max_bars:
            start_idx = max(0, end_idx - max_bars + 1)

    df_win = df_full.iloc[start_idx : end_idx + 1].copy().reset_index(drop=True)

    waves_win: List[dict] = []
    for w in selected:
        dl = int(w["draw_left"])
        dr = int(w["draw_right"])
        if dr < start_idx or dl > end_idx:
            continue
        wt = w["wave_time"]
        birth_global = wave_birth.get(wt)
        idx_trend = 0
        propagated = w.get("index_in_trend")
        if propagated is not None:
            idx_trend = int(propagated)
        elif wave_seq_by_time is not None:
            info = wave_seq_by_time.get(str(wt))
            if info is not None:
                idx_trend = int(getattr(info, "index_in_trend", 0) or 0)
        waves_win.append(
            {
                "dir": w.get("dir"),
                "wave_time": wt,
                "index_in_trend": idx_trend,
                "fib50": w.get("fib50"),
                "sl": w.get("sl"),
                "tp": w.get("tp"),
                "wave_target_tp_price": w.get("wave_target_tp_price"),
                "fib_abort": w.get("fib_abort"),
                "box_top": float(w.get("box_top", w.get("fib50", 0))),
                "box_bottom": float(w.get("box_bottom", w.get("fib50", 0))),
                "move_pct": w.get("move_pct"),
                "is_ext": bool(w.get("is_ext", False)),
                "in_ext_range": bool(w.get("in_ext_range", False)),
                "is_two_sided_counter": bool(
                    w.get("is_two_sided_counter")
                    or w.get("_two_sided_counter")
                    or w.get("two_sided_show")
                ),
                "_two_sided_counter": bool(
                    w.get("_two_sided_counter") or w.get("two_sided_show")
                ),
                "two_sided_show": bool(w.get("two_sided_show")),
                "wave_origin": w.get("wave_origin"),
                "hh_hl_pass": w.get("hh_hl_pass"),
                "post_ext_trend_suppressed": bool(
                    w.get("post_ext_trend_suppressed", False)
                ),
                "post_ext_confirmed_trend_lock": bool(
                    w.get("post_ext_confirmed_trend_lock", False)
                ),
                "wf_continued_classic": bool(w.get("wf_continued_classic", False)),
                "draw_left_win": dl - start_idx,
                "draw_right_win": dr - start_idx,
                "birth_win": (
                    None
                    if birth_global is None
                    else int(birth_global) - start_idx
                ),
            }
        )

    tw = df_win["time"].iloc[0]
    tw_end = df_win["time"].iloc[-1]
    t0 = pd.Timestamp(tw)
    t1 = pd.Timestamp(tw_end)

    trades_win: List[ClosedTrade] = []
    for t in closed_trades:
        et = pd.Timestamp(t.entry_time)
        ct = pd.Timestamp(t.close_time)
        if ct >= t0 and et <= t1:
            trades_win.append(t)

    pending_win: List[dict] = []
    if pending_vis:
        for ev in pending_vis:
            try:
                bi = int(ev.get("bar", -1))
            except (TypeError, ValueError):
                continue
            if start_idx <= bi <= end_idx:
                pending_win.append({**ev, "bar_win": bi - start_idx})

    return WaveVisualBundle(
        df=df_win,
        waves=waves_win,
        trades=trades_win,
        window_start_bar=start_idx,
        window_end_bar=end_idx,
        pending_events=pending_win,
    )


def _order_type_to_dir(order_type: str) -> int:
    ot = str(order_type or "").upper()
    if ot.startswith("BUY"):
        return 1
    if ot.startswith("SELL"):
        return -1
    return 0


def _wave_dict_for_visual_window(
    w: dict,
    *,
    start_idx: int,
    end_idx: int,
    wave_birth: dict,
    wave_seq_by_time: Optional[Dict[str, Any]] = None,
    extra_flags: Optional[dict] = None,
) -> Optional[dict]:
    """Zkopíruje vlnu do souřadnic vizuálního okna bundle."""
    dl = int(w["draw_left"])
    dr = int(w["draw_right"])
    if dr < start_idx or dl > end_idx:
        return None
    wt = w["wave_time"]
    birth_global = wave_birth.get(wt)
    if birth_global is None and w.get("_visual_reconstructed"):
        birth_global = int(w.get("draw_left", dl))
    idx_trend = 0
    propagated = w.get("index_in_trend")
    if propagated is not None:
        idx_trend = int(propagated)
    elif wave_seq_by_time is not None:
        info = wave_seq_by_time.get(str(wt))
        if info is not None:
            idx_trend = int(getattr(info, "index_in_trend", 0) or 0)
    out = {
        "dir": w.get("dir"),
        "wave_time": wt,
        "index_in_trend": idx_trend,
        "fib50": w.get("fib50"),
        "sl": w.get("sl"),
        "tp": w.get("tp"),
        "wave_target_tp_price": w.get("wave_target_tp_price"),
        "fib_abort": w.get("fib_abort"),
        "box_top": float(w.get("box_top", w.get("fib50", 0))),
        "box_bottom": float(w.get("box_bottom", w.get("fib50", 0))),
        "move_pct": w.get("move_pct"),
        "is_ext": bool(w.get("is_ext", False)),
        "in_ext_range": bool(w.get("in_ext_range", False)),
        "is_two_sided_counter": bool(
            w.get("is_two_sided_counter")
            or w.get("_two_sided_counter")
            or w.get("two_sided_show")
        ),
        "_two_sided_counter": bool(
            w.get("_two_sided_counter") or w.get("two_sided_show")
        ),
        "two_sided_show": bool(w.get("two_sided_show")),
        "wave_origin": w.get("wave_origin"),
        "hh_hl_pass": w.get("hh_hl_pass"),
        "post_ext_trend_suppressed": bool(w.get("post_ext_trend_suppressed", False)),
        "post_ext_confirmed_trend_lock": bool(
            w.get("post_ext_confirmed_trend_lock", False)
        ),
        "wf_continued_classic": bool(w.get("wf_continued_classic", False)),
        "draw_left_win": dl - start_idx,
        "draw_right_win": dr - start_idx,
        "birth_win": (
            None if birth_global is None else int(birth_global) - start_idx
        ),
    }
    if extra_flags:
        out.update(extra_flags)
    return out


def _reconstruct_wave_from_pending(
    wt: str,
    trade: ClosedTrade,
    pending_vis: Sequence[dict],
    df_full: pd.DataFrame,
) -> Optional[dict]:
    """Minimální vlna z pending_created — WF merge odstranil wave ze snapshotu."""
    from backtest.plotting import _nearest_bar_ix

    ev = next(
        (
            e
            for e in pending_vis
            if str(e.get("wave_time", "")) == wt and e.get("kind") == "pending_created"
        ),
        None,
    )
    if ev is None:
        return None
    try:
        pending_bar = int(ev["bar"])
        entry_bar = int(_nearest_bar_ix(df_full["time"], trade.entry_time))
    except (KeyError, TypeError, ValueError):
        return None
    dl = min(pending_bar, entry_bar)
    dr = max(pending_bar, entry_bar)
    ep = float(ev.get("ep", trade.entry_price))
    sl = float(ev.get("sl", trade.sl))
    wdir = _order_type_to_dir(str(ev.get("order_type", "")))
    if wdir == 0:
        wdir = 1 if sl < ep else -1
    lo = min(ep, sl)
    hi = max(ep, sl)
    return {
        "dir": wdir,
        "wave_time": wt,
        "draw_left": dl,
        "draw_right": dr,
        "fib50": ep,
        "sl": sl,
        "tp": trade.tp,
        "wave_target_tp_price": trade.tp,
        "box_top": hi,
        "box_bottom": lo,
        "move_pct": None,
        "is_ext": bool(getattr(trade, "is_ext", False)),
        "in_ext_range": False,
        "wave_origin": getattr(trade, "wave_origin", "normal"),
        "hh_hl_pass": True,
    }


def supplement_visual_waves_for_trades(
    bundle: WaveVisualBundle,
    *,
    last_waves: Sequence[dict],
    all_waves: Sequence[dict],
    wave_birth: dict,
    wave_seq_by_time: Optional[Dict[str, Any]] = None,
    pending_vis: Sequence[dict] | None = None,
    df_full: pd.DataFrame,
) -> None:
    """
    Doplní do bundle vlny pro obchody bez wave boxu (jen vizuál, runtime beze změny).

    - Vlna existuje v last_waves, ale visual filtr ji skryl (hh_hl_pass=False + obchod).
    - Vlna zmizela z finálního snapshotu (WF merge) — rekonstrukce z pending_vis.
    BOS_REENTRY_* obchody se neřeší (vlastní styl bez boxu).
    """
    if bundle is None or not bundle.trades:
        return

    existing = {str(w.get("wave_time", "")) for w in bundle.waves if w.get("wave_time")}
    needed: list[str] = []
    for t in bundle.trades:
        wt = str(getattr(t, "wave_time", "") or "")
        if not wt or wt.startswith("BOS_REENTRY_"):
            continue
        if wt not in existing:
            needed.append(wt)
    if not needed:
        return

    by_last = {str(w.get("wave_time", "")): w for w in last_waves if w.get("wave_time")}
    by_all = {str(w.get("wave_time", "")): w for w in all_waves if w.get("wave_time")}
    start_idx = int(bundle.window_start_bar)
    end_idx = int(bundle.window_end_bar)
    birth_map = dict(wave_birth or {})
    pending_list = list(pending_vis or [])

    for wt in sorted(set(needed)):
        src = by_last.get(wt) or by_all.get(wt)
        flags: dict = {"_visual_trade_anchor": True}
        if src is None:
            trade = next(
                (t for t in bundle.trades if str(getattr(t, "wave_time", "")) == wt),
                None,
            )
            if trade is None:
                continue
            src = _reconstruct_wave_from_pending(wt, trade, pending_list, df_full)
            if src is None:
                continue
            flags["_visual_reconstructed"] = True
            birth_map.setdefault(wt, int(src["draw_left"]))
        else:
            src = dict(src)

        win = _wave_dict_for_visual_window(
            src,
            start_idx=start_idx,
            end_idx=end_idx,
            wave_birth=birth_map,
            wave_seq_by_time=wave_seq_by_time,
            extra_flags=flags,
        )
        if win is not None:
            bundle.waves.append(win)
            existing.add(wt)


def visual_enabled_from_combo(
    combo: Optional[dict], cli_visual: bool = False
) -> bool:
    """Grid/base kombinace nebo CLI --visual-waves."""
    if cli_visual:
        return True
    if not combo:
        return False
    return bool(combo.get("visual_waves_enabled", False))


def visual_params_from_combo_and_args(
    combo: Optional[dict],
    *,
    cli_last_n: Optional[int] = None,
    cli_max_bars: Optional[int] = None,
    cli_plotly: Optional[bool] = None,
    cli_visual_waves: bool = False,
    cli_full_span: bool = False,
    cli_visual_clip: bool = False,
) -> tuple[int, int, int, bool, bool]:
    """last_n, max_bars, bars_per_wave_guess, use_plotly_html, full_span.

    full_span (výchozí True): celé načtené období + všechny eligible vlny v HTML/PNG.
    V profilu lze dát visual_waves_full_span: False. CLI --visual-clip vynutí ořez
    (last_n / max_bars). CLI --visual-full-span vynutí celé období i při False v profilu.

    HTML export: --visual-waves (CLI) nebo visual_waves_enabled v profilu → výchozí True.
    visual_waves_plotly_html: False v profilu vypne HTML jen u profilového visual_waves_enabled
    (ne u CLI --visual-waves). Explicitní cli_plotly má prioritu.
    """
    def _i(key: str, default: int) -> int:
        if combo is None:
            return default
        v = combo.get(key, default)
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return default

    last_n = cli_last_n if cli_last_n is not None else _i("visual_last_n_waves", DEFAULT_VISUAL_LAST_N_WAVES)
    max_bars = cli_max_bars if cli_max_bars is not None else _i("visual_waves_max_bars", DEFAULT_VISUAL_WAVES_MAX_BARS)
    guess = _i("visual_bars_per_wave_guess", DEFAULT_VISUAL_BARS_PER_WAVE_GUESS)

    visual_enabled = bool(combo.get("visual_waves_enabled", False)) if combo else False
    profile_html = combo.get("visual_waves_plotly_html", None) if combo else None
    if cli_plotly is not None:
        use_html = bool(cli_plotly)
    elif cli_visual_waves:
        use_html = True
    elif visual_enabled:
        use_html = True if profile_html is None else bool(profile_html)
    elif profile_html is not None:
        use_html = bool(profile_html)
    else:
        use_html = False

    if combo is None:
        combo_full = True
    else:
        v = combo.get("visual_waves_full_span", True)
        combo_full = True if v is None else bool(v)

    if cli_full_span:
        full_span = True
    elif cli_visual_clip:
        full_span = False
    else:
        full_span = combo_full

    return last_n, max_bars, guess, use_html, full_span


def default_visual_output_path(
    output_dir: Any,
    bot_name: str,
    *,
    suffix: str,
    test_pozice: int | None = None,
    tp_mode: str | None = None,
) -> Path:
    root = Path(output_dir) if output_dir else Path("results")
    stem = visual_waves_export_stem(
        bot_name, tp_mode=tp_mode, test_pozice=test_pozice
    )
    return root / f"{stem}_visual_waves.{suffix}"


def append_visual_waves_index(
    output_dir: Any,
    bot_name: str,
    png_path: Optional[Path],
    html_path: Optional[Path],
    *,
    test_pozice: int | None = None,
) -> None:
    """Radka do vizual adresare — mapuje kratky soubor na uplny bot_name (grid_report)."""
    root = Path(output_dir) if output_dir else Path("results")
    idx = root / "visual_waves_index.csv"
    append_csv_row(
        idx,
        [
            int(test_pozice) if test_pozice is not None else "",
            bot_name,
            png_path.name if png_path else "",
            html_path.name if html_path else "",
        ],
        header=["combo_no", "bot_name", "png", "html"],
    )
