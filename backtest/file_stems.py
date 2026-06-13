"""Kratke unikatni zaklady souborovych jmen — Windows MAX_PATH (~260 znaku cele cesty)."""
from __future__ import annotations

import hashlib


def grid_export_stem(bot_name: str, *, ascii_cap: int = 72) -> str:
    """
    Jednoznacny kratsi stem z grid bot_name (~250+ znaku vs plna cesta k projektu).
    Pouzivat pro CSV tradu, PNG grafu, visual waves atd.
    """
    dg = hashlib.sha256(bot_name.encode("utf-8")).hexdigest()[:14]
    s = "".join(c if str(c).isalnum() or c in "._-" else "_" for c in str(bot_name))
    if len(s) > ascii_cap:
        s = s[:ascii_cap]
    return f"{dg}_{s}"


def export_path_stem(
    bot_name: str,
    *,
    long_threshold: int = 96,
    ascii_cap: int = 72,
) -> str:
    """Pro kratke bot_name ponecha puvodni retezec; pro grid (dlouhe) hash + zkratka."""
    if len(bot_name) <= long_threshold:
        return bot_name
    return grid_export_stem(bot_name, ascii_cap=ascii_cap)


def prefixed_export_stem(stem: str, test_pozice: int | None) -> str:
    """
    Stejné číslo jako sloupec combo_no v grid_report.csv — prefix souboru
    (PNG/HTML/trades), např. 00042_<stem>_equity_monthly_waves_scroll.html.
    """
    if test_pozice is None:
        return stem
    return f"{int(test_pozice):05d}_{stem}"


def _ascii_filename_part(text: str, *, cap: int | None = None) -> str:
    s = "".join(c if str(c).isalnum() or c in "._-" else "_" for c in str(text))
    if cap is not None and len(s) > cap:
        s = s[:cap]
    return s


def visual_waves_export_stem(
    bot_name: str,
    *,
    tp_mode: str | None = None,
    test_pozice: int | None = None,
    ascii_cap: int = 72,
) -> str:
    """
    Stem pro visual_waves HTML: {combo_no}_{tp_mode}_{zkrácený bot_name}.
    Bez hash prefixu — tp_mode nahrazuje dg_ z grid_export_stem.
    """
    name_part = _ascii_filename_part(bot_name, cap=ascii_cap)
    if tp_mode is not None:
        tpm = _ascii_filename_part(tp_mode)
        stem = f"{tpm}_{name_part}"
    else:
        stem = grid_export_stem(bot_name, ascii_cap=ascii_cap)
    return prefixed_export_stem(stem, test_pozice)
