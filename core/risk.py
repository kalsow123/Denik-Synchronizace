

# ───── RISK SETTING ──────────────────────────
# Live bot bere data přímo z MT5 z aktuálně obchodovaného symbolu.
# Funkce calc_lot() automaticky pozna, ktery rezim pouzit.
import logging

try:
    import MetaTrader5 as mt5
    _HAS_MT5 = True
except ImportError:
    mt5 = None
    _HAS_MT5 = False

from config.bot_config import BotConfig

log = logging.getLogger(__name__)

    # Zaokrouhlení lot size pro vstupy
def round_to_step(value: float, cfg: BotConfig) -> float:
    step = cfg.lot_step
    return max(cfg.min_lot, min(cfg.max_lot, round(round(value / step) * step, 2)))

    # Spočítá velikost lotu tak, aby ztráta při SL byla "cfg.risk_usd"
    # Live bot používá mt5.symbol_info().trade_contract_size automaticky beroucí data z obchodovaného symbolu
    # Becktester cfg.contract_size když jede z CSV souborů a ne MT5 live
def calc_lot(ep: float, sl: float, cfg: BotConfig) -> float:
    sl_dist = abs(ep - sl)
    if sl_dist == 0:
        return cfg.min_lot

    contract_size = None

    if _HAS_MT5 and mt5 is not None:
        try:
            info = mt5.symbol_info(cfg.symbol)
            if info is not None:
                contract_size = info.trade_contract_size
        except Exception:
            contract_size = None

    if contract_size is None:
        # Backtest rezim nebo MT5 neni inicializovan -> pouzijeme cfg.contract_size
        contract_size = cfg.contract_size

    risk_per_lot = sl_dist * contract_size
    if risk_per_lot <= 0:
        return cfg.min_lot

    # ── DYNAMIC RISK ──
    # Pokud je zapnuty dynamic_risk, vypocita risk_usd z aktualni equity.
    # Jinak pouzije fixni cfg.risk_usd.
    risk_usd = cfg.risk_usd
    if getattr(cfg, "dynamic_risk_enabled", False) and _HAS_MT5 and mt5 is not None:
        try:
            from infra.account import get_equity
            equity = get_equity(cfg)
            if equity is not None and equity > 0:
                pct = getattr(cfg, "risk_pct_of_equity", 0.5)
                risk_usd = equity * (pct / 100.0)
                log.info(
                    f"DYNAMIC_RISK: equity={equity:.2f} pct={pct}% -> risk_usd={risk_usd:.2f}"
                )
            else:
                log.warning(
                    f"DYNAMIC_RISK: equity nedostupna, fallback na cfg.risk_usd={cfg.risk_usd}"
                )
        except Exception as e:
            log.warning(f"DYNAMIC_RISK selhal: {e}, fallback na cfg.risk_usd={cfg.risk_usd}")

    return round_to_step(risk_usd / risk_per_lot, cfg)

    # Becktest only - cfg.contract_size nezávysle na připojení k MT5
def calc_lot_backtest(ep: float, sl: float, cfg: BotConfig) -> float:
    sl_dist = abs(ep - sl)
    if sl_dist == 0:
        return cfg.min_lot

    risk_per_lot = sl_dist * cfg.contract_size
    if risk_per_lot <= 0:
        return cfg.min_lot

    return round_to_step(cfg.risk_usd / risk_per_lot, cfg)
