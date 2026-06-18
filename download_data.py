import MetaTrader5 as mt5
import pandas as pd
from pathlib import Path

from mt5_credentials import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

# Připojení na dedikovaný MT5 terminál tohoto bota (viz mt5_credentials.py)
if not mt5.initialize(
    path=str(MT5_PATH),
    login=MT5_LOGIN,
    password=MT5_PASSWORD,
    server=MT5_SERVER,
):
    print("MT5 initialize() failed, error:", mt5.last_error())
    quit()

ti = mt5.terminal_info()
ai = mt5.account_info()
print("MT5 připojeno:", ti.name if ti else "?", "path:", ti.path if ti else "?")
print("Účet:", ai.login if ai else "?", "server:", ai.server if ai else "?")
if ai and (ai.login != MT5_LOGIN or ai.server != MT5_SERVER):
    print(f"[CHYBA] Připojen jiný účet než v credentials ({MT5_LOGIN} / {MT5_SERVER})")
    mt5.shutdown()
    quit()

# Timeframy
timeframes = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
}

symbol = "EURUSD.x"
bars = 500_000
output_dir = Path(__file__).resolve().parent / "data"
output_dir.mkdir(exist_ok=True)

symbol_info = mt5.symbol_info(symbol)
if symbol_info is None:
    print(f"[CHYBA] Symbol '{symbol}' nebyl v MT5 nalezen. Zkontroluj presny nazev symbolu u brokera.")
    mt5.shutdown()
    quit()

if not symbol_info.visible and not mt5.symbol_select(symbol, True):
    print(f"[CHYBA] Symbol '{symbol}' nejde vybrat v Market Watch, error: {mt5.last_error()}")
    mt5.shutdown()
    quit()

for name, tf in timeframes.items():
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    
    if rates is None or len(rates) == 0:
        print(f"[CHYBA] Nepodařilo se stáhnout {name}, error: {mt5.last_error()}")
        continue
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df[["time", "open", "high", "low", "close", "tick_volume"]]
    df.columns = ["datetime", "open", "high", "low", "close", "volume"]
    
    filename = output_dir / f"{symbol}_{name}.csv"
    df.to_csv(filename, index=False)
    print(f"[OK] {filename} — {len(df)} svíček, od {df['datetime'].iloc[0]} do {df['datetime'].iloc[-1]}")

mt5.shutdown()
print(f"\nHotovo. Soubory jsou uloženy ve složce: {output_dir}")