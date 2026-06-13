"""Terminálový průběh generování grid_report.xlsx (fázový, ne velikost souboru)."""
from __future__ import annotations

import sys

_BAR_WIDTH = 30
_PHASE_NOTE = "(% = faze, ne mereny cas)"


def format_progress_bar(pct: int, width: int = _BAR_WIDTH) -> str:
    pct = max(0, min(100, int(pct)))
    filled = int(width * pct / 100)
    return f"[{'=' * filled}{'·' * (width - filled)}]"


def generating_label(detail: str) -> str:
    """Poznamka u % — co se prave generuje."""
    detail = str(detail).strip()
    if detail.lower().startswith("generuji"):
        return detail
    return f"Generuji: {detail}"


class GridReportProgress:
    """Jednoduchý fázový loader do terminálu (\\r přepis jednoho řádku)."""

    def __init__(
        self,
        *,
        header: str = "Generuji grid_report.xlsx ...",
        show_phase_note: bool = True,
    ) -> None:
        self._header = header
        self._show_phase_note = show_phase_note
        self._started = False
        self._last_pct = -1

    def start(self) -> None:
        if self._started:
            return
        print(self._header, flush=True)
        if self._show_phase_note:
            print(f"  {_PHASE_NOTE}", flush=True)
        self._started = True

    def update(self, pct: int, label: str) -> None:
        self.start()
        pct = max(0, min(100, int(pct)))
        if pct < self._last_pct:
            pct = self._last_pct
        self._last_pct = pct
        bar = format_progress_bar(pct)
        line = f"  {bar} {pct:3d}%  {generating_label(label)}"
        sys.stdout.write("\r" + line.ljust(120))
        sys.stdout.flush()

    def finish(self) -> None:
        if not self._started:
            return
        self.update(100, "grid_report.xlsx — hotovo")
        sys.stdout.write("\n")
        sys.stdout.flush()
