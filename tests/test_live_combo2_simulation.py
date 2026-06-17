"""
Simulace ověření parity combo 2: live MT5 execution vs backtest engine.

Nespouští MT5 — simuluje config, guards, skip logiku a snapshot restore.
"""
from __future__ import annotations

from dataclasses import replace

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

ENTRY_KINDS = (
    "WAVE",
    "COUNTER",
    "EXT_COUNTER",
    "TWO_SIDED",
    "PP",
    "BOS",
    "EXT_SECONDARY",
    "BOS_RETRO",
    "EXT_WAVE",
)

MT5_COMMENTS = (
    ("W202601011030", True, "WAVE"),
    ("CNTR_202601011030@G4", True, "COUNTER"),
    ("TS2_202601011030", True, "TWO_SIDED"),
    ("ECT_202601011030", True, "EXT_COUNTER time"),
    ("ECB_202601011030", True, "EXT_COUNTER BOS"),
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


class TestCombo2ConfigParity:
    def test_live_matches_backtest_engine_flags(self):
        live = resolve_live_execution_config(LIVE_BOT_CONFIG)
        engine = resolve_grid_engine_config(LIVE_BOT_CONFIG)
        mismatches = [
            (k, getattr(live, k), getattr(engine, k))
            for k in PARITY_FLAGS
            if getattr(live, k) != getattr(engine, k)
        ]
        assert mismatches == [], f"config mismatch: {mismatches}"

    def test_execution_mode_wave_slice(self):
        cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
        assert classify_live_execution_mode(cfg) == "wave_slice"
        assert cfg.live_mt5_wave_slice_only is True


class TestCombo2EntryKindSimulation:
    """Simuluje skip_live_non_wave_entry pro každý typ vstupu."""

    @pytest.fixture
    def live_cfg(self):
        return resolve_live_execution_config(LIVE_BOT_CONFIG)

    @pytest.mark.parametrize(
        ("kind", "allowed"),
        [
            ("WAVE", True),
            ("COUNTER", True),
            ("EXT_COUNTER", True),
            ("TWO_SIDED", True),
            ("PP", False),
            ("BOS", False),
            ("EXT_SECONDARY", False),
        ],
    )
    def test_entry_kind_skip_matrix(self, live_cfg, kind, allowed):
        skipped = skip_live_non_wave_entry(live_cfg, kind, wave_time="202601011030")
        assert skipped is (not allowed), f"{kind}: expected allowed={allowed}"


class TestCombo2SendOrderGuardSimulation:
    @pytest.fixture
    def live_cfg(self):
        return resolve_live_execution_config(LIVE_BOT_CONFIG)

    def test_plain_wave_passes(self, live_cfg):
        assert guard_live_send_order(live_cfg, _plain_wave()) is False

    def test_two_sided_mirror_passes(self, live_cfg):
        assert guard_live_send_order(
            live_cfg, _plain_wave(), is_two_sided_mirror=True,
        ) is False

    def test_ext_primary_wave_blocked(self, live_cfg):
        assert guard_live_send_order(
            live_cfg, _plain_wave(move_pct=0.80, is_ext=True),
        ) is True

    def test_bos_retro_blocked(self, live_cfg):
        assert guard_live_send_order(
            live_cfg, _plain_wave(), bypass_trend_filter=True,
        ) is True


class TestCombo2PendingSnapshotSimulation:
    def test_restore_keeps_engine_aligned_orders(self):
        cfg = resolve_live_execution_config(LIVE_BOT_CONFIG)
        snaps = [
            PendingOrderSnapshot(2, 1.1, 1.09, 1.12, 0.1, c, None)
            for c, _, _ in MT5_COMMENTS
        ]
        restored = filter_wave_only_pending_snapshots(cfg, snaps)
        allowed = {c for c, ok, _ in MT5_COMMENTS if ok}
        assert {s.comment for s in restored} == allowed


class TestCombo2Mt5CommentSimulation:
    @pytest.mark.parametrize("comment,allowed,_label", MT5_COMMENTS)
    def test_comment_allowlist(self, comment, allowed, _label):
        assert is_isolation_study_allowed_mt5_comment(comment) is allowed


class TestCombo2FullPipelineReport:
    """Jeden souhrnný test — vypíše simulovaný report (caplog off, assert only)."""

    def test_simulation_summary(self, capsys):
        live = resolve_live_execution_config(LIVE_BOT_CONFIG)
        engine = resolve_grid_engine_config(LIVE_BOT_CONFIG)

        lines = [
            "=== COMBO 2 SIMULACE PARITY ===",
            f"mode: {classify_live_execution_mode(live)}",
            "",
            "CONFIG (live == engine):",
        ]
        for k in PARITY_FLAGS:
            lv, ev = getattr(live, k), getattr(engine, k)
            mark = "OK" if lv == ev else "FAIL"
            lines.append(f"  [{mark}] {k}: live={lv} engine={ev}")

        lines.append("")
        lines.append("ENTRY KINDS (skip=False = poslat na MT5):")
        for kind in ENTRY_KINDS:
            if kind in ("BOS_RETRO", "EXT_WAVE"):
                continue
            skip = skip_live_non_wave_entry(live, kind)
            lines.append(f"  {'ALLOW' if not skip else 'BLOCK':5} {kind}")

        lines.append("")
        lines.append("GUARD send_order:")
        scenarios = [
            ("plain WAVE", guard_live_send_order(live, _plain_wave())),
            ("two-sided mirror", guard_live_send_order(
                live, _plain_wave(), is_two_sided_mirror=True,
            )),
            ("EXT primary", guard_live_send_order(
                live, _plain_wave(move_pct=0.80, is_ext=True),
            )),
            ("BOS retro", guard_live_send_order(
                live, _plain_wave(), bypass_trend_filter=True,
            )),
        ]
        for name, blocked in scenarios:
            lines.append(f"  {'BLOCK' if blocked else 'ALLOW':5} {name}")

        lines.append("")
        lines.append("PENDING RESTORE:")
        for comment, allowed, label in MT5_COMMENTS:
            ok = is_isolation_study_allowed_mt5_comment(comment)
            lines.append(
                f"  {'KEEP' if ok else 'DROP':4} {comment} ({label})",
            )

        report = "\n".join(lines)
        print(report)

        assert all(
            getattr(live, k) == getattr(engine, k) for k in PARITY_FLAGS
        )
        assert guard_live_send_order(live, _plain_wave()) is False
        assert skip_live_non_wave_entry(live, "COUNTER") is False
        assert skip_live_non_wave_entry(live, "PP") is True
