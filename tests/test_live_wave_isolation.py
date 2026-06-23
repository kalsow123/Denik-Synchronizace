"""Live MT5 wave isolation — varianta B (study, MT5 jen WAVE)."""
from __future__ import annotations

from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    filter_wave_only_pending_snapshots,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    live_wave_isolation_mt5_active,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)


def test_live_isolation_active_for_combo2():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert live_wave_isolation_mt5_active(cfg)
    assert classify_live_execution_mode(cfg) == "wave_study_wave_only"


def test_apply_keeps_engine_counter_routing():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert cfg.live_mt5_wave_slice_only is True
    assert cfg.counter_position_enabled is True
    assert cfg.wave_counter_two_sided_enabled is True
    assert cfg.ext_counter_enabled is True
    assert cfg.ext_enabled is True
    assert cfg.pp_enabled is False
    assert cfg.bos_entry_enable is False


def test_guard_blocks_only_mirror_and_post_ext():
    """Parita s backtest WAVE reportem: EXT-primarni i BOS-retro jsou WAVE -> POVOLENO.
    Potlaci se jen two_sided mirror (WAVE_TWO_SIDED) a post_ext_trend_suppressed."""
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    ext = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.8,
        "is_ext": True,
    }
    # EXT-primarni vlna = WAVE v backtestu -> live ji posila (neblokuje)
    assert guard_live_send_order(cfg, ext) is False
    # BOS-retro (bypass_trend_filter) = WAVE v backtestu -> live ji posila
    assert guard_live_send_order(cfg, ext, bypass_trend_filter=True) is False
    plain = {
        "wave_time": "202601011000",
        "dir": 1,
        "fib50": 1.1,
        "sl": 1.09,
        "move_pct": 0.30,
    }
    assert guard_live_send_order(cfg, plain) is False
    assert guard_live_send_order(cfg, plain, bypass_trend_filter=True) is False
    # two_sided mirror = WAVE_TWO_SIDED -> stale blokovano
    assert guard_live_send_order(cfg, plain, is_two_sided_mirror=True) is True
    # post_ext_trend_suppressed = vlna neexistuje -> blokovano
    suppressed = dict(plain, post_ext_trend_suppressed=True)
    assert guard_live_send_order(cfg, suppressed) is True


def test_skip_blocks_counter_ext_two_sided():
    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    assert skip_live_non_wave_entry(cfg, "WAVE") is False
    assert skip_live_non_wave_entry(cfg, "COUNTER") is True
    assert skip_live_non_wave_entry(cfg, "EXT_COUNTER") is True
    assert skip_live_non_wave_entry(cfg, "TWO_SIDED") is True
    assert skip_live_non_wave_entry(cfg, "PP") is True


def test_allowed_mt5_comments_wave_only():
    assert is_isolation_study_allowed_mt5_comment("W202601011000")
    assert not is_isolation_study_allowed_mt5_comment("CNTR_202601011000@G4")
    assert not is_isolation_study_allowed_mt5_comment("ECT_202601011000")
    assert not is_isolation_study_allowed_mt5_comment("PP_202601011000")


def test_snapshot_filter_wave_only():
    from infra.pending_snapshot import PendingOrderSnapshot

    cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
    snaps = [
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "W202601011000", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "CNTR_202601011000@G4", None),
        PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, "PP_202601011000", None),
    ]
    out = filter_wave_only_pending_snapshots(cfg, snaps)
    assert len(out) == 1
    assert out[0].comment == "W202601011000"


def test_wave_id_from_comment_parses_ts2_prefix():
    from infra.trade_tracker import _wave_id_from_comment
    from runtime.two_sided_promote_live import on_bos_flip_promote_two_sided

    assert _wave_id_from_comment("TS2_202603250100") == "202603250100"
    promoted = on_bos_flip_promote_two_sided(
        flipped=True,
        existing_promoted=set(),
        open_comments=["TS2_202603250100", "W202601011000"],
    )
    assert promoted == {"202603250100"}


def test_study_ts2_limit_lot_entry_ref_uses_bar_open():
    from strategy.two_sided import study_ts2_limit_lot_entry_ref

    # SELL same-bar fill: actual = max(ep, open) — parita engine _trigger_pending
    ep = 1.160747
    lot_ep = study_ts2_limit_lot_entry_ref(
        ep,
        is_buy=False,
        bar_open=1.16201,
        decision_ask=1.1623,
        decision_bid=1.16228,
    )
    assert lot_ep == 1.16201


def test_study_ts2_mirror_uses_wick_sl_by_default():
    from config.bot_config import BotConfig
    from strategy.two_sided import (
        prepare_ts2_mirror_entry_signal,
        prepare_two_sided_counter_signal,
    )

    wave = {
        "dir": 1,
        "fib50": 1.10,
        "sl": 1.08,
        "box_bottom": 1.05,
        "box_top": 1.15,
        "wave_time": "202601011000",
    }
    cfg = BotConfig(
        live_study_two_sided_mirror_orders=True,
        live_study_promoted_two_sided_as_wave=True,
    )
    sig = prepare_ts2_mirror_entry_signal(wave, cfg)
    wick_sig = prepare_two_sided_counter_signal(wave, cfg)
    assert sig["sl"] == wick_sig["sl"] == 1.05
