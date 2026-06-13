
# ───── NASTAVENÍ LOGGU ──────────────────────────
# Dual logging: text format pro cloveka, JSON line pro monitoring agenta.
# - stdout (konzole)        -> text format
# - live_bot.log            -> text format
# - live_bot.jsonl          -> JSON line per event (1 event = 1 radek)
# Volitelne log_event(..., log_targets=...) omezi zapis jen na vybrane handlery.

import json
import logging
import os
import socket
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from config.bot_config import BotConfig

# ─── GLOBALNI BOT INSTANCE ID ─────────────────────────
# Vytvori se jednou pri importu modulu (= pri startu procesu).
# Kazdy restart bota = novy bot_instance_id.
BOT_INSTANCE_ID: str = str(uuid.uuid4())

# Hostname stroje, na kterem bot bezi (zatim placeholder za vps_name)
HOSTNAME: str = socket.gethostname()

# Verze logger schema (pro budouci migrace)
LOG_SCHEMA_VERSION: str = "1.0"

# Cíle pro log_event(..., log_targets=...) — omezí výstup jen na vybrané handlery.
LOG_TARGET_CONSOLE: str = "console"
LOG_TARGET_FILE_TEXT: str = "file_text"
LOG_TARGET_FILE_JSON: str = "file_json"
# Konzole + .log (lidský text); bez JSONL
LOG_TARGETS_TEXT_SINKS: frozenset[str] = frozenset({LOG_TARGET_CONSOLE, LOG_TARGET_FILE_TEXT})
# Jen live_bot.jsonl (strukturovaný řádek)
LOG_TARGETS_JSONL_ONLY: frozenset[str] = frozenset({LOG_TARGET_FILE_JSON})


class _LogTargetFilter(logging.Filter):
    """Je-li na záznamu _log_targets, projde jen handler s odpovídajícím id."""

    def __init__(self, target_id: str) -> None:
        super().__init__()
        self._target_id = target_id

    def filter(self, record: logging.LogRecord) -> bool:
        targets = getattr(record, "_log_targets", None)
        if targets is None:
            return True
        return self._target_id in targets


# ─── TEXT FORMATTER (pro lidi) ─────────────────────────
class TextFormatter(logging.Formatter):
    """Standardni textovy formatter pro konzoli a .log soubor."""
    def __init__(self):
        super().__init__(
            fmt="%(asctime)s  %(levelname)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


# ─── JSON FORMATTER (pro monitoring agent) ─────────────
class JsonFormatter(logging.Formatter):
    """
    Formatter pro JSONL output. Pokud log record ma atribut '_event_data'
    (dict), pouzije se primo. Jinak vyrobi minimalni JSON z plain log message.
    """
    def format(self, record: logging.LogRecord) -> str:
        # Strukturovany event z log_event() ?
        event_data = getattr(record, "_event_data", None)

        if event_data is not None:
            # log_event() uz pripravil cely dict
            return json.dumps(event_data, ensure_ascii=False, default=str)

        # Fallback: plain log.info("xxx") -> minimalni JSON
        payload = {
            "ts_iso":           datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "severity":         record.levelname,
            "event":            "LOG",
            "bot_instance_id":  BOT_INSTANCE_ID,
            "vps_name":         HOSTNAME,
            "event_id":         str(uuid.uuid4()),
            "schema_version":   LOG_SCHEMA_VERSION,
            "message":          record.getMessage(),
            "logger":           record.name,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


# ─── SETUP ──────────────────────────────────────────────
def _parse_ts_iso(ts: str) -> datetime | None:
    if not isinstance(ts, str):
        return None
    raw = ts.strip()
    if not raw:
        return None
    # Podporujeme i UTC "Z" suffix.
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def prune_jsonl_history(json_file: str, retention_days: int | float | None) -> None:
    """
    Ponecha v jsonl jen radky s ts_iso >= now - retention_days.
    - Pokud retention_days <= 0 nebo None, pruning se neprovadi.
    - Radky bez validniho ts_iso ponechavame (bezpecny fallback).
    """
    if retention_days is None or retention_days <= 0:
        return
    if not os.path.exists(json_file):
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=float(retention_days))
    temp_file = f"{json_file}.tmp"

    with open(json_file, "r", encoding="utf-8") as src, open(temp_file, "w", encoding="utf-8") as dst:
        for line in src:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                # Nevalidni radek neriskujeme ztratit.
                dst.write(line)
                continue

            ts = _parse_ts_iso(payload.get("ts_iso"))
            if ts is None or ts >= cutoff:
                dst.write(line)

    os.replace(temp_file, json_file)


def write_bot_config_snapshot_jsonl(cfg: BotConfig, output_file: str) -> None:
    """
    Zapise 1 radek JSONL se zakladni konfiguraci bota.
    Soubor se prepise pri startu (neappenduje), behem runu se uz nemeni.
    """
    config_dict = asdict(cfg)
    config_dict["timeframe_label"] = cfg.timeframe_label
    payload = {
        "ts_iso": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "BOT_CONFIG_SNAPSHOT",
        "bot_name": cfg.bot_name,
        "magic": cfg.magic,
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe_label,
        "settings": config_dict,
    }
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def setup_logging(
    log_file: str = "live_bot.log",
    json_file: str = "live_bot.jsonl",
    json_retention_days: int | float | None = None,
) -> logging.Logger:
    """
    Nastavi 3 handlery:
      - StreamHandler        -> konzole (text)
      - FileHandler text     -> live_bot.log (text)
      - FileHandler json     -> live_bot.jsonl (JSON line)
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Vyclisti existujici handlery (kdyby setup_logging bylo volano vickrat)
    root.handlers.clear()

    text_fmt = TextFormatter()
    json_fmt = JsonFormatter()

    # Pred otevrenim JSON handleru odmazeme stare eventy podle ts_iso.
    try:
        prune_jsonl_history(json_file=json_file, retention_days=json_retention_days)
    except Exception:
        # Logging setup nesmi shodit start bota.
        pass

    # 1) Konzole - text
    console = logging.StreamHandler()
    console.setFormatter(text_fmt)
    console.setLevel(logging.INFO)
    console.addFilter(_LogTargetFilter(LOG_TARGET_CONSOLE))
    root.addHandler(console)

    # 2) Soubor - text
    file_text = logging.FileHandler(log_file, encoding="utf-8")
    file_text.setFormatter(text_fmt)
    file_text.setLevel(logging.INFO)
    file_text.addFilter(_LogTargetFilter(LOG_TARGET_FILE_TEXT))
    root.addHandler(file_text)

    # 3) Soubor - JSON line
    file_json = logging.FileHandler(json_file, encoding="utf-8")
    file_json.setFormatter(json_fmt)
    file_json.setLevel(logging.INFO)
    file_json.addFilter(_LogTargetFilter(LOG_TARGET_FILE_JSON))
    root.addHandler(file_json)

    return root


# ─── LOG EVENT ──────────────────────────────────────────
def log_event(
    cfg: BotConfig,
    level: str,
    event: str,
    message: str = "",
    *,
    log_targets: frozenset[str] | None = None,
    **kwargs,
) -> None:
    """
    Zaloguje strukturovany event do handleru.

    Pokud je log_targets None, zapíše se do všech 3 výstupů (konzole, .log, .jsonl).
    Jinak jen do vybraných — např. LOG_TARGETS_TEXT_SINKS nebo LOG_TARGETS_JSONL_ONLY.

    Text format:
      "EVENT=STATUS | BOT=... | SYMBOL=... | TF=... | key=value | MSG=..."

    JSON format (1 radek):
      {"ts_iso":"2026-04-29T13:21:20Z","severity":"INFO","event":"STATUS",
       "bot_id":"EU50p_FINTOKEI_01","bot_instance_id":"...","symbol":"EU50p",
       "tf":"M30","vps_name":"PC-NAME","event_id":"...","balance":100000.0, ...}
    """
    log = logging.getLogger("trading_bot")

    # ── Text format (zachovava puvodni vzhled) ──
    parts = [
        f"EVENT={event}",
        f"BOT={cfg.bot_name}",
        f"SYMBOL={cfg.symbol}",
        f"TF={cfg.timeframe_label}",
    ]
    for key, value in kwargs.items():
        parts.append(f"{key}={value}")
    if message:
        parts.append(f"MSG={message}")
    text_line = " | ".join(parts)

    # ── JSON format (povinna pole + custom payload) ──
    json_payload = {
        "ts_iso":          datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "severity":        level.upper(),
        "event":           event,
        "bot_id":          cfg.bot_name,
        "bot_instance_id": BOT_INSTANCE_ID,
        "symbol":          cfg.symbol,
        "tf":              cfg.timeframe_label,
        "magic":           cfg.magic,
        "vps_name":        HOSTNAME,
        "event_id":        str(uuid.uuid4()),
        "schema_version":  LOG_SCHEMA_VERSION,
    }
    # Custom data z **kwargs (balance, equity, atd.)
    for key, value in kwargs.items():
        json_payload[key] = value
    if message:
        json_payload["message"] = message

    # ── Vytvor LogRecord rucne aby JsonFormatter videl _event_data ──
    log_method = getattr(log, level.lower(), log.info)
    extra: dict = {"_event_data": json_payload}
    if log_targets is not None:
        extra["_log_targets"] = log_targets
    log_method(text_line, extra=extra)