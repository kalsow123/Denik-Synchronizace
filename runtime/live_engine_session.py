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
  Volá se jen když `cfg.live_engine_usage == LiveEngineUsage.BACKTESTER` (default,
  viz `config/enums.py` a FAZE 3 — live_engine_usage E2E recovery.txt, akce 3C).
  `LiveEngineUsage.E2E` deleguje místo toho na `runtime.live_loop_legacy.run_live_loop()`
  (zamrzlá kopie staré implementace před "2F: tenký live_loop" refaktoringem).

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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

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

    LIFECYCLE (FÁZE 3, akce 3D — oprava P3/P4):
      Session se vytváří JEDNOU při startu live loopu (viz `run_live_loop`
      /`_run_live_loop_backtester`), NE při každém novém baru. `BacktestEngine.
      prepare()` je NUTNĚ destruktivní (`.clear()` na `pending_orders`/
      `open_trades`/`closed_trades`/`sent_signals`/`wave_birth_by_time`/...,
      viz `backtest/engine.py`), takže vytvoření NOVÉ session (nebo zavolání
      `prepare()` a zpracování jen posledního baru) při každém pollu ENGINE
      OKAMŽITĚ ZAPOMÍNÁ celou historii (open trades / pendingy / sent_signals) —
      to byl přesně P3 bug.

      Oprava: `refresh_df_if_needed(df)` volá `prepare()` znovu JEN když se
      opravdu objevil nový closed bar (MT5 vrací rolling okno `cfg.startup_bars`
      barů, které se každým barem posouvá — indexy se tedy mezi cykly NEshodují
      1:1 se starým oknem). Aby `prepare()`'s reset nezpůsobil ztrátu
      nahromaděného stavu, `_run_live_loop_backtester` po každém
      `refresh_df_if_needed` PŘEHRAJE CELÉ aktuální okno (`process_closed_bars(df,
      range(1, len(df)))`), NE jen nové indexy — to je deterministický,
      bit-identický ekvivalent dávkového (batch) zpracování aktuálního okna
      (parita ověřená `tests/test_live_catch_up_parity.py` a
      `tests/test_live_persistent_session_parity.py`), takže `engine.sent_signals`
      / `open_trades` / `pending_orders` na konci každého cyklu odpovídají PLNÉ
      historii okna — ne prázdnému stavu po jednom baru. Skutečné duplicitní
      MT5 volání (opětovné odeslání již odeslaného orderu) blokuje
      `LiveExecutor`/`infra.orders` guard vrstva (`guard_live_send_order`,
      `block_duplicate_*`) — bezpečnostní pojistka, NE primární dedup.

      `sent_signals` (loop-level, z `main.py`/`runtime/startup.py` recovery)
      a `tracker_state` se předávají do `LiveExecutor` (viz `__init__` níže) —
      slouží jen k bookkeepingu (`failed_signals_replay`), NE k enginu
      samotnému (ten má vlastní `engine.sent_signals`, primární zdroj pravdy
      pro dedup vstupů).
    """

    def __init__(
        self,
        cfg: BotConfig,
        df: pd.DataFrame,
        *,
        executor: Optional["Executor"] = None,
        apply_orders: bool = True,
        sent_signals: Optional[Set[str]] = None,
        failed_signals: Optional[Dict[str, Dict[str, Any]]] = None,
        tracker_state: Any = None,
        burn_in_df: Optional[pd.DataFrame] = None,
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
        # `sent_signals`/`tracker_state` (3D): loop-level bookkeeping (main.py /
        # runtime/startup.py recovery, failed_signals_replay) — sdílená reference
        # se sadou z live_loop, NE nový primární dedup (ten drží `engine.sent_signals`).
        if executor is None:
            from runtime.live_executor import LiveExecutor

            executor = LiveExecutor(
                engine_cfg,
                sent_signals=sent_signals,
                tracker_state=tracker_state,
                apply_orders=apply_orders,
            )
        self.executor: "Executor" = executor
        self.sent_signals: Optional[Set[str]] = sent_signals
        self.failed_signals: Optional[Dict[str, Dict[str, Any]]] = failed_signals
        self.tracker_state: Any = tracker_state
        self.ctx: Optional["BarContext"] = None
        self._wave_source = None
        self._df: Optional[pd.DataFrame] = None
        self._last_closed_time: Optional[pd.Timestamp] = None
        self._last_len: int = 0
        self.prepare(df, burn_in_df=burn_in_df)

    # ------------------------------------------------------------------
    def prepare(
        self, df: pd.DataFrame, *, burn_in_df: Optional[pd.DataFrame] = None
    ) -> "BarContext":
        """
        Cold start / reset: vlastní `IncrementalWaveSource` + `engine.prepare()`.

        `prepare()` v incremental režimu provede O(n) per-bar advance detektoru a
        naplní `ctx.waves_by_bar` (engine.py). Tím jsou vlny narozené na každém
        baru materializované před `process_bar(i)`.

        POZOR (3D): `engine.prepare()` je destruktivní — resetuje `pending_orders`/
        `open_trades`/`closed_trades`/`sent_signals`/`wave_birth_by_time`/...
        (viz `backtest/engine.py`). Volající (`refresh_df_if_needed` /
        `_run_live_loop_backtester`) musí po `prepare()` PŘEHRÁT celé aktuální
        okno (`process_closed_bars(df, range(1, len(df)))`), ne jen nové bary —
        jinak engine ztrácí nahromaděný stav (P3 bug).

        `burn_in_df` (window-shift bug fix — viz `strategy/wave_source.py`
        `IncrementalWaveSource` docstring a `scripts/_diag_window_shift_check.py`):
        volitelné bary bezprostředně PŘED `df` (stejný zdroj/kontinuální), použité
        JEN k tomu, aby `PineWaveDetector` cold-seedoval o kus dřív a stav
        (pivot/cand/EXT tracker) konvergoval dřív, než dojde na bar 0 `df` —
        tím se vlny uvnitř `df` stanou nezávislé na tom, kde přesně MT5 rolling
        okno začíná. `df`/`ctx`/replay okno (`process_closed_bars`) se NEMĚNÍ —
        burn-in ovlivňuje jen počáteční stav detektoru, ne rozhodovací historii.
        """
        from strategy.wave_source import IncrementalWaveSource

        self._df = df
        self._wave_source = IncrementalWaveSource(
            df, self.engine_cfg, burn_in_df=burn_in_df
        )
        self.ctx = self.engine.prepare(df, wave_source=self._wave_source)
        self.engine._executor = self.executor
        self._last_closed_time = (
            pd.Timestamp(df["time"].iloc[-1]) if len(df) else None
        )
        self._last_len = len(df)
        return self.ctx

    def refresh_df_if_needed(
        self, df: pd.DataFrame, *, burn_in_df: Optional[pd.DataFrame] = None
    ) -> bool:
        """
        `prepare()` znovu JEN pokud se `df` opravdu změnilo (nový closed bar —
        jiná délka nebo jiný poslední timestamp), NE při opakovaném pollu se
        stejným oknem (5s polling dedup na úrovni `live_loop` volá tuto metodu
        jen po zjištění nového baru, toto je defenzivní druhá pojistka).

        Vrací True pokud proběhl `prepare()` (volající pak MUSÍ přehrát celé
        okno přes `process_closed_bars`, viz docstring `prepare()`).

        `burn_in_df`: viz `prepare()` — předává se dál beze změny; volající
        (`_run_live_loop_backtester`) ho fetchuje z MT5 spolu s `df` (bary
        bezprostředně před `df`), viz `runtime/live_loop.py`.
        """
        if len(df) == 0:
            return False
        new_last = pd.Timestamp(df["time"].iloc[-1])
        if len(df) == self._last_len and new_last == self._last_closed_time:
            return False
        self.prepare(df, burn_in_df=burn_in_df)
        return True

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
