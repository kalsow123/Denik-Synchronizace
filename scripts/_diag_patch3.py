import re

with open('scripts/_diag_wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r'(^[ \t]*scenario = ext_scenario_classify\(w, state, bar_close, swing_levels\).*$)',
    r'\1\n                print(f"[{df.iloc[i][\'time\']}] Wave {wt}: scenario={scenario}, ext1_count_window={ext1_count_window}, ext1_counter_idx={ext1_counter_idx}")',
    content,
    flags=re.MULTILINE
)

with open('scripts/_diag_wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write(content)
