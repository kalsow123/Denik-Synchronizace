"""Segmentace wave boxů / linek přes víkendový gap."""
from __future__ import annotations

import pandas as pd

from backtest.waves_plotly_figure import (
    _compute_data_gap_rangebreaks,
    _plot_x_segments,
)


def test_plot_x_segments_splits_weekend_gap():
    bar_td = pd.Timedelta(minutes=30)
    ts = pd.Series(
        pd.to_datetime(
            [
                "2026-03-27 20:00",
                "2026-03-27 20:30",
                "2026-03-27 21:00",
                "2026-03-30 00:00",
                "2026-03-30 00:30",
            ]
        )
    )
    segs = _plot_x_segments(ts, 0, 4, bar_td)
    assert len(segs) == 2
    assert segs[0][1] <= ts.iloc[2] + bar_td
    assert segs[1][0] >= ts.iloc[3] - bar_td
    assert segs[0][1] < segs[1][0]


def test_plot_x_segments_single_run_without_gap():
    bar_td = pd.Timedelta(minutes=30)
    ts = pd.Series(
        pd.to_datetime(
            [
                "2026-03-30 00:00",
                "2026-03-30 00:30",
                "2026-03-30 01:00",
            ]
        )
    )
    segs = _plot_x_segments(ts, 0, 2, bar_td)
    assert len(segs) == 1


def test_data_gap_rangebreaks_keep_friday_bars_visible():
    """Regrese: rangebreaks se pocitaji z REALNYCH mezer, ne z pevneho
    'fri 21:45 -> sun 23:05'. Broker obchodujici v patek do 23:30 a otevirajici
    v pondeli 00:00 nesmi mit skryte patecni vecerni bary."""
    bar_td = pd.Timedelta(minutes=30)
    ts = pd.Series(
        pd.to_datetime(
            [
                "2026-03-13 22:30",  # patek vecer (realny bar)
                "2026-03-13 23:00",  # patek vecer (realny bar — sem padalo minimum)
                "2026-03-13 23:30",  # posledni patecni bar pred vikendem
                "2026-03-16 00:00",  # pondeli open (po 48h mezere)
                "2026-03-16 00:30",
            ]
        )
    )
    rb = _compute_data_gap_rangebreaks(ts, bar_td)
    assert len(rb) == 1
    lo, hi = rb[0]["bounds"]
    # Mezera zacina az ZA poslednim patecnim barem a konci na pondelnim openu.
    assert lo == pd.Timestamp("2026-03-14 00:00")
    assert hi == pd.Timestamp("2026-03-16 00:00")

    def hidden(t):
        return any(d["bounds"][0] <= t < d["bounds"][1] for d in rb)

    # Vsechny patecni vecerni bary MUSI zustat viditelne.
    for t in ts[:3]:
        assert not hidden(t), f"patecni bar {t} se nesmi skryt"
    # Pondelni open viditelny.
    assert not hidden(ts.iloc[3])


def test_data_gap_rangebreaks_none_without_gaps():
    bar_td = pd.Timedelta(minutes=30)
    ts = pd.Series(
        pd.to_datetime(
            ["2026-03-30 00:00", "2026-03-30 00:30", "2026-03-30 01:00"]
        )
    )
    assert _compute_data_gap_rangebreaks(ts, bar_td) == []
