
# ───── REŽIM CHOVÁNÍ KDYŽ JE CENA UŽ ZA LIMIT ENTRY (TREND-FOLLOW STRATEGIE) ────────

from enum import Enum

class EntryMode(str, Enum):
    """
    Strategie:
      Po definici vlny (X opacnych svicek) bot zadava LIMIT order ve smeru vlny:
        - UP vlna  -> BUY  LIMIT na entry_fib_level
        - DOWN vlna -> SELL LIMIT na entry_fib_level
      SL je na sl_fib_level (default 0.8) ve smeru proti pokracovani trendu.

    Pokud je v okamziku zpracovani vlny cena UZ za entry urovni
    (tj. retracement uz prosel pres entry smerem k SL), nelze poslat klasicky
    LIMIT na entry. Pak rozhoduje EntryMode:

      MARKET_FALLBACK  - vstoup hned za market; lot a TP se prepocitaji z aktualni
                          ceny, SL zustava na sl_fib_level.
      STOP_FALLBACK    - vyckej, az se cena vrati zpet pres entry v puvodnim smeru
                          trendu, pak vstup pres BUY_STOP / SELL_STOP na entry urovni.
      NO_FALLBACK      - vlnu preskoc, nic nezadavej.

    Pozn.: LIMIT_FALLBACK zustava v enumu kvuli datum ve starych logech/exportum.
           V live i v backtestu (engine) je rezim deprecated — preskakovany / skipped.
    """

    MARKET_FALLBACK = "market_fallback"
    STOP_FALLBACK   = "stop_fallback"
    NO_FALLBACK     = "no_fallback"
    LIMIT_FALLBACK  = "limit_fallback"   # deprecated vsude (legacy hodnota v grid/logech)


# ───── REŽIM TAKE-PROFITU (TP) ──────────────────────────
class TPMode(str, Enum):
    """
    Rezim vypoctu TAKE-PROFITU pro otevirany obchod.

    RRR_FIXED  (default, stare chovani)
      - TP = entry + cfg.rrr × |entry − sl|   (BUY)
      - TP = entry − cfg.rrr × |entry − sl|   (SELL)
      - "entry" = skutecna entry cena (slipped fill u STOP/LIMIT trigger,
                  market_price u market_fallback). Tj. TP je vzdy R-multiple
                  od skutecne entry, ne od fib50 estimate.
      - Pri triggeru pendingu se TP prepocita podle slipped entry; v live se
        TP pocita pri odeslani orderu (LIMIT z fib50, MARKET z market price).

    BOS_EXIT
      - TP na brokerovi / v backtestu se NENASTAVUJE (None / 0.0 = bez TP).
        Ziskovy vystup z pozice POUZE pri BOS flipu (zavreni pozic / zruseni
        pendingu pri flipu trendu).
      - Pozice muze skoncit jen na SL nebo na BOS flipu — RRR safety cíl
        byl odstranen a jiz se nepouziva.
      - V backtestu se BOS test provadi pred SL/TP kontrolou kazdeho baru;
        v live na konci kazdeho cyklu po update trade_trackeru.

    BOS_EXIT_PRIORITY
      - Ziskovy vystup z pozice POUZE pri BOS flipu (stejna per-bar logika jako
        BOS_EXIT: zavreni pozic / zruseni pendingu pri flipu trendu).
      - TP na brokerovi / v backtestu se NENASTAVUJE (None / 0.0 = bez TP).
        Pozice muze skoncit jen na SL nebo na BOS flipu. Dnes zcela
        identicke chovani jako BOS_EXIT.

    WAVE_TARGET_N (drive nazyvane wave_extension_pct, prejmenovano)
      - TP se bere AZ na N-te vlne ve smeru aktualniho trendu (a pak na kazde
        dalsi N+2, N+4, ...). N je `cfg.tp_target_wave_index` (default 4).
      - Vypocet TP pro vlnu N: TP = pivot_N ± cfg.wave_extension_pct × |prev_same_dir_wave|
        kde |prev_same_dir| = box_top − box_bottom predchozi vlny STEJNEHO smeru
        v aktualnim trendu (napr. pro UP4 se bere UP3, ne DOWN3).
      - Pozice z vln K < N (vc. otevrenych v K) NEMAJI zadny TP — drzi se az
        do BOS flipu, SL nebo do prvni TP-vlny (N, N+2, ...), kde se jim TP
        nastavi 1x a uz se na nej nesaha.
      - Pri TP-vlne se zaroven volitelne stavi PROTIPOZICE (LIMIT v opacnem
        smeru na TP cene) — viz cfg.counter_position_enabled. Limitka nema
        expiraci a rusi se pri BOS flipu noveho trendu (nez se naplni).
      - BOS exit logika je shodna s BOS_EXIT_PRIORITY: na flipu se zavou
        pozice rozbiteho smeru + zrusi se pendingy v rozbitem smeru.
      - Volitelny BOS entry market (cfg.bos_entry_enable): po BOS flipu
        otevre market pozici v novem trendu s SL podle ladderu z velikosti
        posledni vlny v rozbitem smeru.

    WAVE_TARGET_N_G
      - Stejne jadro jako WAVE_TARGET_N (viz vyse), ale exit varianta G:
        forming W(N) qualified + extension price hit pred birth W(N);
        fallback na birth TP_WAVE_N pokud hit nepřijde (cfg.tp_wave_early_fallback_birth).
      - V gridu staci tp_mode=wave_target_n_g bez tp_wave_early_mode / tp_wave_exit_on.
    """

    RRR_FIXED              = "rrr_fixed"  # TP z RRR; WAVE_BOS volitelne pres cfg.bos_entry_in_rrr_fixed
    BOS_EXIT               = "bos_exit"
    BOS_EXIT_PRIORITY      = "bos_exit_priority"
    WAVE_TARGET_N          = "wave_target_n"
    WAVE_TARGET_N_G        = "wave_target_n_g"


# ───── WAVE_TARGET_N — early TP (varianta G / K) ───────────────────────────
class TpWaveEarlyMode(str, Enum):
    """Pod-rezim uvnitr tp_mode=WAVE_TARGET_N / WAVE_TARGET_N_G (G preset u _g).

    OFF — legacy: exit TP_WAVE_N na birth W(N) (default).
    FORMING_QUALIFIED — varianta G: po birth W(N-1) sledovat forming W(N), ARM extension TP
    po move_pct >= wave_min_pct, exit na zasah armed_tp; detekce W(N) beze zmeny.
    """

    OFF = "off"
    FORMING_QUALIFIED = "forming_qualified"


class TpWaveExitOn(str, Enum):
    """Jak se zaviraji pozice pri WAVE_TARGET_N (early vetev).

    BIRTH — market close na birth TP-vlny W(N) (default / legacy).
    EXTENSION_HIT — close na armed extension cene behem forming W(N) (varianta G).
    """

    BIRTH = "birth"
    EXTENSION_HIT = "extension_hit"


class TpWaveIntrabarPriority(str, Enum):
    """Priorita SL vs extension TP na stejnem baru (varianta G)."""

    TP_BEFORE_SL = "tp_before_sl"
    SL_BEFORE_TP = "sl_before_tp"


# ───── REŽIM RUŠENÍ PENDING LIMITŮ (nad rámec tp_mode) ───────────────────
class PendingCancelMode(str, Enum):
    """
    Funkce ovládající rušení pending LIMIT orderů NEZÁVISLE na tp_mode.

    Drive ridil pending lifecycle pouze tp_mode (BOS_EXIT/WAVE_TARGET_N rusi pendingy
    pri BOS flipu, RRR_FIXED je necha az do `order_expiry_days`). To zpusobovalo
    rozdilne pocty otevrenych WAVE pozic napric tp_modes (zombie LIMIT-fill v
    RRR_FIXED vs cancel-on-BOS v BOS_EXIT/WAVE_TARGET_N).

    Tato funkce sjednocuje pending lifecycle:

      NUMBER   (default) — VSECHNY pendingy expiruji po `pending_cancel_after_days`
                  dnech (nezavisle na tp_mode). BOS flip pendingy NERUSI.

      TREND    — VSECHNY pendingy se rusi pri BOS flipu (BOS_EXIT-like cancel
                  i v RRR_FIXED). Expirace zustava na `order_expiry_days`.

    DULEZITE:
      * Tento parametr neovlivnuje:
          - zaviraini otevrenych pozic (close-on-BOS ridi tp_mode, ne pending_cancel_mode),
          - session/weekly cancel_all_pendings (live `cancel_all_pendings` jede
            primo pres MT5, ignorujici tento parametr),
          - EXT WAVE pendingy (jsou trvale chranene pred VSEMI cancel mechanismy a
            maji vlastni expiraci `ext_order_expiry_days`).
      * Counter / two-sided / PP pendingy zustavaji chranene per svou semantiku
        (rusi se jen pri BOS flipu, nikdy expiraci).
    """

    NUMBER = "number"
    TREND  = "trend"
