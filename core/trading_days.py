from __future__ import annotations

from datetime import datetime, timedelta


def business_time_delta(start: datetime, end: datetime) -> timedelta:
    """
    Vrati uplynuly cas mezi start/end bez vikendu (So+Ne).
    Pokud end <= start, vraci 0.
    """
    if end <= start:
        return timedelta(0)

    total = timedelta(0)
    cur = start
    one_day = timedelta(days=1)

    while cur < end:
        next_midnight = (cur.replace(hour=0, minute=0, second=0, microsecond=0) + one_day)
        segment_end = min(next_midnight, end)
        # weekday: 0=Po ... 6=Ne
        if cur.weekday() < 5:
            total += segment_end - cur
        cur = segment_end

    return total


def is_older_than_business_days(start: datetime, end: datetime, days: int) -> bool:
    """
    True pokud uplynulo vice nez `days` obchodnich dni (Po-Pa),
    pricemz vikendy se nezapocitavaji.
    """
    if days <= 0:
        return True
    return business_time_delta(start, end) > timedelta(days=days)
