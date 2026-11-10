import datetime

import pytest
from telethon import utils
from telethon.crypto import AuthKey
from telethon.tl.types import (InputDocument, InputPhoto, InputPeerUser, User,
                               contacts, updates)

SESSION_ID = "session-a"
UTC = datetime.timezone.utc


def _user(user_id=1001, access_hash=2002, username="alice", phone="5511999990000",
          first_name="Alice"):
    return User(id=user_id, access_hash=access_hash, username=username, phone=phone,
                first_name=first_name)


def _state(pts=10, qts=20, ts=1_700_000_000, seq=30):
    return updates.State(pts=pts, qts=qts, date=datetime.datetime.fromtimestamp(ts, tz=UTC),
                         seq=seq, unread_count=0)


def test_dc_and_auth_key_survive_reload(make_container):
    container = make_container()
    session = container.new_session(SESSION_ID)
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(data=b"k" * 256)
    session.save()

    assert container.has_session(SESSION_ID)
    reloaded = make_container().new_session(SESSION_ID)
    assert (reloaded.dc_id, reloaded.server_address, reloaded.port) == (2, "149.154.167.51", 443)
    assert reloaded.auth_key.key == b"k" * 256


def test_has_session_false_without_auth_key(make_container):
    container = make_container()
    assert not container.has_session(SESSION_ID)
    session = container.new_session(SESSION_ID)
    session.set_dc(2, "149.154.167.51", 443)
    session.save()
    assert not container.has_session(SESSION_ID)


def test_update_state_roundtrip_and_overwrite(make_container, local_tz):
    session = make_container().new_session(SESSION_ID)
    # Telethon writes the general state (entity 0) on every disconnect, so overwrites must work.
    session.set_update_state(0, _state(pts=1))
    session.set_update_state(0, _state(pts=2))
    session.save()

    state = make_container().new_session(SESSION_ID).get_update_state(0)
    assert (state.pts, state.qts, state.seq) == (2, 20, 30)
    assert state.date.timestamp() == 1_700_000_000


def test_update_state_date_is_utc_aware(make_container, local_tz):
    # Telethon feeds get_update_states() dates into int(date.timestamp()); a naive UTC
    # datetime is read as local time there and shifts the catch-up date by the UTC offset.
    session = make_container().new_session(SESSION_ID)
    session.set_update_state(0, _state(ts=1_700_000_000))
    session.save()

    state = make_container().new_session(SESSION_ID).get_update_state(0)
    assert state.date.tzinfo is not None
    assert int(state.date.timestamp()) == 1_700_000_000


def test_update_states_are_loaded_after_restart(make_container, local_tz):
    # Telethon's connect() reads persisted states via get_update_states() to catch up.
    session = make_container().new_session(SESSION_ID)
    session.set_update_state(0, _state(pts=5))
    session.set_update_state(777, _state(pts=9))
    session.save()

    states = dict(make_container().new_session(SESSION_ID).get_update_states())
    assert set(states) == {0, 777}
    assert states[0].pts == 5 and states[777].pts == 9
    assert int(states[0].date.timestamp()) == 1_700_000_000


def test_update_states_are_scoped_to_session(make_container):
    container = make_container()
    a = container.new_session("a")
    b = container.new_session("b")
    a.set_update_state(0, _state(pts=1))
    b.set_update_state(0, _state(pts=2))
    a.save()
    assert [s.pts for _, s in container.new_session("a").get_update_states()] == [1]


def test_entities_lookup(make_container):
    session = make_container().new_session(SESSION_ID)
    user = _user()
    session.process_entities(contacts.ResolvedPeer(None, [], [user]))
    session.save()

    session = make_container().new_session(SESSION_ID)
    expected = (utils.get_peer_id(user), 2002)
    assert tuple(session.get_entity_rows_by_username("alice")) == expected
    assert tuple(session.get_entity_rows_by_name("Alice")) == expected
    assert tuple(session.get_entity_rows_by_id(utils.get_peer_id(user))) == expected
    assert tuple(session.get_entity_rows_by_id(1001, exact=False)) == expected


def test_entities_lookup_by_phone(make_container):
    # Telethon resolves "+55 11 99999-0000" style keys via parse_phone -> str digits.
    session = make_container().new_session(SESSION_ID)
    session.process_entities(contacts.ResolvedPeer(None, [], [_user()]))
    session.save()

    session = make_container().new_session(SESSION_ID)
    assert tuple(session.get_entity_rows_by_phone("5511999990000")) == (1001, 2002)
    peer = session.get_input_entity("+55 11 99999-0000")
    assert (peer.user_id, peer.access_hash) == (1001, 2002)


def test_entities_upsert(make_container):
    session = make_container().new_session(SESSION_ID)
    session.process_entities(contacts.ResolvedPeer(None, [], [_user(username="old")]))
    session.process_entities(contacts.ResolvedPeer(None, [], [_user(username="new")]))
    session.save()

    session = make_container().new_session(SESSION_ID)
    assert session.get_entity_rows_by_username("old") is None
    assert tuple(session.get_entity_rows_by_username("new")) == (1001, 2002)


def test_self_entity_hack(make_container):
    # Telethon stores itself as id=0 with access_hash=<own id> on disconnect.
    session = make_container().new_session(SESSION_ID)
    session.process_entities(contacts.ResolvedPeer(None, [InputPeerUser(0, 4242)], []))
    session.save()
    assert make_container().new_session(SESSION_ID).get_input_entity(0).access_hash == 4242


@pytest.mark.parametrize("instance", [InputDocument(id=11, access_hash=12, file_reference=b""),
                                      InputPhoto(id=21, access_hash=22, file_reference=b"")],
                         ids=["document", "photo"])
def test_sent_file_cache(make_container, instance):
    session = make_container().new_session(SESSION_ID)
    digest = b"\x01" * 16
    session.cache_file(digest, 1234, instance)
    session.cache_file(digest, 1234, instance)
    session.save()

    session = make_container().new_session(SESSION_ID)
    assert tuple(session.get_file(digest, 1234, type(instance))) == (instance.id,
                                                                     instance.access_hash)
    assert session.get_file(digest, 999, type(instance)) is None


def test_cache_file_rejects_other_types(make_container):
    session = make_container().new_session(SESSION_ID)
    with pytest.raises(TypeError):
        session.cache_file(b"x", 1, object())


def test_delete_removes_only_own_rows(make_container):
    container = make_container()
    for sid in ("mine", "other"):
        s = container.new_session(sid)
        s.set_dc(2, "149.154.167.51", 443)
        s.auth_key = AuthKey(data=b"k" * 256)
        s.set_update_state(0, _state())
        s.process_entities(contacts.ResolvedPeer(None, [], [_user()]))
        s.save()

    container.new_session("mine").delete()
    container.save()

    assert not container.has_session("mine")
    assert container.has_session("other")
    assert container.new_session("mine").get_update_state(0) is None
    assert container.new_session("other").get_update_state(0) is not None


def test_has_session_matches_across_modes_for_empty_auth_key(make_container):
    # A session row with an empty auth key is what set_dc() leaves before login and what
    # _update_session_table() writes after log_out(); neither is a usable session.
    container = make_container()
    session = container.new_session(SESSION_ID)
    session.set_dc(2, "149.154.167.51", 443)
    session.auth_key = AuthKey(data=b"k" * 256)
    session.save()
    assert container.has_session(SESSION_ID)
    session.auth_key = None
    session.save()
    assert not container.has_session(SESSION_ID)
