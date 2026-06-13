"""Diff WAVE/WAVE_COUNTER trades across tp_mode (bos_exit, rrr_fixed, wave_target_n)."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import openpyxl


BASE = Path("results/EURUSD/grid_EXAMPLE_M30_2026-03-03_2026-05-10_093/trades")


def load(file: Path) -> tuple[list[str], list[tuple]]:
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    return header, rows[1:]


def main() -> None:
    files = sorted(BASE.iterdir())
    # combo_no = 1..6  ; 1=BOS noCtr, 2=BOS Ctr, 3=RRR noCtr, 4=RRR Ctr, 5=WN noCtr, 6=WN Ctr
    combos = {1: "BOS_EXIT_noCtr", 3: "RRR_FIXED_noCtr", 5: "WAVE_N_noCtr",
              2: "BOS_EXIT_Ctr",   4: "RRR_FIXED_Ctr",   6: "WAVE_N_Ctr"}

    per_combo: dict[str, dict] = {}
    for i, f in enumerate(files, start=1):
        if i not in combos:
            continue
        header, rows = load(f)
        ci = {name: header.index(name) for name in header}
        wave = [r for r in rows if r[ci["position_kind"]] == "WAVE"]
        wctr = [r for r in rows if r[ci["position_kind"]] == "WAVE_COUNTER"]
        per_combo[combos[i]] = dict(
            header=header,
            ci=ci,
            wave=wave,
            wave_counter=wctr,
        )
        print(f"combo {i:>2} {combos[i]:<18}  WAVE={len(wave):>3}  WAVE_COUNTER={len(wctr):>3}")

    print()
    print("=" * 80)
    print("WAVE entries DIFFERENCE per (wave_time, dir) — no-counter variant:")
    print("=" * 80)
    sw_bos = {(str(r[per_combo['BOS_EXIT_noCtr']['ci']['wave_time']]), str(r[per_combo['BOS_EXIT_noCtr']['ci']['dir']]))
              for r in per_combo['BOS_EXIT_noCtr']['wave']}
    sw_rrr = {(str(r[per_combo['RRR_FIXED_noCtr']['ci']['wave_time']]), str(r[per_combo['RRR_FIXED_noCtr']['ci']['dir']]))
              for r in per_combo['RRR_FIXED_noCtr']['wave']}
    sw_wn = {(str(r[per_combo['WAVE_N_noCtr']['ci']['wave_time']]), str(r[per_combo['WAVE_N_noCtr']['ci']['dir']]))
             for r in per_combo['WAVE_N_noCtr']['wave']}

    print(f"  shared by all 3 modes: {len(sw_bos & sw_rrr & sw_wn)}")
    print(f"  RRR_FIXED only:        {len(sw_rrr - sw_bos - sw_wn)}")
    print(f"  WAVE_N only:           {len(sw_wn - sw_bos - sw_rrr)}")
    print(f"  BOS_EXIT only:         {len(sw_bos - sw_rrr - sw_wn)}")
    print(f"  RRR+WN, not BOS:       {len(sw_rrr & sw_wn - sw_bos)}")
    print(f"  RRR+BOS, not WN:       {len(sw_rrr & sw_bos - sw_wn)}")
    print(f"  BOS+WN, not RRR:       {len(sw_bos & sw_wn - sw_rrr)}")

    rrr_info = per_combo['RRR_FIXED_noCtr']
    ci_rrr = rrr_info['ci']
    print()
    print("--- WAVE entries ONLY in RRR_FIXED (not in BOS_EXIT, not in WAVE_N):")
    rrr_by_key = {(str(r[ci_rrr['wave_time']]), str(r[ci_rrr['dir']])): r for r in rrr_info['wave']}
    for key in sorted(sw_rrr - sw_bos - sw_wn):
        r = rrr_by_key[key]
        wt = key[0]; d = key[1]
        entry_t = r[ci_rrr['entry_type']]
        close_r = r[ci_rrr['close_reason']]
        entry_time = r[ci_rrr['entry_time']]
        pnl = r[ci_rrr['pnl_usd']]
        print(f"  wave_time={wt} dir={d:<4}  entry_type={str(entry_t):<14} entry_at={entry_time}  close={close_r:<8} pnl={pnl}")

    wn_info = per_combo['WAVE_N_noCtr']
    ci_wn = wn_info['ci']
    print()
    print("--- WAVE entries ONLY in WAVE_N (not in BOS_EXIT, not in RRR_FIXED):")
    wn_by_key = {(str(r[ci_wn['wave_time']]), str(r[ci_wn['dir']])): r for r in wn_info['wave']}
    for key in sorted(sw_wn - sw_bos - sw_rrr):
        r = wn_by_key[key]
        wt = key[0]; d = key[1]
        entry_t = r[ci_wn['entry_type']]
        close_r = r[ci_wn['close_reason']]
        entry_time = r[ci_wn['entry_time']]
        pnl = r[ci_wn['pnl_usd']]
        print(f"  wave_time={wt} dir={d:<4}  entry_type={str(entry_t):<14} entry_at={entry_time}  close={close_r:<8} pnl={pnl}")

    print()
    print("=" * 80)
    print("WAVE_COUNTER DIFFERENCE per (wave_time, dir) — Ctr variant:")
    print("=" * 80)
    c_bos = {(str(r[per_combo['BOS_EXIT_Ctr']['ci']['wave_time']]), str(r[per_combo['BOS_EXIT_Ctr']['ci']['dir']]))
             for r in per_combo['BOS_EXIT_Ctr']['wave_counter']}
    c_rrr = {(str(r[per_combo['RRR_FIXED_Ctr']['ci']['wave_time']]), str(r[per_combo['RRR_FIXED_Ctr']['ci']['dir']]))
             for r in per_combo['RRR_FIXED_Ctr']['wave_counter']}
    c_wn  = {(str(r[per_combo['WAVE_N_Ctr']['ci']['wave_time']]), str(r[per_combo['WAVE_N_Ctr']['ci']['dir']]))
             for r in per_combo['WAVE_N_Ctr']['wave_counter']}
    print(f"  BOS_EXIT={len(c_bos)} | RRR_FIXED={len(c_rrr)} | WAVE_N={len(c_wn)}")
    print(f"  shared by all 3 modes: {len(c_bos & c_rrr & c_wn)}")
    print(f"  RRR_FIXED only:        {len(c_rrr - c_bos - c_wn)}")
    print(f"  BOS_EXIT only:         {len(c_bos - c_rrr - c_wn)}")
    print(f"  WAVE_N only:           {len(c_wn - c_rrr - c_bos)}")
    print(f"  RRR+BOS, not WN:       {len(c_rrr & c_bos - c_wn)}")


if __name__ == "__main__":
    main()
