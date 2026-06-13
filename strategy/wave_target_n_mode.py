"""
WAVE_TARGET_N rodina tp_mode — jedina brana pro wave_target_n / wave_target_n_g.

Nova logika pro wave_target_n? Pouzij is_wave_target_n_family(), NE raw TPMode enum.
Exit timing (legacy birth vs G extension) resi is_wave_target_n_g() / is_wave_target_n_legacy().
"""
from __future__ import annotations

from typing import Any

from config.bot_config import BotConfig
from config.enums import TPMode, TpWaveEarlyMode, TpWaveExitOn, TpWaveIntrabarPriority

# Whitelist: jedine misto kde smi byt literal TPMode.WAVE_TARGET_N / WAVE_TARGET_N_G
# (krome config/enums.py). Test test_wave_target_n_mode_family.py to kontroluje.
WAVE_TARGET_N_FAMILY: frozenset[TPMode] = frozenset({
    TPMode.WAVE_TARGET_N,
    TPMode.WAVE_TARGET_N_G,
})


def cfg_tp_mode(cfg: BotConfig | Any) -> TPMode:
    tpm = getattr(cfg, "tp_mode", TPMode.RRR_FIXED)
    if isinstance(tpm, TPMode):
        return tpm
    try:
        return TPMode(str(tpm).lower())
    except ValueError:
        return TPMode.RRR_FIXED


def is_wave_target_n_family(cfg: BotConfig | Any) -> bool:
    """wave_target_n i wave_target_n_g — sdilene: counter, index, extension vzorec, ochrany."""
    return cfg_tp_mode(cfg) in WAVE_TARGET_N_FAMILY


def is_wave_target_n_g(cfg: BotConfig | Any) -> bool:
    """
    Extension-hit vetev (varianta G):
      - tp_mode=wave_target_n_g (preset pri loadu), nebo
      - tp_mode=wave_target_n + forming_qualified + extension_hit (fine-tuning).
    """
    if cfg_tp_mode(cfg) == TPMode.WAVE_TARGET_N_G:
        return True
    if cfg_tp_mode(cfg) != TPMode.WAVE_TARGET_N:
        return False
    raw_early = getattr(cfg, "tp_wave_early_mode", TpWaveEarlyMode.OFF)
    if isinstance(raw_early, TpWaveEarlyMode):
        early_ok = raw_early == TpWaveEarlyMode.FORMING_QUALIFIED
    else:
        early_ok = str(raw_early).lower() == TpWaveEarlyMode.FORMING_QUALIFIED.value
    if not early_ok:
        return False
    raw_exit = getattr(cfg, "tp_wave_exit_on", TpWaveExitOn.BIRTH)
    if isinstance(raw_exit, TpWaveExitOn):
        return raw_exit == TpWaveExitOn.EXTENSION_HIT
    return str(raw_exit).lower() == TpWaveExitOn.EXTENSION_HIT.value


def is_wave_target_n_legacy(cfg: BotConfig | Any) -> bool:
    """Legacy birth TP_WAVE_N — wave_target_n bez G, nebo _g po extension hit (fallback jinde)."""
    return is_wave_target_n_family(cfg) and not is_wave_target_n_g(cfg)


def apply_wave_target_n_g_preset(cfg: BotConfig) -> BotConfig:
    """
    tp_mode=wave_target_n_g → nastav G early preset (idempotentne).
    Volano z grid translatoru / normalize po sestaveni BotConfig.
    """
    if cfg_tp_mode(cfg) != TPMode.WAVE_TARGET_N_G:
        return cfg
    cfg.tp_wave_early_mode = TpWaveEarlyMode.FORMING_QUALIFIED
    cfg.tp_wave_exit_on = TpWaveExitOn.EXTENSION_HIT
    if getattr(cfg, "tp_wave_early_fallback_birth", None) is None:
        cfg.tp_wave_early_fallback_birth = True
    if getattr(cfg, "tp_wave_intrabar_priority", None) is None:
        cfg.tp_wave_intrabar_priority = TpWaveIntrabarPriority.TP_BEFORE_SL
    return cfg


def normalize_wave_target_n_cfg(cfg: BotConfig) -> BotConfig:
    """Jednotny entry point po vytvoreni BotConfig (grid i live)."""
    return apply_wave_target_n_g_preset(cfg)
