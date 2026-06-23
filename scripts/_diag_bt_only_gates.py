"""Diagnostika: proc replay/live preskakuje 32 plain WAVE vln, ktere backtest obchoduje.
Pro kazdy BT-only wave_time vyhodnoti staticke entry gaty."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATE_FROM = "2025-11-10"
DATE_TO = "2026-05-09"
CSV = ROOT / "data" / "EURUSD_M30.csv"

BT_ONLY = [
    '202511261030', '202511261830', '202512051800', '202512102200', '202601021800',
    '202601122230', '202601220130', '202601261800', '202602031300', '202602040600',
    '202602131530', '202602162330', '202602192330', '202602230130', '202602231400',
    '202602250900', '202602251930', '202603051030', '202603160400', '202603170900',
    '202603171200', '202603181600', '202603242300', '202603250100', '202603310430',
    '202604082200', '202604092030', '202604171600', '202604301400', '202604301800',
    '202605061930', '202605072330',
]


def main() -> None:
    import pandas as pd
    from backtest.data_loader import filter_by_date_range, load_csv
    from config.bot_config import LIVE_BOT_CONFIG
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from strategy.trend_bos import (
        compute_bos_wave_flip_map, compute_trend_states_per_wave,
        reconcile_bos_flip_map_with_wave_sequence, _detect_close_bos_timeline_flips,
    )
    from strategy.wave_sequence import sync_wave_sequence_state
    from strategy.ext_range import ext_range_enabled, reapply_ext_range_tags
    from strategy.ext_logic import is_ext_wave
    from strategy.trend_bos import wave_allowed_for_entry
    from strategy.filters import (
        is_wave_too_old, is_wave_in_allowed_session, is_wave_too_large,
    )

    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    df = filter_by_date_range(load_csv(str(CSV)), DATE_FROM, DATE_TO).reset_index(drop=True)
    waves = detect_waves(df, cfg)
    wave_birth = compute_wave_birth_bars_pine(df, cfg)
    trend_states = compute_trend_states_per_wave(df, waves, cfg)
    seq_info, protected = sync_wave_sequence_state(df, waves, cfg)
    if ext_range_enabled(cfg):
        reapply_ext_range_tags(waves, cfg, df=df, wave_birth=wave_birth)
        seq_info, protected = sync_wave_sequence_state(df, waves, cfg)

    bos_wave_times: set[str] = set()
    if cfg.trend_filter_enabled:
        flips = _detect_close_bos_timeline_flips(df, waves, cfg, wave_birth_bars=wave_birth)
        bos_map = reconcile_bos_flip_map_with_wave_sequence(
            compute_bos_wave_flip_map(df, waves, cfg, wave_birth_bars=wave_birth),
            flips, waves, seq_info, wave_birth,
        )
        bos_wave_times = set(bos_map.values())

    by_time = {str(w["wave_time"]): w for w in waves}

    def _birth_ref_time(wt: str):
        b = wave_birth.get(wt)
        if b is None or int(b) >= len(df):
            return pd.Timestamp(df["time"].iloc[-1]).to_pydatetime()
        return pd.Timestamp(df["time"].iloc[int(b)]).to_pydatetime()

    print(f"{'wave_time':<14}{'found':<6}{'inExt':<6}{'tsNone':<7}{'allowed':<8}{'bosWT':<6}{'old':<5}{'sess':<6}{'large':<6}{'postExt':<8}{'lock':<6}{'wf':<5}")
    for wt in BT_ONLY:
        w = by_time.get(wt)
        if w is None:
            print(f"{wt:<14}NO (vlna v live detekci neexistuje!)")
            continue
        ts = trend_states.get(wt)
        ts_none = ts is None and trend_states.get(str(wt)) is None
        allowed, reason = wave_allowed_for_entry(w, ts, cfg)
        in_ext = bool(w.get("in_ext_range", False))
        is_ext = is_ext_wave(w, cfg)
        bbar = wave_birth.get(wt)
        old = is_wave_too_old(wt, cfg, ref_time=_birth_ref_time(wt))
        sess = not is_wave_in_allowed_session(wt, cfg)
        large = is_wave_too_large(w["move_pct"], cfg, is_ext=is_ext)
        post_ext = bool(w.get("post_ext_trend_suppressed", False))
        lock = bool(w.get("post_ext_confirmed_trend_lock", False))
        wf = bool(w.get("wf_wave_position", False))
        print(f"{wt:<14}b={str(bbar):<6}{('Y' if in_ext else '-'):<6}{('Y' if ts_none else '-'):<7}"
              f"{(('Y:'+reason[:5]) if allowed else ('N:'+reason[:5])):<8}"
              f"{('Y' if wt in bos_wave_times else '-'):<6}{('Y' if old else '-'):<5}"
              f"{('Y' if sess else '-'):<6}{('Y' if large else '-'):<6}"
              f"{('Y' if post_ext else '-'):<8}{('Y' if lock else '-'):<6}{('Y' if wf else '-'):<5}")


if __name__ == "__main__":
    main()
