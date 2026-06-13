import json
from pathlib import Path

transcript_path = Path('C:/Users/a2010/.cursor/projects/c-Users-a2010-Desktop-TRADING-Trading-bot-Perplexity-computer-bot-becktester-01-Z-PC-WAVES-BOS-DOLAZEN-VLN/agent-transcripts/0e20c1e5-b52f-4473-9b71-d88de975601c/0e20c1e5-b52f-4473-9b71-d88de975601c.jsonl')

lines = transcript_path.read_text(encoding='utf-8').splitlines()
messages = []
for line in lines[-150:]: # Check last 150 events
    try:
        event = json.loads(line)
        if 'message' in event and 'role' in event['message']:
            role = event['message']['role']
            text = event['message'].get('content', '')
            if isinstance(text, list):
                text = ' '.join(b.get('text', '') for b in text if b.get('type') == 'text')
            messages.append({'role': role, 'text': text})
    except:
        pass

for m in messages[-10:]:
    print(f"Role: {m['role']}")
    print(f"Text:\n{m['text'][:1000]}..." if len(m['text']) > 1000 else f"Text:\n{m['text']}")
    print('-'*50)
