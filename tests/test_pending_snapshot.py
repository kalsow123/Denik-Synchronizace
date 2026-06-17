"""Session pending snapshot — parse comment a expirace."""

from datetime import datetime, timezone

from config.bot_config import BotConfig
from infra.pending_snapshot import (
    PendingOrderSnapshot,
    wave_time_from_pending_comment,
    _snapshot_expired,
)


def test_wave_time_from_wave_comment():
    assert wave_time_from_pending_comment("W202601011230") == "202601011230"


def test_wave_time_from_counter():
    assert wave_time_from_pending_comment("CNTR_202601011230") == "202601011230"


def test_wave_time_from_ext_prefixes():
    assert wave_time_from_pending_comment("EWP_202601011230") == "202601011230"
    assert wave_time_from_pending_comment("E23_202601011230") == "202601011230"
    assert wave_time_from_pending_comment("ECT_202601011230") == "202601011230"
    assert wave_time_from_pending_comment("ECB_202601011230") == "202601011230"


def test_wave_time_from_pp_and_two_sided():
    assert wave_time_from_pending_comment("PP_202601011230") == "202601011230"
    assert wave_time_from_pending_comment("TS2_202601011230") == "202601011230"


def test_counter_never_expires_by_snapshot():
    cfg = BotConfig(order_expiry_days=3)
    snap = PendingOrderSnapshot(
        order_type=2,
        price=1.1,
        sl=1.0,
        tp=1.2,
        volume=0.1,
        comment="CNTR_202601011230",
    )
    now = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
    assert not _snapshot_expired(cfg, snap, now)


def test_wave_expires_after_order_expiry_days():
    cfg = BotConfig(order_expiry_days=3)
    snap = PendingOrderSnapshot(
        order_type=2,
        price=1.1,
        sl=1.0,
        tp=1.2,
        volume=0.1,
        comment="W202601011200",
    )
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert _snapshot_expired(cfg, snap, now)
