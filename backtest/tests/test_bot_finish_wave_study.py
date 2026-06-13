"""Wave study variant flags for bot_finish."""
from __future__ import annotations

from backtest.grid.backtest_conf import generate_combinations, get_profile

SOURCE_NOS = {
    5148, 25500, 4826, 5145, 25564, 5147, 15570, 15113, 25561, 25994,
    15553, 15708, 15306, 5265, 5339, 4883, 25691,
}


def test_bot_finish_wave_study_variants():
    combos = generate_combinations(get_profile("bot_finish"))
    study = [
        c
        for c in combos
        if c.get("wave_positions_only")
        and c.get("wave_isolation_study")
        and not c.get("wave_counter_two_sided_enabled")
    ]
    assert len(study) >= 136
    wave_target_n = [
        c
        for c in study
        if c.get("tp_mode") == "wave_target_n"
        and c.get("tp_target_wave_index") in (4, 6, 8, 10)
    ]
    assert len(wave_target_n) >= 136
    assert {c["tp_target_wave_index"] for c in wave_target_n} == {4, 6, 8, 10}

    with_meta = [c for c in study if c.get("finish_variant") in ("wave_only", "wave_pp")]
    if with_meta:
        wave_only = [c for c in with_meta if c.get("finish_variant") == "wave_only"]
        wave_pp = [c for c in with_meta if c.get("finish_variant") == "wave_pp"]
        assert len(wave_only) == 68
        assert len(wave_pp) == 68
        assert with_meta[0].get("source_combo_no") in SOURCE_NOS


def test_bot_finish_legacy_counter_off_gets_isolation():
    combos = generate_combinations(get_profile("bot_finish"))
    legacy = [
        c
        for c in combos
        if not c.get("wave_counter_two_sided_enabled")
        and c.get("wave_isolation_study")
        and c.get("wave_positions_only")
    ]
    assert len(legacy) == 418


def test_bot_finish_twin_counts():
    combos = generate_combinations(get_profile("bot_finish"))
    assert len(combos) == 828
    assert sum(1 for c in combos if c.get("__wave_study_full_twin")) == 128
    assert sum(1 for c in combos if c.get("__wave_study_iso_twin")) == 264
