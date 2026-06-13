import re

with open('scripts/_diag_wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r'(^[ \t]*scenario = ext_scenario_classify\(w, state, bar_close, swing_levels\).*$)',
    r'\1\n\g<1>'.replace('scenario = ext_scenario_classify(w, state, bar_close, swing_levels)', 'print(f"[{df.iloc[i][\'time\']}] Wave {wt}: scenario={scenario}")'),
    content,
    flags=re.MULTILINE
)

with open('scripts/_diag_wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write(content)
