"""
Kauzální brány backtesteru — potlačení look-ahead oproti live botu.

Zapnutí: BotConfig.causal_mode=True (nebo --causal v run_backtest).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from config.bot_config import BotConfig


@dataclass
class CausalBacktestPolicy:
    enabled: bool = False
    block_retro_before_birth: bool = True
    clamp_wave_box_to_bar: bool = True
    filter_flip_map_by_birth: bool = True
    debug: dict[str, int] = field(default_factory=dict)

    def bump(self, key: str, n: int = 1) -> None:
        self.debug[key] = int(self.debug.get(key, 0)) + n


def policy_from_cfg(cfg: BotConfig) -> CausalBacktestPolicy:
    # COUPLING (pravidlo #5): wave_detection_mode == "incremental_causal" MUSI
    # zapnout kauzalni brany i kdyby causal_mode nebyl propsan (defense-in-depth
    # vedle BotConfig.__post_init__). Grid (legacy_precompute) zustava bez bran.
    on = bool(getattr(cfg, "causal_mode", False))
    mode = getattr(cfg, "wave_detection_mode", None)
    if mode is not None and str(getattr(mode, "value", mode)) == "incremental_causal":
        on = True
    # FÁZE 3C-b (profil B): relaxed_wave_box_enabled=True vypne clamp_wave_box_to_bar
    # (WAVE vstup nepouzije useknuti boxu na as_of_bar). Default False => clamp ON (STRICT).
    # block_retro_before_birth a filter_flip_map_by_birth NEJSOU timto polem ovlivnene —
    # zustavaji na default True (hard lock, viz VARIANTA A.txt).
    clamp_wave_box_to_bar = not bool(getattr(cfg, "relaxed_wave_box_enabled", False))
    return CausalBacktestPolicy(enabled=on, clamp_wave_box_to_bar=clamp_wave_box_to_bar)


def retro_bos_entry_allowed(
    policy: CausalBacktestPolicy,
    *,
    wave: dict,
    flip_bar: int,
    birth: int | None,
) -> bool:
    """Retro-BOS jen když vlna už existuje (birth < flip_bar) — parita live_loop."""
    if not policy.enabled or not policy.block_retro_before_birth:
        return True
    if birth is None:
        policy.bump("causal_retro_blocked_birth_none")
        return False
    if int(birth) >= int(flip_bar):
        policy.bump("causal_retro_blocked_birth_ge_flip")
        return False
    return True


def bos_flip_wave_at_bar(
    policy: CausalBacktestPolicy,
    flip_map: dict[int, dict],
    bar_idx: int,
    wave_birth_by_time: dict[str, int],
) -> dict | None:
    wave = flip_map.get(int(bar_idx))
    if wave is None:
        return None
    if not policy.enabled or not policy.filter_flip_map_by_birth:
        return wave
    wt = str(wave.get("wave_time", "") or "")
    birth = wave_birth_by_time.get(wt)
    if birth is None or int(birth) > int(bar_idx):
        policy.bump("causal_flip_map_filtered")
        return None
    return wave


def wave_for_entry_at_bar(
    policy: CausalBacktestPolicy,
    wave: dict,
    as_of_bar: int,
    df: pd.DataFrame,
    cfg: BotConfig,
) -> dict:
    """EP/SL/TP z boxu omezeného na as_of_bar (bez budoucího draw_right)."""
    if not policy.enabled or not policy.clamp_wave_box_to_bar:
        return wave
    dr = int(wave.get("draw_right", as_of_bar))
    dl = int(wave.get("draw_left", as_of_bar))
    if dr <= int(as_of_bar):
        return wave
    if int(as_of_bar) <= dl:
        policy.bump("causal_box_clamp_invalid")
        return wave

    w = dict(wave)
    w["draw_right"] = int(as_of_bar)
    seg = df.iloc[dl : int(as_of_bar) + 1]
    if seg.empty:
        policy.bump("causal_box_clamp_empty_seg")
        return wave

    box_top = float(seg["high"].max())
    box_bottom = float(seg["low"].min())
    w_dir = int(w.get("dir", 1))
    w_range = box_top - box_bottom
    if w_range <= 0:
        policy.bump("causal_box_clamp_zero_range")
        return wave

    fib_lvl = float(cfg.entry_fib_level)
    sl_lvl = float(cfg.sl_fib_level)
    fib50 = box_top - w_range * fib_lvl if w_dir == 1 else box_bottom + w_range * fib_lvl
    sl = box_top - w_range * sl_lvl if w_dir == 1 else box_bottom + w_range * sl_lvl
    is_buy = w_dir == 1
    if not ((sl < fib50) if is_buy else (sl > fib50)):
        policy.bump("causal_box_clamp_invalid_sl")
        return wave

    from strategy.wave_detection_pine import _enforce_wave_min_sl

    sl = _enforce_wave_min_sl(fib50, sl, direction=w_dir, cfg=cfg)
    sl_dist = abs(fib50 - sl)
    tp = fib50 + cfg.rrr * sl_dist if is_buy else fib50 - cfg.rrr * sl_dist

    w.update(
        {
            "box_top": box_top,
            "box_bottom": box_bottom,
            "fib50": float(fib50),
            "sl": float(sl),
            "tp": float(tp),
        }
    )
    policy.bump("causal_box_clamped")
    return w


def causal_debug_summary(policy: CausalBacktestPolicy) -> dict[str, Any]:
    return {"causal_mode": policy.enabled, **dict(policy.debug)}
