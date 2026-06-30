"""
LiveEngineSession — strangler vrstva pro LIVE (VARIANTA A.txt §5.2, akce 2B).

CÍL:
  Nahradit duplicitní rozhodování v `runtime/live_loop.py`
  JEDNÍM voláním `BacktestEngine.process_bar()` s `IncrementalWaveSource`. Live tím
  sdílí JEDEN rozhodovač s backtesterem → rozhodnutí se shodují Z KONSTRUKCE.

  Tato session DRŽÍ engine instanci + prepared `BarContext` + executor a poskytuje:
    - `process_closed_bars(df_closed, bar_indices)` — for i in indices: process_bar(i)
    - `catch_up_missed(df_closed, last_processed_ts)` — indexy nových closed barů
      (logika v `new_closed_bar_indices` níže) + log MISSED_BARS_CATCH_UP.
    - `closed_bars_only(df)` — forming-bar strip (live-only kontrakt; MT5 get_bars
      vrací i nedokončený bar).

FEATURE FLAG:
  Volá se jen když `cfg.live_use_process_bar` je True. Default OFF → live_loop běží
  PŘESNĚ jako dnes (žádná změna chování). Viz `runtime/live_loop.py` větvení.

INKREMENTÁLNÍ VLNY (per-bar advance):
  Session si drží vlastní `IncrementalWaveSource` (PineWaveDetector.advance(i),
  O(n)). `engine.prepare(df, wave_source=src)` v incremental režimu materializuje
  `ctx.waves_by_bar[i]` přes per-bar advance (engine.py: smyčka advance(1..n-1)),
  takže před každým `process_bar(i)` jsou vlny narozené na baru i připravené. Pro
  budoucí growing-df live cestu (data přibývají po jednom baru) je hook
  `advance_waves_for_bar(i)` (sjednoceno s 1B/1F WaveSource API).

LIVE-ONLY KONTRAKT (zůstává v orchestraci live_loop / LiveExecutor, NE v process_bar):
  forming-bar strip, guard/dedup (LiveExecutor), recovery (startup.py), TZ align
  (session_manager), session pre-close cancel, filling/retcode (infra/orders).
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, List, Optional

import pandas as pd

from config.bot_config import BotConfig
from config.enums import WaveDetectionMode
from config.position_modes import resolve_grid_engine_config

if TYPE_CHECKING:  # pragma: no cover - jen typy
    from backtest.executor import BarContext, Executor


# Rozhodovací flagy, které `resolve_grid_engine_config` na DRUHÉ aplikaci (na již
# vyřešený engine cfg) chybně vypne (wave_isolation_study už stripnuto). Slouží jako
# detekce "už vyřešeno" — viz `_ensure_grid_engine_config`.
_ENGINE_DECISION_FLAGS = (
    "wave_counter_two_sided_enabled",
    "counter_position_enabled",
    "two_sided_entry_enabled",
    "ext_enabled",
    "ext_counter_enabled",
    "bos_entry_in_rrr_fixed",
)


def _ensure_grid_engine_config(cfg: BotConfig) -> BotConfig:
    """
    Vrať grid engine config — idempotentně vůči `resolve_grid_engine_config`.

    `resolve_grid_engine_config` NENÍ idempotentní: aplikace na již vyřešený engine
    cfg vypne counter/EXT/two_sided. Detekce: pokud by `resolve` vypnul flag, který
    je v `cfg` zapnutý, byl `cfg` už vyřešený engine cfg → použij ho přímo. Jinak
    (raw preset) vrať jeden výsledek `resolve`. Tím engine drží STEJNÉ rozhodovací
    flagy jako backtester (decision parity).
    """
    resolved = resolve_grid_engine_config(cfg)
    for flag in _ENGINE_DECISION_FLAGS:
        if bool(getattr(cfg, flag, False)) and not bool(getattr(resolved, flag, False)):
            return cfg
    return resolved


def new_closed_bar_indices(
    df: pd.DataFrame,
    last_processed: pd.Timestamp | None,
) -> List[int]:
    """
    Indexy uzavřených barů novějších než `last_processed`.
    """
    out: List[int] = []
    for i in range(len(df)):
        ts = pd.Timestamp(df["time"].iloc[i])
        if last_processed is None or ts > last_processed:
            out.append(i)
    return out


def closed_bars_only(df: pd.DataFrame) -> pd.DataFrame:
    """
    Forming-bar strip — strategie běží JEN na uzavřených barech (live-only kontrakt).

    MT5 `get_bars()` vrací i poslední nedokončený (forming) bar; ten se musí
    zahodit PŘED enginem. Zrcadlí `runtime.live_loop._df_closed_bars_only`.
    """
    if len(df) < 2:
        return df
    return df.iloc[:-1].reset_index(drop=True)


class LiveEngineSession:
    """
    Drží `BacktestEngine` + prepared `BarContext` + `Executor` pro live strangler.

    Cold start / reset = `prepare(df)` (zde v __init__). Mezi bary se rozhodnutí
    provádí přes `process_closed_bars()`.
    """

    def __init__(
        self,
        cfg: BotConfig,
        df: pd.DataFrame,
        *,
        executor: Optional["Executor"] = None,
        apply_orders: bool = True,
    ) -> None:
        # Engine config = grid engine pravidla + vynucený incremental_causal režim
        # (referenční pravda pro live paritu; __post_init__ vynutí causal_mode=True).
        #
        # POZOR: `resolve_grid_engine_config` NENÍ idempotentní — 2. aplikace na již
        # vyřešený engine cfg vypne counter/EXT/two_sided (wave_isolation_study už
        # stripnuto). Live flow (`run_live_loop` → `resolve_live_execution_config` →
        # `resolve_grid_engine_config`) i decision-parity test DODÁVAJÍ JIŽ VYŘEŠENÝ
        # engine cfg, takže ho používáme PŘÍMO (jen znovuvyřešíme, pokud ještě nebyl —
        # raw preset). Tím engine drží STEJNÉ rozhodovací flagy jako backtester.
        engine_cfg = _ensure_grid_engine_config(cfg)
        if engine_cfg.wave_detection_mode != WaveDetectionMode.INCREMENTAL_CAUSAL:
            engine_cfg = replace(
                engine_cfg,
                wave_detection_mode=WaveDetectionMode.INCREMENTAL_CAUSAL,
            )
        self.cfg = cfg
        self.engine_cfg = engine_cfg

        from backtest.engine import BacktestEngine

        self.engine = BacktestEngine(engine_cfg)
        # Executor: default LiveExecutor (MT5 pass-through); testy injektují spy.
        if executor is None:
            from runtime.live_executor import LiveExecutor

            executor = LiveExecutor(engine_cfg, apply_orders=apply_orders)
        self.executor: "Executor" = executor
        self.ctx: Optional["BarContext"] = None
        self._wave_source = None
        self._df: Optional[pd.DataFrame] = None
        self.prepare(df)

    # ------------------------------------------------------------------
    def prepare(self, df: pd.DataFrame) -> "BarContext":
        """
        Cold start / reset: vlastní `IncrementalWaveSource` + `engine.prepare()`.

        `prepare()` v incremental režimu provede O(n) per-bar advance detektoru a
        naplní `ctx.waves_by_bar` (engine.py). Tím jsou vlny narozené na každém
        baru materializované před `process_bar(i)`.
        """
        from strategy.wave_source import IncrementalWaveSource

        self._df = df
        self._wave_source = IncrementalWaveSource(df, self.engine_cfg)
        self.ctx = self.engine.prepare(df, wave_source=self._wave_source)
        self.engine._executor = self.executor
        return self.ctx

    def advance_waves_for_bar(self, i: int) -> List[dict]:
        """
        Vlny narozené na baru `i` (per-bar advance hook; sjednoceno s 1B/1F API).

        V cold-start režimu jsou už materializované v `ctx.waves_by_bar` (prepare
        provedl plný incremental advance). Pro budoucí growing-df live cestu (2F)
        sem patří `IncrementalWaveSource.advance(i)` doplnění `ctx.waves_by_bar[i]`.
        """
        if self.ctx is None:
            return []
        return list(self.ctx.waves_by_bar.get(int(i), []))

    # ------------------------------------------------------------------
    def process_closed_bars(
        self, df_closed: pd.DataFrame, bar_indices: List[int]
    ) -> None:
        """
        JÁDRO strangleru: for i in bar_indices → engine.process_bar(i, ctx, executor).

        Index 0 (nejstarší načtený bar) se přeskakuje — `engine.run()` zpracovává
        1..n-1 (bar 0 nemá předchozí bar pro rozhodnutí). Catch-up po jednom i
        batch dávají IDENTICKÝ výsledek (= N× process_bar nad sdíleným ctx).
        """
        if self.ctx is None:
            raise RuntimeError("LiveEngineSession.prepare() nebyl zavolán (ctx is None).")
        for i in bar_indices:
            ii = int(i)
            if ii < 1:
                continue
            self.advance_waves_for_bar(ii)
            self.engine.process_bar(ii, self.ctx, self.executor)

    def catch_up_missed(
        self,
        df_closed: pd.DataFrame,
        last_processed_ts: pd.Timestamp | None,
    ) -> List[int]:
        """
        Vrať indexy nových closed barů (> last_processed_ts) k zpracování.

        Catch-up = N× process_bar nad sdíleným ctx. Když je >1 nový bar (= výpadek /
        restart), zaloguje MISSED_BARS_CATCH_UP (parita s live_loop).
        """
        indices = new_closed_bar_indices(df_closed, last_processed_ts)
        if len(indices) > 1:
            try:
                from core.logging_utils import log_event

                log_event(
                    self.cfg,
                    "info",
                    "MISSED_BARS_CATCH_UP",
                    missed_bars=int(len(indices) - 1),
                    first_bar_idx=int(indices[0]),
                    last_bar_idx=int(indices[-1]),
                )
            except Exception:  # pragma: no cover - log nesmí shodit catch-up
                pass
        return indices

    @staticmethod
    def closed_bars_only(df: pd.DataFrame) -> pd.DataFrame:
        """Forming-bar strip (viz modul-level `closed_bars_only`)."""
        return closed_bars_only(df)
