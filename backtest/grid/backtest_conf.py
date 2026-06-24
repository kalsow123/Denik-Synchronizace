"""
BACKTESTER CONFIGURATION PROFILES:
  - "EXAMPLE"                      - 1 kombinace + komentarovany prehled vsech nastaveni (live + grid/backtest)
  - "full_grid"                    - tisice kombinaci, prvni sirsi prozkum
  - "bot_optimalisation"           - optimalizacni profil (nezavisla kopie EXAMPLE grid bloku)
  - "bot_finish"                   - finalni / OOS profil (nezavisla kopie bot_optimalisation)
  - "positions_setting"            - nastaveni pozic (nezavisla kopie EXAMPLE grid bloku)

Uživatelský slovník parametrů bota a gridu (včetně abort_fib_level): POPIS_NASTAVENI_BOTA_A_BACKTESTERU.txt v kořeni projektu.

Pouziti v CLI:
  python -m backtest.run_backtest --profile grid --grid-profile EXAMPLE
  python scripts/plot_grid_heatmap.py results/grid_EXAMPLE_<cas>/grid_report.csv -o results/heatmap.png

PowerShell (kopie projektu na jinem PC / bez disku D:):
  - Nepouzivejte cd na cizi cestu (napr. D:\\TRADING\\... z desktopu) — na NTB casto disk D: neexistuje.
  - Vzdy pracujte z korene TETO kopie (slozka s backtest/, config/, scripts/).
  - Obnova virtualniho prostredi po kopii (stary .venv muze ukazovat na Python jineho uzivatele):
      .\\scripts\\setup_venv.ps1
  - Rychly test gridu EXAMPLE (nastavi cwd sam):
      .\\scripts\\run_grid_EXAMPLE.ps1
  - Totéž ručně v PowerShell (relativně ke kořeni repa):
      .\\.venv\\Scripts\\python.exe -m backtest.run_backtest --profile grid --grid-profile EXAMPLE --plot --visual-waves --visual-html --visual-full-span --plot-trades --plot-trades-html --output results
  - Jeden soubor na kombinaci (equity + měsíční druhy + vlny, scroll): přidej --plot-scroll-combined-html → složka plots_scroll_combined/ (volitelně --grid-export-top-n N).
  python -m backtest.run_backtest --profile grid --grid-profile full_grid
  python -m backtest.run_backtest --profile grid --grid-profile bot_optimalisation
  python -m backtest.run_backtest --profile grid --grid-profile bot_finish
  python -m backtest.run_backtest --profile grid --grid-profile positions_setting
Struktura kazdeho profilu:
  {
    "grid": [ {parametr: [hodnoty]}, ... ],   # itertools.product pres kazdy dict
    "grid_defaults": {parametr: [hodnoty]},    # volitelne, prida se do kazdeho grid dictu
    "base": { ... },   # volitelne — sdilene parametry pro vsechny kombinace (grid je prepise)
    "prop_firms": { ... }   # volitelne — post-processing po gridu (NENI v BotConfig / live bot)
  }

PROPFIRMS (prop_firms v profilu):
  - Pouze backtester: po grid běhu dopocita scale_factor a sloupce v grid_report.csv.
  - Live bot toto NEPOUZIVA (risk je nastaveny primo na uctu).
  - Presety: FTMO, FXIFY, FINTOKEI (limity v backtest/prop_firm/presets.py).
  - Nastaveni v profilu: blok "prop_firms" NEBO grid klice prop_firms_* (viz EXAMPLE).
  - CLI --prop-firms / --prop-firm-config / --account-size-override / --prop-firm-html
    ma prioritu nad hodnotami z profilu.

Klic "grid" je list dictu - kazdy dict generuje vlastni produkt kombinaci.
Diky tomu lze mit ruzne rozsahy wave_min_pct pro M5 vs H4.

WAVE SESSION FILTER:
  - V "base" muzete nastavit defaultni session filter pro vsechny kombinace.
  - V "grid" muzete pridat dimenzi "wave_allowed_sessions" pro testovani vice
    session-kombinaci najednou. Hodnota None = bez filtru (baseline).
    Translator pak automaticky nastavi wave_session_filter_enabled.

  - Vic casovych oken (date_from / date_to): musi byt dimenze v "grid_defaults" nebo
    v konkretnim "grid" dictu jako listy (napr. "date_from": ["2024-01-01", "2025-01-01"]),
    aby se uplatnil itertools.product. Hodnoty jen v "base" se aplikuji na vsechny
    kombinace stejne — pak budou vysledky pro ruzna obdobi identicke.
    CLI: --date-from / --date-to v grid modu prepisou datum u kazde kombinace.

ENTRY MODE (TREND-FOLLOW LIMIT strategie):
  - Validni hodnoty: "market_fallback", "stop_fallback", "no_fallback".
  - V PROFILECH JE "limit_fallback" nahrazeno za "stop_fallback" (bylo deprecated).

TREND-FOLLOW PRAVIDLO (entry vs SL fib):
  - Musi platit fib_level < sl_fib_level (v gridu vzdy zkontroluj kombinaci s defaultnim
    sl_fib_level=0.8; fib_level 1.0 v tomto modelu nikdy nevytvari platnou BUY geometrii).

SL FIB LEVEL:
  - cfg.sl_fib_level (default 0.8) urcuje hloubku SL retracementu.
  - Pokud chcete grid-skenovat ruzne SL urovne, pridejte dimenzi:
      "sl_fib_level": [0.7, 0.8, 0.9, 1.0]
    Translator (translator.py) musi tuto dimenzi mapovat na BotConfig.

VISUAL WAVES (PNG/HTML vln + obchodu; klice nejsou v BotConfig ani v base profilu):
  - Vychozi chovani pri --visual-waves: cele nactene obdobi + vsechny eligible vlny (full span).
    Muze byt velky/vypomaly export u dlouhych CSV.
  - Orez (poslednich N vln, max. M baru): CLI --visual-clip a volitelne --visual-last-n / --visual-bars.
    Vychozi hodnoty orezu: backtest/visual_waves.py (DEFAULT_VISUAL_*).
  - CLI: --visual-waves (HTML automaticky) , --visual-clip , --visual-last-n , --visual-bars , --visual-full-span
  - Profil bez CLI: visual_waves_enabled + volitelne visual_waves_plotly_html (default True pri visual_waves_enabled)

ABORT FIB LEVEL (pasionka / hluboký retracement — rozšíření jednoho klíče `abort_fib_level`)
  Obecně: po potvrzení vlny se do signálu doplní cena „fib_abort“ (hranice mezi vstupním fibem a SL fibem).
  Podle hodnoty `abort_fib_level` v BotConfig se buď vlna u této hranice přeskočí, nebo se naopak obchoduje
  s posunutým SL (režim řetězce).

  A) Hodnota None
      Pasiónka vypnutá; kódem se fib_abort negeneruje (žádný abort test).

  B) Číslo (float) — musí být striktně mezi entry_fib_level a sl_fib_level
      Klasické chování „pasionky“: je-li při narození vlny cena už za fib_abort (BUY: příliš hluboký retracement
      pod tou cenou; SELL: symetricky nad), signál se NEODEŠLE (live i backtest). Slouží k vynechání vstupů,
      kde by cena už byla „příliš blízko“ k plánovanému SL fibu, ale ještě ne za ním.

  C) Řetězec režimu posunu SL (rozšíření funkce — místo přeskočení se obchoduje)
      Dva zápisy — jedna funkce (aliasy v parse_abort_fib_level_grid):
      • "deep_retrace_shift_sl" — kanonický název (shodný s konstantou ABORT_FIB_SHIFT_SL v bot_config).
      • "shift_sl" — zkrácený alias jen pro pohodlnější grid / čitelnost; chování je 100% stejné.
      Oba řetězce se převádějí na stejnou vnitřní hodnotu; nemixují se s číselnou pasiónkou.

      Obecné chování režimu:
      • Hranice fib_abort se NEČTE z uživatelského čísla, ale interně jako 2/3 mezi entry_fib a sl_fib
        (příklad entry 0.5 a SL 0.8 → ~0.7, tedy podobně jako dřív typické číslo 0.7).
      • Je-li cena za tou hranicí: vlna se NEPŘESKOČÍ. Vstup dál řídí entry_mode (LIMIT primárně;
        jinak fallback). U market_fallback: SL není u ceny z sl_fib_level, ale tak, že vzdálenost
        |vstup − SL| odpovídá původnímu rozmezí |fib50 − SL| z fib geometrie vlny. U stop_fallback:
        čeká návrat na fib50; SL u pendingu zůstává u fib SL (geometrie od plánovaného entry).
      • Backtest: počítadlo wave_debug["waves_opened_abort_shift_sl"] (kolikrát tímto režimem vznikl obchod).
      • Kód: strategy/wave_detection_pine.py (fib_abort), backtest/engine.py, infra/orders.py (live send_order).

  Grid (backtest_conf / kombinace):
    • Číselná osa: "abort_fib_level": [0.68, 0.72, 0.75] …  (= pasiónka, jiné chování než řetězce výše)
    • Režim posunu SL — zvolte jeden zápis v listu (obě varianty jsou ekvivalentní):
        ["deep_retrace_shift_sl"]   nebo   ["shift_sl"]
      (lze i obě hodnoty v jednom listu jen pro scan kombinací — výsledek je pro obě stejný režim.)
  V bot_name zůstává zkratka segmentu afx + hodnota (číslo nebo název řetězce po normalizaci).

  ──────────────────────────────────────────────────────────────────────
  TREND & BOS  (nova oblast — nespleťte se starsimi sekcemi vyse; viz BotConfig)
  ──────────────────────────────────────────────────────────────────────
  Implementace: strategy/trend_bos.py (+ live_loop, backtest engine, infra/orders).

  Klíče gridu / base (vsechny patri do jednoho logickeho celku; v kazdem profilu jsou u hodnot
  podrobne komentare primo v souboru — viz bloky TREND & BOS uvnitr EXAMPLE / full_grid / …):
  - trend_filter_enabled (default False) — vstup jen ve smeru trendu z BOS (UP v bull, DOWN v bear).
    Definice trendu close-based: bull konci close pod LOW posledni UP vlny (= box_bottom);
    bear konci close nad HIGH posledni DOWN vlny (= box_top). Po flipu neutral az prvni vlna.
  - trend_hh_hl_filter_enabled (default False) — jen pri trend_filter_enabled=True: HH+HL / LL+LH
    oproti predchozi vlne stejneho smeru v trendu; prvni vlna v trendu vzdy projde.
    Sumove vlny bez HH/HL neoteviraji pozice, neaktualizuji BOS swing a na grafu
    (--visual-waves / --plot-scroll-combined-html) se nevykresluji jako pozadi.
  - tp_mode (default "rrr_fixed") — RRR_FIXED | bos_exit | bos_exit_priority | wave_target_n | wave_target_n_g
    (detail vzorcu v config/enums.py::TPMode). bos_exit = RRR safety TP + aktivni zavreni pri
    BOS flipu; bos_exit_priority = TP se nenastavuje (None / 0.0), exit jen pri SL nebo BOS;
    wave_target_n = legacy TP_WAVE_N na birth W(N) (default tp_target_wave_index=4);
    wave_target_n_g = stejne jadro + varianta G (forming extension hit, preset pri loadu).
    Fine-tuning G u wave_target_n: tp_wave_early_mode / tp_wave_exit_on / tp_wave_early_fallback_birth /
    tp_wave_intrabar_priority (viz BotConfig; u wave_target_n_g se nastavi automaticky).
  - tp_target_wave_index (default 4) — pro tp_mode wave_target_n / wave_target_n_g: cislo vlny v trendu, kde
    se POPRVE bere TP; dale N+2, N+4, ... .
  - wave_extension_pct (default 0.20) — pro tp_mode wave_target_n / wave_target_n_g: podil velikosti PREDCHOZI
    stejnosmerne vlny pro vzdalenost TP od pivota aktualni vlny.
  - wave_counter_two_sided_enabled (default False) — master: WAVE_COUNTER (protipozice LIMIT
    na TP cene) + WAVE_TWO_SIDED (protipozice na protivlni B). Vzdy jen na TP-vlne (N, N+2, ...)
    dle `tp_target_wave_index`; counter risk = cfg.risk_usd; SL z ladderu s min floorem
    `ext_min_sl_move_pct` (sdileno s EXT secondary).
  - bos_entry_enable (default False) — pri tp_mode="bos_exit" / "bos_exit_priority" /
    "wave_target_n": pri kazdem BOS flipu otevre MARKET pozici v novem smeru trendu, SL z
    ladderu velikosti POSLEDNI vlny rozbiteho smeru.
  - bos_entry_in_rrr_fixed (default False) — jen pri tp_mode="rrr_fixed": WAVE_BOS po
    close-BOS flipu (RRR TP, bez BOS exitu pozic); funguje i s pending_cancel_mode="number".
  - wave_size_sl_ladder_base_pct / _step_pct / _band_size_pct — SL ladder pro counter-position
    a BOS re-entry (default 0.21 / 0.11 / 0.50 → ≤0.49% wave → SL 0.21%; 0.50–0.99% → 0.32%; ...).
  - two_sided_entry_min_wave_pct (default 0.55) + skip_primary_entry_on_parent_wave_enable (default True) —
    jen pri wave_counter_two_sided_enabled: rodic A v [min, ext_wave_min_pct); B = prvni opacna
    Pine vlna po A (>= wave_min_pct). Nesplni-li FIB/EXT → nic. Vstup fib50 B, min SL 0.16 %.
    Obchazi trend_filter; primarni WAVE na B se v tom pripade neplni (dle skip_primary...).
    Pri wave_position_enabled=False se mirror nevolá (závisí na WAVE vstupech).
  - wave_position_enabled (default True) — klasické vlny: primární trend-follow vstupy
    z vlny (LIMIT/MARKET/STOP na entry_fib_level, SL na sl_fib_level). False = tyto vstupy
    vypnuté; lze např. jen PP (pp_enabled=True, wave_position_enabled=False, ostatní dle potřeby).
  - wave_positions_only (default False) — jen klasické WAVE: vynutí wave_position_enabled=True
    a vypne counter/two-sided, PP, BOS, EXT (+ ext_counter). Backtester i live bot
    (main.py, live_loop, LIVE_BOT_CONFIG s wave_positions_only). Implicitně True, pokud
    WAVE on a všechny pomocné moduly off (viz config/position_modes.py).
  - wave_isolation_study (default False) — POUZE backtest/grid (ne live): wave study mód.
    V reportu counter off; engine bezi s plnym counterem → net_pnl_wave_usd = WAVE slice
    z plne kombinace. Vzdy s wave_positions_only=True (bot_finish wave study).
  - wave_min_sl (default 0.12) — minimální SL pro standardní WAVE pozice v % od entry ceny.
    Pokud fib geometrie (typ. entry 0.5 / SL 0.8) vyjde těsněji, SL se od entry odtlačí
    alespoň o tuto hodnotu. Platí jen pro běžné WAVE vstupy; PP / EXT / counter / BOS
    re-entry mají vlastní SL logiku.
  - pp_enabled (default False) + pp_sl_pct (default 0.21) + pp_risk_usd (default 500.0) +
    pp_disabled_in_ext_context (default True) —
    PP pozice: po close-baru nad/pod box_top/box_bottom aktualni trend-dir vlny
    polozi LIMIT na danou uroven (s fallback MARKET v live). SL z `pp_sl_pct` %,
    risk z `pp_risk_usd` (oddeleny od cfg.risk_usd). TP dle aktualniho tp_mode
    (stejna logika jako u beznych trend pozic — pro WAVE_TARGET_N ceka na TP-vlnu).
    Pravidlo: max 1 PP order najednou (novy PP rusi stary PP pending). Kandidat
    je vzdy NEJNOVEJSI narozena vlna ve smeru trendu; starsi vlny se pro PP
    neberou. PP break az PO UKONCENI vlny: bar > birth a musi existovat dalsi
    narozena vlna (libovolny smer). Nova vlna ve smeru trendu rusi predchozi PP pending (is_pp +
    wave_time / MT5 comment PP_{wave_time}). Vznika POUZE 1x per vlna (prvni
    break). Pri BOS flipu se PP pending v broken_dir rusi.

  bot_name zkratky: trend_filter_enabled / trend_hh_hl_filter_enabled segmenty (jen True);
  tp_mode kratky kod (bos / bep / wtN / wtNg); wave_extension_pct / tp_target_wave_index mimo default;
  wave_counter_two_sided_enabled / bos_entry_enable / bos_entry_in_rrr_fixed /
  pp_enabled —
  vlozeny jen pri True (PLNYM nazvem). wave_position_enabled — PLNY nazev v bot_name
  jen pri False (= WAVE vstupy vypnuty; default True = do jmena se nepridava).
  pp_sl_pct / pp_risk_usd / two_sided_entry_min_wave_pct / wave_min_sl —
  vlozeny jen pokud se lisi od defaultu (taky PLNYM nazvem).

  - wave_min_pct_enable (default False) + ext_post_both_sides_wave_min_pct (0.13) + ext_post_both_sides_default_sl_pct (0.10)
    Během EXT both-sides okna (kdy se povolují obě strany trhu) se pro detekci 
    klasičtějších vln použije tento snížený volatilní práh. Pokud ho vlna překoná 
    (např. má move_pct 0.15 %), ale nedosahuje běžného wave_min_pct, dostane flag a je 
    použita pro počítání trendových logik, přičemž se její případný SL distančně dorovná 
    na minimálních `ext_post_both_sides_default_sl_pct` % z důvodu malé velikosti.

  ═══════════════════════════════════════════════════════════════════════════════
  KONEC BLOKU TREND & BOS — dalsi radky v tomto docstringu / dalsi klice v grid dictu
  s trend_bos nesouviseji (nove featury mimo trend/BOS pridejte az za timto oddelenim).
  ═══════════════════════════════════════════════════════════════════════════════
"""
# --- WAVE + (`wave_plus`) — viz BotConfig.wave_plus; v profilech níže vždy [True] -------------------------
# Klíč v gridu / base: "wave_plus". Translator → BotConfig (backtest + live).
# False: čistý Pine výstup (draw_right a box jen z potvrzení).
# True: pro každou potvrzenou vlnu — draw_right až k baru před další vlnou, doplnění finálního HIGH (UP)
#   nebo LOW (DOWN) v useku draw_left…gap_end, přepočet boxu a fib50 / SL / TP z rozšířeného rozsahu.
# Grid: "wave_plus": [True] — jedna varianta v produktu.
from itertools import product
import json
from pathlib import Path

import numpy as np


def arange(start: float, stop: float, step: float) -> list:
    """Helper: vrati list float hodnot od start do stop (inkluzivne) s krokem step."""
    return [round(v, 4) for v in np.arange(start, stop + step / 2, step)]

PROFILES = {}

# ============== EXAMPLE - TESTING ==============    


# ----------- TESTOVACÍ OKNO -------------
# "date_from": ["2024-05-10"], 
# "date_to"  : ["2024-11-09"],

# "date_from": ["2024-11-10"], 
# "date_to"  : ["2025-05-09"],

# "date_from": ["2025-05-10"],
# "date_to"  : ["2025-11-09"],

# "date_from": ["2025-11-10"], 
# "date_to"  : ["2026-05-09"],

# "date_from": ["2025-06-10"],
# "date_to"  : ["2026-06-10"],

PROFILES["EXAMPLE"] = {
    # VARIAC10 — combo_no 50, 53, 280, 207 (2025-06-10 .. 2026-06-10)
    # zdroj: results/EURUSD/grid_report 2025-06-10 2026-06-10.xlsx
    "grid": [
        {  # combo_no 50
            "date_from": ["2025-06-10"],
            "date_to": ["2026-06-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],
            "run_e2e_parity": [False],
            "wave_min_pct": [0.2],
            "rrr": [2.5],
            "tp_mode": ["wave_target_n"],
            "tp_target_wave_index": [2],
            "wave_extension_pct": [0.1],
            "bos_entry_in_rrr_fixed": [False],
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],
            "min_opp_bars": [3],
            "fib_level": [0.55],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["trend"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1],
            "max_wave_age_hours": [20],
            "risk_usd": [500.0],
            "pp_risk_usd": [500],
            "contract_size": [100000.0],
            "magic": [100001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],
            "adx14_equity_gate_enabled": [False],
            "pnl_base_tracker_enabled": [False],
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [True],
            "wave_isolation_study": [True],
            "wave_counter_two_sided_enabled": [False],
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.5],
            "ext_enabled": [True],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [False],
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.1],
            "ext_close_trend_positions_on_bos": [True],
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],
            "prop_firms_presets": ["FTMO"],
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],
        },

        {  # combo_no 53
            "date_from": ["2025-06-10"],
            "date_to": ["2026-06-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],
            "run_e2e_parity": [False],
            "wave_min_pct": [0.2],
            "rrr": [2.5],
            "tp_mode": ["wave_target_n"],
            "tp_target_wave_index": [2],
            "wave_extension_pct": [0.1],
            "bos_entry_in_rrr_fixed": [False],
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],
            "min_opp_bars": [3],
            "fib_level": [0.55],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["trend"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1],
            "max_wave_age_hours": [20],
            "risk_usd": [500.0],
            "pp_risk_usd": [500],
            "contract_size": [100000.0],
            "magic": [100001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],
            "adx14_equity_gate_enabled": [False],
            "pnl_base_tracker_enabled": [False],
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [True],
            "wave_isolation_study": [True],
            "wave_counter_two_sided_enabled": [False],
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.5],
            "ext_enabled": [True],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [True],
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.1],
            "ext_close_trend_positions_on_bos": [True],
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],
            "prop_firms_presets": ["FTMO"],
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],
        },

        {  # combo_no 280
            "date_from": ["2025-06-10"],
            "date_to": ["2026-06-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],
            "run_e2e_parity": [False],
            "wave_min_pct": [0.23],
            "rrr": [2.5],
            "tp_mode": ["wave_target_n"],
            "tp_target_wave_index": [2],
            "wave_extension_pct": [0.1],
            "bos_entry_in_rrr_fixed": [False],
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],
            "min_opp_bars": [3],
            "fib_level": [0.6],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["number"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1],
            "max_wave_age_hours": [20],
            "risk_usd": [500.0],
            "pp_risk_usd": [500],
            "contract_size": [100000.0],
            "magic": [100001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],
            "adx14_equity_gate_enabled": [False],
            "pnl_base_tracker_enabled": [False],
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [True],
            "wave_isolation_study": [True],
            "wave_counter_two_sided_enabled": [False],
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.5],
            "ext_enabled": [True],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [False],
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.1],
            "ext_close_trend_positions_on_bos": [True],
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],
            "prop_firms_presets": ["FTMO"],
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],
        },

        {  # combo_no 207
            "date_from": ["2025-06-10"],
            "date_to": ["2026-06-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],
            "run_e2e_parity": [False],
            "wave_min_pct": [0.2],
            "rrr": [2.5],
            "tp_mode": ["wave_target_n"],
            "tp_target_wave_index": [8],
            "wave_extension_pct": [0.1],
            "bos_entry_in_rrr_fixed": [False],
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],
            "min_opp_bars": [3],
            "fib_level": [0.6],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["number"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1],
            "max_wave_age_hours": [20],
            "risk_usd": [500.0],
            "pp_risk_usd": [500],
            "contract_size": [100000.0],
            "magic": [100001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],
            "adx14_equity_gate_enabled": [False],
            "pnl_base_tracker_enabled": [False],
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [True],
            "wave_isolation_study": [True],
            "wave_counter_two_sided_enabled": [False],
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.5],
            "ext_enabled": [True],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [True],
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.1],
            "ext_close_trend_positions_on_bos": [True],
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],
            "prop_firms_presets": ["FTMO"],
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],
        },
    ],
}

PROFILES["testing"] = {
    "grid": [
        {
# ============== MARKET SETTING ==============
            "date_from": ["2024-05-10"],    # "2024-05-10" , "2025-05-10"
            "date_to":   ["2026-06-10"],    # "2025-10-10" , "2026-06-10"
            "timeframe": ["M30"],
            "causal_mode": [False],  # True = backtest bez look-ahead (parita live)
            "run_e2e_parity": [False],  # True = po BT E2E parity (jen live_match, ne grid worker)

# ============== TP SETTINGS ==============
            "wave_min_pct": [0.26],
            "rrr": [2.0],
            "tp_mode": ["wave_target_n"], # "bos_exit", "rrr_fixed", "wave_target_n", "wave_target_n_g"
            "tp_target_wave_index": [4],
            "wave_extension_pct": [0.10],
            "bos_entry_in_rrr_fixed": [True, False],
            "wave_2_no_tp_enable": [True, False],
            "wave_2_no_tp_max_index": [2],

# ============== MARKET OPTIMALISATION ==============
            "min_opp_bars": [3],
            "fib_level": [0.55],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["trend"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1.0],
            "max_wave_age_hours": [20],

# ============== RISK MANAGEMENT ==============
            "risk_usd": [500.0],
            "pp_risk_usd": [500.0],
            "contract_size": [100_000.0],
            "magic": [100_001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],
            "adx14_equity_gate_enabled": [False],
            "pnl_base_tracker_enabled": [False],

# ============== WAVE & PP ==============
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [False, True],  # jen klasické WAVE, ostatní moduly vynuceně off
            "wave_isolation_study": [False, True],  # engine plná simulace, report = WAVE slice — never turn off
            "wave_counter_two_sided_enabled": [True, False],
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [True, False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],

# ============== TREND FILTER & BOS ==============
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [True, False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.50],

# ============== EXT ==============
            "ext_enabled": [True, False],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [True, False],
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.10],
            "ext_close_trend_positions_on_bos": [True],

# ============== WAVE FILTERING & PROPFIRMS ==============
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],
            "prop_firms_presets": ["FTMO"],
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],
        },
    ],
}

PROFILES["bot_optimalisation"] = {
    "grid": [
        {
# ============== MARKET SETTING ==============
            "date_from": ["2026-01-01"],
            "date_to": ["2026-05-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],  # True = backtest bez look-ahead (parita live)
            "run_e2e_parity": [False],  # True = po BT E2E parity (jen live_match, ne grid worker)

# ============== TP SETTINGS ==============
            "wave_min_pct": [0.29],
            "rrr": [2.0, 2.5, 3.0], 
            "tp_mode": ["bos_exit", "rrr_fixed", "wave_target_n", "wave_target_n_g"],
            "tp_target_wave_index": [4, 6, 8],  # wave_target_n / wave_target_n_g: cislo vlny v trendu pro TP
            "wave_extension_pct": [0.10],  # wave_target_n / wave_target_n_g: podil velikosti predchozi stejnosmerne vlny
            "bos_entry_in_rrr_fixed": [False, True],  # jen tp_mode=rrr_fixed: WAVE_BOS po close-BOS flipu
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],

# ============== MARKET OPTIMALISATION ==============
            "min_opp_bars": [3],
            "fib_level": [0.5, 0.55, 0.60],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD.x"],
            "sl_fib_level": [0.75, 0.8, 0.85],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["number", "trend"], #Trend follower or pending cancel according to dates
            "pending_cancel_after_days": [7],  # Pending cancel according to dates
            "wave_max_pct": [1.0],  # When EXT is ON => uselles - wave % protection - does not apply to EXT
            "max_wave_age_hours": [12], # Duplication protection from night bot restart

# ============== RISK MANAGEMENT ==============
            "risk_usd": [500.0],
            "pp_risk_usd": [500.0],
            "contract_size": [100_000.0],
            "magic": [100_001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],  # výpočet normalizovaného signálu; fit: strategy/adx14_change_indicator.py --fit
            "adx14_equity_gate_enabled": [False],  # blokace nových vstupů při signálu ≥ práh (default 1.3)
            "pnl_base_tracker_enabled": [False],  # křivka PnL základní; pro restart gate přes BOS confirm

# ============== WAVE & PP ==============
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [True],  # live + backtest: jen klasické WAVE, ostatní moduly vynuceně off
            "wave_isolation_study": [True],  # jen backtest wave study: stejné net_pnl_wave_usd jako plná kombinace
            "wave_counter_two_sided_enabled": [True, False],  # WAVE_COUNTER + WAVE_TWO_SIDED — set: True / False
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],  # preskocit primarni WAVE vstup na two-sided rodici A; jen protipozice na protivln
            "wf_enabled": [True],  # Wick Fakeout Recovery   
            "pp_enabled": [True, False], # - set: True / False
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],  # True = neotevírat PP z EXT / in_ext_range vln

# ============== TREND FILTER & BOS ==============
            "trend_filter_enabled": [True],   # WAVE; PP positions in trend only    
            "trend_hh_hl_filter_enabled": [True], # Trend definition for positon oppening in - trend only - Both sides after EXT applies 
            "bos_entry_enable": [True, False], # WAVE_BOS position enable/disable
            "wave_size_sl_ladder_base_pct": [0.21],  # WAVE_COUNTER; WAVE_BOS - min SL a následný posun dle veliksoti WAVE a EXT
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.50],

# ============== EXT ==============
            "ext_enabled": [True, False], # wave true/false and all connected to it.
            "ext_wave_min_pct": [0.76],    
            "ext_secondary_enabled": [False], # 0,236 position - false 
            "ext_weekend_gap_relax_factor": [0.76], 
            "ext_counter_enabled": [True, False],  # master: EXT counter TIME + BOS (fib 0.35)
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],  # min SL floor u EXT counter; False = jen ext_high/low
            "ext_counter_min_sl_pct": [0.16],  # min SL od entry v % (EXT counter TIME + BOS)
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False], # - set: True / False (Během EXT both-sides okna používá nižší volatilní práh pro detekci WAVE)
            "ext_post_both_sides_wave_min_pct": [0.13], # - Hodnota sníženého prahu v procentech (např. 0.13 %)
            "ext_post_both_sides_default_sl_pct": [0.10], # - Hodnota minimálního SL pro volatilní WAVE (např. 0.10 %)
            "ext_close_trend_positions_on_bos": [True, False], # - set: True / False

# ============== WAVE FILTERING & PROPFIRMS ==============
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],  # post-processing po gridu (NENI v BotConfig / live bot)
            "prop_firms_presets": ["FTMO"],  # FTMO | FXIFY | FINTOKEI | all | none | list
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],  # True = prop_firm_compliance.html (CLI ma prioritu)
        },
    ],
}

# ============== BOT FINISH ==============
# Top 300 z Ranking_FTMO (_006) + 154 wave study + 128 auto full twin (= 564 behu).
# Wave study radky v JSON: wave_positions_only=True, wave_isolation_study=True,
# wave_counter_two_sided_enabled=False (report); engine = plna simulace pro stejne WAVE.
# Metrika WAVE: net_pnl_wave_usd | study_mode ve vystupu (aggregator).
# Obnovit study: python scripts/build_bot_finish_wave_study.py --refresh-wave-study

PROFILES["bot_finish"] = {
    "explicit_combos_file": "backtest/grid/bot_finish_combos.json",
    "base": {
        "date_from": "2026-01-01",
        "date_to": "2026-05-10",
        "causal_mode": False,
        "run_e2e_parity": False,
    },
    "prop_firms": {
        "enabled": True,
        "presets": "FTMO",
        "account_size_usd": 100_000,
        "generate_html": False,
    },
    "wave_study": {
        "wave_positions_only": True,
        "wave_isolation_study": True,
        "note": "Study kombinace v JSON; top 300 = full (study_mode=full).",
    },
}

PROFILES["positions_setting"] = {
    "grid": [
        {
# ============== MARKET SETTING ==============
            "date_from": ["2026-03-03"],
            "date_to": ["2026-05-10"],
            "timeframe": ["M30"],
            "causal_mode": [False],  # True = backtest bez look-ahead (parita live)
            "run_e2e_parity": [False],  # True = po BT E2E parity (jen live_match, ne grid worker)

# ============== TP SETTINGS ==============
            "wave_min_pct": [0.26],
            "rrr": [2.0],
            "tp_mode": ["bos_exit", "rrr_fixed", "wave_target_n", "wave_target_n_g"],
            "tp_target_wave_index": [4],  # wave_target_n / wave_target_n_g: cislo vlny v trendu pro TP
            "wave_extension_pct": [0.10],  # wave_target_n / wave_target_n_g: podil velikosti predchozi stejnosmerne vlny
            "bos_entry_in_rrr_fixed": [True],  # jen tp_mode=rrr_fixed: WAVE_BOS po close-BOS flipu
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],

# ============== MARKET OPTIMALISATION ==============
            "min_opp_bars": [3],
            "fib_level": [0.5],
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD.x"],
            "sl_fib_level": [0.8],
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["number", "trend"], #Trend follower or pending cancel according to dates
            "pending_cancel_after_days": [7],  # Pending cancel according to dates
            "wave_max_pct": [1.0],  # When EXT is ON => uselles - wave % protection - does not apply to EXT
            "max_wave_age_hours": [12], # Duplication protection from night bot restart

# ============== RISK MANAGEMENT ==============
            "risk_usd": [500.0],
            "pp_risk_usd": [500.0],
            "contract_size": [100_000.0],
            "magic": [100_001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],  # výpočet normalizovaného signálu; fit: strategy/adx14_change_indicator.py --fit
            "adx14_equity_gate_enabled": [False],  # blokace nových vstupů při signálu ≥ práh (default 1.3)
            "pnl_base_tracker_enabled": [False],  # křivka PnL základní; pro restart gate přes BOS confirm

# ============== WAVE & PP ==============
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [False],  # live + backtest: jen klasické WAVE, ostatní moduly vynuceně off
            "wave_isolation_study": [False],  # jen backtest wave study: stejné net_pnl_wave_usd jako plná kombinace
            "wave_counter_two_sided_enabled": [True],  # WAVE_COUNTER + WAVE_TWO_SIDED — set: True / False
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],  # preskocit primarni WAVE vstup na two-sided rodici A; jen protipozice na protivln
            "wf_enabled": [True],  # Wick Fakeout Recovery   
            "pp_enabled": [True, False], # - set: True / False
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],  # True = neotevírat PP z EXT / in_ext_range vln

# ============== TREND FILTER & BOS ==============
            "trend_filter_enabled": [True],   # WAVE; PP positions in trend only    
            "trend_hh_hl_filter_enabled": [True], # Trend definition for positon oppening in - trend only - Both sides after EXT applies 
            "bos_entry_enable": [True], # WAVE_BOS position enable/disable
            "wave_size_sl_ladder_base_pct": [0.21],  # WAVE_COUNTER; WAVE_BOS - min SL a následný posun dle veliksoti WAVE a EXT
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.50],

# ============== EXT ==============
            "ext_enabled": [True], # wave true/false and all connected to it.
            "ext_wave_min_pct": [0.76],    
            "ext_secondary_enabled": [False], # 0,236 position - false 
            "ext_weekend_gap_relax_factor": [0.76], 
            "ext_counter_enabled": [True],  # master: EXT counter TIME + BOS (fib 0.35)
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],  # min SL floor u EXT counter; False = jen ext_high/low
            "ext_counter_min_sl_pct": [0.16],  # min SL od entry v % (EXT counter TIME + BOS)
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False], # - set: True / False (Během EXT both-sides okna používá nižší volatilní práh pro detekci WAVE)
            "ext_post_both_sides_wave_min_pct": [0.13], # - Hodnota sníženého prahu v procentech (např. 0.13 %)
            "ext_post_both_sides_default_sl_pct": [0.10], # - Hodnota minimálního SL pro volatilní WAVE (např. 0.10 %)
            "ext_close_trend_positions_on_bos": [True], # - set: True / False

# ============== WAVE FILTERING & PROPFIRMS ==============
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
            "prop_firms_enabled": [True],  # post-processing po gridu (NENI v BotConfig / live bot)
            "prop_firms_presets": ["FTMO"],  # FTMO | FXIFY | FINTOKEI | all | none | list
            "prop_firms_account_size_usd": [100_000],
            "prop_firms_generate_html": [False],  # True = prop_firm_compliance.html (CLI ma prioritu)
        },
    ],
}

PROFILES["full_grid"] = {
    "grid": [
        {
# ============== MARKET SETTING ==============
            "timeframe": ["M30"],
            "causal_mode": [False],  # True = backtest bez look-ahead (parita live)
            "run_e2e_parity": [False],  # True = po BT E2E parity (jen live_match, ne grid worker)

# ============== TP SETTINGS ==============
            "wave_min_pct": arange(0.18, 0.34, 0.02),
            "rrr": [2.0],
            "tp_mode": ["rrr_fixed", "bos_exit", "wave_target_n", "wave_target_n_g"],
            "tp_target_wave_index": [4],
            "wave_extension_pct": [0.10],
            "bos_entry_in_rrr_fixed": [False, True],
            "wave_2_no_tp_enable": [True],
            "wave_2_no_tp_max_index": [2],

# ============== MARKET OPTIMALISATION ==============
            "min_opp_bars": [2, 3, 4],
            "fib_level": arange(0.46, 0.56, 0.02),
            "entry_mode": ["market_fallback"],
            "symbol": ["EURUSD.x"],
            "sl_fib_level": arange(0.7, 0.9, 0.05),
            "abort_fib_level": ["shift_sl"],
            "wave_plus": [True],
            "order_expiry_days": [3],
            "ext_order_expiry_days": [7],
            "pending_cancel_mode": ["number", "trend"],
            "pending_cancel_after_days": [7],
            "wave_max_pct": [1.0],
            "max_wave_age_hours": [12],

# ============== RISK MANAGEMENT ==============
            "risk_usd": [500.0],
            "pp_risk_usd": [500.0],
            "contract_size": [100_000.0],
            "magic": [100_001],
            "spread": [0.0001],
            "slippage": [0.0],
            "adx14_change_enabled": [False],  # výpočet normalizovaného signálu; fit: strategy/adx14_change_indicator.py --fit
            "adx14_equity_gate_enabled": [False],  # blokace nových vstupů při signálu ≥ práh (default 1.3)
            "pnl_base_tracker_enabled": [False],  # křivka PnL základní; pro restart gate přes BOS confirm

# ============== WAVE & PP ==============
            "wave_min_sl": [0.12],
            "wave_position_enabled": [True],
            "wave_positions_only": [False],  # live + backtest: jen klasické WAVE, ostatní moduly vynuceně off
            "wave_isolation_study": [False],  # jen backtest wave study: stejné net_pnl_wave_usd jako plná kombinace
            "wave_counter_two_sided_enabled": [True, False],  # WAVE_COUNTER + WAVE_TWO_SIDED — set: True / False
            "two_sided_entry_min_wave_pct": [0.55],
            "skip_primary_entry_on_parent_wave_enable": [True],
            "wf_enabled": [True],
            "pp_enabled": [True, False],
            "pp_sl_pct": [0.21],
            "pp_disabled_in_ext_context": [True],

# ============== TREND FILTER & BOS ==============
            "trend_filter_enabled": [True],
            "trend_hh_hl_filter_enabled": [True],
            "bos_entry_enable": [True, False],
            "wave_size_sl_ladder_base_pct": [0.21],
            "wave_size_sl_ladder_step_pct": [0.16],
            "wave_size_sl_ladder_band_size_pct": [0.50],

# ============== EXT ==============
            "ext_enabled": [True],
            "ext_wave_min_pct": [0.76],
            "ext_secondary_enabled": [False],
            "ext_weekend_gap_relax_factor": [0.76],
            "ext_counter_enabled": [True],  # master: EXT counter TIME + BOS (fib 0.35)
            "ext_counter_time": ["23:00"],
            "ext_counter_min_sl_enabled": [True],
            "ext_counter_min_sl_pct": [0.16],
            "ext_trade_both_sides_in_range": [True],
            "wave_min_pct_enable": [False],
            "ext_post_both_sides_wave_min_pct": [0.13],
            "ext_post_both_sides_default_sl_pct": [0.10],
            "ext_close_trend_positions_on_bos": [True],

# ============== WAVE FILTERING & PROPFIRMS ==============
            "wave_allowed_sessions": [None],
            "wave_custom_window": [None],
            "track_concurrent_positions": [True],
            "backtest_position_cap_mode": ["off"],
            "backtest_max_open_positions": [None],
        },
    ],
    "base": {
        "symbol": "EURUSD.x",
        "risk_usd": 500.0,
        "contract_size": 100_000.0,
        "max_wave_age_hours": 12,
        "wave_min_sl": 0.12,  # explicitni default pro viditelnost v base profilu
        "spread": 0.0001,
        "slippage": 0.0,
        "date_from": "2024-04-24",
        "date_to": "2026-04-24",
    },
    "prop_firms": {
        "enabled": True,
        "presets": "FTMO",
        "account_size_usd": 100_000,
        "generate_html": False,
    },
}

# ---------------------------------------------------------------------------
# Generator kombinaci
# ---------------------------------------------------------------------------

_PROP_FIRMS_GRID_KEYS = (
    "prop_firms_enabled",
    "prop_firms_presets",
    "prop_firms_account_size_usd",
    "prop_firms_generate_html",
)


def _grid_scalar(value):
    """Prvni hodnota z grid listu, jinak skalar."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _prop_firms_dict_from_grid_source(d: dict) -> dict | None:
    """Sestavi prop_firms dict z grid klicu prop_firms_* (backtest only)."""
    if not any(k in d for k in _PROP_FIRMS_GRID_KEYS):
        return None
    out: dict = {}
    if "prop_firms_enabled" in d:
        out["enabled"] = bool(_grid_scalar(d["prop_firms_enabled"]))
    if "prop_firms_presets" in d:
        out["presets"] = _grid_scalar(d["prop_firms_presets"])
    if "prop_firms_account_size_usd" in d:
        out["account_size_usd"] = _grid_scalar(d["prop_firms_account_size_usd"])
    if "prop_firms_generate_html" in d:
        out["generate_html"] = bool(_grid_scalar(d["prop_firms_generate_html"]))
    return out


def get_profile_prop_firms_settings(profile: dict) -> dict:
    """Nastaveni prop-firm z profilu nebo grid dimenzi prop_firms_* (backtest only)."""
    defaults = {
        "enabled": False,
        "presets": "none",
        "account_size_usd": None,
        "generate_html": False,
    }
    raw = profile.get("prop_firms")
    if not raw:
        for source in (profile.get("grid_defaults"), *profile.get("grid", [])):
            if not isinstance(source, dict):
                continue
            raw = _prop_firms_dict_from_grid_source(source)
            if raw:
                break
    if not raw:
        return defaults
    out = dict(defaults)
    out.update(raw)
    # Zpetna kompatibilita se starsim klicem
    if out.get("account_size_usd") is None and out.get("account_size_override") is not None:
        out["account_size_usd"] = out["account_size_override"]
    return out


def resolve_grid_prop_firms(profile: dict, args) -> dict:
    """
    Slouci prop_firms z profilu a CLI (CLI ma prioritu).
    Vraci: preset_names, config_path, account_size_usd, generate_html.
    """
    from backtest.prop_firm.presets import resolve_prop_firm_names

    pf = get_profile_prop_firms_settings(profile)

    if getattr(args, "prop_firms", None) is not None:
        spec = args.prop_firms
    elif pf.get("enabled"):
        presets = pf.get("presets", "FTMO")
        if isinstance(presets, (list, tuple)):
            spec = ",".join(str(p) for p in presets)
        else:
            spec = str(presets)
    else:
        spec = "none"

    preset_names = resolve_prop_firm_names(spec)

    # Volitelne jen z CLI (v profilu neni potreba pro bezny backtest)
    config_path = getattr(args, "prop_firm_config", None)

    account_size = getattr(args, "account_size_override", None)
    if account_size is None:
        account_size = pf.get("account_size_usd")

    generate_html = bool(
        getattr(args, "prop_firm_html", False) or pf.get("generate_html")
    )

    return {
        "preset_names": preset_names,
        "config_path": config_path,
        "account_size_usd": account_size,
        "generate_html": generate_html,
    }


def _grid_bot_name_core_keys() -> set:
    return {"timeframe", "wave_min_pct", "min_opp_bars", "rrr", "fib_level", "entry_mode"}


def _grid_bot_name_extra_short() -> dict:
    # Mapovani key -> segment v bot_name. Pro nove WAVE_TARGET_N klice se pouzivaji
    # CELE NAZVY (uzivatelsky pozadavek "vse celymi nazvy"); pro starsi klice
    # se ponechavaji zkratky kvuli zpetne kompatibilite (cache, plot index, ...).
    return {
        "order_expiry_days": "exp",
        # EXT WAVE expirace + pending_cancel_mode: krátké tagy, aby se vešly do bot_name.
        "ext_order_expiry_days": "extexp",
        "pending_cancel_mode": "pcm",
        "pending_cancel_after_days": "pcad",
        "wave_max_pct": "mxw",
        "backtest_position_cap_mode": "pcap",
        "backtest_max_open_positions": "mp",
        "date_from": "df",
        "date_to": "dt",
        "sl_fib_level": "sf",
        # abort_fib_level → segment „afx“ v bot_name (např. afx0.7, afxdeep_retrace_shift_sl, afxshift_sl).
        # Pozn.: deep_retrace_shift_sl a shift_sl jsou aliasy — v názvu se objeví přesná řetězcová hodnota z gridu.
        "abort_fib_level": "afx",
        # wave_plus: zkratka v bot_name jen když True (řádek níže při False přeskakuje celý klíč)
        "wave_plus": "wp",  # WAVE + zapnuto → do jména např. wp1; viz _grid_bool / build_grid_bot_name_from_cfg
        # TREND FILTER (BOS) — zkratky v bot_name, jen pokud je hodnota True
        # (False = filter vypnuty, do nazvu se neprida, viz `bool_skip_when_false` v build_grid_bot_name_from_cfg).
        "trend_filter_enabled": "tdir",         # tdirTrue = obchoduj jen ve smeru trendu (BOS)
        "trend_hh_hl_filter_enabled": "thhl",   # thhlTrue = navic vyzaduj HH+HL / LL+LH strukturu
        # TP MODE — vznika v bot_name jen kdyz hodnota neni default (= rrr_fixed).
        # tpmrrr_fixed (= default → vyhozen); jinak napr. tpmbos_exit / tpmwave_target_n.
        "tp_mode": "tpm",
        # WAVE_TARGET_N: PLNE NAZVY (zadne zkratky) podle uzivatelske specifikace.
        # Hodnoty se v bot_name objevi jen kdyz se lisi od defaultu nebo jsou True.
        "tp_target_wave_index": "tp_target_wave_index",
        "wave_extension_pct": "wave_extension_pct",
        "wave_positions_only": "wave_positions_only",
        "wave_isolation_study": "wave_isolation_study",
        "wave_counter_two_sided_enabled": "wave_counter_two_sided_enabled",
        "counter_position_enabled": "wave_counter_two_sided_enabled",
        "bos_entry_enable": "bos_entry_enable",
        "bos_reentry_enabled": "bos_entry_enable",
        "bos_entry_in_rrr_fixed": "bos_entry_in_rrr_fixed",
        "wave_size_sl_ladder_base_pct": "wave_size_sl_ladder_base_pct",
        "wave_size_sl_ladder_step_pct": "wave_size_sl_ladder_step_pct",
        "wave_size_sl_ladder_band_size_pct": "wave_size_sl_ladder_band_size_pct",
        # TWO-SIDED ENTRY + PP: PLNE NAZVY (uzivatelske pravidlo).
        "two_sided_entry_enabled": "wave_counter_two_sided_enabled",
        "two_sided_entry_min_wave_pct": "two_sided_entry_min_wave_pct",
        "two_sided_entry_bypass_trend_filter": "two_sided_entry_bypass_trend_filter",
        "wave_position_enabled": "wave_position_enabled",
        "wave_min_sl": "wave_min_sl",
        "wave_min_sl_pct": "wave_min_sl",
        "wave_min_sl_%": "wave_min_sl",
        "pp_enabled": "pp_enabled",
        "pp_sl_pct": "pp_sl_pct",
        "pp_risk_usd": "pp_risk_usd",
        "pp_disabled_in_ext_context": "pp_no_ext",
        "adx14_change_enabled": "ADX14",
        "adx14_equity_gate_enabled": "adx14_equity_gate_enabled",
        "pnl_base_tracker_enabled": "pnl_base_tracker_enabled",
        "ext_enabled": "ext_enabled",
        "ext_wave_min_pct": "ext_wave_min_pct",
        "ext_counter_enabled": "ext_counter_enabled",
        "ext_counter_time": "extct",
        "ext_counter_min_sl_enabled": "ext_counter_min_sl_enabled",
        "ext_counter_min_sl_pct": "ext_counter_min_sl_pct",
        "ext_trade_both_sides_in_range": "ext_trade_both_sides_in_range",
        "wave_min_pct_enable": "wave_min_pct_enable",
        "ext_close_trend_positions_on_bos":         "ext_close_trend_positions_on_bos",
        "wave_2_no_tp_enable": "w2notp",
        "wave_2_no_tp_max_index": "w2notpi",
    }


def build_grid_bot_name_from_cfg(cfg: dict, keys: list[str]) -> str:
    """Stejna logika jako v generate_combinations — pro refresh po uprave datumu."""
    core_name_keys = _grid_bot_name_core_keys()
    extra_short = _grid_bot_name_extra_short()
    mode_short = {
        "market_fallback": "mkt",
        "stop_fallback": "stp",
        "no_fallback": "nof",
        # legacy nazvy v cache / starych runech
        "limit_fallback": "lmt",
    }
    base_name = (
        f"{cfg['timeframe']}"
        f"_w{round(cfg['wave_min_pct'], 4)}"
        f"_o{cfg['min_opp_bars']}"
        f"_r{cfg['rrr']}"
        f"_f{cfg['fib_level']}"
        f"_{mode_short.get(cfg.get('entry_mode', ''), cfg.get('entry_mode', ''))}"
    )
    # Bool grid flagy, ktere se do bot_name pripisuji JEN kdyz jsou True
    # (False = default/vypnuto → setrime delku jmena, stejna konvence jako u wave_plus).
    bool_skip_when_false = {
        "wave_plus",                   # WAVE + extension
        "trend_filter_enabled",        # TREND FILTER (BOS) — smer trendu
        "trend_hh_hl_filter_enabled",  # HH+HL / LL+LH subfilter
        "wave_counter_two_sided_enabled",  # WAVE_COUNTER + WAVE_TWO_SIDED
        "wave_positions_only",  # jen klasické WAVE (live + backtest)
        "wave_isolation_study",  # backtest wave study (engine plna simulace)
        "counter_position_enabled",    # legacy alias → stejny segment
        "bos_entry_enable",            # BOS entry market po flipu
        "bos_reentry_enabled",         # starsi alias stejne funkce
        # WAVE_BOS jen v tp_mode rrr_fixed (MARKET po close-BOS flipu, RRR TP, bez BOS exitu).
        "bos_entry_in_rrr_fixed",
        "two_sided_entry_enabled",     # legacy alias → stejny segment
        "pp_enabled",                  # PP (Push-through) pozice
        "adx14_change_enabled",        # ADX14 změna indikátor + HTML
        "adx14_equity_gate_enabled",   # ADX14 gate (blokuje nové vstupy)
        "pnl_base_tracker_enabled",    # PnL základní křivka
        "ext_enabled",
        "ext_counter_enabled",
        "ext_trade_both_sides_in_range",
        "wave_min_pct_enable",
        "ext_close_trend_positions_on_bos",
        "wave_2_no_tp_enable",
    }
    # Bool flagy ktere se do bot_name pripisuji JEN kdyz jsou False (= odchylka od True default).
    bool_skip_when_true = {
        "two_sided_entry_bypass_trend_filter",  # default True; v bot_name jen kdyz False
        "wave_position_enabled",  # default True (klasické vlny zapnuté); v bot_name jen kdyz False
        "ext_counter_min_sl_enabled",  # default True; v bot_name jen kdyz False
        "pp_disabled_in_ext_context",  # default True; v bot_name jen kdyz False
    }
    # Hodnotove grid klice, ktere se do bot_name pripisuji JEN kdyz se lisi od defaultu.
    # (Default = se ani neobjevi → bit-perfect bot_name jako pred zavedenim TP modu.)
    value_skip_when_default = {
        "tp_mode": "rrr_fixed",                     # default RRR_FIXED → kompatibilita se starymi bot_names
        "tp_target_wave_index": 4,                  # default N=4 (WAVE_TARGET_N) → vyhozen
        "wave_extension_pct": 0.20,                 # default 0.20 → vyhozen
        "wave_size_sl_ladder_base_pct": 0.21,       # default 0.21
        "wave_size_sl_ladder_step_pct": 0.11,       # default 0.11
        "wave_size_sl_ladder_band_size_pct": 0.50,  # default 0.50
        "two_sided_entry_min_wave_pct": 0.55,     # default 0.55 → vyhozen
        "wave_min_sl": 0.12,                        # default min SL pro standardní WAVE
        "wave_min_sl_pct": 0.12,                    # alias
        "wave_min_sl_%": 0.12,                      # alias
        "pp_sl_pct": 0.21,                          # default 0.21 → vyhozen
        "pp_risk_usd": 500.0,                       # default 500.0 → vyhozen
        "ext_wave_min_pct": 0.76,
        "ext_weekend_gap_relax_factor": 0.0,        # default off; v bot_name jen kdyz != 0
        "ext_counter_time": "21:00",
        "ext_counter_min_sl_pct": 0.16,
    }
    extra_parts = []
    for key in keys:
        if key in _PROP_FIRMS_GRID_KEYS:
            continue
        if key in core_name_keys:
            continue
        short_key = extra_short.get(key, key)
        val = cfg.get(key)
        if key in bool_skip_when_false and not val:
            continue
        if key in bool_skip_when_true and val:
            continue
        if key in value_skip_when_default and val == value_skip_when_default[key]:
            continue
        if key in ("date_from", "date_to") and val is not None:
            val = str(val)[:10].replace("-", "")
        elif key == "ext_counter_time" and val is not None:
            val = str(val).replace(":", "")
        elif isinstance(val, float):
            val = round(val, 4)
        extra_parts.append(f"{short_key}{val}")
    extras = f"_{'_'.join(extra_parts)}" if extra_parts else ""
    sess_label = _sessions_label(cfg.get("wave_allowed_sessions"))
    return f"{base_name}{extras}_{sess_label}"


def refresh_grid_combo_bot_name(combo: dict) -> None:
    """Po zmene date_from/date_to (nebo CLI override) prepocita bot_name podle puvodniho poradi dimenzi."""
    keys = combo.get("__grid_name_keys")
    if not keys:
        return
    combo["bot_name"] = build_grid_bot_name_from_cfg(combo, list(keys))


def finalize_grid_combo_bot_name(combo: dict) -> None:
    """
    Po sestaveni kombinace (a prip. CLI prepisu datumu) nastavi konecny bot_name.
    Datumy jen v base (ne dimenze gridu) pripoji na konec, aby se okna v reportu nelisila jen configem bez nazvu.
    """
    refresh_grid_combo_bot_name(combo)
    keys = set(combo.get("__grid_name_keys") or [])
    parts = []
    for key, short in (("date_from", "df"), ("date_to", "dt")):
        if key in keys:
            continue
        v = combo.get(key)
        if v:
            parts.append(f"{short}{str(v)[:10].replace('-', '')}")
    if parts:
        combo["bot_name"] = f"{combo['bot_name']}_{'_'.join(parts)}"


def _sessions_label(sessions) -> str:
    """Pomocna funkce: vyrobi zkratku session pro bot_name."""
    if sessions is None:
        return "NOFILT"
    if not isinstance(sessions, (list, tuple)):
        return str(sessions)
    short = {
        "ASIA": "ASIA",
        "LONDON": "LON",
        "USA": "USA",
        "OVERLAP_LON_USA": "OVL",
    }
    return "+".join(short.get(s, s) for s in sessions)


# Podmíněné grid dimenze dle tp_mode (hodnota se srazí na fix, duplicity se odstraní).
TP_MODES_EXT_CLOSE_GRID_DIMENSION = frozenset({"wave_target_n", "wave_target_n_g"})
EXT_CLOSE_TREND_POSITIONS_ON_BOS_KEY = "ext_close_trend_positions_on_bos"
EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES = False

# rrr: sweep jen u rrr_fixed (u wave_target_n* se TP počítá z geometrie vlny).
# U bos_exit zůstane fixní safety TP — viz RRR_FIXED_FOR_NON_RRR_GRID_TP_MODES.
TP_MODES_RRR_GRID_DIMENSION = frozenset({"rrr_fixed"})
RRR_KEY = "rrr"
RRR_FIXED_FOR_NON_RRR_GRID_TP_MODES = 2.5

# tp_target_wave_index: sweep jen u wave_target_n / wave_target_n_g.
TP_MODES_TP_TARGET_GRID_DIMENSION = frozenset({"wave_target_n", "wave_target_n_g"})
TP_TARGET_WAVE_INDEX_KEY = "tp_target_wave_index"
TP_TARGET_WAVE_INDEX_FIXED_FOR_NON_WAVE_TP_MODES = 4

# bos_entry_enable: sweep u bos_exit + wave_target_n*; u rrr_fixed řídí bos_entry_in_rrr_fixed.
TP_MODES_BOS_ENTRY_GRID_DIMENSION = frozenset(
    {"bos_exit", "bos_exit_priority", "wave_target_n", "wave_target_n_g"}
)
BOS_ENTRY_ENABLE_KEY = "bos_entry_enable"
BOS_ENTRY_FIXED_FOR_RRR_FIXED = False


def _apply_tp_mode_conditional_grid_rules(cfg: dict) -> None:
    """Srazí mrtvé grid dimenze podle tp_mode; dedup pak odstraní duplicity."""
    tp = str(cfg.get("tp_mode", ""))

    if tp not in TP_MODES_EXT_CLOSE_GRID_DIMENSION:
        cfg[EXT_CLOSE_TREND_POSITIONS_ON_BOS_KEY] = EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES

    if tp not in TP_MODES_RRR_GRID_DIMENSION:
        cfg[RRR_KEY] = RRR_FIXED_FOR_NON_RRR_GRID_TP_MODES

    if tp not in TP_MODES_TP_TARGET_GRID_DIMENSION:
        cfg[TP_TARGET_WAVE_INDEX_KEY] = TP_TARGET_WAVE_INDEX_FIXED_FOR_NON_WAVE_TP_MODES

    if tp not in TP_MODES_BOS_ENTRY_GRID_DIMENSION:
        cfg[BOS_ENTRY_ENABLE_KEY] = BOS_ENTRY_FIXED_FOR_RRR_FIXED


def _apply_ext_close_tp_mode_grid_rule(cfg: dict) -> None:
    """Zpetna kompatibilita — vola sjednocene pravidlo."""
    _apply_tp_mode_conditional_grid_rules(cfg)


def _combo_config_dedup_key(cfg: dict) -> tuple:
    return tuple(
        (k, cfg[k])
        for k in sorted(cfg)
        if not str(k).startswith("__")
    )


def _resolve_explicit_combos_path(profile: dict) -> Path | None:
    rel = profile.get("explicit_combos_file")
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


_PROFILE_BASE_DATE_KEYS = ("date_from", "date_to")


def _strip_profile_base_keys(item: dict, base: dict) -> dict:
    """Klice z profile['base'] nepatri do JSON combo — aplikuje se pri loadu."""
    skip = set(base.keys())
    return {k: v for k, v in item.items() if k not in skip}


def _append_wave_study_twins(combos: list[dict], profile: dict) -> None:
    """
    1. Pro study radky bez plne dvojice v JSON doplni full twin (counter on, bez isolation).
    2. Pro plne radky doplni wave_isolation twin (ciste vlny), pokud jeste neexistuje.
    Umozni v reportu porovnat net_pnl_wave_usd u libovolne kombinace z top 300.
    """
    if not profile.get("wave_study"):
        return
    import copy

    from config.position_modes import grid_backtest_isolation_study

    from backtest.grid.study_mode import STUDY_PAIR_SKIP_KEYS

    def _base_key(cfg: dict) -> tuple:
        return tuple(
            (k, cfg[k])
            for k in sorted(cfg)
            if not str(k).startswith("__") and k not in STUDY_PAIR_SKIP_KEYS
        )

    full_keys = {
        _base_key(c)
        for c in combos
        if c.get("wave_counter_two_sided_enabled")
        and not grid_backtest_isolation_study(c)
    }
    iso_keys = {
        _base_key(c)
        for c in combos
        if grid_backtest_isolation_study(c)
    }
    
    extras: list[dict] = []
    
    # 1. Missing full twins for isolation rows
    for c in combos:
        if not grid_backtest_isolation_study(c):
            continue
        bk = _base_key(c)
        if bk in full_keys:
            continue
        twin = copy.deepcopy(c)
        twin.pop("wave_positions_only", None)
        twin.pop("wave_isolation_study", None)
        twin.pop("finish_variant", None)
        twin.pop("source_combo_no", None)
        twin["wave_counter_two_sided_enabled"] = True
        twin["__wave_study_full_twin"] = True
        name_keys = list(twin.get("__grid_name_keys", []))
        if "wave_counter_two_sided_enabled" not in name_keys:
            name_keys.append("wave_counter_two_sided_enabled")
        twin["__grid_name_keys"] = name_keys
        extras.append(twin)
        full_keys.add(bk)
        
    # 2. Missing isolation twins for full rows
    for c in combos:
        if not c.get("wave_counter_two_sided_enabled") or grid_backtest_isolation_study(c):
            continue
        bk = _base_key(c)
        if bk in iso_keys:
            continue
        twin = copy.deepcopy(c)
        twin["wave_positions_only"] = True
        twin["wave_isolation_study"] = True
        twin["wave_counter_two_sided_enabled"] = False
        twin["__wave_study_iso_twin"] = True
        name_keys = list(twin.get("__grid_name_keys", []))
        if "wave_positions_only" not in name_keys:
            name_keys.append("wave_positions_only")
        if "wave_isolation_study" not in name_keys:
            name_keys.append("wave_isolation_study")
        twin["__grid_name_keys"] = name_keys
        extras.append(twin)
        iso_keys.add(bk)

    combos.extend(extras)


def _load_explicit_combos(profile: dict) -> list[dict]:
    """Nacte presny seznam kombinaci z JSON (napr. top N z Ranking_FTMO)."""
    path = _resolve_explicit_combos_path(profile)
    if path is None:
        raise ValueError("Profil nema explicit_combos_file.")
    if not path.is_file():
        raise FileNotFoundError(f"explicit_combos_file nenalezen: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("combos")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"Soubor {path} neobsahuje neprazdny seznam 'combos'.")
    base = profile.get("base", {}).copy()
    combos: list[dict] = []
    seen: set[tuple] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        item = _strip_profile_base_keys(item, base)
        cfg = base.copy()
        cfg.update(item)
        from config.position_modes import normalize_legacy_wave_study_combo

        normalize_legacy_wave_study_combo(cfg, profile)
        if "__grid_name_keys" not in cfg:
            cfg["__grid_name_keys"] = [
                k for k in item.keys() if not str(k).startswith("__")
            ]
        else:
            cfg["__grid_name_keys"] = [
                k
                for k in cfg["__grid_name_keys"]
                if k not in base and not str(k).startswith("__")
            ]
        dedup_key = _combo_config_dedup_key(cfg)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        combos.append(cfg)
    _append_wave_study_twins(combos, profile)
    for cfg in combos:
        finalize_grid_combo_bot_name(cfg)
    return combos


def generate_combinations(profile: dict) -> list:
    """
    Vygeneruje seznam dictu kombinaci pro dany profil.
    Kazdy dict ma vsechny parametry (z grid + base) + auto-generovany bot_name.

    Profil muze misto grid productu pouzit:
      "explicit_combos_file": "backtest/grid/bot_finish_combos.json"

    ext_close_trend_positions_on_bos [True, False] se testuje jen pri
    tp_mode wave_target_n / wave_target_n_g; u bos_exit a rrr_fixed zustane
    EXT_CLOSE_FIXED_FOR_NON_WAVE_TP_MODES (default False).
    """
    if profile.get("explicit_combos_file"):
        return _load_explicit_combos(profile)

    combos = []
    seen: set[tuple] = set()

    for grid in profile["grid"]:
        # grid_defaults jsou globalni dimenze profilu (aplikuji se na vsechny grid bloky).
        # Konkretni grid blok ma prioritu a muze default prebit.
        grid_defaults = profile.get("grid_defaults", {})
        merged_grid = dict(grid_defaults)
        merged_grid.update(grid)

        keys = list(merged_grid.keys())
        values = list(merged_grid.values())
        for combo in product(*values):
            cfg = profile.get("base", {}).copy()
            cfg.update(dict(zip(keys, combo)))
            cfg["__grid_name_keys"] = list(keys)
            _apply_tp_mode_conditional_grid_rules(cfg)
            dedup_key = _combo_config_dedup_key(cfg)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            combos.append(cfg)
    for cfg in combos:
        finalize_grid_combo_bot_name(cfg)
    return combos


def list_profiles() -> list:
    """Vrati seznam dostupnych profilu."""
    return list(PROFILES.keys())


def get_profile(name: str) -> dict:
    """Bezpecne vrati profil podle jmena, jinak ValueError."""
    if name not in PROFILES:
        raise ValueError(
            f"Neznamy grid profil: '{name}'. Dostupne: {list_profiles()}"
        )
    return PROFILES[name]
