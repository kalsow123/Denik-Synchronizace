"""Grid: rrr / tp_target_wave_index / bos_entry_enable jen u relevantnich tp_mode."""
from __future__ import annotations

from backtest.grid.backtest_conf import (
    BOS_ENTRY_FIXED_FOR_RRR_FIXED,
    RRR_FIXED_FOR_NON_RRR_GRID_TP_MODES,
    TP_TARGET_WAVE_INDEX_FIXED_FOR_NON_WAVE_TP_MODES,
    generate_combinations,
    get_profile,
)


def _by_tp(combos: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in combos:
        out.setdefault(str(c["tp_mode"]), []).append(c)
    return out


def test_rrr_sweep_only_rrr_fixed():
    combos = generate_combinations(get_profile("bot_optimalisation"))
    by_tp = _by_tp(combos)

    assert {c["rrr"] for c in by_tp["rrr_fixed"]} == {2.0, 2.5, 3.0}
    for tp in ("bos_exit", "wave_target_n", "wave_target_n_g"):
        assert {c["rrr"] for c in by_tp[tp]} == {RRR_FIXED_FOR_NON_RRR_GRID_TP_MODES}


def test_tp_target_sweep_only_wave_modes():
    combos = generate_combinations(get_profile("bot_optimalisation"))
    by_tp = _by_tp(combos)

    for tp in ("wave_target_n", "wave_target_n_g"):
        assert {c["tp_target_wave_index"] for c in by_tp[tp]} == {4, 6, 8}
    for tp in ("bos_exit", "rrr_fixed"):
        assert {
            c["tp_target_wave_index"] for c in by_tp[tp]
        } == {TP_TARGET_WAVE_INDEX_FIXED_FOR_NON_WAVE_TP_MODES}


def test_bos_entry_sweep_not_rrr_fixed():
    combos = generate_combinations(get_profile("bot_optimalisation"))
    by_tp = _by_tp(combos)

    assert {c["bos_entry_enable"] for c in by_tp["rrr_fixed"]} == {
        BOS_ENTRY_FIXED_FOR_RRR_FIXED
    }
    for tp in ("bos_exit", "wave_target_n", "wave_target_n_g"):
        assert {c["bos_entry_enable"] for c in by_tp[tp]} == {True, False}


def test_bot_optimalisation_combo_count_shrinks():
    combos = generate_combinations(get_profile("bot_optimalisation"))
    # Aktualni bot_optimalisation grid: raw product 82944 (2^10 * 3^4); po
    # podminenem gridu (rrr/tp_target/bos_entry/ext_close srazene dle tp_mode)
    # + dedup => 16704. Dimenze gridu byly oproti puvodnimu navrhu rozsireny
    # (zamerne, optimalizacni profil), proto je ocekavana hodnota 16704.
    assert len(combos) == 16704
