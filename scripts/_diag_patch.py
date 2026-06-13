import re

with open('scripts/_diag_wave_sequence.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(
    r'(^[ \t]*ext1_count_window = False.*$)',
    r'\1\n\g<1>'.replace('ext1_count_window = False', 'print(f"[{df.iloc[i][\'time\']}] ext1_count_window = False")'),
    content,
    flags=re.MULTILINE
)

with open('scripts/_diag_wave_sequence.py', 'w', encoding='utf-8') as f:
    f.write(content)
