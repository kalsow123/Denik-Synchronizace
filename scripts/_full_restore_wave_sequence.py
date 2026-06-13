"""Reverse all transcript StrReplace ops on wave_sequence.py to pre-L188 state."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = Path(
    r"C:\Users\Teodor\.cursor\projects\d-TRADING-EURUSD-Trading-Bot-01-Z-PC-WAVES-BOS-DOLAZEN-VLN"
    r"\agent-transcripts\65934898-8548-4f8e-88eb-551ac6208448"
    r"\65934898-8548-4f8e-88eb-551ac6208448.jsonl"
)
TARGET = ROOT / "strategy" / "wave_sequence.py"
BOS_START_LINE = 188  # first BOS implementation turn


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
            ops.append(
                {
                    "line": i,
                    "old": inp.get("old_string", ""),
                    "new": inp.get("new_string", ""),
                }
            )
    return ops


def apply_reverse(text: str, ops: list[dict]) -> tuple[str, list[str]]:
    logs: list[str] = []
    for idx, op in enumerate(reversed(ops)):
        new_s, old_s = op["new"], op["old"]
        ln = op["line"]
        if not new_s:
            continue
        if new_s in text:
            text = text.replace(new_s, old_s, 1)
            logs.append(f"OK rev op#{idx} L{ln} new->old")
            continue
        if old_s in text and new_s not in text:
            logs.append(f"SKIP op#{idx} L{ln} already old")
            continue
        logs.append(f"FAIL op#{idx} L{ln} new_len={len(new_s)} old_len={len(old_s)}")
    return text, logs


def cleanup_artifacts(text: str) -> str:
    """Remove BOS-only helpers if any fragments remain after reverse."""
    for fn in (
        "_effective_wave_birth_bars",
        "_iter_close_flip_targets_by_bar",
        "_bos_flip_target_by_bar",
        "_apply_bos_map_flip_at_bar",
    ):
        text = re.sub(
            rf"\ndef {fn}\([\s\S]*?\n\n(?=def )",
            "\n",
            text,
            count=1,
        )
    text = text.replace(
        "from dataclasses import dataclass, replace\n",
        "from dataclasses import dataclass\n",
    )
    text = re.sub(
        r"        # KROK 2: Iterace vln s draw_right == i\s+# KROK 2: Iterace vln s draw_right == i \(index_in_trend\)\n",
        "        # KROK 2: Iterace vln s draw_right == i (index_in_trend)\n",
        text,
    )
    # Remove try/finally wrapper if left from broken restore
    text = text.replace("        for w in new_waves:\n            try:\n", "        for w in new_waves:\n")
    text = re.sub(
        r"\n            finally:\n"
        r"                if not w\.get\(\"post_ext_trend_suppressed\"\):\n"
        r"                    state = _maybe_seed_state_from_ext_post_trend\(state, w\)\n"
        r"                    maybe_update_trend_state_with_wave\(state, w, cfg\)\n",
        "\n",
        text,
        count=1,
    )
    # Unindent one level inside for w if double-indented body remains
    lines = text.splitlines(True)
    out: list[str] = []
    in_loop = False
    for line in lines:
        if line == "        for w in new_waves:\n":
            in_loop = True
            out.append(line)
            continue
        if in_loop:
            if line.startswith("        # Per-bar EXT-1"):
                in_loop = False
                out.append(line)
                continue
            if line.startswith("            ") and not line.startswith("        #"):
                out.append(line[4:])
                continue
        out.append(line)
    return "".join(out)


def main() -> None:
    all_ops = collect_ops()
    bos_ops = [o for o in all_ops if o["line"] >= BOS_START_LINE]
    print(f"all_ops={len(all_ops)} bos_ops={len(bos_ops)} from line {BOS_START_LINE}")

    text = TARGET.read_text(encoding="utf-8")

    # Step 1: reverse ALL post-BOS edits (including failed manual revert at L338+)
    text, logs1 = apply_reverse(text, bos_ops)
    print("--- reverse bos_ops ---")
    for ln in logs1:
        print(ln)

    text = cleanup_artifacts(text)

    # Step 2: verify pre-BOS markers
    checks = {
        "Mechanismus C klasicky": "Mechanismus C: klasický swing BOS" in text,
        "no KROK 1-BOS": "KROK 1-BOS" not in text,
        "no _bos_flip_target": "_bos_flip_target_by_bar" not in text,
        "no _apply_bos_map": "_apply_bos_map_flip_at_bar" not in text,
        "no _bos_close import": "_bos_close_flip_with_forgive" not in text,
        "docstring draw_right": "casovani k `draw_right`, ne k `birth_bar`" in text,
        "maybe_update present": "maybe_update_trend_state_with_wave" in text,
    }
    print("--- checks ---")
    for k, v in checks.items():
        print(k, v)

    fails = [op for op in bos_ops if op["new"] in text]
    if fails:
        print(f"WARNING: {len(fails)} new_strings still in file")
        for op in fails[:10]:
            print(f"  L{op['line']} new_head={op['new'][:80]!r}")

    compile(text, "wave_sequence.py", "exec")
    TARGET.write_text(text, encoding="utf-8")
    print(f"written {TARGET} lines={text.count(chr(10))+1}")


if __name__ == "__main__":
    main()
