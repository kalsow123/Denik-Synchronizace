"""
Globální formát CSV exportů (český Excel vs. mezinárodní).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal, Union

import pandas as pd

CsvFormat = Literal["cz", "international", "auto"]

GLOBAL_CSV_FORMAT: CsvFormat = "cz"

# Sloupce s „pnl“ v názvu (case-insensitive) → zaokrouhlení při exportu (CSV i Excel).
PNL_ROUND_DECIMALS = 2

# Původní pandas metoda (používá export_csv interně — bez hooku, aby nevznikla rekurze).
_PANDAS_TO_CSV_ORIGINAL = pd.DataFrame.to_csv
_CSV_HOOK_INSTALLED = False


def set_global_csv_format(fmt: str) -> None:
    """Nastaví GLOBAL_CSV_FORMAT ('cz' nebo 'international')."""
    global GLOBAL_CSV_FORMAT
    if fmt not in ("cz", "international"):
        raise ValueError(f"Neplatný csv-format: {fmt!r} (povoleno: cz, international)")
    GLOBAL_CSV_FORMAT = fmt  # type: ignore[assignment]


def _resolve_format(fmt: str | None) -> CsvFormat:
    if fmt is None or fmt == "auto":
        raise ValueError("Interní chyba: _resolve_format vyžaduje explicitní cz/international")
    if fmt not in ("cz", "international"):
        raise ValueError(f"Neplatný formát CSV: {fmt!r}")
    return fmt  # type: ignore[return-value]


def _format_kwargs(fmt: CsvFormat) -> dict[str, Any]:
    if fmt == "cz":
        return {"sep": ";", "decimal": ",", "encoding": "utf-8-sig"}
    return {"sep": ",", "decimal": ".", "encoding": "utf-8"}


def detect_csv_format(path: Union[str, Path]) -> CsvFormat:
    """Detekce z prvního řádku: poměr středníků vs. čárek."""
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    first_line = text.splitlines()[0] if text else ""
    semi = first_line.count(";")
    comma = first_line.count(",")
    return "cz" if semi > comma else "international"


def is_pnl_column(name: str) -> bool:
    """True pokud název sloupce obsahuje 'pnl' (např. net_pnl_usd, FTMO__scaled_net_pnl_usd)."""
    return "pnl" in str(name).lower()


def _column_series(df: pd.DataFrame, col: str) -> pd.Series:
    """Jeden sloupec jako Series (při duplicitních názvech první výskyt)."""
    sel = df[col]
    if isinstance(sel, pd.DataFrame):
        return sel.iloc[:, 0]
    return sel


def round_pnl_columns(df: pd.DataFrame, decimals: int = PNL_ROUND_DECIMALS) -> pd.DataFrame:
    """Zaokrouhlí numerické PNL sloupce na `decimals` desetinných míst."""
    out = df.loc[:, ~df.columns.duplicated()].copy()
    for col in out.columns:
        if not is_pnl_column(col):
            continue
        ser = _column_series(out, col)
        if pd.api.types.is_numeric_dtype(ser):
            out[col] = ser.round(decimals)
    return out


def _prepare_df_for_export(df: pd.DataFrame) -> pd.DataFrame:
    out = round_pnl_columns(df.copy())
    for col in out.columns:
        ser = _column_series(out, col)
        if pd.api.types.is_datetime64_any_dtype(ser):
            out[col] = pd.to_datetime(ser, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        elif ser.dtype == bool or (
            hasattr(ser.dtype, "name") and ser.dtype.name == "boolean"
        ):
            out[col] = ser.map({True: "True", False: "False", pd.NA: ""})
        elif ser.dtype == object:
            # bool v object sloupci
            def _fmt_cell(v: Any) -> Any:
                if v is True:
                    return "True"
                if v is False:
                    return "False"
                if pd.isna(v):
                    return ""
                return v

            out[col] = ser.map(_fmt_cell)
    return out


def _write_dataframe_to_csv_file(
    df: pd.DataFrame,
    path: Union[str, Path],
    fmt: CsvFormat,
    *,
    index: bool = False,
    **kwargs: Any,
) -> None:
    params = _format_kwargs(fmt)
    out = _prepare_df_for_export(df)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _PANDAS_TO_CSV_ORIGINAL(
        out,
        path,
        index=index,
        sep=params["sep"],
        decimal=params["decimal"],
        encoding=params["encoding"],
        quoting=csv.QUOTE_MINIMAL,
        na_rep="",
        **kwargs,
    )


def export_csv(
    df: pd.DataFrame,
    path: Union[str, Path],
    format: str | None = None,
    *,
    index: bool = False,
    **kwargs: Any,
) -> None:
    """
    Uloží DataFrame do CSV.
    format=None → GLOBAL_CSV_FORMAT (výchozí 'cz'); 'cz' | 'international'.
    """
    fmt = _resolve_format(format if format is not None else GLOBAL_CSV_FORMAT)
    _write_dataframe_to_csv_file(df, path, fmt, index=index, **kwargs)


def _patched_dataframe_to_csv(self, path_or_buf=None, *args: Any, **kwargs: Any):
    """Zachytí df.to_csv('soubor.csv') a přesměruje na export_csv (český formát)."""
    if path_or_buf is not None:
        path_str = str(path_or_buf)
        if path_str.lower().endswith(".csv"):
            index = kwargs.pop("index", True)
            passthrough = {
                k: v
                for k, v in kwargs.items()
                if k not in ("sep", "decimal", "encoding", "quoting", "na_rep", "lineterminator")
            }
            export_csv(self, path_or_buf, index=index, **passthrough)
            return None
    return _PANDAS_TO_CSV_ORIGINAL(self, path_or_buf, *args, **kwargs)


def ensure_csv_export_defaults() -> None:
    """
    Aktivuje výchozí český formát a hook na DataFrame.to_csv pro soubory .csv.
    Volá se automaticky při importu modulu — není potřeba žádný speciální CLI přepínač.
    """
    global _CSV_HOOK_INSTALLED, GLOBAL_CSV_FORMAT
    GLOBAL_CSV_FORMAT = "cz"
    if not _CSV_HOOK_INSTALLED:
        pd.DataFrame.to_csv = _patched_dataframe_to_csv  # type: ignore[method-assign]
        _CSV_HOOK_INSTALLED = True


ensure_csv_export_defaults()


def read_csv(
    path: Union[str, Path],
    format: str = "auto",
    **kwargs: Any,
) -> pd.DataFrame:
    """
    Načte CSV. format='auto' detekuje cz vs. international z prvního řádku.
    """
    path = Path(path)
    if format == "auto":
        fmt = detect_csv_format(path)
    else:
        fmt = _resolve_format(format)
    params = _format_kwargs(fmt)
    read_kw = dict(kwargs)
    read_kw.setdefault("sep", params["sep"])
    read_kw.setdefault("decimal", params["decimal"])
    read_kw.setdefault("encoding", params["encoding"])
    read_kw.setdefault("keep_default_na", True)
    return pd.read_csv(path, **read_kw)


def append_csv_row(
    path: Union[str, Path],
    row: list[Any],
    *,
    header: list[str] | None = None,
    format: str | None = None,
) -> None:
    """Přidá jeden řádek do CSV (index soubory); respektuje GLOBAL_CSV_FORMAT."""
    fmt = _resolve_format(format if format is not None else GLOBAL_CSV_FORMAT)
    params = _format_kwargs(fmt)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    encoding = params["encoding"]
    with path.open("a", newline="", encoding=encoding) as f:
        w = csv.writer(
            f,
            delimiter=params["sep"],
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        if new_file and header:
            w.writerow(header)
        w.writerow(row)
