"""Pytest: backtest.io.csv_export — český / mezinárodní CSV."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from backtest.io import csv_export as ce


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close_time": [
                datetime(2024, 1, 15, 10, 30, 0),
                datetime(2024, 2, 20, 14, 0, 0),
            ],
            "pnl_usd": [1.5, -0.25],
            "close_reason": ["TP", "TP, partial"],
            "ok": [True, False],
            "note": [float("nan"), 3.0],
        }
    )


@pytest.fixture
def profitable_trades_df() -> pd.DataFrame:
    rows = []
    for m in range(1, 13):
        rows.append(
            {
                "close_time": datetime(2024, m, 10),
                "pnl_usd": 100.0,
                "close_reason": "TP",
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def losing_trades_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close_time": [datetime(2024, m, 5) for m in range(1, 13)],
            "pnl_usd": [-50.0] * 12,
            "close_reason": ["SL"] * 12,
        }
    )


@pytest.fixture
def mixed_trades_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close_time": [datetime(2024, m, 1) for m in range(1, 13)],
            "pnl_usd": [80.0, -40.0] * 6,
            "close_reason": ["TP"] * 12,
        }
    )


class TestDuplicateColumns:
    def test_prepare_export_handles_duplicate_column_names(self):
        df = pd.DataFrame(
            {
                "bot_name": ["a"],
                "pnl_usd": [1.234],
                "pnl_usd_dup": [1.234],
            }
        )
        df = pd.concat([df, df[["pnl_usd"]].rename(columns={"pnl_usd": "pnl_usd"})], axis=1)
        assert isinstance(df["pnl_usd"], pd.DataFrame)
        prepared = ce._prepare_df_for_export(df)
        assert "pnl_usd" in prepared.columns


class TestPnlRounding:
    def test_round_pnl_columns_by_name(self):
        df = pd.DataFrame(
            {
                "net_pnl_usd": [123.456789, -0.001],
                "FTMO__scaled_net_pnl_usd": [99.999, 50.004],
                "profit_factor": [1.234567, 2.0],
                "trades": [10, 20],
            }
        )
        out = ce.round_pnl_columns(df)
        assert out["net_pnl_usd"].tolist() == [123.46, -0.0]
        assert out["FTMO__scaled_net_pnl_usd"].tolist() == [100.0, 50.0]
        assert out["profit_factor"].tolist() == [1.234567, 2.0]

    def test_export_rounds_pnl_in_file(self, tmp_path: Path):
        df = pd.DataFrame({"pnl_usd": [1.234567]})
        path = tmp_path / "pnl.csv"
        ce.export_csv(df, path, format="international")
        assert "1.23" in path.read_text(encoding="utf-8")


class TestExportFormats:
    def test_cz_decimal_comma_in_file(self, sample_df, tmp_path: Path):
        path = tmp_path / "cz.csv"
        ce.export_csv(sample_df, path, format="cz")
        text = path.read_text(encoding="utf-8-sig")
        assert ";" in text.splitlines()[0]
        assert "1,5" in text
        assert "TP, partial" in text or '"TP, partial"' in text

    def test_international_decimal_dot_in_file(self, sample_df, tmp_path: Path):
        path = tmp_path / "intl.csv"
        ce.export_csv(sample_df, path, format="international")
        text = path.read_text(encoding="utf-8")
        assert "," in text.splitlines()[0]
        assert "1.5" in text
        assert ";" not in text.splitlines()[0]

    def test_nan_empty_not_nan_string(self, sample_df, tmp_path: Path):
        path = tmp_path / "nan.csv"
        ce.export_csv(sample_df, path, format="cz")
        assert "nan" not in path.read_text(encoding="utf-8-sig").lower()

    def test_bool_true_false(self, sample_df, tmp_path: Path):
        path = tmp_path / "bool.csv"
        ce.export_csv(sample_df, path, format="cz")
        text = path.read_text(encoding="utf-8-sig")
        assert "True" in text
        assert "False" in text


class TestRoundTrip:
    def test_round_trip_cz(self, sample_df, tmp_path: Path):
        path = tmp_path / "rt_cz.csv"
        ce.export_csv(sample_df, path, format="cz")
        back = ce.read_csv(path, format="cz")
        assert list(back.columns) == list(sample_df.columns)
        assert back["pnl_usd"].iloc[0] == pytest.approx(1.5)
        assert back["close_reason"].iloc[1] == "TP, partial"

    def test_round_trip_international(self, sample_df, tmp_path: Path):
        path = tmp_path / "rt_intl.csv"
        ce.export_csv(sample_df, path, format="international")
        back = ce.read_csv(path, format="international")
        assert back["pnl_usd"].iloc[0] == pytest.approx(1.5)

    def test_auto_detect_cz(self, sample_df, tmp_path: Path):
        path = tmp_path / "auto_cz.csv"
        ce.export_csv(sample_df, path, format="cz")
        back = ce.read_csv(path, format="auto")
        assert back["pnl_usd"].iloc[0] == pytest.approx(1.5)

    def test_auto_detect_international(self, sample_df, tmp_path: Path):
        path = tmp_path / "auto_intl.csv"
        ce.export_csv(sample_df, path, format="international")
        back = ce.read_csv(path, format="auto")
        assert back["pnl_usd"].iloc[0] == pytest.approx(1.5)


class TestDetectFormat:
    def test_detect_cz_vs_intl(self, sample_df, tmp_path: Path):
        cz_p = tmp_path / "a.csv"
        intl_p = tmp_path / "b.csv"
        ce.export_csv(sample_df, cz_p, format="cz")
        ce.export_csv(sample_df, intl_p, format="international")
        assert ce.detect_csv_format(cz_p) == "cz"
        assert ce.detect_csv_format(intl_p) == "international"


class TestDataFrameHook:
    def test_dataframe_to_csv_uses_cz_format(self, sample_df, tmp_path: Path):
        """df.to_csv('file.csv') bez export_csv() — stejný český formát."""
        path = tmp_path / "hook.csv"
        sample_df.to_csv(path, index=False)
        text = path.read_text(encoding="utf-8-sig")
        assert ";" in text.splitlines()[0]
        assert "1,5" in text


class TestGlobalFormat:
    def test_global_format_default_export(self, sample_df, tmp_path: Path):
        old = ce.GLOBAL_CSV_FORMAT
        try:
            ce.set_global_csv_format("cz")
            path = tmp_path / "global.csv"
            ce.export_csv(sample_df, path)
            assert ";" in path.read_text(encoding="utf-8-sig").splitlines()[0]
        finally:
            ce.set_global_csv_format(old)
