"""Birth-bar gate: parita s backtestem, bez blokace startup recovery."""

from config.bot_config import LIVE_BOT_CONFIG
from runtime.live_loop import (
    _apply_birth_bar_gate,
    _bos_flip_bar_for_wave,
    _wave_born_on_last_bar,
)


def test_bos_flip_bar_for_wave_inverse_map():
    flip_map = {52: "202601011200", 60: "202601021200"}
    assert _bos_flip_bar_for_wave("202601011200", flip_map) == 52
    assert _bos_flip_bar_for_wave("202601021200", flip_map) == 60
    assert _bos_flip_bar_for_wave("missing", flip_map) is None
    birth = {"202601011200": 99}
    assert _wave_born_on_last_bar("202601011200", wave_birth_by_time=birth, last_bar_idx=99)
    assert not _wave_born_on_last_bar("202601011200", wave_birth_by_time=birth, last_bar_idx=100)


def test_gate_allows_current_bar():
    sent: set[str] = set()
    birth = {"202601011200": 50}
    assert _apply_birth_bar_gate(
        "202601011200",
        wave_birth_by_time=birth,
        last_bar_idx=50,
        sent_signals=sent,
        sig_key="k1",
    )
    assert "k1" not in sent


def test_gate_marks_missed_birth_bar():
    sent: set[str] = set()
    birth = {"202601011200": 48}
    assert not _apply_birth_bar_gate(
        "202601011200",
        wave_birth_by_time=birth,
        last_bar_idx=50,
        sent_signals=sent,
        sig_key="k1",
    )
    assert "k1" in sent


def test_gate_waits_bos_retro_until_flip_without_marking():
    sent: set[str] = set()
    birth = {"202601011200": 48}
    assert not _apply_birth_bar_gate(
        "202601011200",
        wave_birth_by_time=birth,
        last_bar_idx=49,
        sent_signals=sent,
        sig_key="k1",
        bos_flip_bar=52,
        is_bos_retro_candidate=True,
    )
    assert "k1" not in sent


def test_gate_marks_bos_retro_after_missed_flip():
    sent: set[str] = set()
    birth = {"202601011200": 48}
    assert not _apply_birth_bar_gate(
        "202601011200",
        wave_birth_by_time=birth,
        last_bar_idx=55,
        sent_signals=sent,
        sig_key="k1",
        bos_flip_bar=52,
        is_bos_retro_candidate=True,
    )
    assert "k1" in sent


def test_gate_waits_future_birth_without_marking():
    sent: set[str] = set()
    birth = {"202601011200": 52}
    assert not _apply_birth_bar_gate(
        "202601011200",
        wave_birth_by_time=birth,
        last_bar_idx=50,
        sent_signals=sent,
        sig_key="k1",
    )
    assert "k1" not in sent


def test_recovery_path_not_using_gate():
    """Po wake-up jsou vlny v sent_signals z recovery/block — gate se neaplikuje."""
    sent = {"existing_recovery_key"}
    birth = {"202601011200": 10}
    # sig_key already in sent -> send_order vetev se vubec nevolá
    assert "existing_recovery_key" in sent
    assert not _wave_born_on_last_bar("202601011200", wave_birth_by_time=birth, last_bar_idx=99)


def test_live_config_order_expiry_covers_session_gap():
    cfg = LIVE_BOT_CONFIG
    assert cfg.session_enabled is True
    assert cfg.order_expiry_days == 3
    assert cfg.session_pre_close_buffer_min == 5
