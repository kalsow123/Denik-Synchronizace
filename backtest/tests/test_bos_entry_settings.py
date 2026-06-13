from __future__ import annotations

from backtest.grid.translator import grid_dict_to_bot_config
from config.bot_config import BotConfig


def test_bot_config_new_and_old_bos_entry_names_stay_synced():
    cfg_new = BotConfig(bos_entry_enable=True)
    cfg_old = BotConfig(bos_reentry_enabled=True)

    assert cfg_new.bos_entry_enable is True
    assert cfg_new.bos_reentry_enabled is True
    assert cfg_old.bos_entry_enable is True
    assert cfg_old.bos_reentry_enabled is True


def test_grid_translator_accepts_new_and_old_bos_entry_names():
    base = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
    }

    cfg_new = grid_dict_to_bot_config({**base, "bos_entry_enable": True})
    cfg_old = grid_dict_to_bot_config({**base, "bos_reentry_enabled": True})

    assert cfg_new.bos_entry_enable is True
    assert cfg_new.bos_reentry_enabled is True
    assert cfg_old.bos_entry_enable is True
    assert cfg_old.bos_reentry_enabled is True


def test_grid_translator_reads_bos_entry_in_rrr_fixed():
    base = {
        "timeframe": "M30",
        "wave_min_pct": 0.26,
        "min_opp_bars": 3,
        "rrr": 2.0,
        "fib_level": 0.5,
        "tp_mode": "rrr_fixed",
    }
    cfg = grid_dict_to_bot_config({**base, "bos_entry_in_rrr_fixed": True})
    assert cfg.bos_entry_in_rrr_fixed is True
