"""
Live MT5 execution — parita s grid backtesterem podle rezimu pozic.

Rezimy (classify_live_execution_mode):
  - wave_study_wave_only: wave_isolation_study=True (varianta B) — engine plny routing
    (counter/EXT logika), MT5 posila JEN klasické WAVE; wave_pnl = equity slice
  - wave_slice: legacy live_mt5_wave_slice_only bez study
  - wave_only: jen klasické WAVE, bez EXT/counter/PP/BOS orderů (engine config)
  - full: vsechny moduly podle engine configu
  - wave_disabled: wave_position_enabled=False → jen counter-only na TP-vlnach (pokud counter ON)
"""
from __future__ import annotations

from dataclasses import fields, replace
from typing import Any, Literal

from config.bot_config import BotConfig
from config.position_modes import bot_config_is_wave_positions_only, resolve_grid_engine_config
from core.logging_utils import log_event
from runtime.live_wave_stats import position_kind_from_mt5_comment
from strategy.ext_logic import is_ext_wave

LiveExecutionMode = Literal[
    "wave_study_wave_only",
    "wave_slice",
    "wave_only",
    "full",
    "wave_disabled",
]

# Varianta B: study režim — na MT5 jen WAVE (engine counter/EXT routing beze zmeny).
_ISOLATION_STUDY_ALLOWED_ENTRY_KINDS = frozenset({"WAVE"})


def live_wave_isolation_study_active(cfg: BotConfig) -> bool:
    """Combo 2: wave_positions_only + wave_isolation_study."""
    return bool(getattr(cfg, "wave_positions_only", False)) and bool(
        getattr(cfg, "wave_isolation_study", False)
    )


def live_wave_isolation_mt5_active(cfg: BotConfig) -> bool:
    """True = MT5 filtr orderu aktivni."""
    if bool(getattr(cfg, "live_mt5_wave_slice_only", False)):
        return True
    return live_wave_isolation_study_active(cfg)


def live_wave_isolation_requested(cfg: BotConfig) -> bool:
    """Pred resolve_grid_engine_config — combo 2 ma oba flagy."""
    return live_wave_isolation_study_active(cfg)


def classify_live_execution_mode(cfg: BotConfig) -> LiveExecutionMode:
    """Aktualni MT5 execution rezim po resolve_live_execution_config()."""
    if not bool(getattr(cfg, "wave_position_enabled", True)):
        return "wave_disabled"
    if live_wave_isolation_study_active(cfg):
        return "wave_study_wave_only"
    if bool(getattr(cfg, "live_mt5_wave_slice_only", False)):
        return "wave_slice"
    if bool(getattr(cfg, "wave_positions_only", False)) or bot_config_is_wave_positions_only(
        cfg
    ):
        return "wave_only"
    return "full"


def resolve_live_execution_config(cfg: BotConfig) -> BotConfig:
    """
    Engine parita (resolve_grid_engine_config) + MT5 varianta B pri study:
    routing counter/EXT bezi, na ucet jde jen WAVE.
    """
    requested_slice = live_wave_isolation_requested(cfg)
    cfg = resolve_grid_engine_config(cfg)
    if requested_slice:
        names = {f.name for f in fields(BotConfig)}
        cfg = replace(
            cfg,
            **{k: v for k, v in {"wave_isolation_study": True}.items() if k in names},
        )
    return apply_live_mt5_wave_slice_execution(cfg, requested=requested_slice)


def log_live_execution_mode(cfg: BotConfig) -> None:
    mode = classify_live_execution_mode(cfg)
    messages = {
        "wave_study_wave_only": (
            "MT5 varianta B: engine plny routing (counter/EXT logika), "
            "na ucet jen klasické WAVE. wave_pnl = equity WAVE slice."
        ),
        "wave_slice": (
            "MT5: legacy slice — WAVE + counter + EXT counter. "
            "Bez PP / BOS entry / EXT primary / EXT secondary."
        ),
        "wave_only": (
            "MT5: jen klasické WAVE vstupy. Pomocné moduly vypnuté v engine configu."
        ),
        "full": "MT5: plny engine — WAVE, counter, EXT, PP, BOS dle configu.",
        "wave_disabled": (
            "MT5: primární WAVE vypnuty. Counter-only na TP-vlnach (pokud counter zapnut)."
        ),
    }
    log_event(
        cfg,
        "info",
        "LIVE_EXECUTION_MODE",
        mode=mode,
        message=messages[mode],
    )


def is_wave_mt5_comment(comment: str) -> bool:
    c = str(comment or "")
    return c.startswith("W") and len(c) == 13 and c[1:].isdigit()


def apply_live_mt5_wave_slice_execution(
    cfg: BotConfig,
    *,
    requested: bool | None = None,
) -> BotConfig:
    """
    Po resolve_grid_engine_config(): study — zachova counter/EXT v engine configu.
    Vypne PP/BOS entry/EXT secondary a zapne live_mt5_wave_slice_only.
    """
    active = (
        live_wave_isolation_mt5_active(cfg)
        if requested is None
        else (requested or live_wave_isolation_mt5_active(cfg))
    )
    if not active:
        return cfg

    names = {f.name for f in fields(BotConfig)}
    overrides: dict[str, Any] = {
        "live_mt5_wave_slice_only": True,
        "pp_enabled": False,
        "bos_entry_enable": False,
        "bos_reentry_enabled": False,
        "ext_secondary_enabled": False,
    }
    return replace(cfg, **{k: v for k, v in overrides.items() if k in names})


def is_isolation_study_allowed_mt5_comment(comment: str) -> bool:
    """Pending/pozice povolene ve study variante B — jen W{wave_time}."""
    return is_wave_mt5_comment(str(comment or ""))


def skip_live_non_wave_entry(
    cfg: BotConfig,
    entry_kind: str,
    **log_fields: Any,
) -> bool:
    """True = neposilat na MT5 (blokovano)."""
    if not live_wave_isolation_mt5_active(cfg):
        return False
    if str(entry_kind).upper() in _ISOLATION_STUDY_ALLOWED_ENTRY_KINDS:
        return False
    log_event(
        cfg,
        "info",
        "LIVE_WAVE_ISOLATION_SKIP",
        entry_kind=str(entry_kind),
        **log_fields,
    )
    return True


def guard_live_send_order(
    cfg: BotConfig,
    signal: dict,
    *,
    is_two_sided_mirror: bool = False,
    bypass_trend_filter: bool = False,
) -> bool:
    """
    True = blokovat send_order (nic neposilat).
    Vraci True i kdyz by se melo skipnout jako 'hotovo' (zabrani replay smyckam).
    """
    if not live_wave_isolation_mt5_active(cfg):
        return False

    wt = str(signal.get("wave_time", "") or "")
    if is_two_sided_mirror:
        skip_live_non_wave_entry(
            cfg, "TWO_SIDED", wave_id=wt, reason="two_sided_mirror",
        )
        return True
    if bypass_trend_filter:
        skip_live_non_wave_entry(
            cfg, "BOS_RETRO", wave_id=wt, reason="bos_retro_entry",
        )
        return True
    if bool(signal.get("post_ext_trend_suppressed", False)):
        return True
    try:
        if is_ext_wave(signal, cfg):
            skip_live_non_wave_entry(
                cfg, "EXT_WAVE", wave_id=wt, reason="ext_primary_wave",
            )
            return True
    except Exception:
        pass
    return False


def filter_wave_only_pending_snapshots(
    cfg: BotConfig,
    snapshots: list,
) -> list:
    """Session snapshot restore — jen WAVE pending (varianta B)."""
    if not live_wave_isolation_mt5_active(cfg):
        return snapshots
    return [
        s for s in snapshots
        if is_isolation_study_allowed_mt5_comment(getattr(s, "comment", ""))
    ]


def audit_mt5_non_wave_exposure(cfg: BotConfig) -> None:
    """Pri startu varuj, pokud na uctu jsou typy orderu mimo study WAVE-only."""
    if not live_wave_isolation_mt5_active(cfg):
        return
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return

    foreign_orders: list[str] = []
    foreign_positions: list[str] = []

    for o in mt5.orders_get(symbol=cfg.symbol) or []:
        if o.magic != cfg.magic:
            continue
        c = str(o.comment or "")
        if not is_isolation_study_allowed_mt5_comment(c):
            foreign_orders.append(c)

    for p in mt5.positions_get(symbol=cfg.symbol) or []:
        if p.magic != cfg.magic:
            continue
        c = str(p.comment or "")
        if is_isolation_study_allowed_mt5_comment(c):
            continue
        kind = position_kind_from_mt5_comment(c)
        foreign_positions.append(f"{c}:{kind}")

    if foreign_orders or foreign_positions:
        log_event(
            cfg,
            "warning",
            "LIVE_WAVE_ISOLATION_FOREIGN_EXPOSURE",
            foreign_pending=int(len(foreign_orders)),
            foreign_positions=int(len(foreign_positions)),
            sample_pending=foreign_orders[:5],
            sample_positions=foreign_positions[:5],
            message="Na uctu jsou ordery mimo study WAVE-only (povoleno: jen W{wave_time})",
        )
