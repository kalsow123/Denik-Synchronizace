"""Test: post-detection cleanup pro wick-invalidovanou korekci.

Scenar:
  Mezi dvema trend-vlnami stejneho smeru (A, C) lezi korekce B.
  Po B-confirmation prijde bar, jehoz wick prekroci B-extrem (= BOS line),
  ale CLOSE ho neprekroci. C pak v trendu pokracuje za B-extrem (nove low/high).
  -> B je sum; ma byt odstranena a C pohlti celou "NIC" zonu.

  Funkce NESMI zasahnout:
   - EXT korekce (`is_ext`)
   - normalni triplety bez wicku
   - triplety, kde C neprekroci B-extrem (nepokracovani trendu)
"""
from __future__ import annotations

import pandas as pd

from config.bot_config import BotConfig
from strategy.wave_detection_pine import (
    _remove_wick_invalidated_corrections,
    run_pine_wave_simulation,
)


def _cfg(**kwargs) -> BotConfig:
    base = dict(
        wave_min_pct=0.26,
        min_opp_bars=3,
        entry_fib_level=0.5,
        sl_fib_level=0.8,
        rrr=2.0,
        wave_plus=True,
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    base.update(kwargs)
    return BotConfig(**base)


def _make_wave(
    *,
    dir_: int,
    wave_time: str,
    draw_left: int,
    draw_right: int,
    box_top: float,
    box_bottom: float,
    is_ext: bool = False,
) -> dict:
    return {
        "dir": dir_,
        "wave_time": wave_time,
        "draw_left": draw_left,
        "draw_right": draw_right,
        "box_top": box_top,
        "box_bottom": box_bottom,
        "fib50": (box_top + box_bottom) / 2.0,
        "sl": box_top - (box_top - box_bottom) * 0.8 if dir_ == 1 else box_bottom + (box_top - box_bottom) * 0.8,
        "tp": 0.0,
        "move_pct": abs(box_top - box_bottom) / max(1e-12, box_bottom) * 100.0,
        "is_ext": is_ext,
    }


def test_wick_invalidated_up_correction_removed_in_down_trend():
    """DOWN trend: UP korekce wicknuta nad svuj box_top bez close. DOWN pokracuje na nove low."""
    times = pd.to_datetime(
        [f"2026-03-10 {h:02d}:{m:02d}" for h in range(0, 24) for m in (0, 30)]
    )
    df = pd.DataFrame({"time": times})
    n = len(df)

    base_o = 1.16
    df["open"] = [base_o] * n
    df["high"] = [base_o + 0.0001] * n
    df["low"] = [base_o - 0.0001] * n
    df["close"] = [base_o] * n

    # A: DOWN trend wave bary 0..6 (1.1700 -> 1.1640), close DOWN po dropu
    a_prices = [1.1700, 1.1690, 1.1680, 1.1660, 1.1650, 1.1645, 1.1640]
    for i, p in enumerate(a_prices):
        df.at[i, "open"] = p + 0.0001
        df.at[i, "high"] = p + 0.0003
        df.at[i, "low"] = p - 0.0003
        df.at[i, "close"] = p - 0.0001

    # B: UP korekce bary 7..12 (1.1640 -> 1.1670), box_top ~ 1.1672
    b_prices = [1.1645, 1.1655, 1.1665, 1.1670, 1.1668, 1.1666]
    for j, p in enumerate(b_prices):
        i = 7 + j
        df.at[i, "open"] = p - 0.0001
        df.at[i, "high"] = p + 0.0002
        df.at[i, "low"] = p - 0.0002
        df.at[i, "close"] = p + 0.0001

    # WICK BAR 13: high pres 1.1672, ale close pod 1.1672 (NENI BOS, JEN KNOT)
    df.at[13, "open"] = 1.1666
    df.at[13, "high"] = 1.1685
    df.at[13, "low"] = 1.1660
    df.at[13, "close"] = 1.1664

    # Bary 14..23: pokracovani DOWN na nove low (pod B.box_bottom = ~1.1638)
    cont_prices = [1.1660, 1.1640, 1.1620, 1.1600, 1.1580, 1.1560, 1.1540, 1.1530, 1.1525, 1.1522]
    for j, p in enumerate(cont_prices):
        i = 14 + j
        df.at[i, "open"] = p + 0.0001
        df.at[i, "high"] = p + 0.0003
        df.at[i, "low"] = p - 0.0003
        df.at[i, "close"] = p - 0.0001

    # Pripravime umelou waves listu, kterou by simulator vratil bez fixu.
    waves = [
        _make_wave(
            dir_=-1, wave_time="A", draw_left=0, draw_right=6,
            box_top=1.1703, box_bottom=1.1637,
        ),
        _make_wave(
            dir_=1, wave_time="B", draw_left=7, draw_right=12,
            box_top=1.1672, box_bottom=1.1643,
        ),
        _make_wave(
            dir_=-1, wave_time="C", draw_left=14, draw_right=23,
            box_top=1.1670, box_bottom=1.1519,
        ),
    ]
    # B-confirmation pred wick barem (13), C-confirmation pozdeji.
    birth = {"A": 8, "B": 12, "C": 21}

    cfg = _cfg()
    out, new_birth = _remove_wick_invalidated_corrections(df, cfg, waves, birth)

    # B musi byt odstranena
    assert len(out) == 2, f"ocekavany 2 vlny po cleanup, mam {len(out)}: {out}"
    assert "B" not in new_birth
    a = out[0]
    c = out[1]
    assert a["wave_time"] == "A"
    assert c["wave_time"] == "C"
    # C absorbuje NIC zonu: draw_left = B.draw_left = 7
    assert int(c["draw_left"]) == 7
    # box_top obsahuje wick high (1.1685)
    assert float(c["box_top"]) >= 1.1685 - 1e-9
    # box_bottom je puvodni C low
    assert float(c["box_bottom"]) <= 1.1519 + 1e-9
    # Smer nezmenen
    assert int(c["dir"]) == -1


def test_wick_invalidated_down_correction_removed_in_up_trend():
    """UP trend: DOWN korekce wicknuta pod svuj box_bottom bez close. UP pokracuje na nove high."""
    times = pd.to_datetime(
        [f"2026-03-10 {h:02d}:{m:02d}" for h in range(0, 24) for m in (0, 30)]
    )
    df = pd.DataFrame({"time": times})
    n = len(df)
    base = 1.16
    df["open"] = [base] * n
    df["high"] = [base + 0.0001] * n
    df["low"] = [base - 0.0001] * n
    df["close"] = [base] * n

    # A: UP wave 1.1500 -> 1.1560 bary 0..6
    a_prices = [1.1500, 1.1510, 1.1520, 1.1540, 1.1550, 1.1555, 1.1560]
    for i, p in enumerate(a_prices):
        df.at[i, "open"] = p - 0.0001
        df.at[i, "high"] = p + 0.0003
        df.at[i, "low"] = p - 0.0003
        df.at[i, "close"] = p + 0.0001

    # B: DOWN korekce 1.1555 -> 1.1530 bary 7..12, box_bottom ~ 1.1528
    b_prices = [1.1555, 1.1545, 1.1535, 1.1530, 1.1532, 1.1534]
    for j, p in enumerate(b_prices):
        i = 7 + j
        df.at[i, "open"] = p + 0.0001
        df.at[i, "high"] = p + 0.0002
        df.at[i, "low"] = p - 0.0002
        df.at[i, "close"] = p - 0.0001

    # WICK BAR 13: low pod 1.1528, ale close nad 1.1528
    df.at[13, "open"] = 1.1534
    df.at[13, "high"] = 1.1538
    df.at[13, "low"] = 1.1515
    df.at[13, "close"] = 1.1535

    # Bary 14..23: pokracovani UP na nove high
    cont_prices = [1.1540, 1.1560, 1.1580, 1.1600, 1.1620, 1.1640, 1.1660, 1.1680, 1.1685, 1.1688]
    for j, p in enumerate(cont_prices):
        i = 14 + j
        df.at[i, "open"] = p - 0.0001
        df.at[i, "high"] = p + 0.0003
        df.at[i, "low"] = p - 0.0003
        df.at[i, "close"] = p + 0.0001

    waves = [
        _make_wave(
            dir_=1, wave_time="A", draw_left=0, draw_right=6,
            box_top=1.1563, box_bottom=1.1497,
        ),
        _make_wave(
            dir_=-1, wave_time="B", draw_left=7, draw_right=12,
            box_top=1.1557, box_bottom=1.1528,
        ),
        _make_wave(
            dir_=1, wave_time="C", draw_left=14, draw_right=23,
            box_top=1.1691, box_bottom=1.1532,
        ),
    ]
    birth = {"A": 8, "B": 12, "C": 21}

    cfg = _cfg()
    out, new_birth = _remove_wick_invalidated_corrections(df, cfg, waves, birth)

    assert len(out) == 2, f"ocekavany 2 vlny po cleanup, mam {len(out)}: {out}"
    assert "B" not in new_birth
    c = out[1]
    assert int(c["draw_left"]) == 7
    # box_bottom musi zachytit wick low (1.1515)
    assert float(c["box_bottom"]) <= 1.1515 + 1e-9
    assert int(c["dir"]) == 1


def test_no_cleanup_when_no_wick():
    """Triplet bez wicku zustane netknuty."""
    times = pd.to_datetime(
        [f"2026-03-10 {h:02d}:{m:02d}" for h in range(0, 24) for m in (0, 30)]
    )
    df = pd.DataFrame(
        {
            "time": times,
            "open": [1.16] * len(times),
            "high": [1.1601] * len(times),
            "low": [1.1599] * len(times),
            "close": [1.16] * len(times),
        }
    )
    waves = [
        _make_wave(
            dir_=-1, wave_time="A", draw_left=0, draw_right=6,
            box_top=1.1700, box_bottom=1.1640,
        ),
        _make_wave(
            dir_=1, wave_time="B", draw_left=7, draw_right=12,
            box_top=1.1670, box_bottom=1.1645,
        ),
        _make_wave(
            dir_=-1, wave_time="C", draw_left=13, draw_right=23,
            box_top=1.1670, box_bottom=1.1610,
        ),
    ]
    birth = {"A": 8, "B": 12, "C": 21}
    out, _ = _remove_wick_invalidated_corrections(df, _cfg(), waves, birth)
    assert len(out) == 3, "bez wicku se nesmi nic odstranit"


def test_no_cleanup_when_c_does_not_extend_past_b():
    """Triplet kde C neudela nove low/high zustane netknuty."""
    times = pd.to_datetime(
        [f"2026-03-10 {h:02d}:{m:02d}" for h in range(0, 24) for m in (0, 30)]
    )
    df = pd.DataFrame({"time": times})
    n = len(df)
    df["open"] = [1.16] * n
    df["high"] = [1.1601] * n
    df["low"] = [1.1599] * n
    df["close"] = [1.16] * n

    # Wick bar 13 nad B.box_top (1.1672)
    df.at[13, "open"] = 1.1666
    df.at[13, "high"] = 1.1685
    df.at[13, "low"] = 1.1660
    df.at[13, "close"] = 1.1664

    waves = [
        _make_wave(
            dir_=-1, wave_time="A", draw_left=0, draw_right=6,
            box_top=1.1700, box_bottom=1.1640,
        ),
        _make_wave(
            dir_=1, wave_time="B", draw_left=7, draw_right=12,
            box_top=1.1672, box_bottom=1.1640,
        ),
        # C koncí na 1.1640 = stejne low jako B.box_bottom (NEPRESAHL)
        _make_wave(
            dir_=-1, wave_time="C", draw_left=14, draw_right=23,
            box_top=1.1670, box_bottom=1.1640,
        ),
    ]
    birth = {"A": 8, "B": 12, "C": 21}
    out, _ = _remove_wick_invalidated_corrections(df, _cfg(), waves, birth)
    assert len(out) == 3, "bez pokracovani trendu se nesmi nic odstranit"


def test_ext_correction_is_never_removed():
    """EXT vlna nikdy nesmi byt odstranena, i kdyby ostatni podminky sedeli."""
    times = pd.to_datetime(
        [f"2026-03-10 {h:02d}:{m:02d}" for h in range(0, 24) for m in (0, 30)]
    )
    df = pd.DataFrame({"time": times})
    n = len(df)
    df["open"] = [1.16] * n
    df["high"] = [1.1601] * n
    df["low"] = [1.1599] * n
    df["close"] = [1.16] * n
    df.at[13, "high"] = 1.1685
    df.at[13, "close"] = 1.1664

    waves = [
        _make_wave(
            dir_=-1, wave_time="A", draw_left=0, draw_right=6,
            box_top=1.1700, box_bottom=1.1640,
        ),
        _make_wave(
            dir_=1, wave_time="B_EXT", draw_left=7, draw_right=12,
            box_top=1.1672, box_bottom=1.1640, is_ext=True,
        ),
        _make_wave(
            dir_=-1, wave_time="C", draw_left=14, draw_right=23,
            box_top=1.1670, box_bottom=1.1500,
        ),
    ]
    birth = {"A": 8, "B_EXT": 12, "C": 21}
    out, _ = _remove_wick_invalidated_corrections(df, _cfg(), waves, birth)
    assert len(out) == 3
    assert any(w["wave_time"] == "B_EXT" for w in out)


def test_real_data_fix_activates_and_is_surgical():
    """3 mesice EURUSD M30: post-detection cleanup nesmi byt no-op (= scenare existuji)
    a soucasne nesmi drasticky zredukovat pocet vln (= je dostatecne uzky).
    """
    import os

    csv_path = "data/EURUSD.x_M30.csv"
    if not os.path.exists(csv_path):
        return

    cfg = _cfg(wave_plus=True)
    df = pd.read_csv(csv_path, parse_dates=["datetime"]).rename(columns={"datetime": "time"})
    df = df[(df["time"] >= "2026-03-03") & (df["time"] <= "2026-05-10")].reset_index(drop=True)

    # 1) Reseni s fixem
    waves_with, _, _, _ = run_pine_wave_simulation(df, cfg)

    # 2) Disable fixu pres monkey-patch
    import strategy.wave_detection_pine as wp
    original = wp._remove_wick_invalidated_corrections
    wp._remove_wick_invalidated_corrections = lambda d, c, w, b: (w, b)
    try:
        waves_no, _, _, _ = run_pine_wave_simulation(df, cfg)
    finally:
        wp._remove_wick_invalidated_corrections = original

    removed = len(waves_no) - len(waves_with)
    # Fix nesmi byt no-op (na 3 mesicich M30 existuji scenare)
    assert removed >= 1, f"fix nebyl aktivovan ani jednou: {removed}"
    # Fix musi byt chirurgicky — nesmi smazat vic nez ~10 % vln
    assert removed <= max(10, int(0.1 * len(waves_no))), (
        f"fix odstranil prilis mnoho vln ({removed}/{len(waves_no)})"
    )
    # Po fixu zustanou alespon 80 % vln
    assert len(waves_with) >= 0.9 * len(waves_no), (
        f"po fixu zustalo prilis malo vln: {len(waves_with)}/{len(waves_no)}"
    )

    # Vsechny odstranene vlny musi mit move_pct pod ext_wave_min_pct (= nejsou EXT)
    times_with = {w["wave_time"] for w in waves_with}
    removed_waves = [w for w in waves_no if w["wave_time"] not in times_with]
    for rw in removed_waves:
        assert not bool(rw.get("is_ext", False)), f"odstranena EXT vlna: {rw}"
        assert float(rw.get("move_pct", 0)) < float(cfg.ext_wave_min_pct), (
            f"odstranena vlna nad ext_wave_min_pct: {rw}"
        )
