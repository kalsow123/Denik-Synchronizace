"""Profil bot_finish — explicitni seznam kombinaci z Ranking_FTMO."""
from __future__ import annotations

import json
from pathlib import Path

from backtest.grid.backtest_conf import generate_combinations, get_profile

ROOT = Path(__file__).resolve().parents[2]
COMBOS_JSON = ROOT / "backtest" / "grid" / "bot_finish_combos.json"


def test_bot_finish_loads_explicit_combos():
    combos = generate_combinations(get_profile("bot_finish"))
    payload = json.loads(COMBOS_JSON.read_text(encoding="utf-8"))
    assert payload["count"] == 436
    assert len(combos) == 828
    assert sum(1 for c in combos if c.get("__wave_study_full_twin")) == 128
    assert all(c.get("bot_name") for c in combos)
    assert len({c["bot_name"] for c in combos}) == len(combos)


def test_bot_finish_dates_from_profile_base():
    profile = get_profile("bot_finish")
    combos = generate_combinations(profile)
    assert profile["base"]["date_from"] == "2026-01-01"
    assert profile["base"]["date_to"] == "2026-05-10"
    assert all(c["date_from"] == profile["base"]["date_from"] for c in combos)
    assert all(c["date_to"] == profile["base"]["date_to"] for c in combos)


def test_bot_finish_wave_study_metadata():
    profile = get_profile("bot_finish")
    ws = profile.get("wave_study", {})
    assert ws.get("wave_positions_only") is True
    assert ws.get("wave_isolation_study") is True


def test_bot_finish_prop_firms_ftmo():
    from backtest.grid.backtest_conf import resolve_grid_prop_firms
    from types import SimpleNamespace

    pf = resolve_grid_prop_firms(get_profile("bot_finish"), SimpleNamespace(
        prop_firms=None,
        prop_firm_config=None,
        prop_firm_html=False,
        account_size_override=None,
    ))
    assert pf["preset_names"] == ["FTMO"]
    assert pf["account_size_usd"] == 100_000
