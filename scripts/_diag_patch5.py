import re

with open('scripts/_diag_wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r'print\(f"\[\{df\.iloc\[i\]\[\\\'time\\\'\]\}\] Wave \{wt\}: scenario=\{scenario\}, ext1_count_window=\{ext1_count_window\}, ext1_counter_idx=\{ext1_counter_idx\}"\)',
    'print(f"[{df.iloc[i][\'time\']}] Wave {wt}: scenario={scenario}, ext1_count_window={ext1_count_window}, ext1_counter_idx={ext1_counter_idx}")',
    content
)

# Actually, let's just replace the whole line
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'scenario = ext_scenario_classify' in line:
        lines[i] = line + '\n                print("Wave " + str(wt) + " scenario=" + str(scenario) + " ext1_count_window=" + str(ext1_count_window) + " ext1_counter_idx=" + str(ext1_counter_idx))'
    if 'print(f"[{df.iloc[i]' in line:
        lines[i] = '' # remove the broken one

with open('scripts/_diag_wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
