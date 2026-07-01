"""Unit testy causal backtest policy."""
from dataclasses import replace

from backtest.causal_policy import (
    CausalBacktestPolicy,
    policy_from_cfg,
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


# --- FÁZE 3C-b: relaxed_wave_box_enabled (profil B) ---------------------------


def test_relaxed_wave_box_disabled_by_default():
    cfg = BotConfig()
    assert cfg.relaxed_wave_box_enabled is False


def test_policy_from_cfg_relaxed_wave_box_disabled_keeps_clamp():
    cfg = BotConfig(causal_mode=True, relaxed_wave_box_enabled=False)
    policy = policy_from_cfg(cfg)
    assert policy.clamp_wave_box_to_bar is True
    assert policy.block_retro_before_birth is True
    assert policy.filter_flip_map_by_birth is True


def test_policy_from_cfg_relaxed_wave_box_enabled_drops_clamp():
    cfg = BotConfig(causal_mode=True, relaxed_wave_box_enabled=True)
    policy = policy_from_cfg(cfg)
    assert policy.clamp_wave_box_to_bar is False
    # retro/flip brany zustavaji VZDY ON, nezavisle na relaxed_wave_box_enabled.
    assert policy.block_retro_before_birth is True
    assert policy.filter_flip_map_by_birth is True


def test_policy_from_cfg_relaxed_wave_box_via_replace():
    """dataclasses.replace(...) (grid translator) musi respektovat pole shodne jako BotConfig(...)."""
    cfg = replace(BotConfig(causal_mode=True), relaxed_wave_box_enabled=True)
    assert policy_from_cfg(cfg).clamp_wave_box_to_bar is False
