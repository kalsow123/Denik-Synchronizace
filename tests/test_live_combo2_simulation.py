"""
Simulace parity varianty B: engine = backtest, MT5 jen WAVE.
"""
from __future__ import annotations

import pytest

from config.bot_config import LIVE_BOT_CONFIG
from config.position_modes import resolve_grid_engine_config
from infra.pending_snapshot import PendingOrderSnapshot
from runtime.live_wave_isolation import (
    classify_live_execution_mode,
    filter_wave_only_pending_snapshots,
    guard_live_send_order,
    is_isolation_study_allowed_mt5_comment,
    resolve_live_execution_config,
    skip_live_non_wave_entry,
)

PARITY_FLAGS = (
    "counter_position_enabled",
    "wave_counter_two_sided_enabled",
    "two_sided_entry_enabled",
    "ext_counter_enabled",
    "ext_enabled",
    "ext_secondary_enabled",
    "pp_enabled",
    "bos_entry_enable",
    "bos_reentry_enabled",
    "wave_position_enabled",
)

MT5_COMMENTS = (
    ("W202601011030", True, "WAVE"),
    ("CNTR_202601011030@G4", False, "COUNTER"),
    ("TS2_202601011030", False, "TWO_SIDED"),
    ("ECT_202601011030", False, "EXT_COUNTER time"),
    ("ECB_202601011030", False, "EXT_COUNTER BOS"),
    ("PP_202601011030", False, "PP"),
    ("E23_202601011030", False, "EXT primary"),
    ("BOS_202601011030", False, "BOS"),
)


def _plain_wave(**kw) -> dict:
    base = {
        "wave_time": "202601011030",
        "dir": 1,
        "fib50": 1.1000,
        "sl": 1.0950,
        "move_pct": 0.35,
    }
    base.update(kw)
    return base


class TestVariantBConfigParity:
    def test_live_matches_backtest_engine_flags(self):
        live = resolve_live_execution_config(LIVE_BOT_CONFIG)
        engine = resolve_grid_engine_config(LIVE_BOT_CONFIG)
        mismatches = [
            (k, getattr(live, k), getattr(engine, k))
            for k in PARITY_FLAGS
            if getattr(live, k) != getattr(engine, k)
        ]
        assert mismatches == [], f"config mismatch: {mismatches}"

    def test_execution_mode_wave_study_wave_only(self):
        cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
        assert classify_live_execution_mode(cfg) == "wave_study_wave_only"
        assert cfg.live_mt5_wave_slice_only is True


class TestVariantBEntryKindSimulation:
    @pytest.fixture
    def live_cfg(self):
        return resolve_live_execution_config(LIVE_BOT_CONFIG)

    @pytest.mark.parametrize(
        ("kind", "allowed"),
        [
            ("WAVE", True),
            ("COUNTER", False),
            ("EXT_COUNTER", False),
            ("TWO_SIDED", False),
            ("PP", False),
            ("BOS", False),
            ("EXT_SECONDARY", False),
        ],
    )
    def test_entry_kind_skip_matrix(self, live_cfg, kind, allowed):
        skipped = skip_live_non_wave_entry(live_cfg, kind, wave_time="202601011030")
        assert skipped is (not allowed), f"{kind}: expected allowed={allowed}"


class TestVariantBSendOrderGuardSimulation:
    @pytest.fixture
    def live_cfg(self):
        return resolve_live_execution_config(LIVE_BOT_CONFIG)

    def test_plain_wave_passes(self, live_cfg):
        assert guard_live_send_order(live_cfg, _plain_wave()) is False

    def test_two_sided_mirror_passes_when_enabled(self, live_cfg):
        assert live_cfg.live_study_two_sided_mirror_orders is True
        assert guard_live_send_order(
            live_cfg, _plain_wave(), is_two_sided_mirror=True,
        ) is False

    def test_ext_primary_wave_passes(self, live_cfg):
        # Parita s backtest WAVE reportem: EXT-primarni vlna je WAVE -> posila se.
        assert guard_live_send_order(
            live_cfg, _plain_wave(move_pct=0.80, is_ext=True),
        ) is False

    def test_bos_retro_passes(self, live_cfg):
        # BOS-retro (bypass_trend_filter) je v backtestu WAVE -> live ji posila.
        assert guard_live_send_order(
            live_cfg, _plain_wave(), bypass_trend_filter=True,
        ) is False


class TestVariantBPendingSnapshotSimulation:
    def test_restore_keeps_wave_and_ts2_mirror(self):
        cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
        snaps = [
            PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, c, None)
            for c, _, _ in MT5_COMMENTS
        ]
        restored = filter_wave_only_pending_snapshots(cfg, snaps)
        assert len(restored) == 2
        assert {s.comment for s in restored} == {
            "W202601011030",
            "TS2_202601011030",
        }


class TestVariantBMt5CommentSimulation:
    @pytest.mark.parametrize("comment,allowed,_label", MT5_COMMENTS)
    def test_comment_allowlist(self, comment, allowed, _label):
        assert is_isolation_study_allowed_mt5_comment(comment) is allowed
