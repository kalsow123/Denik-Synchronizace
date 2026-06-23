"""Unit testy causal backtest policy."""
from backtest.causal_policy import (
    CausalBacktestPolicy,
    retro_bos_entry_allowed,
    bos_flip_wave_at_bar,
)
from config.bot_config import BotConfig


def test_retro_blocked_when_birth_after_flip():
    p = CausalBacktestPolicy(enabled=True)
    wave = {"wave_time": "202601300400", "dir": 1}
    assert retro_bos_entry_allowed(p, wave=wave, flip_bar=100, birth=107) is False
    assert p.debug.get("causal_retro_blocked_birth_ge_flip") == 1


def test_retro_allowed_when_birth_before_flip():
    p = CausalBacktestPolicy(enabled=True)
    wave = {"wave_time": "202601300400", "dir": 1}
    assert retro_bos_entry_allowed(p, wave=wave, flip_bar=100, birth=95) is True


def test_flip_map_filters_future_birth():
    p = CausalBacktestPolicy(enabled=True)
    w = {"wave_time": "T1", "dir": -1}
    flip_map = {50: w}
    birth = {"T1": 60}
    assert bos_flip_wave_at_bar(p, flip_map, 50, birth) is None
    assert p.debug.get("causal_flip_map_filtered") == 1


def test_policy_from_cfg_off_by_default():
    cfg = BotConfig()
    assert cfg.causal_mode is False
    assert cfg.run_e2e_parity is False
