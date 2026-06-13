"""Grid report xlsx progress loader."""
from backtest.grid.grid_report_progress import format_progress_bar, generating_label


def test_format_progress_bar_edges():
    assert format_progress_bar(0).startswith("[")
    assert "·" in format_progress_bar(0)
    assert format_progress_bar(100).count("=") >= 1
    assert format_progress_bar(150) == format_progress_bar(100)
    assert format_progress_bar(-5) == format_progress_bar(0)


def test_generating_label_prefix():
    assert generating_label("tabulku vysledku").startswith("Generuji:")
    assert generating_label("Generuji: CSV") == "Generuji: CSV"
