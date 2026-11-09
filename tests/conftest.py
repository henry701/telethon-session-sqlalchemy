import os
import uuid

import pytest
import sqlalchemy as sql

from alchemysession import AlchemySessionContainer
from alchemysession.core import AlchemyCoreSession

# SQLite always runs; server backends run when their URL is provided (CI service containers).
BACKEND_URL_ENV = {
    "postgresql": "TEST_POSTGRES_URL",
}

MODES = ("orm", "core", "core-generic")


def _backend_params():
    params = [pytest.param("sqlite", id="sqlite")]
    for name, env in BACKEND_URL_ENV.items():
        params.append(pytest.param(name, id=name, marks=pytest.mark.skipif(
            not os.environ.get(env), reason="{} not set".format(env))))
    return params


@pytest.fixture(params=_backend_params())
def engine(request, tmp_path):
    if request.param == "sqlite":
        url = "sqlite:///{}".format(tmp_path / "session.sqlite3")
    else:
        url = os.environ[BACKEND_URL_ENV[request.param]]
    eng = sql.create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture(params=MODES)
def mode(request):
    return request.param


@pytest.fixture
def make_container(engine, mode):
    prefix = "t{}_".format(uuid.uuid4().hex[:8])
    created = []

    def factory():
        container = AlchemySessionContainer(engine=engine, table_prefix=prefix)
        if mode == "core":
            container.core_mode = True
        elif mode == "core-generic":
            container.alchemy_session_class = AlchemyCoreSession
        created.append(container)
        return container

    yield factory

    for container in created:
        container.db.remove()
    if created:
        created[0].Version.metadata.drop_all(engine)
