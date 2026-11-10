"""Exercises the session through Telethon's own client code paths, without network."""
import asyncio
import datetime
from unittest import mock

import pytest
from sqlalchemy import insert
from telethon import TelegramClient
from telethon._updates import ChannelState, SessionState
from telethon.sessions import SQLiteSession
from telethon.tl.types import InputPeerChannel, InputPeerUser, contacts, updates

from test_session import SESSION_ID, UTC

MAIN_TS = 1_700_000_000
SELF_ID, SELF_HASH = 4242, 99  # SELF_HASH is our own access hash
CHANNEL_ID, CHANNEL_HASH, CHANNEL_PTS = 555, 777, 42


class _StopAfterCatchUpLoad(Exception):
    pass


def _client(session, catch_up):
    return TelegramClient(session, api_id=1, api_hash="0" * 32, catch_up=catch_up,
                          receive_updates=True)


async def _save_like_disconnect(session):
    # _save_states_and_entities() is exactly what TelegramClient._disconnect_coro() persists.
    client = _client(session, catch_up=False)
    client._message_box.load(SessionState(0, 0, False, pts=100, qts=7, date=MAIN_TS, seq=3,
                                          takeout_id=None),
                             [ChannelState(CHANNEL_ID, CHANNEL_PTS)])
    client._mb_entity_cache.set_self_user(SELF_ID, False, SELF_HASH)
    await client._save_states_and_entities()
    return client._message_box.session_state()


async def _connect_until_catch_up_loaded(session):
    # Runs the real connect() up to the first network request, which comes right after the
    # catch-up block that reads get_update_states() into the MessageBox.
    client = _client(session, catch_up=True)
    with mock.patch.object(client._sender, "connect", mock.AsyncMock(return_value=True)), \
            mock.patch.object(client._sender, "send",
                              mock.AsyncMock(side_effect=_StopAfterCatchUpLoad)):
        with pytest.raises(_StopAfterCatchUpLoad):
            await client.connect()
    return client


def test_client_restart_restores_message_box(make_container, local_tz):
    session = make_container().new_session(SESSION_ID)
    # Normally cached while running (get_me(), dialogs); connect() needs them to rebuild state.
    session.process_entities(contacts.ResolvedPeer(
        None, [InputPeerUser(SELF_ID, SELF_HASH), InputPeerChannel(CHANNEL_ID, CHANNEL_HASH)],
        []))
    saved = asyncio.run(_save_like_disconnect(session))

    restored = asyncio.run(_connect_until_catch_up_loaded(
        make_container().new_session(SESSION_ID)))

    assert restored._message_box.session_state() == saved
    assert saved[0]["date"] == datetime.datetime.fromtimestamp(MAIN_TS, tz=UTC)
    assert saved[1] == {CHANNEL_ID: CHANNEL_PTS}
    assert restored._mb_entity_cache.self_id == SELF_ID
    assert restored._mb_entity_cache.get(CHANNEL_ID).hash == CHANNEL_HASH


def test_client_restart_without_saved_state_starts_empty(make_container):
    restored = asyncio.run(_connect_until_catch_up_loaded(
        make_container().new_session(SESSION_ID)))
    assert restored._message_box.is_empty()


def _states_as_telethon_sees_them(states):
    # connect() only uses pts/qts/seq/int(date.timestamp()) of entity 0 and pts of channels.
    return {entity_id: (s.pts, s.qts, s.seq, int(s.date.timestamp()) if entity_id == 0 else None)
            for entity_id, s in states}


def test_get_update_states_matches_telethon_sqlite_session(make_container, tmp_path, local_tz):
    reference = SQLiteSession(str(tmp_path / "reference"))
    ours = make_container().new_session(SESSION_ID)
    other = make_container().new_session("someone-else")
    writes = [
        (0, updates.State(100, 7, datetime.datetime.fromtimestamp(MAIN_TS, tz=UTC), 3, 0)),
        # Channels get a naive now() from Telethon; only their pts is ever read back.
        (CHANNEL_ID, updates.State(CHANNEL_PTS, 0, datetime.datetime.now(), 0, 0)),
        (CHANNEL_ID + 1, updates.State(1, 0, datetime.datetime.now(), 0, 0)),
        (0, updates.State(101, 8, datetime.datetime.fromtimestamp(MAIN_TS + 5, tz=UTC), 4, 0)),
    ]
    for entity_id, state in writes:
        reference.set_update_state(entity_id, state)
        ours.set_update_state(entity_id, state)
    other.set_update_state(0, updates.State(1, 1, datetime.datetime.now(tz=UTC), 1, 0))
    reference.save()
    ours.save()

    expected = list(reference.get_update_states())
    got = list(make_container().new_session(SESSION_ID).get_update_states())
    reference.close()

    assert _states_as_telethon_sees_them(got) == _states_as_telethon_sees_them(expected)
    assert len(got) == len(expected)
    assert all(s.date.tzinfo is not None for _, s in got)
    assert all(abs(g.date.timestamp() - e.date.timestamp()) < 1
               for (_, g), (_, e) in zip(sorted(got, key=lambda x: x[0]),
                                         sorted(expected, key=lambda x: x[0])))


def test_naive_local_now_keeps_its_epoch(make_container, local_tz):
    # TelegramClient._save_states_and_entities() stores channel states with a naive
    # datetime.datetime.now(); reading it back must not shift it by the UTC offset.
    now = datetime.datetime.now()
    session = make_container().new_session(SESSION_ID)
    session.set_update_state(CHANNEL_ID, updates.State(CHANNEL_PTS, 0, now, 0, unread_count=0))
    session.save()

    (entity_id, state), = make_container().new_session(SESSION_ID).get_update_states()
    assert (entity_id, state.pts) == (CHANNEL_ID, CHANNEL_PTS)
    # Integer columns round sub-second precision on server backends.
    assert abs(state.date.timestamp() - now.timestamp()) < 1


def test_rows_written_with_integer_dates_are_readable(make_container, local_tz):
    # Rows persisted before this fork's fixes hold epoch seconds as integers; they must still load.
    container = make_container()
    t = container.UpdateState.__table__
    with container.db_engine.begin() as conn:
        conn.execute(insert(t), [
            dict(session_id=SESSION_ID, entity_id=0, pts=5, qts=6, date=MAIN_TS, seq=7,
                 unread_count=0),
            dict(session_id=SESSION_ID, entity_id=CHANNEL_ID, pts=9, qts=0, date=MAIN_TS,
                 seq=0, unread_count=None),
        ])

    states = dict(make_container().new_session(SESSION_ID).get_update_states())
    assert (states[0].pts, states[0].qts, states[0].seq) == (5, 6, 7)
    assert int(states[0].date.timestamp()) == MAIN_TS
    assert states[CHANNEL_ID].pts == 9
