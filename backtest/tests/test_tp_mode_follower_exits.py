from __future__ import annotations

from datetime import datetime

from backtest.engine import BacktestEngine, OpenTrade, PendingOrder
from backtest.grid.translator import grid_dict_to_bot_config
from config.enums import TPMode
from strategy.wave_sequence import WaveSequenceInfo


def _cfg(tp_mode: str):
    return grid_dict_to_bot_config(
        {
            "timeframe": "M30",
            "wave_min_pct": 0.26,
            "min_opp_bars": 3,
            "rrr": 2.0,
            "fib_level": 0.5,
            "entry_mode": "market_fallback",
            "symbol": "EURUSD.x",
            "sl_fib_level": 0.8,
            "wave_plus": True,
            "risk_usd": 500.0,
            "contract_size": 100_000.0,
            "counter_position_enabled": True,
            "two_sided_entry_enabled": True,
            "tp_mode": tp_mode,
            "tp_target_wave_index": 4,
            "wave_extension_pct": 0.20,
            "wave_size_sl_ladder_base_pct": 0.21,
            "wave_size_sl_ladder_step_pct": 0.16,
            "wave_size_sl_ladder_band_size_pct": 0.50,
        }
    )


    def test_tp_wave_n_closes_two_sided_mirror():
        cfg = _cfg("wave_target_n")
        eng = BacktestEngine(cfg)
        eng._tp_mode = TPMode.WAVE_TARGET_N
        eng.wave_sequence_info = {"curr": WaveSequenceInfo(4, "prev")}
    
        po = PendingOrder(
            signal={"wave_time": "ts", "dir": -1},
            order_type="SELL_LIMIT",
            entry_price=1.1300,
            sl=1.1350,
            tp=None,
            lot=0.1,
            created_bar=9,
            created_time=datetime(2026, 5, 1, 9, 0),
            dir_override=-1,
            is_two_sided_mirror=True,
            entry_tag="two_sided_mirror",
        )
        eng.open_trades = [
            OpenTrade(
                po, 9, 1.1300, datetime(2026, 5, 1, 9, 30), "LIMIT", 1.1350, None
            )
        ]
        wave = {
            "wave_time": "curr",
            "dir": 1,
            "wave_target_tp_price": 1.1300,
            "box_bottom": 1.1150,
            "box_top": 1.1250,
        }
        eng.waves_by_wave_time = {"curr": wave}
    
        eng._maybe_fire_tp_wave_event(
            wave=wave,
            bar_idx=10,
            bar_time=datetime(2026, 5, 1, 10, 0),
            bar_close=1.1280,
            bar_high=1.1290,
            bar_low=1.1270,
        )
    
        # Two-sided mirror trades are now protected from TP_WAVE_N closures
        assert len(eng.open_trades) == 1
        assert eng.open_trades[0].entry_tag == "two_sided_mirror"
        assert len(eng.closed_trades) == 0


def test_bos_flip_closes_wave_counter_and_two_sided():
    cfg = _cfg("bos_exit")
    eng = BacktestEngine(cfg)
    eng.trend_states_per_bar = [
        type("S", (), {"direction": "bear"})(),
        type("S", (), {"direction": "bull"})(),
    ]
    eng._wave_2_no_tp_protected_waves = set()

    ctr_po = PendingOrder(
        signal={"wave_time": "c", "dir": -1},
        order_type="SELL_LIMIT",
        entry_price=1.1300,
        sl=1.1350,
        tp=1.1200,
        lot=0.1,
        created_bar=0,
        created_time=datetime(2026, 5, 1, 9, 0),
        dir_override=-1,
        is_counter=True,
        entry_tag="wave_counter",
    )
    ts_po = PendingOrder(
        signal={"wave_time": "ts", "dir": -1},
        order_type="SELL_LIMIT",
        entry_price=1.1300,
        sl=1.1350,
        tp=1.1200,
        lot=0.1,
        created_bar=0,
        created_time=datetime(2026, 5, 1, 9, 0),
        dir_override=-1,
        is_two_sided_mirror=True,
        entry_tag="two_sided_mirror",
    )
    eng.open_trades = [
        OpenTrade(ctr_po, 0, 1.1300, datetime(2026, 5, 1, 9, 0), "LIMIT", 1.1350, 1.1200),
        OpenTrade(ts_po, 0, 1.1300, datetime(2026, 5, 1, 9, 0), "LIMIT", 1.1350, 1.1200),
    ]
    
    # Simulate a BOS flip
    eng._handle_bos_exit_on_bar(
        bar_idx=1,
        bar_time=datetime(2026, 5, 1, 10, 0),
        bar_close=1.1320,
        bar_high=1.1330,
        bar_low=1.1310,
        close_positions=True,
        cancel_pendings=False,
    )

    # They should NOT be closed anymore, they should survive the BOS flip
    assert len(eng.open_trades) == 2
    assert eng.open_trades[0].entry_tag == "wave_counter"
    assert eng.open_trades[1].entry_tag == "two_sided_mirror"
    assert len(eng.closed_trades) == 0
