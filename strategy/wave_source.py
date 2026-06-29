"""
WaveSource — adaptery nad detekci vln pro engine (VARIANTA A.txt §3.2).

Sjednocuji rozhrani "ktere vlny jsou k dispozici na baru i" pro dva rezimy
(`config.enums.WaveDetectionMode`):

  LegacyWaveSource (legacy_precompute, default)
    - Obal nad `run_pine_wave_simulation` (pres cache) — JEDNA projekce nad
      celym df + look-ahead post-processing (wave_plus extend do konce rady,
      merge pres gapy, wick cleanup). Reprodukuje DNESNI chovani; vlny jsou
      mapovane na svuj birth bar.

  IncrementalWaveSource (incremental_causal, reference pro live paritu)
    - Obal nad `PineWaveDetector` — per-bar inkrementalni detektor; `waves_at(i)`
      vraci vlny narozene (birth == i) na baru i, wave_plus extend max do bar_i.
      Stav se drzi inkrementalne mezi bary → cely beh O(n) (NE O(n^2)).

POZN.: Toto je cisty strategy/ procesor — zadne live-only veci (MT5, session,
lock). Napojeni do engine.prepare se dela az pozdeji (pred akci 1C), ne zde.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from config.bot_config import BotConfig
from config.enums import WaveDetectionMode


class WaveSource:
    """Spolecne rozhrani: `waves_at(i)` = vlny s birth == i na baru i."""

    df: pd.DataFrame
    cfg: BotConfig

    def waves_at(self, i: int) -> List[dict]:  # pragma: no cover - rozhrani
        raise NotImplementedError

    def birth_map(self) -> Dict[str, int]:  # pragma: no cover - rozhrani
        raise NotImplementedError

    def all_waves(self) -> List[dict]:  # pragma: no cover - rozhrani
        raise NotImplementedError


class LegacyWaveSource(WaveSource):
    """
    Legacy precompute: obaluje dnesni `run_pine_wave_simulation(df)` (pres cache)
    a mapuje vlny na jejich birth bar. Reprodukuje DNESNI chovani.
    """

    def __init__(self, df: pd.DataFrame, cfg: BotConfig, *, use_cache: bool = True) -> None:
        self.df = df
        self.cfg = cfg
        if use_cache:
            from backtest.wave_sim_cache import run_pine_wave_simulation_cached

            waves, birth, ext_suppress, ext_forming = run_pine_wave_simulation_cached(
                df, cfg
            )
        else:
            from strategy.wave_detection_pine import run_pine_wave_simulation

            waves, birth, ext_suppress, ext_forming = run_pine_wave_simulation(df, cfg)

        self._waves: List[dict] = list(waves)
        self._birth: Dict[str, int] = dict(birth)
        self.ext_counter_suppress_from_bar: Dict[str, int] = dict(ext_suppress)
        self.ext_forming_first_bar: Dict[str, int] = dict(ext_forming)

        # vlny seskupene podle birth baru — "dostupne na baru i" = narozene na i.
        self._by_bar: Dict[int, List[dict]] = {}
        for w in self._waves:
            b = self._birth.get(str(w.get("wave_time")))
            if b is None:
                continue
            self._by_bar.setdefault(int(b), []).append(w)

    def waves_at(self, i: int) -> List[dict]:
        return list(self._by_bar.get(int(i), []))

    def waves_available_until(self, i: int) -> List[dict]:
        """Vsechny vlny narozene do baru i vcetne (birth <= i)."""
        return [
            w
            for w in self._waves
            if self._birth.get(str(w.get("wave_time")), 1 << 62) <= int(i)
        ]

    def birth_map(self) -> Dict[str, int]:
        return dict(self._birth)

    def all_waves(self) -> List[dict]:
        return list(self._waves)


class IncrementalWaveSource(WaveSource):
    """
    Incremental causal: obaluje `PineWaveDetector` — per-bar advance(i).

    `waves_at(i)` vraci vlny s birth == i (0..1 vlna). Volat sekvencne,
    vzestupne (stav se drzi inkrementalne, O(n)).
    """

    def __init__(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        *,
        start_bar: int = 1,
        initial_state: dict | None = None,
    ) -> None:
        from strategy.wave_detection_pine import PineWaveDetector

        self.df = df
        self.cfg = cfg
        self._det = PineWaveDetector(
            df, cfg, start_bar=start_bar, initial_state=initial_state
        )

    def waves_at(self, i: int) -> List[dict]:
        return self._det.advance(i)

    def birth_map(self) -> Dict[str, int]:
        return dict(self._det.birth)

    def all_waves(self) -> List[dict]:
        return list(self._det._all_waves)

    @property
    def ext_counter_suppress_from_bar(self) -> Dict[str, int]:
        return dict(self._det.ext_counter_suppress_from_bar)

    @property
    def ext_forming_first_bar(self) -> Dict[str, int]:
        return dict(self._det.ext_forming_first_bar)


def make_wave_source(df: pd.DataFrame, cfg: BotConfig) -> WaveSource:
    """Vyber WaveSource podle `cfg.wave_detection_mode`."""
    mode = getattr(cfg, "wave_detection_mode", WaveDetectionMode.LEGACY_PRECOMPUTE)
    if str(getattr(mode, "value", mode)) == WaveDetectionMode.INCREMENTAL_CAUSAL.value:
        return IncrementalWaveSource(df, cfg)
    return LegacyWaveSource(df, cfg)
