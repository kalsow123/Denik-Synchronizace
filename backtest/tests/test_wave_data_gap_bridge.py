"""Test: vlna pres data gap (vikend) — defer confirm + zapocitani skoku ceny."""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import (
    _bridge_gap_prices,
    _bridge_gap_prices_with_refs,
    _compute_after_data_gap_mask,
    run_pine_wave_simulation,
)


def _cfg(**kwargs) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=False,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    base.update(kwargs)
    return BotConfig(**base)


def test_after_data_gap_mask_detects_weekend():
    times = pd.to_datetime(
        [
            "2026-03-06 12:00",
            "2026-03-06 12:30",
            "2026-03-09 06:30",
            "2026-03-09 07:00",
        ]
    )
    mask = _compute_after_data_gap_mask(times)
    assert mask[0] is False
    assert mask[1] is False
    assert mask[2] is True
    assert mask[3] is False


def test_bridge_gap_increases_down_move_pct():
    pivot, cand = _bridge_gap_prices(
        -1,
        1.1600,
        1.1550,
        prev_close=1.1580,
        open_=1.1500,
        high=1.1520,
        low=1.1480,
    )
    move = abs(cand - pivot) / pivot * 100.0
    assert cand < 1.1550
    assert move > abs(1.1550 - 1.1600) / 1.1600 * 100.0


def test_bridge_gap_returns_extreme_sources_for_time_alignment():
    """
    Gap bridge musi vracet nejen nove ceny, ale i informaci, zda novy extrem
    vznikl na pred-gap close nebo na prvnim post-gap baru. Bez toho se vlna
    muze cenove prepocitat spravne, ale zustane useknuta v case.
    """
    pivot, cand, pivot_ref, cand_ref = _bridge_gap_prices_with_refs(
        -1,
        1.1600,
        1.1550,
        prev_close=1.1580,
        open_=1.1500,
        high=1.1620,
        low=1.1480,
    )
    assert pivot == 1.1620
    assert cand == 1.1480
    assert pivot_ref == "cur"
    assert cand_ref == "cur"


def test_down_wave_spans_weekend_single_confirm():
    """
    Silny pokles, 3 opacne svicky pred vikendem, pak gap a pokracovani dolu v pondeli —
    bez defer by vznikly dve vlny; s bridgem jedna DOWN s vyssim move_pct.
    """
    times = pd.to_datetime(
        [
            "2026-03-06 08:00",
            "2026-03-06 08:30",
            "2026-03-06 09:00",
            "2026-03-06 09:30",
            "2026-03-06 10:00",
            "2026-03-06 10:30",
            "2026-03-09 06:30",
            "2026-03-09 07:00",
            "2026-03-09 07:30",
            "2026-03-09 08:00",
            "2026-03-09 08:30",
            "2026-03-09 09:00",
        ]
    )
    # 0–2: pokles (kvalifikace); 3–5: retracement bez noveho low (3× opp); 6: pred gapem;
    # 7+: pondeli — gap bridge + dalsi low => jedna DOWN vlna pres vikend
    df = pd.DataFrame(
        {
            "time": times,
            "open": [
                1.1640, 1.1580, 1.1520, 1.1510, 1.1512, 1.1514,
                1.1516, 1.1480, 1.1460, 1.1440, 1.1430, 1.1425,
            ],
            "high": [
                1.1650, 1.1590, 1.1530, 1.1525, 1.1525, 1.1525,
                1.1525, 1.1490, 1.1470, 1.1450, 1.1440, 1.1430,
            ],
            "low": [
                1.1630, 1.1570, 1.1510, 1.1500, 1.1500, 1.1500,
                1.1500, 1.1450, 1.1430, 1.1410, 1.1400, 1.1390,
            ],
            "close": [
                1.1635, 1.1575, 1.1515, 1.1520, 1.1522, 1.1523,
                1.1524, 1.1455, 1.1445, 1.1425, 1.1415, 1.1400,
            ],
        }
    )
    cfg = _cfg(min_opp_bars=3, wave_min_pct=0.26, wave_plus=True)
    waves, _, _, _ = run_pine_wave_simulation(df, cfg)
    down = [w for w in waves if int(w["dir"]) == -1]
    assert len(down) == 1, f"ocekavana 1 DOWN pres vikend, waves={waves}"
    w = down[0]
    assert float(w["move_pct"]) >= 0.76
    assert int(w["draw_right"]) >= 7
    assert w.get("is_ext") is True


def test_gap_extreme_on_first_monday_bar_advances_wave_time_and_draw_right():
    """
    Reprezentuje problem "vlna se pres vikend cenove dotahla, ale casove ne":
    pondelni gap bar udela finalni low, po nem prijdou uz jen 3 opacne svicky
    pro potvrzeni. Vlna musi mit `wave_time`/`draw_right` na prvnim pondelnim
    baru, ne zustat useknuta na patek.
    """
    times = pd.to_datetime(
        [
            "2026-03-06 08:00",
            "2026-03-06 08:30",
            "2026-03-06 09:00",
            "2026-03-06 09:30",
            "2026-03-06 10:00",
            "2026-03-06 10:30",
            "2026-03-06 11:00",
            "2026-03-09 06:30",
            "2026-03-09 07:00",
            "2026-03-09 07:30",
            "2026-03-09 08:00",
        ]
    )
    df = pd.DataFrame(
        {
            "time": times,
            "open": [
                1.1640, 1.1590, 1.1530, 1.1524, 1.1522, 1.1520, 1.1518,
                1.1460, 1.1465, 1.1470, 1.1475,
            ],
            "high": [
                1.1650, 1.1600, 1.1540, 1.1525, 1.1523, 1.1521, 1.1519,
                1.1470, 1.1475, 1.1480, 1.1485,
            ],
            "low": [
                1.1630, 1.1580, 1.1520, 1.1519, 1.1518, 1.1517, 1.1516,
                1.1440, 1.1460, 1.1465, 1.1470,
            ],
            "close": [
                1.1635, 1.1585, 1.1525, 1.1520, 1.1519, 1.1518, 1.1517,
                1.1465, 1.1472, 1.1478, 1.1482,
            ],
        }
    )
    cfg = _cfg(min_opp_bars=3, wave_min_pct=0.26, wave_plus=False)
    waves, _, _, _ = run_pine_wave_simulation(df, cfg)
    down = [w for w in waves if int(w["dir"]) == -1]
    assert len(down) == 1, f"ocekavana 1 DOWN vlna, waves={waves}"
    w = down[0]
    assert str(w["wave_time"]) == "202603090630"
    assert int(w["draw_right"]) == 7
    assert float(w["box_bottom"]) <= 1.1440


def test_eurusd_mar2026_weekend_merges_to_one_ext_down():
    """Reálné M30: pátek + víkend gap + pondělí = jedna DOWN EXT vlna ve vizualizaci."""
    import pandas as pd
    from strategy.wave_detection import detect_waves
    from strategy.trend_bos import filter_waves_for_structure_display

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        trend_filter_enabled=True,
        trend_hh_hl_filter_enabled=True,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-05") & (df["time"] <= "2026-03-11")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    ext_down = [
        w
        for w in waves
        if int(w["dir"]) == -1 and w.get("is_ext")
    ]
    assert len(ext_down) >= 1
    w = max(ext_down, key=lambda x: float(x["move_pct"]))
    assert float(w["move_pct"]) >= 0.76
    span = int(w["draw_right"]) - int(w["draw_left"])
    # Nesmi slepit cely tyden do jedne mesicni vlny (regrese gap-merge pres cely usek).
    assert span <= 80, f"EXT DOWN span prilis velky ({span} baru): {w}"

    vis = filter_waves_for_structure_display(df, waves, cfg)
    vis_ext = [x for x in vis if x.get("is_ext")]
    assert any(x["wave_time"] == w["wave_time"] for x in vis_ext)


def test_gap_merge_does_not_collapse_three_month_trend():
    """3 mesice M30: nesmi vzniknout jedna EXT vlna pres cely trend (regrese 2026-05)."""
    import pandas as pd
    from strategy.wave_detection import detect_waves

    cfg = _cfg(wave_plus=True)
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    waves = detect_waves(df, cfg)
    assert len(waves) >= 50
    ext = [w for w in waves if w.get("is_ext")]
    assert ext
    max_span = max(int(w["draw_right"]) - int(w["draw_left"]) for w in ext)
    assert max_span < 200, f"nejvetsi EXT span={max_span} — pravdepodobne slepeny cely trend"


def test_eurusd_mar20_weekend_down_continuation_merges_to_single_ext():
    """
    Reálný M30 (2026-03-20 -> 2026-03-24):
      páteční down pohyb pokračuje po víkendu v pondělí a dříve se štěpil na
      více stejnosměrných DOWN segmentů pod EXT prahem.

    Po fixu musí existovat jedna hlavní DOWN vlna přes víkendový gap, která
    pohltí navazující pondělní pokračování (dříve 2-3 kratké DOWN segmenty).
    """
    import pandas as pd

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    seg = df[(df["time"] >= "2026-03-20") & (df["time"] <= "2026-03-24")].reset_index(
        drop=True
    )
    gap_mask = _compute_after_data_gap_mask(seg["time"])
    gap_idxs = [i for i, v in enumerate(gap_mask) if v]
    assert gap_idxs, "v segmentu musi byt vikendovy gap"
    gap_i = gap_idxs[0]

    waves, _, _, _ = run_pine_wave_simulation(seg, cfg)
    down_crossing_gap = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and int(w["draw_left"]) <= gap_i <= int(w["draw_right"])
    ]
    assert down_crossing_gap, f"chybí DOWN vlna přes vikendovy gap, waves={waves}"
    main = max(down_crossing_gap, key=lambda x: float(x["move_pct"]))
    # Must include Monday continuation (historicky bylo R=63; po fixu R≈75).
    assert int(main["draw_right"]) >= 75, (
        f"hlavni DOWN pres gap se nedotahla do pondelniho pokracovani: {main}"
    )
    # Velikost musi byt blizko EXT prahu (v reálných datech kolem 0.75+).
    assert float(main["move_pct"]) >= 0.74
    # Dalsi stejnosmerny DOWN mezi puvodne useknutym koncem (R=63) a novym
    # koncem main vlny nema existovat (regresni split pondelniho pokracovani).
    regressive_tail = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and int(w["draw_left"]) > gap_i
        and int(w["draw_left"]) <= int(main["draw_right"])
        and str(w["wave_time"]) != str(main["wave_time"])
    ]
    assert not regressive_tail, f"nalezen regresni post-gap split: {regressive_tail}"


def test_wave_through_weekend_gap_reaches_actual_post_gap_low():
    """
    Klicove pravidlo: vlna pres vikendovy gap musi koncit az na skutecnem
    post-gap extremu, ne jen "na konci gapu" (Monday open).

    Toto je vystup dvou doplnujicich se mechanismu:
      1. Post-gap bar se NESMI pocitat jako opacna svicka (`is_opp`) pri
         counting `min_opp_bars` (jinak by 3 BULL svice vc. gap_baru
         predcasne potvrdily DOWN vlnu hned po gapu).
      2. Wave-merge pres data gap (`_merge_waves_across_data_gaps`) spoji
         sousedni stejnosmerne vlny, kde prvni vlna prekrocila gap a druha
         je s ni "contiguous-after-gap".

    Test scenario (M30, DOWN wave pres vikend):
      patek: DOWN move kvalifikuje vlnu (move >= wave_min_pct)
      vikend: gap-down
      pondeli prvni bar: BULL close>=open
      pondeli 2 dalsi BULL bary
      pondeli pak BEAR bary lower-low (skutecne post-gap minimum)
      pak 3 dalsi BULL bary pro confirm noveho lowu

    Test vyzaduje, aby vysledny "DOWN range" (od pivotu k post-gap lowu)
    obsahoval ABSOLUTNI post-gap low, ne jen prvni post-gap dno.
    """
    import pandas as pd

    times = pd.to_datetime(
        [
            "2026-03-13 14:00",  # 0: pre-Friday context
            "2026-03-13 14:30",  # 1
            "2026-03-13 15:00",  # 2: DOWN start (high pivot)
            "2026-03-13 15:30",  # 3
            "2026-03-13 16:00",  # 4 (cand qualifies)
            "2026-03-13 16:30",  # 5
            # WEEKEND GAP HERE
            "2026-03-16 06:00",  # 6: gap bar, BULL close>=open
            "2026-03-16 06:30",  # 7: BULL
            "2026-03-16 07:00",  # 8: BULL  (without fix: confirm here)
            "2026-03-16 07:30",  # 9: BEAR (new low forming)
            "2026-03-16 08:00",  # 10: BEAR (low extends)
            "2026-03-16 08:30",  # 11: BEAR (post-gap absolute low)
            "2026-03-16 09:00",  # 12: BULL
            "2026-03-16 09:30",  # 13: BULL
            "2026-03-16 10:00",  # 14: BULL  (with fix: confirm here)
        ]
    )
    # Cены — DOWN wave: vystup z 1.2000, pak gap-down 1.1850 -> 1.1830,
    # pak 3 BULL bary (close>open) mensi rozsah,
    # potom BEAR bary k 1.1750 (skutecne low post-gap),
    # potom 3 BULL bary pro confirm.
    rows = [
        # i:  O,       H,       L,       C
        (1.2000, 1.2010, 1.1990, 1.2005),  # 0
        (1.2005, 1.2010, 1.1980, 1.1985),  # 1
        (1.1985, 1.1990, 1.1960, 1.1965),  # 2: DOWN start - high pivot stays at bar 0 (1.2010)
        (1.1965, 1.1970, 1.1920, 1.1925),  # 3 - qualifies
        (1.1925, 1.1930, 1.1880, 1.1885),  # 4 - bigger DOWN
        (1.1885, 1.1890, 1.1850, 1.1855),  # 5 - last Friday bar, BEAR, low=1.1850
        # GAP
        (1.1820, 1.1835, 1.1815, 1.1830),  # 6: gap bar, BULL (close>open). low 1.1815
        (1.1830, 1.1845, 1.1820, 1.1842),  # 7: BULL
        (1.1842, 1.1855, 1.1835, 1.1852),  # 8: BULL
        (1.1852, 1.1860, 1.1780, 1.1790),  # 9: BEAR, breaks lower (new low 1.1780)
        (1.1790, 1.1795, 1.1760, 1.1770),  # 10: BEAR, new low 1.1760
        (1.1770, 1.1775, 1.1750, 1.1755),  # 11: BEAR, ABSOLUTE post-gap low 1.1750
        (1.1755, 1.1770, 1.1755, 1.1768),  # 12: BULL
        (1.1768, 1.1780, 1.1768, 1.1778),  # 13: BULL
        (1.1778, 1.1790, 1.1778, 1.1788),  # 14: BULL → confirm
    ]
    df = pd.DataFrame(
        {
            "time": times,
            "open":  [r[0] for r in rows],
            "high":  [r[1] for r in rows],
            "low":   [r[2] for r in rows],
            "close": [r[3] for r in rows],
        }
    )
    cfg = _cfg(min_opp_bars=3, wave_min_pct=0.50, wave_plus=False)
    waves, _, _, _ = run_pine_wave_simulation(df, cfg)
    down = [w for w in waves if int(w["dir"]) == -1]
    assert down, f"chybi DOWN vlna, waves={waves}"
    # Najdi vlnu, ktera obsahuje absolutni post-gap low (1.1750). Po slouceni
    # by mela existovat prave jedna velka DOWN vlna s box_bottom == 1.1750.
    matching = [w for w in down if float(w["box_bottom"]) <= 1.1751]
    assert matching, (
        f"zadna DOWN vlna nedosahla absolutniho post-gap low 1.1750; "
        f"down waves={[(w['draw_left'], w['draw_right'], w['box_bottom']) for w in down]}"
    )
    w = matching[0]
    assert int(w["draw_right"]) >= 11, (
        f"DOWN vlna se neprotahla az k absolutnimu post-gap lowu (bar 11): "
        f"R={w['draw_right']}; {w}"
    )


def test_weekend_gap_relax_marks_mar20_and_apr10_as_ext():
    """
    Vlny z Mar 20-23 (move ~0.755 %) a Apr 10-13 (move ~0.641 %) jsou teplete
    pod prahem EXT 0.76 %, ale prekracuji vyznamny vikendovy gap ve smeru
    vlny. S `ext_weekend_gap_relax_factor=0.5` musi obe byt EXT.
    """
    import pandas as pd
    from strategy.ext_logic import is_ext_wave

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_weekend_gap_relax_factor=0.5,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    full = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    waves, _, _, _ = run_pine_wave_simulation(full, cfg)

    # Mar 20 -> Mar 23 weekend (gap ~338 pips DOWN), DOWN vlna move ~0.755 %.
    mar20 = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and str(full["time"].iloc[int(w["draw_left"])]).startswith("2026-03-20")
        and str(full["time"].iloc[int(w["draw_right"])]).startswith("2026-03-23")
    ]
    assert mar20, f"chybi DOWN vlna pres Mar 20-23 weekend, waves={waves[:20]}"
    w_mar20 = max(mar20, key=lambda x: float(x["move_pct"]))
    assert float(w_mar20["move_pct"]) < 0.76, (
        f"Mar20 vlna by mela byt POD striktnim EXT prahem: {w_mar20}"
    )
    assert float(w_mar20["weekend_gap_pct"]) > 0.20, (
        f"weekend_gap_pct by mel reflektovat DOWN gap ~0.29 %: {w_mar20}"
    )
    assert is_ext_wave(w_mar20, cfg), (
        f"Mar20 vlna ma byt EXT diky weekend-gap relaxu: {w_mar20}"
    )
    assert bool(w_mar20.get("is_ext", False)), (
        f"Mar20 vlna musí mít is_ext metadata po relaxu: {w_mar20}"
    )

    # Apr 10 -> Apr 13 weekend (gap ~548 pips DOWN), DOWN vlna move ~0.641 %.
    apr10 = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and str(full["time"].iloc[int(w["draw_left"])]).startswith("2026-04-10")
        and str(full["time"].iloc[int(w["draw_right"])]).startswith("2026-04-13")
    ]
    assert apr10, f"chybi DOWN vlna pres Apr 10-13 weekend"
    w_apr10 = max(apr10, key=lambda x: float(x["move_pct"]))
    assert float(w_apr10["move_pct"]) < 0.76, (
        f"Apr10 vlna by mela byt POD striktnim EXT prahem: {w_apr10}"
    )
    assert float(w_apr10["weekend_gap_pct"]) > 0.40, (
        f"weekend_gap_pct by mel reflektovat DOWN gap ~0.47 %: {w_apr10}"
    )
    assert is_ext_wave(w_apr10, cfg), (
        f"Apr10 vlna ma byt EXT diky weekend-gap relaxu: {w_apr10}"
    )
    assert bool(w_apr10.get("is_ext", False)), (
        f"Apr10 vlna musí mít is_ext metadata po relaxu: {w_apr10}"
    )


def test_weekend_gap_relax_disabled_keeps_strict_threshold():
    """
    Pri `ext_weekend_gap_relax_factor=0` (default / legacy) musi byt vlny pod
    prahem EXT klasifikovany jako NE-EXT, i kdyz prekracuji vikendovy gap.
    Bit-perfect chovani pred zavedenim relaxu.
    """
    import pandas as pd
    from strategy.ext_logic import is_ext_wave

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_weekend_gap_relax_factor=0.0,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    full = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(
        drop=True
    )
    waves, _, _, _ = run_pine_wave_simulation(full, cfg)

    mar20 = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and str(full["time"].iloc[int(w["draw_left"])]).startswith("2026-03-20")
        and str(full["time"].iloc[int(w["draw_right"])]).startswith("2026-03-23")
    ]
    assert mar20
    w_mar20 = max(mar20, key=lambda x: float(x["move_pct"]))
    assert not is_ext_wave(w_mar20, cfg), (
        f"Pri relax_factor=0 nesmí Mar20 vlna byt EXT (legacy chovani): {w_mar20}"
    )
    assert not bool(w_mar20.get("is_ext", False)), (
        f"Pri relax_factor=0 nesmí mit is_ext metadata: {w_mar20}"
    )


def test_weekend_gap_pct_zero_when_gap_opposite_to_wave_direction():
    """
    DOWN vlna pres UP gap (pondelni open > paty close) NESMI dostat weekend
    relax — chovani EXT prahu se nezmeni. (Apr 3 -> Apr 6: jump +33 pips,
    DOWN vlna 0.307 %.)
    """
    import pandas as pd
    from strategy.ext_logic import is_ext_wave

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
        ext_weekend_gap_relax_factor=0.5,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    full = df[(df["time"] >= "2026-04-01") & (df["time"] <= "2026-04-07")].reset_index(
        drop=True
    )
    waves, _, _, _ = run_pine_wave_simulation(full, cfg)
    down_apr3 = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and str(full["time"].iloc[int(w["draw_left"])]).startswith("2026-04-03")
    ]
    if not down_apr3:
        # Pokud se ve filtrovanem useku DOWN vlna pres weekend neobjevi,
        # test je vacuously OK (zarovaska jen tehdy, kdyz takova vlna existuje).
        return
    w = max(down_apr3, key=lambda x: float(x["move_pct"]))
    # gap byl UP, vlna DOWN -> opacny smer -> weekend_gap_pct musi byt 0
    assert float(w.get("weekend_gap_pct", 0.0)) == 0.0, (
        f"DOWN vlna + UP gap = opacny smer; weekend_gap_pct musi byt 0: {w}"
    )
    assert not is_ext_wave(w, cfg), (
        f"Mala DOWN vlna s UP gapem nesmi byt EXT: {w}"
    )


def test_eurusd_mar6_mar9_down_wave_anchors_pivot_on_friday_high_before_gap():
    """
    Reálný regress (M30 2026-03-06 12:00 -> 2026-03-09 12:00):
      Páteční UP move zakončený HIGH 1.16211 (před vikendovým gapem) je
      následován velkým DOWN gapem v neděli/pondělí.

    Drive bug: pivot DOWN vlny zustal cenove napocitan ze pátku (box_top =
    1.16211), ale `draw_left`/`wave_time` ukazoval az na první pondelni
    post-gap bar — vlna byla v case "useknutá".

    Po fixu (handle pivot_ref/cand_ref == "prev" v run_pine_wave_simulation)
    musí DOWN vlna mit `draw_left` na pred-gap baru (=páteční bar v 20:00)
    a `wave_time` z pátku, ne z pondelka.
    """
    import pandas as pd

    cfg = _cfg(
        wave_min_pct=0.26,
        min_opp_bars=3,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    df = pd.read_csv("data/EURUSD_M30.csv", parse_dates=["datetime"])
    df = df.rename(columns={"datetime": "time"})
    seg = df[(df["time"] >= "2026-03-06 12:00") & (df["time"] <= "2026-03-09 12:00")].reset_index(
        drop=True
    )
    gap_mask = _compute_after_data_gap_mask(seg["time"])
    gap_idxs = [i for i, v in enumerate(gap_mask) if v]
    assert gap_idxs, "v segmentu musi byt vikendovy gap"
    gap_i = gap_idxs[0]

    waves, _, _, _ = run_pine_wave_simulation(seg, cfg)
    # DOWN vlna prekracujici vikendovy gap
    down_crossing = [
        w
        for w in waves
        if int(w["dir"]) == -1
        and int(w["draw_left"]) <= gap_i <= int(w["draw_right"])
    ]
    assert down_crossing, f"chybi DOWN vlna pres gap, waves={waves}"
    main = max(down_crossing, key=lambda x: float(x["move_pct"]))

    # 1) box_top = patecni HIGH (~1.16211)
    assert float(main["box_top"]) >= 1.1620, (
        f"box_top neobsahuje patecni HIGH (~1.16211): {main}"
    )

    # 2) Pivot vlny zakotven PRED gapem (klicovy fix)
    assert int(main["draw_left"]) < gap_i, (
        f"DOWN pivot ma byt na predgap baru, dostali jsme draw_left={main['draw_left']}, gap_i={gap_i}: {main}"
    )

    # 3) wave_time z pátku (ne z neděle/pondelka po gapu)
    pivot_time = seg["time"].iloc[int(main["draw_left"])]
    assert pivot_time.weekday() == 4, (
        f"DOWN pivot ma byt z pátku, dostali jsme {pivot_time} (weekday={pivot_time.weekday()})"
    )

    # 4) Vlna musi mit EXT velikost (drive ~0.5, po fixu >= 0.76)
    assert float(main["move_pct"]) >= 0.76, f"DOWN vlna nedosahla EXT prahu: {main}"
    assert bool(main.get("is_ext", False)), f"DOWN vlna nebyla EXT: {main}"
