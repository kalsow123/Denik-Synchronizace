import re

with open("strategy/wave_sequence.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix Scenario A duplicate
content = re.sub(
    r'                    result\[wt\] = WaveSequenceInfo\(new_idx, prev_wt, is_bos_wave=True\)\n                    ext_active_wave = w\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    maybe_update_trend_state_with_wave\(state, w, cfg\)\n                    continue\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    maybe_update_trend_state_with_wave\(state, w, cfg\)\n                    continue',
    r'                    result[wt] = WaveSequenceInfo(new_idx, prev_wt, is_bos_wave=True)\n                    ext_active_wave = w\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    maybe_update_trend_state_with_wave(state, w, cfg)\n                    continue',
    content
)

# Fix Scenario B duplicate
content = re.sub(
    r'                    result\[wt\] = WaveSequenceInfo\(new_idx, prev_wt, is_bos_wave=True\)\n                    ext_active_wave = w\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    continue\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    continue',
    r'                    result[wt] = WaveSequenceInfo(new_idx, prev_wt, is_bos_wave=True)\n                    ext_active_wave = w\n                    first_ext_counter_wt = None\n                    ext_climax_reversal_dir = None\n                    climax_dir = climax_idx = climax_extreme = None\n                    state.is_bos_wave_pending = False\n                    trend_established_by_ext = True\n                    ext1_count_window = True\n                    ext1_counter_idx = 0\n                    last_ext1_counter_wt = None\n                    continue',
    content
)

# Remove prints
content = re.sub(r'            print\(f"BOS check pre:.*?\n', '', content)
content = re.sub(r'                print\(f"BOS check:.*?\n', '', content)
content = re.sub(r'            print\(f"After BOS check:.*?\n', '', content)

with open("strategy/wave_sequence.py", "w", encoding="utf-8") as f:
    f.write(content)
