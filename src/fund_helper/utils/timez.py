from __future__ import annotations

from datetime import datetime, timezone

CST_OFFSET_HOURS = 8


def now_cst() -> datetime:
    from datetime import timedelta
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=CST_OFFSET_HOURS)))
