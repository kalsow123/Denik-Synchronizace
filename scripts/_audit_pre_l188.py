"""Audit wave_sequence.py vs transcript StrReplace ops (pre/post L188)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = Path(
    r"C:\Users\Teodor\.cursor\projects\d-TRADING-EURUSD-Trading-Bot-01-Z-PC-WAVES-BOS-DOLAZEN-VLN"
    r"\agent-transcripts\65934898-8548-4f8e-88eb-551ac6208448"
    r"\65934898-8548-4f8e-88eb-551ac6208448.jsonl"
)
TARGET = ROOT / "strategy" / "wave_sequence.py"
BOS_START = 188


def collect_ops() -> list[dict]:
    ops: list[dict] = []
    for i, line in enumerate(TRANSCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("role") != "assistant":
            continue
        for part in obj.get("message", {}).get("content", []):
            if part.get("type") != "tool_use" or part.get("name") != "StrReplace":
                continue
            inp = part.get("input", {})
            if "wave_sequence.py" not in inp.get("path", ""):
                continue
            ops.append({"line": i, "old": inp.get("old_string", ""), "new": inp.get("new_string", "")})
    return ops


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    ops = collect_ops()
    pre = [o for o in ops if o["line"] < BOS_START]
    post = [o for o in ops if o["line"] >= BOS_START]
    print(f"transcript ops: pre-L188={len(pre)} post-L188={len(post)}")

    still_new: list[tuple[int, int, str]] = []
    for o in post:
        ns = o["new"]
        if ns and ns in text:
            still_new.append((o["line"], len(ns), ns[:80].replace("\n", " ")))
    print(f"post-L188 new_string fragments STILL present: {len(still_new)}")
    for ln, lnlen, head in still_new:
        print(f"  L{ln} len={lnlen} {head!r}")

    missing_pre_new = [o["line"] for o in pre if o["new"] and o["new"] not in text]
    print(f"pre-L188 new_string MISSING (accidental revert): {missing_pre_new}")

    # Reverse-simulate: start from current, apply reverse of post ops; should match pre-state old strings
    rev_fail = 0
    for o in reversed(post):
        ns, old = o["new"], o["old"]
        if not ns:
            continue
        if ns in text:
            rev_fail += 1
    print(f"post ops not fully reversed (new still in file): {rev_fail}/{len(post)}")

    import_block = text.split("from strategy.trend_bos import (", 1)
    bos_import = import_block[1].split(")", 1)[0] if len(import_block) > 1 else ""
    markers = {
        "docstring draw_right": "casovani k `draw_right`, ne k `birth_bar`" in text,
        "mech_c_draw_right": "not any(w.get(\"draw_right\") == i for w in waves)" in text,
        "no KROK 1-BOS": "KROK 1-BOS" not in text,
        "no waves_by_birth_bar": "waves_by_birth_bar" not in text,
        "no _bos_close in import": "_bos_close_flip_with_forgive" not in bos_import,
        "_ghost_skip_wave": "def _ghost_skip_wave" in text,
        "sync_wave_sequence_state": "def sync_wave_sequence_state" in text,
        "maybe_update x3": text.count("maybe_update_trend_state_with_wave") == 3,
        "pending no ghost_skip": "if state.is_bos_wave_pending:" in text
        and "_ghost_skip_wave" not in text.split("if state.is_bos_wave_pending:")[1].split("# KROK 2: EXT")[0],
    }
    print("--- pre-L188 + ghost-gate markers ---")
    for k, v in markers.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
