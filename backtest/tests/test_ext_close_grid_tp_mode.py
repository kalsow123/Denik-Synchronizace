"""Grid: ext_close_trend_positions_on_bos jen u wave_target_n / wave_target_n_g."""
from __future__ import annotations

from backtest.grid.backtest_conf import (
    EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES,
    generate_combinations,
    get_profile,
)


# Pozn.: EXAMPLE byl zamerne prepsan (commit f56800d) na 4 fixni VARIAC10
# kombinace (jen tp_mode=wave_target_n), takze uz neprokryva vsechny tp_mode.
# Pravidlo "ext_close jen u wave tp modu" se proto overuje na profilu
# bot_optimalisation, ktery dal sweepuje vsechny 4 tp_mode i ext_close [True, False].
def test_example_ext_close_doubles_only_wave_tp_modes():
    combos = generate_combinations(get_profile("bot_optimalisation"))

    by_tp: dict[str, set[bool | None]] = {}
    for c in combos:
        tp = c["tp_mode"]
        by_tp.setdefault(tp, set()).add(c.get("ext_close_trend_positions_on_bos"))

    assert by_tp["bos_exit"] == {EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES}
    assert by_tp["rrr_fixed"] == {EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES}
    assert by_tp["wave_target_n"] == {True, False}
    assert by_tp["wave_target_n_g"] == {True, False}

    names = [c["bot_name"] for c in combos]
    assert len(names) == len(set(names))


def test_wave_tp_modes_still_have_both_ext_close_variants():
    combos = generate_combinations(get_profile("bot_optimalisation"))
    for tp in ("wave_target_n", "wave_target_n_g"):
        for pcm in ("number", "trend"):
            for pp in (True, False):
                flags = {
                    c["ext_close_trend_positions_on_bos"]
                    for c in combos
                    if c["tp_mode"] == tp
                    and c["pending_cancel_mode"] == pcm
                    and c["pp_enabled"] is pp
                }
                assert flags == {True, False}, (tp, pcm, pp, flags)
