"""EXT range — vlny na obe strany + bypass trend filtru."""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestEngine
from config.bot_config import BotConfig
from config.enums import TPMode
from strategy.wave_detection import detect_waves
from strategy.trend_bos import (
    compute_trend_states_per_wave,
    wave_allowed_for_entry,
)
from strategy.ext_logic import is_ext_wave


def _cfg(**kw) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_trade_both_sides_in_range=True,
        ext_range_wave_min_pct=0.13,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )
    base.update(kw)
    return BotConfig(**base)


def test_first_up_after_bear_ext_in_range_and_allowed():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-11")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    ext = [w for w in waves if is_ext_wave(w, cfg) and int(w["dir"]) == -1]
    assert ext
    up_after = [
        w
        for w in waves
        if int(w["dir"]) == 1
        and int(w["draw_left"]) > int(ext[-1]["draw_left"])
        and w.get("in_ext_range")
    ]
    assert up_after, "ocekavana UP vlna v EXT range po BEAR EXT"
    w = up_after[0]
    ts_map = compute_trend_states_per_wave(df, waves, cfg)
    allowed, reason = wave_allowed_for_entry(w, ts_map[w["wave_time"]], cfg)
    assert allowed is True
    assert reason == "ext_range_both_sides"


def test_ext_range_stops_after_any_wave_bos_flip():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-12")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert by_wt["202603090830"].get("in_ext_range") is True
    assert by_wt["202603091130"].get("in_ext_range") is True
    assert by_wt["202603091930"].get("in_ext_range") is False


def test_ext_range_stops_on_second_same_direction_wave_even_if_interleaved():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-18") & (df["time"] <= "2026-03-21")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert by_wt["202603200830"].get("in_ext_range") is True
    assert by_wt["202603201100"].get("in_ext_range") is True
    assert by_wt["202603201300"].get("in_ext_range") is False
    assert by_wt["202603201300"].get("ext_post_trend_seed_dir") == -1


def test_second_same_direction_wave_after_ext_seeds_new_trend():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-22") & (df["time"] <= "2026-03-31")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert by_wt["202603231900"].get("in_ext_range") is True
    assert by_wt["202603232130"].get("in_ext_range") is True
    assert by_wt["202603240700"].get("in_ext_range") is False
    assert by_wt["202603240700"].get("ext_post_trend_seed_dir") == -1

    ts_map = compute_trend_states_per_wave(df, waves, cfg)
    allowed, reason = wave_allowed_for_entry(by_wt["202603240700"], ts_map["202603240700"], cfg)
    assert allowed is True
    assert reason == "first_in_trend"

    allowed, reason = wave_allowed_for_entry(by_wt["202603241000"], ts_map["202603241000"], cfg)
    assert allowed is False
    assert reason == "wave_against_trend"


def test_second_same_direction_wave_after_ext_still_seeds_even_after_bos():
    """
    Po EXT se maji pro seed definici trendu pocitat vsechny vlny i nad ramec
    mezitimniho BOS flipu. Scenar:

      EXT DOWN -> UP1 -> DOWN1 -> (mezitimni BOS) -> UP2

    Trade-range (`in_ext_range`) uz muze byt vypnuty, ale `UP2` se musi
    i tak stat seed-vlnou noveho bull trendu. Následující bear vlna uz pak
    spada do bull-lock zony a musi byt potlacena.
    """
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-10 23:59:59")].reset_index(
        drop=True
    )

    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    assert by_wt["202603090430"].get("in_ext_range") is True  # EXT DOWN
    assert by_wt["202603090830"].get("in_ext_range") is True  # UP1
    assert by_wt["202603091130"].get("in_ext_range") is True  # DOWN1

    # UP2 uz nelezi v trade-range, ale musi se stat seed vlnou bull trendu.
    assert by_wt["202603091930"].get("in_ext_range") is False
    assert by_wt["202603091930"].get("ext_post_trend_seed_dir") == 1

    # Nasledujici bear vlna po seed-wave je uz proti potvrzenemu bull trendu
    # po EXT, takze se musi potlacit.
    assert by_wt["202603092200"].get("post_ext_trend_suppressed") is True

    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    vis = {str(w["wave_time"]) for w in eng.last_waves_for_visual}
    assert "202603091930" in vis
    assert "202603092200" not in vis


def test_ext_trade_range_termination_does_not_drop_classic_wave_measurement():
    cfg = _cfg()
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-19") & (df["time"] <= "2026-03-31")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    by_wt = {str(w["wave_time"]): w for w in waves}

    # Vlny z nizsiho ext_range_wave_min_pct musi zustat zachovane i po ukonceni
    # obchodniho "both-sides" rezimu (in_ext_range=False).
    for wt in ("202603260730", "202603261300", "202603270330", "202603270830"):
        assert wt in by_wt
        assert by_wt[wt].get("in_ext_range") is False

    # Posledni klasicka vlna 27. brezna musi zustat zachovana; po presnejsim
    # vikendovem zarovnani se muze jeji exact `wave_time` posunout driv
    # (20:30 misto drivejsiho 23:00), ale nesmi zmizet.
    late_mar27_candidates = ("202603272030", "202603272300")
    present = [wt for wt in late_mar27_candidates if wt in by_wt]
    assert present, f"chybi pozdni klasicka vlna 27. brezna, candidates={late_mar27_candidates}"
    for wt in present:
        assert by_wt[wt].get("in_ext_range") is False


def test_visual_keeps_counter_wave_that_precedes_bos_flip():
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-03-06 23:59:59")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)
    vis_times = {str(w["wave_time"]) for w in eng.last_waves_for_visual}
    # BOS vlna z 5. brezna musi byt vykreslena nezavisle na tom, zda z ni
    # otevrelo trade — jen kvuli tomu, ze zpusobila close-based flip trendu.
    assert "202603050800" in vis_times
    assert "202603050800" in getattr(eng, "_bos_wave_times", set())


def test_post_ext_trend_suppression_april_1_bear_wave_invisible():
    """
    Po EXT v 03/31, kdy se v ext_range vytvori 2 UP vlny + 1 DOWN vlna,
    `202604011130` je seed-wave (bull trend potvrzen). BEAR vlna v 04/01 15:30
    (`202604011530`) tedy spada do bull-lock zony az do baru BOS flipu (21:00).
    Tato vlna musi byt potlacena:
      * NENI ve vizualu,
      * NENI v `_bos_wave_times` (ani kdyz by jinak BOS zpusobila),
      * ma tag `post_ext_trend_suppressed=True`.
    """
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-30") & (df["time"] <= "2026-04-03 23:59:59")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    by_wt = {str(w["wave_time"]): w for w in eng.last_waves}
    assert by_wt["202604011130"].get("ext_post_trend_seed_dir") == 1
    assert by_wt["202604011530"].get("post_ext_trend_suppressed") is True

    vis = {str(w["wave_time"]) for w in eng.last_waves_for_visual}
    assert "202604011530" not in vis
    assert "202604011530" not in set(eng._bos_wave_times)

    # Sanity: UP seed wave + bear wave po BOS flipu (22:00) zustavaji viditelne.
    assert "202604011130" in vis
    assert "202604012200" in vis


def test_post_ext_trend_suppression_march_24_up_waves_invisible():
    """
    Po UP EXT v 03/23, posloupnost bear-up-bear definuje BEAR trend
    (seed-wave `202603240700` ma `ext_post_trend_seed_dir=-1`).
    Vsechny UP vlny v bear-lock zone (do nasledujiciho close-based BOS flipu)
    musi byt potlacene a neviditelne.
    """
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-22") & (df["time"] <= "2026-03-26 23:59:59")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    by_wt = {str(w["wave_time"]): w for w in eng.last_waves}
    assert by_wt["202603240700"].get("ext_post_trend_seed_dir") == -1
    # UP vlny v zamcene bear zone - obe musi byt potlacene.
    for wt in ("202603241000", "202603241730"):
        assert by_wt[wt].get("post_ext_trend_suppressed") is True, wt
        print(f"{wt} dir={by_wt[wt]['dir']} is_ext={by_wt[wt].get('is_ext')} lock={by_wt[wt].get('post_ext_confirmed_trend_lock')} supp={by_wt[wt].get('post_ext_trend_suppressed')} seed={by_wt[wt].get('ext_post_trend_seed_dir')} origin={by_wt[wt].get('wave_origin')} wf_cont={by_wt[wt].get('wf_continued_classic')}")

    vis = {str(w["wave_time"]) for w in eng.last_waves_for_visual}
    print("TEST VIS:", vis)
    print("BOS WAVES:", eng._bos_wave_times)
    print("TWO SIDED:", eng._two_sided_fired_wave_times)
    print("WF WAVES:", [w.get("wave_time") for w in eng._wf_visual_waves])
    assert "202603241000" not in vis
    assert "202603241730" not in vis


def test_post_ext_suppressed_wave_is_not_picked_as_bos_wave():
    """
    Vlna s `post_ext_trend_suppressed=True` se nesmi stat BOS-vlnou pro
    `compute_bos_wave_flip_map`/`_bos_wave_times`, i kdyby svym extrémem
    zpusobila close-based flip. Tim je oddelena funkcnost:
      * post-EXT zamek potlacuje vlny v zamcene zone (samostatne pravidlo),
      * BOS retro mechanismus pres ne neprochazi (= nikdy je nevykresli).
    """
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT
    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-30") & (df["time"] <= "2026-04-03 23:59:59")].reset_index(
        drop=True
    )
    eng = BacktestEngine(cfg)
    eng.run(df, retain_wave_snapshot=True)

    by_wt = {str(w["wave_time"]): w for w in eng.last_waves}
    suppressed = {wt for wt, w in by_wt.items() if w.get("post_ext_trend_suppressed")}
    assert suppressed, "ocekavana alespon jedna suppressed vlna v tomto segmentu"
    assert suppressed.isdisjoint(set(eng._bos_wave_times))


def test_retro_bos_wave_only_contains_actual_bos_causing_waves():
    """
    _bos_wave_times musi obsahovat POUZE skutecne BOS-vlny (jedna na flip).
    Vlny okolo (sumove, proti-trendove bez vlivu na flip) se sem nepritahuji.
    Pocet polozek == pocet flipu v segmentu — to overuje shoda s
    `_bos_flip_wave_by_bar`.
    """
    cfg = _cfg()
    cfg.wave_position_enabled = True
    cfg.tp_mode = TPMode.BOS_EXIT

    df = pd.read_csv("data/EURUSD.x_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})

    # Segment 10. brezna — BOS-vlna pro bear flip (b95) je 202603102230:
    # vlna, jejiz extrem se tvori prave na baru flipu (dl=88, dr=98, flip@95).
    # NE stara 202603100700 (extrem b62, 33 baru pred flipem) — tu drive vybirala
    # "posledni potvrzena vlna" logika, ackoliv strukturu prorazila az 202603102230.
    eng = BacktestEngine(cfg)
    seg = df[(df["time"] >= "2026-03-07") & (df["time"] <= "2026-03-11 23:59:59")].reset_index(
        drop=True
    )
    eng.run(seg, retain_wave_snapshot=True)
    bos_set = set(getattr(eng, "_bos_wave_times", set()))
    flip_map = dict(getattr(eng, "_bos_flip_wave_by_bar", {}))
    assert "202603102230" in bos_set
    # Pro kazdy flip prave jedna vlna; v setu zadne dalsi vlny mimo flipy.
    assert len(bos_set) == len({str(w.get("wave_time", "")) for w in flip_map.values()})
    assert bos_set == {str(w.get("wave_time", "")) for w in flip_map.values()}

    # Segment 5. brezna — bos-vlna 202603050800 musi byt v setu.
    eng2 = BacktestEngine(cfg)
    seg2 = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-03-06 23:59:59")].reset_index(
        drop=True
    )
    eng2.run(seg2, retain_wave_snapshot=True)
    bos_set2 = set(getattr(eng2, "_bos_wave_times", set()))
    flip_map2 = dict(getattr(eng2, "_bos_flip_wave_by_bar", {}))
    assert "202603050800" in bos_set2
    assert bos_set2 == {str(w.get("wave_time", "")) for w in flip_map2.values()}
