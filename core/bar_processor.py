"""
BarProcessor — rozhodovaci jadro jednoho baru (VARIANTA A.txt §1.5 / §5.1, akce 2A).

CIL (cisty refactor, NULOVA zmena chovani):
  Telo `BacktestEngine.process_bar()` (rozhodovani 1–8 dle §3.4) bylo presunuto
  sem. `BarProcessor` drzi POUZE referenci na `engine` (`self.engine`) a vsechen
  stav (pending/open/closed trady, trackery, EXT/PP/WF state, wave_debug,
  sent_signals, wave_sequence_info, ...) zustava na enginu — pristupuje se k nemu
  PRES SDILENOU REFERENCI `eng = self.engine`, nikdy kopii. Diky tomu se stav
  nerozjede a vystup je bit-identicky s puvodnim monolitem (golden 164 / +39040.88,
  fingerprint 226 closed_trades, incremental 147 / +38100.69).

  `BacktestEngine.process_bar()` zustava jako tenky delegat na
  `self._bar_processor.process_bar(...)` (zachovani verejneho API pro testy).
  Ve fazi 2 stejny BarProcessor pobezi i nad LiveExecutorem — rozhodovani je
  oddelene od provedeni (Executor) i od kontejneru (engine).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from strategy.wave_sequence import compute_wave_2_no_tp_protected_waves
from strategy.two_sided import (
    find_parent_wave_for_two_sided,
    parent_monitor_start_bar,
    parent_wave_qualifies,
    should_open_two_sided_counter,
    skip_primary_entry_on_parent_wave,
    two_sided_enabled,
    wave_counter_two_sided_orders_enabled,
)
from strategy.ext_logic import (
    apply_first_opposite_wave_sl_after_ext,
    ext_bos_on_bar_handler_enabled,
    is_ext_wave,
)
from strategy.trend_bos import _wave_is_wf_origin
from strategy.wick_fakeout import build_wf_wave
from backtest.causal_policy import (
    bos_flip_wave_at_bar,
    retro_bos_entry_allowed,
    wave_for_entry_at_bar,
)

if TYPE_CHECKING:  # pragma: no cover - jen typy
    from backtest.engine import BacktestEngine
    from backtest.executor import BarContext, Executor


class BarProcessor:
    """
    Rozhodovaci jadro jednoho baru. Drzi referenci na `engine`; stav zustava
    na enginu (sdilena reference), takze chovani je bit-identicke s monolitem.
    """

    def __init__(self, engine: "BacktestEngine") -> None:
        self.engine = engine

    def process_bar(self, i: int, ctx: "BarContext", executor: "Executor") -> None:
        """
        Zpracuje jeden bar `i` v poradi 1–8 (viz VARIANTA A.txt §3.4):
          1. EXT1 / forming TP watch
          2. BOS exit + cancel pendings
          3. position-cap prune
          4. two-sided tracker update
          5. TP_WAVE_N event (pred entries)
          6. nove vlny → entry pipeline
          7. WF / EXT / PP / counter / BOS reentry
          8. executor: trigger pending → SL/TP check

        Rozhodovani je v enginu/strategy; order lifecycle (place/cancel/close/
        modify/fill) JEN pres `executor`. Stav baru je v `ctx`.
        """
        eng = self.engine
        cfg = eng.cfg
        df = ctx.df
        ohlc = ctx.ohlc
        waves_by_bar = ctx.waves_by_bar
        waves_by_end_bar = ctx.waves_by_end_bar
        bar = ohlc.bar_view(i)
        bar_time = ohlc.bar_time_at(i)
        high = bar.high
        low = bar.low
        open_ = bar.open
        close_ = bar.close
        mid_price = open_

        eng._maybe_rrr_fixed_better_exit_after_ext1_protect_end(
            i, bar_time, close_,
        )

        # Dynamický výpočet protected_waves per bar (pouze vlny, které se už narodily)
        # Optimalizace: počítáme jen když vznikne nová vlna
        new_waves = waves_by_bar.get(i, [])
        if new_waves:
            ctx.waves_up_to_now.extend(new_waves)
            ctx.protected_waves_bar = compute_wave_2_no_tp_protected_waves(
                ctx.waves_up_to_now, eng.wave_sequence_info, cfg,
            )

        eng._update_forming_tp_watch_on_bar(high, low)

        eng._maybe_model_session_pre_close_cancel(i, bar_time, executor)

        tp_before_bos = bool(
            getattr(cfg, "backtest_tp_wave_before_bos_same_bar", False)
        ) and eng._bar_has_tp_wave_n_birth(new_waves)

        # 0) BOS_EXIT / TP_WAVE_N: default BOS→TP (legacy golden); volitelne TP→BOS (1G).
        if tp_before_bos:
            eng._run_tp_wave_events_on_bar(
                new_waves, i, bar_time, close_, high, low,
            )
            eng._run_bos_exit_block(
                i, bar_time, close_, high, low, ctx.protected_waves_bar,
            )
        else:
            eng._run_bos_exit_block(
                i, bar_time, close_, high, low, ctx.protected_waves_bar,
            )
            eng._run_tp_wave_events_on_bar(
                new_waves, i, bar_time, close_, high, low,
            )

        # Position cap varianta 2: preventivne prune pendingy bez re-queue
        # (position-cap prune jde pres executor — gap-check).
        for o in executor.prune_pendings(mid_price):
            eng._append_pending_vis("pending_pruned", i, bar_time, o)

        if two_sided_enabled(cfg):
            for w in waves_by_end_bar.get(i, []):
                ts_parent = eng.trend_states_per_wave.get(
                    str(w.get("wave_time", ""))
                )
                eng._two_sided_tracker.register_parent(
                    w,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=parent_monitor_start_bar(w),
                    trend_state=ts_parent,
                )
            eng._two_sided_tracker.update_bar(high, low, i)

        # 3) Nove signaly vznikajici na tomto baru
        # (new_waves uz nacteno na zacatku smycky)
        # 3.a) TP-wave event (WAVE_TARGET_N): viz _run_tp_wave_events_on_bar
        #      (volano pred/po BOS dle backtest_tp_wave_before_bos_same_bar).
        # 3.b) Bezne zpracovani nove vlny (entry pipeline) + volitelny
        # TWO-SIDED counter (doplnkovy WAVE na protivlni po doteku FIB rodice).
        for wave in new_waves:
            # POST-EXT ZAMEK: vlna proti seed-smeru v zamcene zone vubec
            # neexistuje — ani jako rodic two-sided mirroru, ani pro PP /
            # EXT BOS state. Skip celou iteraci a zaznamenej do debugu.
            if bool(wave.get("post_ext_trend_suppressed", False)):
                eng.wave_debug["waves_skipped_post_ext_trend_suppressed"] = (
                    eng.wave_debug.get(
                        "waves_skipped_post_ext_trend_suppressed", 0
                    ) + 1
                )
                continue
            eng._advance_ext_bos_state_with_wave(wave, i)
            if bool(getattr(cfg, "pp_enabled", False)):
                eng._pp_on_new_wave_born(wave, i, bar_time)
            eng.wave_debug["waves_birth_total"] += 1
            wave_is_ext = bool(wave.get("is_ext", False)) and is_ext_wave(wave, cfg)
            ext_bypass_trend = False
            if bool(getattr(cfg, "ext_trade_both_sides_in_range", False)):
                if wave_is_ext:
                    ext_bypass_trend = True
                elif bool(wave.get("in_ext_range", False)):
                    ext_bypass_trend = True
            wave_entry = wave
            if wave_is_ext:
                eng._ext_sl_anchor = wave
                if two_sided_enabled(cfg):
                    eng._two_sided_tracker.clear_all()
            else:
                wave_entry, eng._ext_sl_anchor = apply_first_opposite_wave_sl_after_ext(
                    wave,
                    ext_anchor=eng._ext_sl_anchor,
                    cfg=cfg,
                )
                if wave_entry.get("sl") != wave.get("sl"):
                    eng.wave_debug["wave_sl_at_ext_extreme"] = (
                        eng.wave_debug.get("wave_sl_at_ext_extreme", 0) + 1
                    )
            ts_current = eng.trend_states_per_wave.get(
                str(wave.get("wave_time", ""))
            )
            if two_sided_enabled(cfg):
                eng._two_sided_tracker.link_counter_b_wave_if_matches(
                    wave_entry,
                    eng._all_waves,
                    cfg,
                    trend_states_per_wave=eng.trend_states_per_wave,
                )
            waves_for_two_sided = (
                eng._two_sided_tracker.waves_with_armed_parents(eng._all_waves)
                if two_sided_enabled(cfg)
                else eng._all_waves
            )
            prev_wave = find_parent_wave_for_two_sided(
                waves_for_two_sided, wave_entry, cfg,
                trend_states_per_wave=eng.trend_states_per_wave,
            )
            two_sided_only = False
            if (
                two_sided_enabled(cfg)
                and bool(getattr(cfg, "wave_position_enabled", True))
                and prev_wave is not None
            ):
                parent_wt = str(prev_wave.get("wave_time", ""))
                touched = eng._two_sided_tracker.fib_was_touched(parent_wt)
                ts_parent = eng.trend_states_per_wave.get(parent_wt)
                if should_open_two_sided_counter(
                    prev_wave,
                    wave_entry,
                    cfg,
                    parent_fib_touched=touched,
                    parent_trend_state=ts_parent,
                    counter_trend_state=ts_current,
                ):
                    two_sided_only = True
                    eng._two_sided_tracker.register_counter_b_wave(
                        str(wave_entry.get("wave_time", ""))
                    )
                    if wave_counter_two_sided_orders_enabled(cfg):
                        eng._maybe_fire_two_sided_counter(
                            prev_wave, wave_entry, i, bar_time, bar
                        )

            skip_parent_primary = skip_primary_entry_on_parent_wave(
                wave_entry, cfg, trend_state=ts_current,
            )
            skip_b_primary = (
                two_sided_enabled(cfg)
                and eng._two_sided_tracker.is_b_wave_for_any_parent(
                    str(wave_entry.get("wave_time", ""))
                )
            )
            if skip_b_primary:
                eng.wave_debug["two_sided_primary_skip_tracker"] = (
                    eng.wave_debug.get("two_sided_primary_skip_tracker", 0) + 1
                )
            if not two_sided_only and not skip_parent_primary and not skip_b_primary:
                eng._process_new_wave(
                    wave_entry,
                    i,
                    bar_time,
                    bar,
                    bypass_trend_filter=ext_bypass_trend,
                )
            if wave_is_ext:
                eng.wave_debug["ext_waves_detected"] = (
                    eng.wave_debug.get("ext_waves_detected", 0) + 1
                )
                eng._ext_active_waves.append(wave)
                eng._ext_bos_state[str(wave["wave_time"])] = "armed"
                if bool(getattr(cfg, "ext_secondary_enabled", False)):
                    eng._process_ext_secondary_for_wave(wave, i, bar_time, bar)

            if two_sided_enabled(cfg) and parent_wave_qualifies(
                wave, cfg, trend_state=ts_current,
            ):
                eng._two_sided_tracker.register_parent(
                    wave,
                    i,
                    cfg,
                    df=df,
                    sync_from_bar=parent_monitor_start_bar(wave),
                    trend_state=ts_current,
                )
        # =====================================================================
        # WICK FAKEOUT RECOVERY (WF)
        # ---------------------------------------------------------------------
        # Co to dělá:
        #   WF řeší situaci, kdy po dokončení vlny ve směru trendu přijde
        #   protisměrový pohyb, který NENÍ validní BOS (jen wick nad/pod
        #   extrémem last wave, žádný close na druhé straně). Pak se trh
        #   vrátí ve směru trendu a udělá close za opačným extrémem last wave.
        #   Engine by tuto situaci jinak nechal bez definice — WF v tomto
        #   momentě vytvoří NOVOU continuation vlnu od fakeout pivotu
        #   (nejvyšší wick high pro downtrend, nejnižší wick low pro uptrend).
        #
        # Kdy se aktivuje (downtrend):
        #   1) Last wave šla dolů, má definované last_wave_high a last_wave_low.
        #   2) V okně mezi koncem last wave a aktuálním barem byl alespoň
        #      jeden bar s high > last_wave_high (= wick).
        #   3) ŽÁDNÝ bar v okně neměl close > last_wave_high (= nebyl validní
        #      close-based BOS).
        #   4) Aktuální bar má close < last_wave_low (= trend pokračuje).
        #   5) Trh NENÍ ve stavu EXT.
        #   Pro uptrend mirror (last wave nahoru, wick pod low, žádný close
        #   pod low, close nad high last wave, ne EXT).
        #
        # Fakeout pivot:
        #   = max(bar.high) v okně pro downtrend (nejvyšší wick).
        #   = min(bar.low)  v okně pro uptrend  (nejnižší wick).
        #   Bez ohledu na to, ve kterém pořadí v okně tento wick byl.
        #
        # Co se stane:
        #   Vznikne nová vlna ve směru trendu, jejíž swing extrém = fakeout
        #   pivot. Dál ji engine obhospodařuje standardně — standardní pending
        #   STOP setup po jejím dokončení, LFT (pokud zapnuté), filtry, dedup,
        #   všechno jako u jakékoli jiné vlny.
        #
        # WF NEMÁ vlastní entry logiku:
        #   WF jen "dořeší" vykreslení vlny. Vstupy řeší existující flow.
        #
        # Žádný timeout, žádný lookback limit:
        #   Okno je definováno strukturou (konec last wave → aktivační close).
        #   Buď přijde aktivační close → WF aktivace.
        #   Nebo přijde close-based BOS → standardní logika obratu, WF se
        #   neaktivuje.
        #   Nebo trh zůstává uvnitř range → engine čeká dál.
        #
        # Výjimka EXT:
        #   Pokud je trh ve stavu EXT, WF se NEAKTIVUJE. EXT režim má vlastní
        #   logiku a WF tam nepatří. Logni WF_SKIPPED_EXT pro debug.
        #
        # Config:
        #   WF_ENABLED: bool — master switch (default False).
        #   Žádné další WF-specific configy. Vše ostatní (RRR, RISK_USD,
        #   filtry, MAGIC, atd.) sdílené se standardním flow.
        # =====================================================================
        # Krok 1: Aktualizuj WF okno o data aktuálního baru.
        eng._wf_tracker.on_bar(high, low, close_, bar_idx=i)

        # Krok 2: Zkontroluj WF podmínky (před resetem trackeru novou vlnou).
        if bool(getattr(cfg, "wf_enabled", False)):
            _wf_result = eng._wf_tracker.check_wf(close_, bar_idx=i, cfg=cfg)
            if _wf_result is not None:
                if _wf_result["status"] == "ext_skipped":
                    eng.wave_debug["wf_skipped_ext"] += 1
                    import logging as _logging
                    _logging.getLogger(__name__).debug(
                        "WF_SKIPPED_EXT | wave_id=%s | reason=ext_active",
                        str(_wf_result["last_wave"].get("wave_time", "?")),
                    )
                elif _wf_result["status"] == "activate":
                    # Krok 3: Vytvoř syntetický WF wave dict.
                    _wf_wt_raw = df["time"].iloc[i]
                    _wf_wt_str = (
                        _wf_wt_raw.strftime("%Y%m%d%H%M")
                        if hasattr(_wf_wt_raw, "strftime")
                        else str(_wf_wt_raw)
                    )
                    _wf_wave = build_wf_wave(
                        cfg,
                        last_wave=_wf_result["last_wave"],
                        fakeout_pivot=float(_wf_result["fakeout_pivot"]),
                        fakeout_bar_idx=int(_wf_result["fakeout_bar_idx"]),
                        activation_bar_idx=i,
                        wave_time_str=_wf_wt_str,
                        window_min_low=_wf_result.get("window_min_low"),
                        window_max_high=_wf_result.get("window_max_high"),
                    )
                    if _wf_wave is not None:
                        _wf_wave["wave_time_dt"] = pd.to_datetime(
                            _wf_wave["wave_time"], format="%Y%m%d%H%M"
                        )
                        eng.wave_debug["wf_activations"] += 1
                        import logging as _logging
                        _wf_last = _wf_result["last_wave"]
                        _wf_ref_h = float(
                            _wf_result.get(
                                "wf_ref_high",
                                _wf_last.get("box_top", 0.0),
                            )
                        )
                        _wf_ref_l = float(
                            _wf_result.get(
                                "wf_ref_low",
                                _wf_last.get("box_bottom", 0.0),
                            )
                        )
                        _logging.getLogger(__name__).info(
                            "WF_ACTIVATED | wave_id=%s | w_dir=%s"
                            " | last_wave_high=%.5f | last_wave_low=%.5f"
                            " | fakeout_pivot=%.5f | activation_close=%.5f"
                            " | window_size_bars=%d",
                            _wf_wt_str,
                            "down" if int(_wf_last.get("dir", 0)) == -1 else "up",
                            _wf_ref_h,
                            _wf_ref_l,
                            float(_wf_result["fakeout_pivot"]),
                            float(close_),
                            int(_wf_result["window_size"]),
                        )
                        # Krok 4–7: WF entry (fib pozice), PP, navázání klasických vln.
                        eng._on_wf_wave_activated(
                            _wf_wave,
                            i,
                            bar_time,
                            bar,
                            df,
                            waves_by_bar,
                        )
                        # Krok 8: WF vlna se stane novým last_wave pro příští okno.
                        eng._wf_tracker.on_new_wave(
                            _wf_wave,
                            birth_bar=int(_wf_wave.get("draw_right", i)),
                            df=df,
                            force_reset=True,
                        )
                        eng._wf_visual_waves.append(_wf_wave)

        # Krok 6: Reset / nastavení trackeru novou potvrzenou vlnou (po WF check).
        for _wf_w in new_waves:
            if not bool(_wf_w.get("post_ext_trend_suppressed", False)):
                eng._wf_tracker.on_new_wave(_wf_w, birth_bar=i, df=df)
        # ─────────────────────────────────────────────────────────────

        # BOS-vlna retro-aktivace: na baru close-based flipu se pokusi
        # otevrit pozici z TE jedne vlny, kterou flip zpusobil
        # (bypass trend filtru). Funguje pro vsechny tp_mody.
        bos_wave = eng._bos_flip_wave_by_bar.get(i)
        if bos_wave is not None and _wave_is_wf_origin(bos_wave):
            bos_wave = None
        if bos_wave is not None and eng._causal_policy.enabled:
            bos_wave = bos_flip_wave_at_bar(
                eng._causal_policy,
                eng._bos_flip_wave_by_bar,
                i,
                eng.wave_birth_by_time,
            )
        if bos_wave is not None:
            bos_wt = str(bos_wave.get("wave_time", "") or "")
            birth_ix = eng.wave_birth_by_time.get(bos_wt)
            if bos_wt and bos_wt not in eng._retro_bos_attempted:
                if retro_bos_entry_allowed(
                    eng._causal_policy,
                    wave=bos_wave,
                    flip_bar=i,
                    birth=birth_ix,
                ):
                    eng._retro_bos_attempted.add(bos_wt)
                    entry_wave = wave_for_entry_at_bar(
                        eng._causal_policy,
                        bos_wave,
                        i,
                        df,
                        cfg,
                    )
                    eng._process_new_wave(
                        entry_wave,
                        i,
                        bar_time,
                        bar,
                        bypass_trend_filter=True,
                        is_two_sided_mirror=False,
                    )

        # 8) Executor fill model — trigger pending az PO vytvoreni novych orderu
        # na tomto baru (two-sided LIMIT muze fillnout jeste na stejnem baru).
        executor.on_bar_open(i, bar_time, high, low, open_)
        executor.enforce_overflow(i, bar_time, mid_price)

        if bool(getattr(cfg, "ext_enabled", False)) and bool(
            getattr(cfg, "ext_counter_enabled", False)
        ):
            eng._process_ext_counter_time(i, bar_time, open_)

        eng._maybe_fire_extension_tp_on_bar(
            i, bar_time, open_, high, low, close_,
        )
        executor.on_bar_range(i, bar_time, high, low)

        # 3.c) PP break — kontroluje, zda na tomto baru nedoslo k close-baru
        # nad/pod high/low aktualni trend-dir vlny. Pokud ano, polozi PP LIMIT.
        if bool(getattr(cfg, "pp_enabled", False)):
            eng._process_pp_break_on_bar(i, bar_time, close_)

        if ext_bos_on_bar_handler_enabled(cfg):
            eng._process_ext_bos_on_bar(i, bar_time, close_)

        # Kdyz vzniknou nove pendingy v tomto baru, varianta 2 je hned proreze.
        for o in executor.prune_pendings(mid_price):
            eng._append_pending_vis("pending_pruned", i, bar_time, o)

        # 4) Expiry pending (pres executor)
        executor.expire_pendings(i, bar_time)

        # ADX14 gate timeline až po SL/TP/nových vstupech — BOS restart může skončit pauzu v tomže baru.
        if eng.adx14_sim is not None and eng.adx14_sim.active:
            eng.adx14_sim.on_bar(i, bar_time)
