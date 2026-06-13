import re

with open("strategy/wave_sequence.py", "r", encoding="utf-8") as f:
    content = f.read()

# Revert Scenario A
content = re.sub(
    r'                if scenario == "A":\n                    # EXT je BOS vlna\n                    state.direction = "bear" if wdir == -1 else "bull"\n                    \n                    new_idx = 1\n                    prev_wt = None\n                    if ext1_count_window:\n                        new_idx = ext1_counter_idx \+ 1\n                        prev_wt = last_ext1_counter_wt\n\n                    if wdir == 1:\n                        counter_up = new_idx\n                        last_same_dir_up_wt = wt\n                        counter_down = 0\n                        last_same_dir_down_wt = None\n                    else:\n                        counter_down = new_idx\n                        last_same_dir_down_wt = wt\n                        counter_up = 0\n                        last_same_dir_up_wt = None\n                    result\[wt\] = WaveSequenceInfo\(new_idx, prev_wt, is_bos_wave=True\)',
    r'                if scenario == "A":\n                    # EXT je BOS vlna\n                    state.direction = "bear" if wdir == -1 else "bull"\n                    if wdir == 1:\n                        counter_up = 1\n                        last_same_dir_up_wt = wt\n                        counter_down = 0\n                        last_same_dir_down_wt = None\n                    else:\n                        counter_down = 1\n                        last_same_dir_down_wt = wt\n                        counter_up = 0\n                        last_same_dir_up_wt = None\n                    result[wt] = WaveSequenceInfo(1, None, is_bos_wave=True)',
    content
)

# Revert Scenario B
content = re.sub(
    r'                elif scenario == "B":\n                    # EXT je counter k aktualnimu trendu. Uziv. pozadavek:\n                    # opacna vlna po/pri EXT zaklada novy smer => okamzity flip,\n                    # idx 1 \(EXT vlna MUSI mit cislo\).\n                    state.direction = "bear" if wdir == -1 else "bull"\n                    \n                    new_idx = 1\n                    prev_wt = None\n                    if ext1_count_window:\n                        new_idx = ext1_counter_idx \+ 1\n                        prev_wt = last_ext1_counter_wt\n\n                    if wdir == 1:\n                        counter_up = new_idx\n                        last_same_dir_up_wt = wt\n                        counter_down = 0\n                        last_same_dir_down_wt = None\n                    else:\n                        counter_down = new_idx\n                        last_same_dir_down_wt = wt\n                        counter_up = 0\n                        last_same_dir_up_wt = None\n                    result\[wt\] = WaveSequenceInfo\(new_idx, prev_wt, is_bos_wave=True\)',
    r'                elif scenario == "B":\n                    # EXT je counter k aktualnimu trendu. Uziv. pozadavek:\n                    # opacna vlna po/pri EXT zaklada novy smer => okamzity flip,\n                    # idx 1 (EXT vlna MUSI mit cislo).\n                    state.direction = "bear" if wdir == -1 else "bull"\n                    if wdir == 1:\n                        counter_up = 1\n                        last_same_dir_up_wt = wt\n                        counter_down = 0\n                        last_same_dir_down_wt = None\n                    else:\n                        counter_down = 1\n                        last_same_dir_down_wt = wt\n                        counter_up = 0\n                        last_same_dir_up_wt = None\n                    result[wt] = WaveSequenceInfo(1, None, is_bos_wave=True)',
    content
)

with open("strategy/wave_sequence.py", "w", encoding="utf-8") as f:
    f.write(content)
