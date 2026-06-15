"""Centralizovaná pravidla viditelnosti vln — shodná s HTML chartem a index_in_trend."""
from __future__ import annotations

import copy
from typing import Any, FrozenSet, List, Set

from strategy.trend_bos import _wave_is_wf_origin
from strategy.wick_fakeout import WAVE_ORIGIN_WF


def wave_passes_visual_filter(
    wave: dict,
    cfg: Any,
    *,
    check_bos: bool = True,
    bos_wave_times: Set[str] | FrozenSet[str] | None = None,
    wf_visual_wave_times: Set[str] | FrozenSet[str] | None = None,
    two_sided_fired_times: Set[str] | FrozenSet[str] | None = None,
    exclude_wf_from_bos: bool = True,
    include_lock_trend_waves: bool = False,
) -> bool:
    """
    True pokud se vlna vykresluje v HTML (box + label).

    check_bos=False: BOS výjimka se nevyhodnocuje (počítadlo index_in_trend —
    BOS větve řeší engine/wave_sequence zvlášť).

    include_lock_trend_waves: jen HTML export — vlny v post-EXT lock zóně ve
    směru potvrzeného trendu (`post_ext_confirmed_trend_dir`) se vykreslí;
    protisměrné lock vlny zůstanou skryté. wave_sequence volá s False (default).

    _visual_trade_anchor: jen HTML — vlna doplněná u obchodu bez boxu (viz
    supplement_visual_waves_for_trades); runtime filtr se nemění.
    """
    if wave.get("_visual_trade_anchor"):
        return True

    wt = str(wave.get("wave_time", "") or "")
    bos_set = bos_wave_times or frozenset()
    wf_vis = wf_visual_wave_times or frozenset()
    ts_set = two_sided_fired_times or frozenset()

    if check_bos and wt and wt in bos_set:
        if not (exclude_wf_from_bos and _wave_is_wf_origin(wave)):
            return True

    is_ext = bool(wave.get("is_ext", False))
    if not is_ext and cfg is not None:
        try:
            from strategy.ext_logic import is_ext_wave

            is_ext = is_ext_wave(wave, cfg)
        except Exception:
            pass

    is_in_ext_range = bool(wave.get("in_ext_range", False)) and bool(
        getattr(cfg, "ext_trade_both_sides_in_range", False)
    )
    is_wf = (
        str(wave.get("wave_origin", "")) == WAVE_ORIGIN_WF
        or bool(wave.get("is_wf", False))
        or (wt and wt in wf_vis)
    )
    is_two_sided = bool(
        wave.get("_two_sided_counter")
        or wave.get("two_sided_show")
        or wave.get("is_two_sided_counter")
        or (wt and wt in ts_set)
    )

    if is_ext or is_in_ext_range or is_wf or is_two_sided:
        return True

    if wave.get("post_ext_trend_suppressed", False):
        return False

    lock_trend_visible = False
    if wave.get("post_ext_confirmed_trend_lock", False):
        lock_dir = wave.get("post_ext_confirmed_trend_dir")
        wdir = int(wave.get("dir", 0) or 0)
        if (
            include_lock_trend_waves
            and lock_dir in (1, -1)
            and wdir == int(lock_dir)
        ):
            lock_trend_visible = True
        else:
            return False

    if (
        not lock_trend_visible
        and cfg is not None
        and getattr(cfg, "trend_hh_hl_filter_enabled", False)
    ):
        if not wave.get("hh_hl_pass", True):
            return False

    return True


def merge_lock_trend_segments_for_visual(
    waves: List[dict],
    df: Any,
    cfg: Any,
) -> List[dict]:
    """
    Visual-only: sousední lock trend segmenty stejného směru sloučí do jednoho boxu
    (typicky bear pokračování po EXT lock — Jul 17).

    Runtime vlny se nemění; absorbované segmenty se z visual seznamu vynechají.
    """
    if len(waves) < 2:
        return list(waves)

    ordered = sorted(
        waves,
        key=lambda w: (int(w.get("draw_left", 0)), str(w.get("wave_time", ""))),
    )
    absorb: set[str] = set()
    merged: List[dict] = []

    i = 0
    while i < len(ordered):
        base = ordered[i]
        chain = [base]
        base_dir = int(base.get("dir", 0) or 0)
        j = i + 1
        while j < len(ordered):
            nxt = ordered[j]
            if int(nxt.get("dir", 0) or 0) != base_dir:
                break
            if not bool(nxt.get("post_ext_confirmed_trend_lock")):
                break
            lock_dir = nxt.get("post_ext_confirmed_trend_dir")
            if lock_dir not in (1, -1) or int(lock_dir) != base_dir:
                break
            chain.append(nxt)
            j += 1

        if len(chain) > 1:
            head = copy.deepcopy(chain[0])
            tail = chain[-1]
            lo = int(head.get("draw_left", 0))
            hi = int(tail.get("draw_right", lo))
            head["draw_right"] = hi
            head["_visual_lock_merged"] = True
            head["_visual_merged_from"] = [
                str(w.get("wave_time", "")) for w in chain[1:] if w.get("wave_time")
            ]
            for w in chain[1:]:
                wt = str(w.get("wave_time", "") or "")
                if wt:
                    absorb.add(wt)

            if df is not None and cfg is not None and hi >= lo:
                try:
                    from strategy.wave_detection_pine import (
                        _append_wave_sig,
                        _compute_after_data_gap_mask,
                        _segment_extremes_with_gaps,
                    )

                    after_gap = _compute_after_data_gap_mask(df["time"])
                    bt, bb = _segment_extremes_with_gaps(
                        df, lo, hi, base_dir, after_gap
                    )
                    if bt > bb:
                        pivot_level = bb if base_dir == 1 else bt
                        cand_level = bt if base_dir == 1 else bb
                        new_sig = _append_wave_sig(
                            cfg,
                            w_dir=base_dir,
                            pivot_level=float(pivot_level),
                            cand_level=float(cand_level),
                            box_top=bt,
                            box_bottom=bb,
                            pivot_bar_idx=lo,
                            cand_bar_idx=hi,
                            wave_time_str=str(head.get("wave_time", "")),
                        )
                        if new_sig is not None:
                            idx_keep = head.get("index_in_trend")
                            head.update(new_sig)
                            if idx_keep is not None:
                                head["index_in_trend"] = idx_keep
                except Exception:
                    head["box_bottom"] = min(
                        float(w.get("box_bottom", 0)) for w in chain
                    )
                    head["box_top"] = max(float(w.get("box_top", 0)) for w in chain)
            else:
                head["box_bottom"] = min(float(w.get("box_bottom", 0)) for w in chain)
                head["box_top"] = max(float(w.get("box_top", 0)) for w in chain)

            merged.append(head)
            i = j
        else:
            merged.append(base)
            i += 1

    if not absorb:
        return list(waves)

    return [w for w in merged if str(w.get("wave_time", "") or "") not in absorb]
