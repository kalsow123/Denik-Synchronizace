import re

with open('strategy/wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove _reset_ext1_count_state in KROK 1
content = re.sub(
    r'[ \t]*ext1_count_window, ext1_counter_idx, last_ext1_counter_wt = \(\n[ \t]*_reset_ext1_count_state\(\)\n[ \t]*\)\n',
    '',
    content
)

# 2. Update retro claim
old_retro = """        if state.is_bos_wave_pending:
            claimed = _retro_claim_bos_seed_wave(result, waves, i, state.direction)
            if claimed is not None:
                wt_claim, wdir_claim = claimed
                result[wt_claim] = WaveSequenceInfo(1, None, is_bos_wave=True)
                if wdir_claim == 1:
                    counter_up = 1
                    last_same_dir_up_wt = wt_claim
                    counter_down = 0
                    last_same_dir_down_wt = None
                else:
                    counter_down = 1
                    last_same_dir_down_wt = wt_claim
                    counter_up = 0
                    last_same_dir_up_wt = None
                state.is_bos_wave_pending = False
                ext_climax_reversal_dir = None"""

new_retro = """        if state.is_bos_wave_pending:
            claimed = _retro_claim_bos_seed_wave(result, waves, i, state.direction)
            if claimed is not None:
                wt_claim, wdir_claim = claimed
                new_idx = 1
                prev_wt = None
                if ext1_count_window:
                    ext1_counter_idx += 1
                    new_idx = ext1_counter_idx
                    prev_wt = last_ext1_counter_wt
                result[wt_claim] = WaveSequenceInfo(new_idx, prev_wt, is_bos_wave=True)
                if wdir_claim == 1:
                    counter_up = new_idx
                    last_same_dir_up_wt = wt_claim
                    counter_down = 0
                    last_same_dir_down_wt = None
                else:
                    counter_down = new_idx
                    last_same_dir_down_wt = wt_claim
                    counter_up = 0
                    last_same_dir_up_wt = None
                state.is_bos_wave_pending = False
                ext_climax_reversal_dir = None
                ext1_count_window, ext1_counter_idx, last_ext1_counter_wt = _reset_ext1_count_state()"""

content = content.replace(old_retro, new_retro)

# 3. Update KROK 0 is_bos_wave_pending
old_krok0 = """            if state.is_bos_wave_pending:
                wave_dir_matches_flip = (
                    (state.direction == "bull" and wdir == 1)
                    or (state.direction == "bear" and wdir == -1)
                )
                if wave_dir_matches_flip:
                    new_idx = 1
                    prev_wt = None
                    if ext1_count_window:
                        ext1_counter_idx += 1
                        new_idx = ext1_counter_idx
                        prev_wt = last_ext1_counter_wt
                        last_ext1_counter_wt = wt

                    if wdir == 1:
                        counter_up = new_idx
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = new_idx
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(
                        new_idx, prev_wt, is_bos_wave=True
                    )
                    state.is_bos_wave_pending = False
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    trend_established_by_ext = bool(w.get("is_ext"))
                    if w.get("is_ext"):
                        ext1_count_window = True
                        ext_active_wave = w
                        first_ext_counter_wt = None
                        ext1_counter_idx = 0
                        last_ext1_counter_wt = None
                    continue"""

new_krok0 = """            if state.is_bos_wave_pending and not is_ext:
                wave_dir_matches_flip = (
                    (state.direction == "bull" and wdir == 1)
                    or (state.direction == "bear" and wdir == -1)
                )
                if wave_dir_matches_flip:
                    new_idx = 1
                    prev_wt = None
                    if ext1_count_window:
                        ext1_counter_idx += 1
                        new_idx = ext1_counter_idx
                        prev_wt = last_ext1_counter_wt
                        last_ext1_counter_wt = wt

                    if wdir == 1:
                        counter_up = new_idx
                        last_same_dir_up_wt = wt
                        counter_down = 0
                        last_same_dir_down_wt = None
                    else:
                        counter_down = new_idx
                        last_same_dir_down_wt = wt
                        counter_up = 0
                        last_same_dir_up_wt = None
                    result[wt] = WaveSequenceInfo(
                        new_idx, prev_wt, is_bos_wave=True
                    )
                    state.is_bos_wave_pending = False
                    ext_climax_reversal_dir = None
                    climax_dir = climax_idx = climax_extreme = None
                    trend_established_by_ext = False
                    ext1_count_window, ext1_counter_idx, last_ext1_counter_wt = _reset_ext1_count_state()
                    continue"""

content = content.replace(old_krok0, new_krok0)

# 4. Update KROK 2 scenario
old_krok2 = """            # KROK 2: EXT vlna detekce
            if is_ext:
                swing_levels = {
                    "last_up_box_bottom": state.last_up_box_bottom,
                    "last_down_box_top": state.last_down_box_top,
                }
                scenario = ext_scenario_classify(w, state, bar_close, swing_levels)"""

new_krok2 = """            # KROK 2: EXT vlna detekce
            if is_ext:
                swing_levels = {
                    "last_up_box_bottom": state.last_up_box_bottom,
                    "last_down_box_top": state.last_down_box_top,
                }
                if state.is_bos_wave_pending:
                    scenario = "A"
                else:
                    scenario = ext_scenario_classify(w, state, bar_close, swing_levels)"""

content = content.replace(old_krok2, new_krok2)

with open('strategy/wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write(content)
