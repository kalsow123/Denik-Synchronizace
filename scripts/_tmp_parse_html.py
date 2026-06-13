"""Temporary: parse visual waves HTML for WF/BOS analysis."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
s = path.read_text(encoding="utf-8", errors="replace")

# Find plotly data JSON
m = re.search(r"Plotly\.newPlot\(\s*\"[^\"]+\",\s*(\[)", s)
if not m:
    print("no plotly data")
    sys.exit(1)

start = m.start(1)
# bracket match
depth = 0
i = start
while i < len(s):
    c = s[i]
    if c == "[":
        depth += 1
    elif c == "]":
        depth -= 1
        if depth == 0:
            end = i + 1
            break
    i += 1
else:
    print("no end bracket")
    sys.exit(1)

traces = json.loads(s[start:end])

for ti, tr in enumerate(traces):
    name = tr.get("name", "")
    ht = tr.get("hovertemplate", "") or ""
    text = tr.get("text")
    mode = tr.get("mode", "")
    if any(k in (name + ht + str(text)).upper() for k in ("WF", "BOS", "WAVE", "UP", "DOWN")):
        if "WF continuation" in ht or "WAVE_BOS" in ht or "BOS" in name or "WAVE" in name:
            print(f"\n--- trace {ti} name={name!r} mode={mode}")
            print("hover:", ht[:300])
            if text:
                print("text:", text if isinstance(text, str) else text[:5])

# Also grep wave_time patterns in full file
for pat in [r"20260508\d{4}", r"20260430\d{4}", r"20260303\d{4}"]:
    hits = sorted(set(re.findall(pat, s)))
    if hits:
        print(f"\nwave_times {pat}:", hits[:20])
