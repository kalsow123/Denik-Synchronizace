import sys
import os
import pandas as pd

# Add the current directory to the path so we can import the strategy
sys.path.insert(0, os.path.abspath('.'))

from strategy.wave_sequence import compute_wave_sequence_info_per_wave

class BotConfig:
    pass

def test_ext_wave_numbering():
    # Mock data
    df = pd.DataFrame({
        "close": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0]
    })
    
    waves = [
        # EXT 1 DOWN
        {"wave_time": "t1", "dir": -1, "is_ext": True, "draw_right": 1, "box_top": 1.5, "box_bottom": 1.0},
        # Counter UP 1
        {"wave_time": "t2", "dir": 1, "is_ext": False, "draw_right": 3, "box_top": 1.2, "box_bottom": 1.1},
        # Counter UP 2
        {"wave_time": "t3", "dir": 1, "is_ext": False, "draw_right": 5, "box_top": 1.3, "box_bottom": 1.2},
        # EXT UP (Should be 3)
        {"wave_time": "t4", "dir": 1, "is_ext": True, "draw_right": 7, "box_top": 1.6, "box_bottom": 1.3},
    ]
    
    cfg = BotConfig()
    
    res = compute_wave_sequence_info_per_wave(df, waves, cfg)
    
    for wt, info in res.items():
        print(f"Wave {wt}: index={info.index_in_trend}, is_bos={info.is_bos_wave}")

if __name__ == "__main__":
    test_ext_wave_numbering()
