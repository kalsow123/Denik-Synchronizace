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

    `burn_in_df` (window-shift bug fix, viz `scripts/_diag_window_shift_check.py`):
    `PineWaveDetector` cold-seeduje pivot/cand stav z baru 0 svého vstupního `df`
    s HARDCODED `pivot_dir=1` (viz `wave_detection_pine.run_pine_wave_simulation`,
    initial_state==None větev). Pro FIXNÍ `df` (celý backtest) je to jen jeden
    (byť arbitrární) seed bod na začátku historie — vlny o desítky/stovky barů
    dál jsou už na něm prakticky nezávislé (stavový stroj "zapomene" seed).

    ALE živý bot (`runtime.live_engine_session.LiveEngineSession.refresh_df_if_needed`)
    dostává z MT5 ROLLING okno (posledních `cfg.startup_bars` barů) — s KAŽDÝM
    novým barem se okno posune o 1, takže bar 0 (=seed bod) je JINÝ bar při
    KAŽDÉM refreshi. Empiricky (diag skript) to i pro posun o 1 bar mění ~0.5 %
    definic vln, s rozdíly hluboko (stovky barů) do okna — vlny už POUŽITÉ pro
    vstup se retroaktivně překlasifikují jen kvůli posunu okna, ne kvůli nové
    ceně. To je bug, ne genuine nová informace.

    Fix: pokud je dodán `burn_in_df` (bary bezprostředně PŘED `df`, ze stejného
    zdroje), detektor se cold-seeduje o `len(burn_in_df)` barů DŘÍV (na začátku
    burn-in prefixu), takže do doby, kdy dojde na bar 0 volajícím viditelného
    `df`, stavový stroj už seed „zapomněl“ (viz konvergenční hloubka v diag
    skriptu — burn-in je zvolen s bezpečnou rezervou nad touto hloubkou).
    Burn-in bary samotné se navenek NEVYSTAVUJÍ (`waves_at`/`birth_map`/
    `all_waves`/ext mapy vrací jen bary/vlny s indexem >= 0 v `df`-relativní
    číselné ose) — engine tedy dál pracuje jen s `df` (žádná změna replay
    okna/rozhodovací historie), jen s KONVERGOVANÝM vstupním stavem detektoru.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        cfg: BotConfig,
        *,
        start_bar: int = 1,
        initial_state: dict | None = None,
        burn_in_df: pd.DataFrame | None = None,
    ) -> None:
        from strategy.wave_detection_pine import PineWaveDetector

        self.df = df
        self.cfg = cfg
        self._burn_in_len = 0
        wide_df = df
        # initial_state (segmentovy WF resume) uz nese explicitni seed — burn-in
        # by ho jen zbytecne prepocitaval (a posunul indexaci), proto se pouzije
        # jen pri cold-startu (initial_state is None).
        if burn_in_df is not None and len(burn_in_df) > 0 and initial_state is None:
            wide_df = pd.concat(
                [burn_in_df.reset_index(drop=True), df.reset_index(drop=True)],
                ignore_index=True,
            )
            self._burn_in_len = len(burn_in_df)
        self._det = PineWaveDetector(
            wide_df, cfg, start_bar=start_bar, initial_state=initial_state
        )

    def _to_local_bar(self, wide_bar: int) -> int:
        """Prevede bar index ve `wide_df` (burn-in + df) na index v `df`."""
        return int(wide_bar) - self._burn_in_len

    def _shift_wave(self, w: dict) -> dict:
        if self._burn_in_len <= 0:
            return w
        shifted = dict(w)
        for key in ("draw_left", "draw_right"):
            if key in shifted:
                shifted[key] = self._to_local_bar(shifted[key])
        return shifted

    def waves_at(self, i: int) -> List[dict]:
        wide_i = int(i) + self._burn_in_len
        born = self._det.advance(wide_i)
        if not born:
            return []
        return [self._shift_wave(w) for w in born]

    def birth_map(self) -> Dict[str, int]:
        if self._burn_in_len <= 0:
            return dict(self._det.birth)
        # Vlny narozene behem burn-in (mimo `df`) se navenek nevystavuji —
        # patri jen ke konvergenci detektoru, ne k rozhodovaci historii `df`.
        return {
            wt: self._to_local_bar(b)
            for wt, b in self._det.birth.items()
            if b >= self._burn_in_len
        }

    def all_waves(self) -> List[dict]:
        if self._burn_in_len <= 0:
            return list(self._det._all_waves)
        valid = self.birth_map()
        return [
            self._shift_wave(w)
            for w in self._det._all_waves
            if str(w.get("wave_time")) in valid
        ]

    @property
    def ext_counter_suppress_from_bar(self) -> Dict[str, int]:
        if self._burn_in_len <= 0:
            return dict(self._det.ext_counter_suppress_from_bar)
        valid = self.birth_map()
        return {
            wt: max(0, self._to_local_bar(b))
            for wt, b in self._det.ext_counter_suppress_from_bar.items()
            if wt in valid
        }

    @property
    def ext_forming_first_bar(self) -> Dict[str, int]:
        if self._burn_in_len <= 0:
            return dict(self._det.ext_forming_first_bar)
        valid = self.birth_map()
        return {
            wt: max(0, self._to_local_bar(b))
            for wt, b in self._det.ext_forming_first_bar.items()
            if wt in valid
        }


def make_wave_source(df: pd.DataFrame, cfg: BotConfig) -> WaveSource:
    """Vyber WaveSource podle `cfg.wave_detection_mode`."""
    mode = getattr(cfg, "wave_detection_mode", WaveDetectionMode.LEGACY_PRECOMPUTE)
    if str(getattr(mode, "value", mode)) == WaveDetectionMode.INCREMENTAL_CAUSAL.value:
        return IncrementalWaveSource(df, cfg)
    return LegacyWaveSource(df, cfg)
