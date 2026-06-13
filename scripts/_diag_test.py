import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath('.'))

from strategy.wave_sequence import compute_wave_sequence_info_per_wave

class BotConfig:
    pass

def test_ext_bos_via_fib_35_flips_trend():
    df = pd.DataFrame({
        "time": ["T0", "T1", "T2", "T3"],
        "close": [1.15, 1.20, 1.10, 1.05]
    })
    
    def _w(wt, d, dr, **kwargs):
        w = {"wave_time": wt, "dir": d, "draw_right": dr}
        w.update(kwargs)
        return w

    waves = [
        _w("EXT", 1, 1, is_ext=True, ext_fib_35_level=1.12, box_bottom=1.10, box_top=1.30),
        # T2: close 1.10 < 1.12 -> Mech B flip
        _w("D1", -1, 3, box_bottom=1.00, box_top=1.10) # Prvni vlna ve smeru flipu
    ]
    
    res = compute_wave_sequence_info_per_wave(df, waves, BotConfig())
    print("D1 index:", res["D1"].index_in_trend)
    print("D1 is_bos:", res["D1"].is_bos_wave)

if __name__ == "__main__":
    test_ext_bos_via_fib_35_flips_trend()
