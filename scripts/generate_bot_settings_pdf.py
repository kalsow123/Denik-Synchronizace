"""Generuje Bot_settings.pdf — slovnik grid parametru z profilu EXAMPLE."""
from __future__ import annotations

import os
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Bot_settings.pdf"
WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
FONT_REG = WIN_FONTS / "arial.ttf"
FONT_BOLD = WIN_FONTS / "arialbd.ttf"

SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Zakladni obchodni parametry",
        [
            (
                "symbol",
                "Obchodovany instrument (napr. EURUSD). Nastavuje par, na ktery "
                "backtester nacita data a simuluje obchody. Moznosti: libovolny symbol "
                "podporovany CSV daty projektu.",
            ),
            (
                "timeframe",
                "Casovy ramec svickove analyzy. Ovlivnuje detekci vln i casovani vstupu. "
                "Moznosti: M1, M3, M5, M15, M30, H1, H4, D1, W1. V EXAMPLE: M30.",
            ),
            (
                "wave_min_pct",
                "Minimalni velikost vlny v % od ceny (move_pct). Vlny mensi nez tato "
                "hranice se ignoruji. Typicky rozsah gridu 0.18–0.34. V EXAMPLE: 0.26.",
            ),
            (
                "wave_max_pct",
                "Maximalni povolena velikost vlny v %. Vlny nad touto hranici se "
                "nepouzivaji pro standardni WAVE vstupy. None = bez limitu. V EXAMPLE: 1.0.",
            ),
            (
                "min_opp_bars",
                "Minimalni pocet opacne smernych baru pred potvrzenim vlny (Pine logika). "
                "Vyssi hodnota = prisnejsi filtr. Moznosti: typicky 2, 3 nebo 4.",
            ),
            (
                "rrr",
                "Risk-Reward Ratio — nasobitel vzdalenosti entry–SL pro vypocet TP "
                "(TP = entry +/- rrr × |entry − SL|). Pouziva se u rezimu rrr_fixed a "
                "jako safety TP u bos_exit. Moznosti: libovolne kladne cislo (napr. 2.0, 2.5).",
            ),
            (
                "fib_level",
                "Uroven Fibonacci retracementu pro VSTUP (v BotConfig: entry_fib_level). "
                "Hodnota 0.5 = 50% retracement od konce vlny. Musi byt < sl_fib_level. "
                "Typicky rozsah: 0.46–0.56.",
            ),
            (
                "sl_fib_level",
                "Uroven Fibonacci retracementu pro STOP LOSS. Musi byt > fib_level a <= 1.0. "
                "Priklad: entry 0.5 / SL 0.8. Typicky rozsah gridu: 0.70–0.90.",
            ),
            (
                "entry_mode",
                "Chovani pri hlubokem retracementu (cena uz prosla entry smerem k SL). "
                "Moznosti: market_fallback (vstup za market), stop_fallback (ceka navrat "
                "na entry pres STOP order), no_fallback (vlna se preskoci).",
            ),
            (
                "abort_fib_level",
                "Ochrana pred prilis hlubokym retracementem. None = vypnuto. Cislo mezi "
                "fib_level a sl_fib_level = pasionka (signál se zahodi). Retezec "
                "shift_sl nebo deep_retrace_shift_sl = misto preskoceni se obchoduje "
                "s posunutym SL (ekvivalentni aliasy).",
            ),
            (
                "wave_plus",
                "Rozsireni boxu vlny v case (WAVE+). True: po potvrzeni vlny se box "
                "protahne k dalsi vlne, doplni se finalni HIGH/LOW a prepocita fib50/SL/TP. "
                "False: cisty Pine vystup bez protaeni. Moznosti: True / False.",
            ),
        ],
    ),
    (
        "Expirace a ruseni pending orderu",
        [
            (
                "order_expiry_days",
                "Po kolika dnech (business days, bez vikendu) se zrusi bezne pending "
                "LIMIT ordery. Moznosti: libovolne cele cislo (napr. 3, 7, 14).",
            ),
            (
                "ext_order_expiry_days",
                "Vlastni expirace pro EXT WAVE pending (LIMIT z 0.5 fib EXT vlny). "
                "Delssi nez order_expiry_days; EXT pending se nerusi BOS flip ani "
                "pending_cancel_mode. V EXAMPLE: 7.",
            ),
            (
                "pending_cancel_mode",
                "Rezim ruseni pending orderu nezavisle na tp_mode. number = vsechny "
                "pendingy expiruji po pending_cancel_after_days dnech; BOS flip je "
                "nerusi. trend = vsechny pendingy se rusi pri BOS flipu. Moznosti: "
                "number / trend.",
            ),
            (
                "pending_cancel_after_days",
                "Pocet dnu pro pending_cancel_mode=number (business days). "
                "Nema vliv pri rezimu trend. Moznosti: libovolne cele cislo.",
            ),
            (
                "max_wave_age_hours",
                "Maximalni stari vlny v hodinach pro nove signaly. Starsi vlny se "
                "nepouzivaji pro vstupy. Moznosti: libovolne cele cislo hodin.",
            ),
        ],
    ),
    (
        "Risk, velikost pozice a identita",
        [
            (
                "risk_usd",
                "Fixni riziko v USD na jeden obchod (pro standardni WAVE, counter, BOS "
                "re-entry). Z risk_usd a vzdalenosti SL se pocita lot. Moznosti: "
                "libovolna kladna castka (napr. 500.0).",
            ),
            (
                "contract_size",
                "Velikost kontraktu pro vypocet lotu v backtesteru (forex typicky "
                "100 000). Live bot bere data z MT5. Moznosti: cislo dle instrumentu.",
            ),
            (
                "magic",
                "Unikatni identifikator bota pro MT5 (magic number). Musi byt odlisny "
                "od jinych botu. Moznosti: libovolne cele cislo.",
            ),
        ],
    ),
    (
        "Session filter a casova okna",
        [
            (
                "wave_allowed_sessions",
                "Filtr obchodnich session pro detekci vln. None = bez filtru (baseline). "
                "List session: ASIA, LONDON, USA, OVERLAP_LON_USA. Translator automaticky "
                "nastavi wave_session_filter_enabled.",
            ),
            (
                "wave_custom_window",
                "Vlastni casove okno (tuple HH:MM, HH:MM) misto wave_allowed_sessions. "
                "None = nepouzito. Prepise session filter pokud je zadano.",
            ),
            (
                "date_from / date_to",
                "Rozsah historickych dat pro backtest. V base profilu = stejne pro vsechny "
                "kombinace. V gridu jako list = kazda kombinace obdobi (itertools.product). "
                "Format: YYYY-MM-DD. CLI --date-from / --date-to ma prioritu.",
            ),
        ],
    ),
    (
        "Backtest simulace (jen backtester)",
        [
            (
                "spread",
                "Simulovany spread v cene (napr. 0.0001 pro EURUSD). Ovlivnuje fill "
                "ceny vstupu a vystupu. Live bot pouziva realny spread brokera.",
            ),
            (
                "slippage",
                "Simulovany skluz pri exekuci v cene. 0.0 = bez skluzu. Jen backtester.",
            ),
            (
                "track_concurrent_positions",
                "True = backtester sleduje a reportuje maximalni pocet soucasne otevrenych "
                "pozic. False = nevypisuje tuto statistiku. Moznosti: True / False.",
            ),
            (
                "backtest_position_cap_mode",
                "Limit otevrenych pozic v backtestu. off = vypnuto. market_close = prebytek "
                "zavre marketem. pending_prune = preventivne orezava pendingy. "
                "Moznosti: off / market_close / pending_prune.",
            ),
            (
                "backtest_max_open_positions",
                "Maximalni pocet otevrenych pozic pri zapnutem cap modu. None = cap "
                "vypnuty bez ohledu na mode. Moznosti: None nebo cele cislo (napr. 4, 6).",
            ),
        ],
    ),
    (
        "Trend filter a BOS (Break of Structure)",
        [
            (
                "trend_filter_enabled",
                "Filtr vstupu podle smeru trendu z BOS. False = obchod obousmerne "
                "(UP→BUY, DOWN→SELL). True = jen UP v bull trendu, jen DOWN v bear; "
                "v neutral nic. Trend = close pod LOW posledni UP vlny (bull konci) "
                "resp. close nad HIGH posledni DOWN vlny. Moznosti: True / False.",
            ),
            (
                "trend_hh_hl_filter_enabled",
                "Strukturalni filtr vln (ucinny jen pri trend_filter_enabled=True). "
                "True = bull vyzaduje HH+HL oproti predchozi UP vlne; bear LL+LH "
                "oproti predchozi DOWN. Prvni vlna v trendu vzdy projde. "
                "Moznosti: True / False.",
            ),
            (
                "tp_mode",
                "Rezim take-profit a vystupu z pozic. rrr_fixed = klasicky RRR TP. "
                "bos_exit = RRR safety TP + zavreni pri BOS flipu. bos_exit_priority = "
                "bez TP, exit jen SL nebo BOS. wave_target_n = TP na birth vlne W(N), "
                "N+2, N+4… wave_target_n_g = stejne jadro + G preset (extension hit). "
                "V EXAMPLE tri vetve gridu testuji ruzne tp_mode.",
            ),
            (
                "tp_target_wave_index",
                "Pro tp_mode wave_target_n / wave_target_n_g: cislo vlny ve smeru trendu, "
                "kde se poprve nastavi TP (default 4). Dalsi TP na N+2, N+4… "
                "Moznosti: libovolne cele cislo (napr. 2, 4, 6).",
            ),
            (
                "wave_extension_pct",
                "Pro wave_target_n*: podil velikosti predchozi stejnosmerne vlny pro "
                "vzdalenost TP od pivota aktualni vlny. 0.10 = 10% velikosti predchozi "
                "vlny. Default v BotConfig: 0.20.",
            ),
            (
                "counter_position_enabled",
                "Protipozice LIMIT v opacnem smeru na TP cene pri TP-vlne. Risk = risk_usd; "
                "SL z ladderu velikosti vlny. Moznosti: True / False.",
            ),
            (
                "bos_entry_enable",
                "Pri BOS flipu otevre MARKET pozici v novem smeru trendu (pro tp_mode "
                "bos_exit, bos_exit_priority, wave_target_n*). SL z ladderu posledni "
                "vlny rozbiteho smeru. Moznosti: True / False.",
            ),
            (
                "bos_entry_in_rrr_fixed",
                "Jen pri tp_mode=rrr_fixed: WAVE_BOS — MARKET po close-BOS flipu, "
                "RRR TP, bez BOS-driven zavreni ostatnich pozic. Funguje i s "
                "pending_cancel_mode=number. Moznosti: True / False.",
            ),
            (
                "wave_size_sl_ladder_base_pct",
                "Zakladni SL v % pro SL ladder (counter-position, BOS re-entry). "
                "Pro vlny <= prvni pasma. Default: 0.21.",
            ),
            (
                "wave_size_sl_ladder_step_pct",
                "Prirustek SL v % za kazde paso velikosti vlny v ladderu. "
                "Default: 0.11 (v EXAMPLE 0.16).",
            ),
            (
                "wave_size_sl_ladder_band_size_pct",
                "Sirka pasma velikosti vlny v % pro ladder (floor(wave_pct / band)). "
                "Default: 0.50.",
            ),
        ],
    ),
    (
        "WAVE vstupy a two-sided entry",
        [
            (
                "wave_position_enabled",
                "Klasické trend-follow vstupy z vlny (LIMIT/STOP/MARKET na fib_level, "
                "SL na sl_fib_level). True = standardni WAVE obchody. False = WAVE "
                "vstupy vypnuty (lze jen PP, counter, BOS). Moznosti: True / False.",
            ),
            (
                "wave_min_sl",
                "Minimalni vzdalenost SL od entry v % pro standardni WAVE pozice. "
                "Pokud fib geometrie vyjde těsněji, SL se odtlačí alespoň o tuto "
                "hodnotu. Neplati pro PP/EXT/counter/BOS. Default: 0.12.",
            ),
            (
                "two_sided_entry_enabled",
                "Two-sided counter: po velke vlne A v pasu [min, ext_wave_min_pct) "
                "ceka protivlni B a obchoduje fib50 na B. Obchazi trend_filter. "
                "Moznosti: True / False.",
            ),
            (
                "two_sided_entry_min_wave_pct",
                "Spodni hranice velikosti rodicovske vlny A pro two-sided entry. "
                "Default: 0.55.",
            ),
            (
                "skip_primary_entry_on_parent_wave_enable",
                "True = na rodicovske vlne A se neplni primarni WAVE vstup, jen "
                "protipozice na B. False = A obchoduje i svuj primarni WAVE navic "
                "k two-sided na B. Moznosti: True / False.",
            ),
        ],
    ),
    (
        "PP pozice (Push-through / break boxu)",
        [
            (
                "pp_enabled",
                "PP pozice: po close-baru nad box_top (UP) nebo pod box_bottom (DOWN) "
                "se polozi LIMIT na danou uroven (fallback MARKET v live). Max 1 PP "
                "pending najednou. Moznosti: True / False.",
            ),
            (
                "pp_sl_pct",
                "Stop-loss PP pozice v % od entry ceny. Oddeleny od fib SL standardnich "
                "WAVE. Default: 0.21.",
            ),
            (
                "pp_risk_usd",
                "Riziko v USD pouze pro PP pozice (misto cfg.risk_usd). Default: 500.0.",
            ),
        ],
    ),
    (
        "EXT subsystem (velke vlny)",
        [
            (
                "ext_enabled",
                "Master switch EXT rezimu pro vlny >= ext_wave_min_pct. Zapina "
                "specialni logiku velkych vln (range, counter, BOS). Moznosti: True / False.",
            ),
            (
                "ext_wave_min_pct",
                "Minimalni velikost vlny v % pro klasifikaci jako EXT. V EXAMPLE: 0.76.",
            ),
            (
                "ext_secondary_enabled",
                "Sekundarni EXT vstup (tag ext_0236) na fib 0.236. V EXAMPLE vypnuto. "
                "Moznosti: True / False.",
            ),
            (
                "ext_weekend_gap_relax_factor",
                "Uvolneni EXT prahu pro vlny pres vikendovy gap. 0.0 = vypnuto, "
                "0.5 = snizeni prahu o polovinu gap_pct (doporuceno), 1.0 = agresivni.",
            ),
            (
                "ext_counter_enabled",
                "Master switch EXT counter market: TIME (ext_counter_time) i BOS "
                "(fib 0.35). Moznosti: True / False.",
            ),
            (
                "ext_counter_time",
                "Cas spusteni EXT counter logiky (broker time). Format HH:MM. "
                "V EXAMPLE: 23:00.",
            ),
            (
                "ext_counter_min_sl_enabled",
                "Min SL floor u EXT counter; False = jen ext_high/low. "
                "Plati pro TIME i BOS counter.",
            ),
            (
                "ext_counter_min_sl_pct",
                "Min SL od entry v % pro EXT counter (TIME + BOS).",
            ),
            (
                "ext_trade_both_sides_in_range",
                "Behem EXT range povolit obchody na obe strany trhu. "
                "Moznosti: True / False.",
            ),
            (
                "wave_min_pct_enable",
                "Behem EXT both-sides okna pouzit snizeny wave_min_pct pro detekci "
                "klasictejsich vln. Moznosti: True / False.",
            ),
            (
                "ext_post_both_sides_wave_min_pct",
                "Snizeny volatilni prah v % behem EXT both-sides okna (napr. 0.13). "
                "Plati jen pri wave_min_pct_enable=True.",
            ),
            (
                "ext_post_both_sides_default_sl_pct",
                "Minimalni SL v % pro male vlny detekovane snizenym prahem (napr. 0.10).",
            ),
            (
                "ext_close_trend_positions_on_bos",
                "Pri BOS flipu zavrit trend pozice z EXT kontextu. "
                "Moznosti: True / False.",
            ),
        ],
    ),
    (
        "Wick Fakeout Recovery a ochrana W2",
        [
            (
                "wf_enabled",
                "Wick Fakeout Recovery — po wicku proti trendu bez validniho BOS close "
                "vytvori novou continuation vlnu od fakeout pivotu. Neaktivni ve stavu "
                "EXT. Moznosti: True / False.",
            ),
            (
                "wave_2_no_tp_enable",
                "Ochrana pozic ve vlne 2 trendu: behem EXT-1 okna se pozice nezaviraji "
                "na BOS (TP/SL bezi standardne). Moznosti: True / False.",
            ),
            (
                "wave_2_no_tp_max_index",
                "Maximalni index vlny v trendu pro ochranu wave_2_no_tp (typicky 2).",
            ),
        ],
    ),
    (
        "Prop firms (post-processing — jen backtester)",
        [
            (
                "prop_firms.enabled",
                "True = po grid behu dopocita scale_factor a sloupce v grid_report "
                "(PnL %, DD %, headroom dle prop firm limitu). Live bot nepouziva.",
            ),
            (
                "prop_firms.presets",
                "Preset prop firm: FTMO, FXIFY, FINTOKEI, all, none, nebo list "
                '["FTMO","FXIFY"]. CLI --prop-firms ma prioritu.',
            ),
            (
                "prop_firms.account_size_usd",
                "Velikost uctu v USD pro vypocet % limitu. None = default z presetu "
                "(typicky 100 000).",
            ),
            (
                "prop_firms.generate_html",
                "True = ulozi prop_firm_compliance.html. CLI --prop-firm-html ma prioritu.",
            ),
        ],
    ),
]


class BotSettingsPDF(FPDF):
    def header(self) -> None:
        self.set_font("Arial", "B", 10)
        self.set_text_color(80, 80, 80)
        self.cell(0, 8, "Bot settings — grid profil EXAMPLE (backtest_conf.py)", align="C")
        self.ln(10)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Arial", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Strana {self.page_no()}/{{nb}}", align="C")


def build_pdf() -> None:
    pdf = BotSettingsPDF()
    pdf.alias_nb_pages()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("Arial", "", str(FONT_REG))
    pdf.add_font("Arial", "B", str(FONT_BOLD))
    pdf.add_page()
    w = pdf.epw

    pdf.set_font("Arial", "B", 16)
    pdf.cell(w, 10, "Bot_settings", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(
        w,
        5,
        "Prehled vsech grid parametru z profilu EXAMPLE (grid blok, base, prop_firms). Kazdy parametr lze v gridu zadat jako list hodnot — backtester "
        "generuje vsechny kombinace pres itertools.product.",
    )
    pdf.ln(4)

    for section_title, items in SECTIONS:
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_font("Arial", "B", 12)
        pdf.set_fill_color(235, 240, 248)
        pdf.cell(w, 8, section_title, new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.ln(2)

        for name, desc in items:
            if pdf.get_y() > 260:
                pdf.add_page()
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Arial", "B", 10)
            pdf.set_text_color(20, 60, 120)
            pdf.multi_cell(w, 5, name)
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Arial", "", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(w, 4.5, desc)
            pdf.ln(2)

    pdf.output(str(OUTPUT))
    print(f"PDF vytvoreno: {OUTPUT}")


if __name__ == "__main__":
    build_pdf()
