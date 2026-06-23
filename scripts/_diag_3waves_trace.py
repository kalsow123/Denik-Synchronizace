"""Per-bar trace 3 velkych BT-only vln z REALNEHO E2E behu."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGETS = ["202603250100"]
DATE_FROM, DATE_TO = "2025-11-10", "2026-05-09"


def main() -> None:
    from config.bot_config import LIVE_BOT_CONFIG
    from config.position_modes import resolve_grid_engine_config
    from scripts.e2e_live_broker_sim import install_fake, run_e2e, _clean_wave_time

    cfg = resolve_grid_engine_config(LIVE_BOT_CONFIG)
    fake = install_fake(cfg.symbol, cfg.contract_size)

    from backtest.data_loader import filter_by_date_range, load_csv
    from runtime.live_wave_isolation import resolve_live_execution_config
    from strategy.wave_detection import detect_waves
    from strategy.wave_detection_pine import compute_wave_birth_bars_pine
    from runtime.wf_live import WfLiveRuntime
    import runtime.missed_bar_replay as mbr

    df = filter_by_date_range(load_csv("data/EURUSD_M30.csv"), DATE_FROM, DATE_TO).reset_index(drop=True)
    live_cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)

    # post-WF membership: replikuj run_e2e zacatek
    waves = detect_waves(df, cfg)
    pre_wf = {str(w["wave_time"]) for w in waves}
    wave_birth = compute_wave_birth_bars_pine(df, cfg)
    wf = WfLiveRuntime()
    wf.process(df, cfg, waves, wave_birth_by_time=wave_birth)
    post_wf = {str(w["wave_time"]): w for w in waves}

    print("max_wave_age_hours =", getattr(cfg, "max_wave_age_hours", "n/a"),
          " trend_filter_enabled =", cfg.trend_filter_enabled)
    for wt in TARGETS:
        w = post_wf.get(wt)
        print(f"\n{wt}: pre_WF_detect={wt in pre_wf}  post_WF_detect={wt in post_wf}  "
              f"birth={wave_birth.get(wt)}  "
              f"wf_continued_classic={w.get('wf_continued_classic') if w else 'N/A'}  "
              f"wf_wave_position={w.get('wf_wave_position') if w else 'N/A'}")

    # trace
    mbr._TRACE_WAVES = set(TARGETS)
    mbr._TRACE_LOG.clear()
    lv = run_e2e(df, live_cfg, fake)
    lv_wt = {_clean_wave_time(getattr(t, "comment", "")) for t in lv}

    by_wt = {}
    for bar_idx, wt, branch, kw in mbr._TRACE_LOG:
        by_wt.setdefault(str(wt), []).append((bar_idx, branch, kw))

    for wt in TARGETS:
        print("\n" + "=" * 70)
        print(f"TRACE {wt}  (live obchodoval={wt in lv_wt})")
        print("=" * 70)
        rows = by_wt.get(wt, [])
        # zhustit: prvni vyskyt kazde unikatni (branch) + bar rozsah
        for bar_idx, branch, kw in rows[:40]:
            extra = " ".join(f"{k}={v}" for k, v in kw.items())
            print(f"  bar {bar_idx:>5}  {branch:<28} {extra}")
        if len(rows) > 40:
            print(f"  ... (+{len(rows)-40} dalsich zaznamu)")


if __name__ == "__main__":
    main()
