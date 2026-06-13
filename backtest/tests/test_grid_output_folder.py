"""
Ověření: výstupy gridu jdou do results/{PÁR}/, kde PÁR = symbol z PROFILES bez .x/.r/.cash.
"""
from __future__ import annotations

import copy
from pathlib import Path

from backtest.grid.backtest_conf import generate_combinations, get_profile
from backtest.grid.grid_runner import run_grid
from backtest.output_paths import (
    grid_output_symbol,
    grid_run_output_dir,
    symbol_folder_name,
)


def test_symbol_from_profile_maps_to_folder_name():
    """bot_optimalisation má EURUSD.x → složka EURUSD."""
    combos = generate_combinations(get_profile("bot_optimalisation"))
    assert combos, "profil musí mít alespoň jednu kombinaci"
    assert combos[0]["symbol"] == "EURUSD.x"
    assert grid_output_symbol(combos) == "EURUSD"
    assert symbol_folder_name(combos[0]["symbol"]) == "EURUSD"


def test_usdcad_example_pair():
    combos = [{"symbol": "USDCAD.x", "timeframe": "M15"}]
    assert grid_output_symbol(combos) == "USDCAD"


def test_grid_run_output_dir_creates_pair_dir_under_results(tmp_path: Path):
    base = tmp_path / "results"
    combos = [
        {
            "symbol": "USDCAD.x",
            "timeframe": "M30",
            "date_from": "2025-04-24",
            "date_to": "2026-04-24",
        }
    ]
    out = grid_run_output_dir(base, "bot_optimalisation", combos)
    assert out == base / "USDCAD" / "grid_bot_optimalisation_M30_2025-04-24_2026-04-24_001"
    assert out.is_dir()
    assert "USDCAD.x" not in str(out)


def test_run_grid_writes_under_profile_pair_folder(tmp_path: Path, monkeypatch):
    """run_grid vytvoří results/USDCAD/... když profil má symbol USDCAD.x (mock backtestu)."""
    profile = copy.deepcopy(get_profile("bot_optimalisation"))
    profile["grid"][0]["symbol"] = ["USDCAD.x"]
    profile["grid"][0]["date_from"] = ["2023-04-24"]
    profile["grid"][0]["date_to"] = ["2026-04-24"]

    monkeypatch.setattr(
        "backtest.grid.grid_runner.get_profile",
        lambda _name: profile,
    )

    def _mock_run_single(combo: dict):
        name = combo.get("bot_name", "mock_bot")
        return name, {
            "config": dict(combo),
            "net_pnl_usd": 100.0,
            "total_trades": 1,
            "win_rate_pct": 50.0,
            "profit_factor": 1.0,
            "max_drawdown_pct": 1.0,
            "max_drawdown_pct_vs_peak": 1.0,
            "max_drawdown_usd": -10.0,
            "sharpe_ratio": 0.5,
        }

    monkeypatch.setattr("backtest.grid.grid_runner.run_single", _mock_run_single)

    results, output_dir = run_grid(
        "bot_optimalisation",
        sequential=True,
        quiet=True,
        checkpoint_every=0,
        base_output=tmp_path / "results",
    )

    assert output_dir.name == "grid_bot_optimalisation_M30_2023-04-24_2026-04-24_001"
    assert output_dir.parent == tmp_path / "results" / "USDCAD"
    assert (tmp_path / "results" / "USDCAD").is_dir()
    assert output_dir.is_dir()
    assert "USDCAD.x" not in output_dir.parts

    for _name, stats in results.items():
        assert stats["config"]["symbol"] == "USDCAD.x"
