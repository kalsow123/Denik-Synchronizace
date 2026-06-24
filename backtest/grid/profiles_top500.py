PROFILES["top500"] = {
    "grid_defaults": {
        "causal_mode": [False],  # True = backtest bez look-ahead (parita live)
        "run_e2e_parity": [False],  # True = po BT E2E parity (jen live_match, ne grid worker)
    },
    "grid": [
        # H1 o=2 r=1.50 f=0.450 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.8],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=2 r=2.00 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.85],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=2 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=2 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.7, 0.8, 0.85, 0.9, 0.95],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=3 r=1.00 f=0.500 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.8, 0.85, 0.9, 0.95, 1.0, 1.05],
            "min_opp_bars": [3],
            "rrr":          [1.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=3 r=1.50 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=3 r=1.50 f=0.450 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [1.05],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=3 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.85],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.00 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2],
            "min_opp_bars": [4],
            "rrr":          [1.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.00 f=0.450 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85],
            "min_opp_bars": [4],
            "rrr":          [1.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.00 f=0.500 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2],
            "min_opp_bars": [4],
            "rrr":          [1.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.00 f=0.618 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [1.05],
            "min_opp_bars": [4],
            "rrr":          [1.0],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.50 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=4 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.05],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=5 r=1.00 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 1.0, 1.05, 1.1, 1.15, 1.2],
            "min_opp_bars": [5],
            "rrr":          [1.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=5 r=1.00 f=0.450 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.85],
            "min_opp_bars": [5],
            "rrr":          [1.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=5 r=1.50 f=0.382 lmt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6],
            "min_opp_bars": [5],
            "rrr":          [1.5],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # H1 o=2 r=1.50 f=0.500 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.8],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=2 r=2.00 f=0.382 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.65, 0.7, 0.8, 0.85],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=2 r=2.00 f=0.450 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=2 r=2.00 f=0.500 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=3 r=1.00 f=0.500 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.8, 0.9],
            "min_opp_bars": [3],
            "rrr":          [1.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=3 r=1.50 f=0.450 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.1],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.45],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=3 r=1.50 f=0.500 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 1.15],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=3 r=2.00 f=0.450 mkt
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.6, 0.7, 0.8],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["market_fallback"],
        },
        # H1 o=4 r=1.50 f=0.618 nof
        {
            "timeframe":    ["H1"],
            "wave_min_pct": [0.85],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["no_fallback"],
        },
        # M15 o=2 r=2.00 f=0.382 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=1.50 f=0.450 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=2.00 f=0.382 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=4 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.38],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=4 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.38],
            "min_opp_bars": [4],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=4 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.26, 0.28, 0.3, 0.32, 0.34, 0.38, 0.4],
            "min_opp_bars": [4],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=5 r=1.00 f=0.618 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.26, 0.28, 0.3, 0.32, 0.34],
            "min_opp_bars": [5],
            "rrr":          [1.0],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=5 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.3],
            "min_opp_bars": [5],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=5 r=1.50 f=0.618 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28],
            "min_opp_bars": [5],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=5 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38],
            "min_opp_bars": [5],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=5 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [5],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M15 o=3 r=1.50 f=0.500 mkt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.26, 0.28],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M15 o=3 r=1.50 f=0.618 mkt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.28],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["market_fallback"],
        },
        # M15 o=3 r=2.00 f=0.382 mkt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.28, 0.3, 0.32, 0.36, 0.38],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["market_fallback"],
        },
        # M15 o=3 r=2.00 f=0.450 mkt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["market_fallback"],
        },
        # M15 o=3 r=2.00 f=0.500 mkt
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M15 o=2 r=2.00 f=0.382 nof
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.4],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["no_fallback"],
        },
        # M15 o=3 r=1.50 f=0.500 nof
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.24, 0.26, 0.28, 0.3, 0.32, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["no_fallback"],
        },
        # M15 o=3 r=2.00 f=0.450 nof
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["no_fallback"],
        },
        # M15 o=3 r=2.00 f=0.500 nof
        {
            "timeframe":    ["M15"],
            "wave_min_pct": [0.2, 0.22, 0.24, 0.26, 0.28, 0.3, 0.32, 0.34, 0.36, 0.38, 0.4],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=1.00 f=0.618 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.43, 0.46, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [1.0],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=1.50 f=0.450 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.55],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=1.50 f=0.500 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=1.50 f=0.618 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=2.00 f=0.382 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=2.00 f=0.618 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=3 r=2.00 f=0.450 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=3 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=4 r=1.50 f=0.618 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=4 r=2.00 f=0.500 lmt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.43, 0.46, 0.52, 0.55, 0.58],
            "min_opp_bars": [4],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["limit_fallback"],
        },
        # M30 o=2 r=1.50 f=0.500 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.52, 0.55],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=2 r=2.00 f=0.382 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=2 r=2.00 f=0.450 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.46, 0.52, 0.55],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=2 r=2.00 f=0.500 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.31, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=2 r=2.00 f=0.618 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.46, 0.52, 0.55],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.618],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=3 r=1.00 f=0.618 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.52, 0.55, 0.58],
            "min_opp_bars": [3],
            "rrr":          [1.0],
            "fib_level":    [0.618],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=3 r=2.00 f=0.500 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.34, 0.52, 0.55, 0.58],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=3 r=2.00 f=0.618 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.52, 0.55],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.618],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=4 r=1.50 f=0.618 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.31, 0.34, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=4 r=2.00 f=0.500 mkt
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.43, 0.46, 0.49, 0.52, 0.55],
            "min_opp_bars": [4],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["market_fallback"],
        },
        # M30 o=2 r=1.00 f=0.618 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.43, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [1.0],
            "fib_level":    [0.618],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=1.50 f=0.618 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=2.00 f=0.382 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.55],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.382],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=2.00 f=0.450 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=2.00 f=0.500 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.43, 0.46, 0.52, 0.55, 0.58],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=2 r=2.00 f=0.618 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.28, 0.31, 0.34, 0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61],
            "min_opp_bars": [2],
            "rrr":          [2.0],
            "fib_level":    [0.618],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=3 r=2.00 f=0.450 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.49, 0.55],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.45],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=3 r=2.00 f=0.500 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.37, 0.4, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58],
            "min_opp_bars": [3],
            "rrr":          [2.0],
            "fib_level":    [0.5],
            "entry_mode":   ["no_fallback"],
        },
        # M30 o=4 r=1.50 f=0.618 nof
        {
            "timeframe":    ["M30"],
            "wave_min_pct": [0.55],
            "min_opp_bars": [4],
            "rrr":          [1.5],
            "fib_level":    [0.618],
            "entry_mode":   ["no_fallback"],
        },
    ],
    "base": {
        "symbol":            "US500.cash",
        "risk_usd":          500.0,
        "contract_size":     1.0,
        "order_expiry_days": 14,
        # EXT WAVE pending (0.5 fib LIMIT z EXT vlny) — vlastni delsi expiraci.
        # NIKDY se nezavre zadnou jinou funkci (BOS, TP-wave, pending_cancel_mode).
        "ext_order_expiry_days": 7,
        # PENDING CANCEL MODE (nad ramec tp_mode) — ridi ruseni LIMIT pendingu:
        #   "number" - vsechny pendingy expiruji po `pending_cancel_after_days` dnech
        #   "trend"  - vsechny pendingy se rusi pri BOS flipu (i v RRR_FIXED)
        # POZN.: Session/weekly cancel_all_pendings (Friday close) jede NEZAVISLE
        # a vzdy zavre vsechny pendingy bez ohledu na pending_cancel_mode.
        "pending_cancel_mode": "number",
        "pending_cancel_after_days": 14,
        # Weekend-gap relax pro EXT prah (viz BotConfig docstring):
        #   0.0 = vypnuto (legacy striktni prah)
        #   0.5 = doporuceno (snizi prah o polovinu velikosti gapu)
        #   1.0 = agresivni (snizi prah o celou velikost gapu)
        # Aplikuje se jen pokud `ext_enabled=True` a vlna prekracuje vikendovy
        # gap, jehoz smer souhlasi se smerem vlny.
        "ext_weekend_gap_relax_factor": 0.5,
        "max_wave_age_hours": 8,
        "spread":            1,
        "slippage":          0.0,
        "track_concurrent_positions": True,  # True / False - zobrazení max počtu otevřených pozic
        # WAVE SESSION FILTER - default vypnuto.
        # Az najdes nejlepsi session v best_candidates, zafixuj ji tady, napr.:
        #   "wave_allowed_sessions": ["LONDON", "USA"],
        "wave_allowed_sessions": None,
        "wave_custom_window":    None,
        "wf_enabled":            True,  # Wick Fakeout Recovery
        "date_from":         "2022-04-24",
        "date_to":           "2026-04-24",
    },
}
