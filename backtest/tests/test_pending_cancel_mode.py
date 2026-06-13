"""
Testy pro funkci `pending_cancel_mode` + EXT WAVE pending protection.

Pokryti:
  - "number" → vsechny pendingy expiruji po `pending_cancel_after_days` dnech,
                 zadne rusi na BOS
  - "trend"  → vsechny pendingy se rusi na BOS flipu (i v RRR_FIXED)
  - EXT WAVE pending → trvale chranene pred kazdym cancel mechanismem
  - EXT WAVE expirace → pouziva `ext_order_expiry_days` (default 7)
"""
from __future__ import annotations

from datetime import datetime

from backtest.engine import BacktestEngine, PendingOrder
from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import PendingCancelMode, TPMode


def _cfg(**overrides) -> dict:
    base = dict(
        timeframe="M30",
        wave_min_pct=0.26,
        min_opp_bars=3,
        rrr=2.0,
        fib_level=0.5,
        entry_mode="market_fallback",
        symbol="EURUSD.x",
        sl_fib_level=0.8,
        wave_plus=True,
        risk_usd=500.0,
        contract_size=100_000.0,
        order_expiry_days=14,
        ext_order_expiry_days=7,
        pending_cancel_mode="number",
        pending_cancel_after_days=14,
        tp_mode="rrr_fixed",
    )
    base.update(overrides)
    return grid_dict_to_bot_config(base)


def _make_pending(wave_time: str, dir_: int, *, ep: float = 1.1, sl: float = 1.099,
                  created_time: datetime,
                  is_ext: bool = False, is_counter: bool = False,
                  is_pp: bool = False, is_two_sided: bool = False) -> PendingOrder:
    sig = {"wave_time": wave_time, "dir": dir_, "fib50": ep, "sl": sl}
    return PendingOrder(
        signal=sig,
        order_type=("BUY_LIMIT" if dir_ == 1 else "SELL_LIMIT"),
        entry_price=ep,
        sl=sl,
        tp=None,
        lot=0.01,
        created_bar=0,
        created_time=created_time,
        is_counter=is_counter,
        is_pp=is_pp,
        is_two_sided_mirror=is_two_sided,
        is_ext=is_ext,
        entry_tag=("ext_secondary" if is_ext else "base"),
    )


# ──────────────────────────────────────────────────────────────────────────
# EXT WAVE pending: NIKDY se neztrati ani BOS-flipem, ani TP-wave eventem
# ──────────────────────────────────────────────────────────────────────────


def test_ext_pending_survives_bos_flip_in_bos_exit_mode():
    cfg = _cfg(tp_mode="bos_exit", pending_cancel_mode="trend")
    eng = BacktestEngine(cfg)
    # bull-trend kontext, EXT pending v BEAR smeru (= proti trendu)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    ext_p = _make_pending(
        "wt_ext", dir_=1,  # BUY_LIMIT (broken_dir=+1 v bear-trendu)
        created_time=datetime(2026, 5, 1, 10, 0),
        is_ext=True,
    )
    eng.pending_orders = [ext_p]
    # Vyvolame BOS flip: bull → bear, broken_dir = +1
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=True,
        cancel_pendings=True,
    )
    assert ext_p in eng.pending_orders, (
        "EXT pending musi prezit BOS flip i v bos_exit modu"
    )


def test_ext_pending_survives_bos_flip_in_pending_cancel_mode_trend():
    cfg = _cfg(tp_mode="rrr_fixed", pending_cancel_mode="trend")
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    ext_p = _make_pending(
        "wt_ext", dir_=1, is_ext=True,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    # Obycejny WAVE pending (broken_dir) — musi byt zruseny
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [ext_p, wave_p]
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=False,  # RRR_FIXED nezavre pozice
        cancel_pendings=True,
    )
    assert ext_p in eng.pending_orders
    assert wave_p not in eng.pending_orders


# ──────────────────────────────────────────────────────────────────────────
# pending_cancel_mode = "number" → BOS flip pendingy NErusi
# ──────────────────────────────────────────────────────────────────────────


def test_pending_cancel_mode_number_does_not_cancel_on_bos():
    """
    Pri pending_cancel_mode="number" se v BOS_EXIT modu pendingy NErusi BOS flipem
    (jen casem). To je hlavni rozdil oproti "trend"+BOS_EXIT.
    """
    cfg = _cfg(tp_mode="bos_exit", pending_cancel_mode="number")
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [wave_p]
    # Test: kdyz cancel_pendings=False (= co dela run() pri pcm=NUMBER),
    # pending zustane i pri BOS flipu.
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=True,
        cancel_pendings=False,
    )
    assert wave_p in eng.pending_orders


# ──────────────────────────────────────────────────────────────────────────
# pending_cancel_mode = "trend" v RRR_FIXED → pendingy se rusi na BOS
# ──────────────────────────────────────────────────────────────────────────


def test_pending_cancel_mode_trend_cancels_on_bos_in_rrr_fixed():
    """
    Pri pending_cancel_mode="trend" se v RRR_FIXED modu pendingy proti
    novemu trendu rusi BOS flipem — i kdyz RRR_FIXED jinak BOS-cancellation
    nepouziva.
    """
    cfg = _cfg(tp_mode="rrr_fixed", pending_cancel_mode="trend")
    assert eng_resolves_pcm(cfg, PendingCancelMode.TREND)
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [wave_p]
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=False,  # RRR_FIXED neuzaviraini pozic
        cancel_pendings=True,   # ale rusime pendingy
    )
    assert wave_p not in eng.pending_orders


def eng_resolves_pcm(cfg, expected: PendingCancelMode) -> bool:
    eng = BacktestEngine(cfg)
    return eng._pending_cancel_mode == expected


# ──────────────────────────────────────────────────────────────────────────
# Expirace per pending: ext / number / default
# ──────────────────────────────────────────────────────────────────────────


def test_primary_ext_pending_from_add_pending_survives_bos_flip():
    """Primarni fib LIMIT na EXT vlne musi mit is_ext=True (entry_tag=base)."""
    cfg = _cfg(
        tp_mode="rrr_fixed",
        pending_cancel_mode="trend",
        ext_enabled=True,
        ext_wave_min_pct=0.76,
    )
    eng = BacktestEngine(cfg)
    eng.wave_debug = {"orders_created_pending": 0}
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bull"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    ext_wave = {
        "wave_time": "202605011200",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "tp": 1.12,
        "move_pct": 1.0,
        "box_top": 1.2,
        "box_bottom": 1.0,
    }
    eng._add_pending(
        ext_wave, "BUY_LIMIT", 1.1, 1.09, 1.12, 0.01, 0,
        datetime(2026, 5, 1, 12, 0),
    )
    assert len(eng.pending_orders) == 1
    assert eng.pending_orders[0].is_ext is True
    assert eng.pending_orders[0].entry_tag == "base"
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders.append(wave_p)
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=False,
        cancel_pendings=True,
    )
    assert eng.pending_orders[0].is_ext is True
    assert wave_p not in eng.pending_orders


def test_expire_pending_uses_ext_limit_for_ext_pending():
    """
    EXT WAVE pending pouziva `ext_order_expiry_days` (default 7), ne
    `order_expiry_days` (default 14).
    """
    cfg = _cfg(order_expiry_days=14, ext_order_expiry_days=2)
    eng = BacktestEngine(cfg)
    # EXT pending 3 dny stary — pri ext_limit=2 dny musi expirovat
    ext_p = _make_pending(
        "wt_ext", dir_=1, is_ext=True,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    # Normalni pending 3 dny stary — order_expiry_days=14 → drzime
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [ext_p, wave_p]
    # Cas o 3 dny pozdeji
    eng._expire_pending(bar_idx=100, bar_time=datetime(2026, 5, 6, 10, 0))
    # Vikend Sa 2.5. + Ne 3.5. se nezapocita (business_time_delta) → realne 3 dny
    assert ext_p not in eng.pending_orders, "EXT pending mel expirovat po 3 dnech (limit=2)"
    assert wave_p in eng.pending_orders, "Normalni pending nemel expirovat (limit=14)"


def test_expire_pending_uses_number_limit_when_pcm_number():
    """
    pending_cancel_mode="number" pouzije `pending_cancel_after_days` pro
    vsechny non-EXT pendingy (nezavisle na tp_mode / order_expiry_days).
    """
    cfg = _cfg(
        order_expiry_days=14,
        pending_cancel_mode="number",
        pending_cancel_after_days=2,
    )
    eng = BacktestEngine(cfg)
    wave_p = _make_pending(
        "wt_wave", dir_=1, is_ext=False,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [wave_p]
    eng._expire_pending(bar_idx=100, bar_time=datetime(2026, 5, 6, 10, 0))
    assert wave_p not in eng.pending_orders, (
        "pcm=number → pending mel expirovat po 3 business dnech (limit=2)"
    )


def test_counter_and_pp_pendings_never_expire_by_time():
    """
    Counter / two-sided / PP pendingy nikdy neexpiruji timeoutem — jen na BOS flipu
    (resp. specifickou logikou). Toto chovani plati ve vsech pending_cancel_mode.
    """
    cfg = _cfg(order_expiry_days=1, pending_cancel_mode="number",
               pending_cancel_after_days=1)
    eng = BacktestEngine(cfg)
    counter_p = _make_pending(
        "wt_c", dir_=1, is_counter=True,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    pp_p = _make_pending(
        "wt_p", dir_=1, is_pp=True,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    two_sided_p = _make_pending(
        "wt_t", dir_=1, is_two_sided=True,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.pending_orders = [counter_p, pp_p, two_sided_p]
    eng._expire_pending(bar_idx=100, bar_time=datetime(2026, 5, 31, 10, 0))
    assert counter_p in eng.pending_orders
    assert pp_p in eng.pending_orders
    assert two_sided_p in eng.pending_orders


# ──────────────────────────────────────────────────────────────────────────
# EXT range W-pending: ochrana pred BOS broken_dir cancel
# ──────────────────────────────────────────────────────────────────────────


def test_ext_range_pending_survives_bos_broken_dir_cancel():
    cfg = _cfg(
        tp_mode="rrr_fixed",
        pending_cancel_mode="trend",
        ext_enabled=True,
        ext_trade_both_sides_in_range=True,
        ext_range_protect_pendings_from_bos_cancel=True,
    )
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bear"})(),
        type("S", (), {"direction": "bear"})(),
    ]
    ext_range_p = _make_pending(
        "wt_ext_range", dir_=1,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    normal_p = _make_pending(
        "wt_normal", dir_=1,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.waves_by_wave_time = {
        "wt_ext_range": {"wave_time": "wt_ext_range", "dir": 1, "in_ext_range": True},
        "wt_normal": {"wave_time": "wt_normal", "dir": 1, "in_ext_range": False},
    }
    eng.pending_orders = [ext_range_p, normal_p]
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 30),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=False,
        cancel_pendings=True,
    )
    assert ext_range_p in eng.pending_orders
    assert normal_p not in eng.pending_orders
    assert eng.wave_debug.get("ext_range_pending_bos_cancel_skipped", 0) == 1


def test_ext_range_pending_not_protected_when_flag_disabled():
    cfg = _cfg(
        tp_mode="rrr_fixed",
        pending_cancel_mode="trend",
        ext_enabled=True,
        ext_trade_both_sides_in_range=True,
        ext_range_protect_pendings_from_bos_cancel=False,
    )
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bear"})(),
    ]
    ext_range_p = _make_pending(
        "wt_ext_range", dir_=1,
        created_time=datetime(2026, 5, 1, 10, 0),
    )
    eng.waves_by_wave_time = {
        "wt_ext_range": {"wave_time": "wt_ext_range", "dir": 1, "in_ext_range": True},
    }
    eng.pending_orders = [ext_range_p]
    eng._handle_bos_exit_on_bar(
        bar_idx=0,
        bar_time=datetime(2026, 5, 1, 10, 0),
        bar_close=1.1, bar_high=1.1, bar_low=1.1,
        close_positions=False,
        cancel_pendings=True,
    )
    assert ext_range_p not in eng.pending_orders
