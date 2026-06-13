from datetime import datetime


# ───── DEDUPLIKACE / OTISK SIGNALU ──────────────────────────
# Bot nezpracuje signal - vlny dvakrat.
#
# entry_tag (default "base") rozsiruje deduplikaci o EXT vstupy a podobne typy
# orderu, ktere mohou vzniknout ze STEJNE vlny vice nez jednou:
#   "base"             — standardni wave entry (i EXT primary).
#   "ext_0236"         — sekundarni EXT vstup (cfg.ext_secondary_fib_level).
#   "ext_counter_time" — EXT counter pozice spustena casovym pravidlem.
#   "ext_counter_bos"  — EXT counter pozice z EXT BOS triggeru.
# Pri "base" se key generuje EXACT podle stareho formatu (bez prefixu), aby
# stary state / cache pokracovaly bez nutnosti migrace.
def _normalize_wave_time(value) -> str:
    if value is None:
        return "na"
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d%H%M")
    return str(value)


def get_signal_key(wave, digits: int = 4, entry_tag: str = "base") -> str:
    try:
        precision = max(0, int(digits))
    except Exception:
        precision = 4

    fib_bucket = round(float(wave["fib50"]), precision)
    sl_bucket = round(float(wave["sl"]), precision)
    wave_time = _normalize_wave_time(wave.get("wave_time"))
    base_key = f"{wave['dir']}_{fib_bucket}_{sl_bucket}_{wave_time}"
    tag = (entry_tag or "base").strip() or "base"
    if tag == "base":
        return base_key
    return f"{tag}|{base_key}"
