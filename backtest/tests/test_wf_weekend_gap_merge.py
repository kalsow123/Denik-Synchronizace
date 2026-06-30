"""
Regrese: vikendovy gap-merge musi fungovat i kdyz drive v behu doslo k WF
aktivaci (ktera prepne detekci do segment_mode).

Pozadi (root cause):
  Po prvni WF aktivaci se cely zbytek behu re-detekuje pres
  `run_pine_wave_simulation_from_seed(..., segment_mode=True)`. Drive
  `segment_mode` vypinal `_merge_waves_across_data_gaps`, takze kazdy vikend
  po prvni WF aktivaci se rozpadl: jedna trendova vlna pres vikend se
  detekovala jako dve utnute casti (napr. bear vlna pred/po pondelnim openu).

  Konkretni scenar EURUSD M30, vikend 2026-03-20 -> 2026-03-23:
    * ciste detect_waves (gap-merge ON): jedna DOWN vlna 202603230330
      dl=234 dr=259 (move ~0.446 %).
    * engine s WF (segment_mode): rozpad na 202603230330 (dr=255) +
      202603230930 (dl=256) — dve male vlny, ktere se neobchoduji spravne.

Tento test overuje, ze engine vyprodukuje STEJNOU spojenou vlnu jako ciste
detect_waves (tj. WF segment_mode uz vikendovy merge nerozbiji).
"""
import pandas as pd

from config.bot_config import LIVE_BOT_CONFIG
from backtest.engine import BacktestEngine
from strategy.wave_detection import detect_waves


def _load_df():
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"]).rename(
        columns={"datetime": "time"}
    )
    return df[
        (df["time"] >= "2026-03-16") & (df["time"] <= "2026-03-27 23:59:59")
    ].reset_index(drop=True)


def test_weekend_bear_wave_merged_even_after_wf_activation():
    cfg = LIVE_BOT_CONFIG
    df = _load_df()

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    # Pojistka: v tomto rozsahu doslo k aspon jedne WF aktivaci (jinak by
    # test nehlidal segment_mode cestu).
    assert eng.wave_debug.get("wf_activations", 0) >= 1

    eng_by_wt = {str(w["wave_time"]): w for w in eng.last_waves}
    clean = detect_waves(df, cfg)
    clean_by_wt = {str(w["wave_time"]): w for w in clean}

    # Spojena vikendova DOWN vlna musi existovat a koncit na dr=259 (jako
    # v cistem detect_waves), NE byt utnuta na dr=255.
    assert "202603230330" in eng_by_wt, "vikendova DOWN vlna chybi v engine"
    merged = eng_by_wt["202603230330"]
    assert int(merged["dir"]) == -1
    assert int(merged["draw_right"]) == int(
        clean_by_wt["202603230330"]["draw_right"]
    ), "vikendova vlna se v engine neutnula stejne jako v cistem detect_waves"
    assert int(merged["draw_right"]) == 259

    # Rozpadova druha cast NESMI vzniknout (drive 202603230930 dl=256).
    assert "202603230930" not in eng_by_wt, (
        "vikendova bear vlna se stale rozpada na dve casti (segment_mode merge OFF)"
    )

    # A engine wave se cenove shoduje s cistym detect_waves (spojena geometrie).
    assert abs(float(merged["box_bottom"]) - float(
        clean_by_wt["202603230330"]["box_bottom"]
    )) < 1e-9
    assert abs(float(merged["move_pct"]) - float(
        clean_by_wt["202603230330"]["move_pct"]
    )) < 1e-6
