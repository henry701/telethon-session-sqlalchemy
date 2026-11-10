import datetime
from typing import Optional, Union

from telethon.tl.types import updates


def to_state(pts: int, qts: int, date: float, seq: int, unread_count: int) -> updates.State:
    # Telethon calls int(state.date.timestamp()); a naive datetime would be read as local time.
    return updates.State(pts, qts, datetime.datetime.fromtimestamp(date, tz=datetime.timezone.utc),
                         seq, unread_count)


def phone_key(phone: Union[str, int]) -> Optional[int]:
    # The phone column is BigInteger, but Telethon passes digit strings; PostgreSQL drivers
    # that bind typed parameters (psycopg 3) reject bigint = varchar.
    try:
        return int(phone)
    except (TypeError, ValueError):
        return None
