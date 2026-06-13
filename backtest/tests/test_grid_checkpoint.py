"""Průběžný checkpoint grid_report — logika modulo + init xlsx."""
from pathlib import Path

import pandas as pd

from backtest.grid.grid_report_io import GRID_CHECKPOINT_EVERY, init_grid_report_workbook
from backtest.io.excel_export import GRID_REPORT_XLSX, GRID_SHEET_VYSLEDKY, load_grid_report_sheet


def test_checkpoint_every_constant():
    assert GRID_CHECKPOINT_EVERY == 100


def _should_checkpoint(done: int, total: int, every: int = 100) -> bool:
    if done <= 0:
        return False
    return done % every == 0 or done == total


def test_checkpoint_triggers():
    assert _should_checkpoint(100, 250)
    assert _should_checkpoint(200, 250)
    assert _should_checkpoint(250, 250)
    assert not _should_checkpoint(50, 250)
    assert _should_checkpoint(50, 50)
    assert not _should_checkpoint(0, 100)


def test_init_grid_report_workbook_empty_sheets(tmp_path):
    """Prázdný init nesmí spadnout na 'At least one sheet must be visible'."""
    out = init_grid_report_workbook(tmp_path)
    assert out == tmp_path / GRID_REPORT_XLSX
    assert out.is_file()
    df = load_grid_report_sheet(out, GRID_SHEET_VYSLEDKY)
    assert isinstance(df, pd.DataFrame)
