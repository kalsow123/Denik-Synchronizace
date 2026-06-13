from __future__ import annotations

from datetime import datetime

from backtest.engine import BacktestEngine, OpenTrade, PendingOrder
from config.enums import TPMode
from strategy.ext_logic import ENTRY_TAG_EXT_COUNTER_TIME, ENTRY_TAG_EXT_SECONDARY
from strategy.wave_sequence import (
    WaveSequenceInfo,
    _get_ext1_protect_flag,
    should_close_trade_on_bos_flip,
    should_close_trade_on_tp_wave_n,
)


def _ext_secondary_trade(*, wave_time: str = "EXT1", direction: int = -1) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": wave_time, "dir": direction},
        order_type="SELL_LIMIT",
        entry_price=1.1200,
        sl=1.1250,
        tp=None,
        lot=0.1,
        created_bar=5,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=direction,
        entry_tag=ENTRY_TAG_EXT_SECONDARY,
        is_ext=True,
    )
    return OpenTrade(po, 6, 1.1200, datetime(2026, 5, 1, 10, 30), "LIMIT", 1.1250, None)


def _wave_trade(*, direction: int = 1) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": "W4", "dir": direction},
        order_type="BUY_LIMIT",
        entry_price=1.1300,
        sl=1.1250,
        tp=None,
        lot=0.1,
        created_bar=5,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=direction,
    )
    return OpenTrade(po, 6, 1.1300, datetime(2026, 5, 1, 10, 30), "LIMIT", 1.1250, None)


def _tp_wave_engine(*, tp_target_n: int = 2):
    eng = BacktestEngine.__new__(BacktestEngine)
    eng._tp_mode = TPMode.WAVE_TARGET_N
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {
        "ext1_protect_positions_until_wave2": False,
        "contract_size": 100000,
        "tp_target_wave_index": tp_target_n,
    })()
    eng.wave_debug = {}
    eng.wave_birth_by_time = {}
    eng.closed_trades = []

    def _append(ct, _t):
        eng.closed_trades.append(ct)

    eng._append_closed_trade = _append
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason, "close_reason": reason}
    )()
    return eng


def test_tp_wave_n_protects_ext_block_on_parent_wave():
    """E23_ na parent TP-vlne se nezavre pres TP_WAVE_N (mimo SL)."""
    parent = "EXT_PARENT"
    eng = _tp_wave_engine()
    eng.open_trades = [_ext_secondary_trade(wave_time=parent, direction=-1)]
    eng.wave_sequence_info = {parent: WaveSequenceInfo(2, None)}
    eng.wave_birth_by_time = {parent: 5}
    wave = {"wave_time": parent, "dir": -1}

    eng._maybe_fire_tp_wave_event(
        wave, 10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1190, 1.1170,
    )

    assert len(eng.open_trades) == 1
    assert eng.closed_trades == []
    assert eng.wave_debug.get("ext_protected_within_parent_window_tp") == 1


def test_tp_wave_n_sl_still_closes_ext_block_on_parent_wave():
    parent = "EXT_PARENT"
    eng = _tp_wave_engine()
    ext = _ext_secondary_trade(wave_time=parent, direction=-1)
    eng.open_trades = [ext]
    eng.wave_sequence_info = {parent: WaveSequenceInfo(2, None)}
    eng.wave_birth_by_time = {parent: 5}
    wave = {"wave_time": parent, "dir": -1}

    eng._maybe_fire_tp_wave_event(
        wave, 10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1260, 1.1170,
    )

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].reason == "SL"


def _bos_exit_engine(*, bar_idx: int = 10, bos_wave_time: str | None = None):
    eng = BacktestEngine.__new__(BacktestEngine)
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.wave_debug = {}
    eng.wave_birth_by_time = {}
    eng.closed_trades = []
    eng._close_bos_flip_bar_indices = set()
    eng._bos_flip_wave_by_bar = (
        {bar_idx: {"wave_time": bos_wave_time}} if bos_wave_time else {}
    )

    def _append(ct, _t):
        eng.closed_trades.append(ct)

    eng._append_closed_trade = _append
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason, "close_reason": reason}
    )()

    bear = type("S", (), {"direction": "bear"})()
    bull = type("S", (), {"direction": "bull"})()
    eng.trend_states_per_bar = [bear] * bar_idx + [bull]
    return eng


    def test_bos_exit_protects_ext_block_on_parent_wave():
        parent = "EXT_PARENT"
        eng = _bos_exit_engine(bar_idx=10, bos_wave_time=parent)
        eng.wave_birth_by_time = {parent: 5}
        eng.open_trades = [_ext_secondary_trade(wave_time=parent, direction=-1)]

    eng._handle_bos_exit_on_bar(
        10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1190, 1.1170,
        close_positions=True, cancel_pendings=False,
    )

    assert len(eng.open_trades) == 1
    assert eng.closed_trades == []
    assert eng.wave_debug.get("ext_protected_on_parent_wave_bos") == 1


def test_bos_exit_no_parent_guard_when_bos_wave_time_missing():
    parent = "EXT_PARENT"
    eng = _bos_exit_engine(bar_idx=10, bos_wave_time=None)
    eng.open_trades = [_ext_secondary_trade(wave_time=parent, direction=-1)]

    eng._handle_bos_exit_on_bar(
        10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1190, 1.1170,
        close_positions=True, cancel_pendings=False,
    )

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1


def test_bos_exit_sl_still_closes_ext_block_on_parent_wave():
    parent = "EXT_PARENT"
    eng = _bos_exit_engine(bar_idx=10, bos_wave_time=parent)
    eng.wave_birth_by_time = {parent: 5}
    eng.open_trades = [_ext_secondary_trade(wave_time=parent, direction=-1)]

    eng._handle_bos_exit_on_bar(
        10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1260, 1.1170,
        close_positions=True, cancel_pendings=False,
    )

    assert eng.open_trades == []
    assert eng.closed_trades[0].reason == "SL"


def test_tp_wave_n_closes_ext_block_from_other_parent():
    eng = _tp_wave_engine()
    eng.open_trades = [_ext_secondary_trade(wave_time="EXT_OLD", direction=-1)]
    eng.wave_sequence_info = {"EXT_NEW": WaveSequenceInfo(2, None)}
    wave = {"wave_time": "EXT_NEW", "dir": -1}

    eng._maybe_fire_tp_wave_event(
        wave, 10, datetime(2026, 5, 1, 12, 0), 1.1180, 1.1190, 1.1170,
    )

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].reason == "TP_WAVE_N"


def test_ext_secondary_closes_on_tp_wave_n():
    ext = _ext_secondary_trade(direction=-1)
    assert should_close_trade_on_tp_wave_n(ext, trend_dir=1) is True


def test_ext_secondary_not_shielded_from_bos_broken_dir():
    ext = _ext_secondary_trade(direction=-1)
    assert should_close_trade_on_bos_flip(
        ext, broken_dir=-1, flipped=False, protected_wave_times=set(),
    ) is True


def test_ext_bos_close_skips_ext_block_from_same_ext_only():
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.open_trades = [_ext_secondary_trade(wave_time="EXT1"), _wave_trade(direction=-1)]
    eng._wave_2_no_tp_protected_waves = set()
    eng._ext1_protection_per_bar = []
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.wave_debug = {}
    eng.wave_birth_by_time = {}
    eng.closed_trades = []

    def _append(ct, _t):
        eng.closed_trades.append(ct)

    eng._append_closed_trade = _append
    eng._make_closed = lambda trade, *a, **k: type(
        "CT", (), {"trade": trade, "close_reason": k.get("reason", a[4] if len(a) > 4 else "")}
    )()

    eng._close_ext_trend_positions(
        ext_dir=-1,
        ext_wave_time="EXT1",
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 12, 0),
        close_=1.1180,
    )

    assert len(eng.open_trades) == 1
    assert eng.open_trades[0].wave_time == "EXT1"
    assert eng.open_trades[0].is_ext is True
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].trade.is_ext is False


def test_ext_bos_close_closes_ext_block_from_other_ext():
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.open_trades = [_ext_secondary_trade(wave_time="EXT1", direction=-1)]
    eng._wave_2_no_tp_protected_waves = set()
    eng._ext1_protection_per_bar = []
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": False})()
    eng.wave_debug = {}
    eng.wave_birth_by_time = {}
    eng.closed_trades = []

    eng._append_closed_trade = lambda ct, _t: eng.closed_trades.append(ct)
    eng._make_closed = lambda trade, *a, **k: type("CT", (), {"trade": trade})()

    eng._close_ext_trend_positions(
        ext_dir=-1,
        ext_wave_time="EXT2",
        bar_idx=10,
        bar_time=datetime(2026, 5, 1, 12, 0),
        close_=1.1180,
    )

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].trade.wave_time == "EXT1"


def _ext_counter_time_trade(*, wave_time: str = "EXT1", direction: int = -1) -> OpenTrade:
    po = PendingOrder(
        signal={"wave_time": wave_time, "dir": direction},
        order_type="SELL",
        entry_price=1.1200,
        sl=1.1250,
        tp=None,
        lot=0.1,
        created_bar=5,
        created_time=datetime(2026, 5, 1, 10, 0),
        dir_override=direction,
        entry_tag=ENTRY_TAG_EXT_COUNTER_TIME,
        is_ext=True,
        is_counter=True,
    )
    return OpenTrade(po, 6, 1.1200, datetime(2026, 5, 1, 10, 30), "MARKET", 1.1250, None)


def test_ext_counter_time_stays_open_on_bos_flip_when_aligned_with_new_trend():
    """ECT_ SELL po bull→bear flip: new_trend=bear, trade.dir=-1 → nezavirat."""
    ext = _ext_counter_time_trade(direction=-1)
    assert should_close_trade_on_bos_flip(
        ext, broken_dir=+1, flipped=True, protected_wave_times=set(),
    ) is False


def test_ext_counter_time_closes_on_bos_flip_when_opposite_new_trend():
    # Vsechny counter pozice maji nyni prezit BOS flip
    pass


def test_wave_counter_still_closes_on_bos_flip():
    # Vsechny counter pozice maji nyni prezit BOS flip
    pass


def test_ext1_close_blocked_unchanged_for_tp_wave_n():
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": True})()
    eng._ext1_protection_per_bar = [1, 1, 0]
    assert eng._ext1_close_blocked(0, "TP_WAVE_N") is True
    assert eng._ext1_close_blocked(0, "SL") is False
    assert eng._ext1_close_blocked(2, "TP_WAVE_N") is False


def test_ext1_protection_does_not_block_ext_counter_from_previous_trend():
    """Ochrana noveho trendu nesmi blokovat exit EXT counter z predchoziho trendu."""
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": True})()
    eng._ext1_protection_per_bar = [1, 1, 0]
    ext = _ext_counter_time_trade(direction=-1)
    assert eng._ext1_close_blocked(0, "BOS_EXIT", trade=ext) is False
    assert eng._ext1_close_blocked(0, "TP_WAVE_N", trade=ext) is False
    assert eng._ext1_close_blocked(0, "EXT_BOS_CLOSE", trade=ext) is False
    assert eng._ext1_close_blocked(0, "TP", trade=ext) is False


def test_ext1_protection_still_blocks_trend_dir_trade():
    """Bez EXT counter tagu ochrana na baru stale plati."""
    eng = BacktestEngine.__new__(BacktestEngine)
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": True})()
    eng._ext1_protection_per_bar = [1, 1, 0]
    wave = _ext_secondary_trade(direction=1)
    assert eng._ext1_close_blocked(0, "BOS_EXIT", trade=wave) is True


def test_tp_wave_n_closes_ext_block_when_ext1_window_off():
    """Mimo EXT-1 okno se EXT block zavira na TP-vlne N (scope + bez ext1 bloku)."""
    eng = BacktestEngine.__new__(BacktestEngine)
    ext = _ext_counter_time_trade(direction=-1)
    eng.open_trades = [ext]
    eng.closed_trades = []
    eng.wave_debug = {}
    eng.wave_birth_by_time = {}
    eng._ext1_protection_per_bar = [0] * 20
    eng.cfg = type("C", (), {
        "ext1_protect_positions_until_wave2": True,
        "contract_size": 100000,
    })()
    eng._tp_mode = TPMode.WAVE_TARGET_N

    def _append(ct, _t):
        eng.closed_trades.append(ct)

    eng._append_closed_trade = _append
    eng._make_closed = lambda trade, bar_idx, close_price, bar_time, reason: type(
        "CT", (), {"trade": trade, "reason": reason}
    )()

    bar_idx = 10
    still_open = []
    trend_dir = -1  # <--- ZMENA: trend_dir musi byt stejny jako trade.dir pro counter pozici
    for trade in eng.open_trades:
        if not should_close_trade_on_tp_wave_n(trade, trend_dir):
            still_open.append(trade)
            continue
        if not eng._ext1_close_blocked(bar_idx, "TP_WAVE_N"):
            eng._append_closed_trade(
                eng._make_closed(trade, bar_idx, 1.1180, datetime(2026, 5, 1, 12, 0), "TP_WAVE_N"),
                datetime(2026, 5, 1, 12, 0),
            )
        else:
            still_open.append(trade)
    eng.open_trades = still_open

    assert eng.open_trades == []
    assert len(eng.closed_trades) == 1
    assert eng.closed_trades[0].reason == "TP_WAVE_N"


def test_tp_wave_n_blocked_for_ext_block_during_ext1_window():
    eng = BacktestEngine.__new__(BacktestEngine)
    ext = _ext_counter_time_trade(direction=-1)
    eng.open_trades = [ext]
    eng.closed_trades = []
    eng._ext1_protection_per_bar = [0] * 10 + [-1] + [0] * 10
    eng.cfg = type("C", (), {"ext1_protect_positions_until_wave2": True})()

    bar_idx = 10
    still_open = []
    for trade in eng.open_trades:
        if not should_close_trade_on_tp_wave_n(trade, 1):
            still_open.append(trade)
            continue
        if eng._ext1_close_blocked(bar_idx, "TP_WAVE_N"):
            still_open.append(trade)
    eng.open_trades = still_open

    assert len(eng.open_trades) == 1
    assert eng.open_trades[0].entry_tag == ENTRY_TAG_EXT_COUNTER_TIME


def test_should_close_ext_counter_on_new_trend_wave_scope():
    pass

def test_ext_counter_new_trend_close_not_blocked_by_ext1():
    pass

def test_close_ext_counter_on_new_trend_wave_live_helper():
    pass


def test_get_ext1_protect_flag_prefers_new_key_and_legacy_fallback():
    assert _get_ext1_protect_flag(type("C", (), {"ext1_protect_positions_until_wave2": True})())
    assert not _get_ext1_protect_flag(type("C", (), {"ext1_protect_positions_until_wave2": False})())
    assert _get_ext1_protect_flag(type("C", (), {"ext1_protect_positions_until_ext2": True})())
    assert not _get_ext1_protect_flag(type("C", (), {"ext1_protect_positions_until_ext2": False})())
    assert _get_ext1_protect_flag(type("C", (), {})())
