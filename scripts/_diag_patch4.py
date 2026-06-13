import re

with open('scripts/_diag_wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    r"print(f\"[{df.iloc[i][\'time\']}] Wave {wt}: scenario={scenario}, ext1_count_window={ext1_count_window}, ext1_counter_idx={ext1_counter_idx}\")",
    r"print(f'[{df.iloc[i][\"time\"]}] Wave {wt}: scenario={scenario}, ext1_count_window={ext1_count_window}, ext1_counter_idx={ext1_counter_idx}')"
)

with open('scripts/_diag_wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write(content)
