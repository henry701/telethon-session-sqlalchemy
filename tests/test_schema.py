import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.orm import declarative_base

from alchemysession import AlchemySessionContainer


def _md5_digest_ddl(dialect):
    sent_file = AlchemySessionContainer.create_table_classes(None, "", declarative_base())[3]
    return sent_file.__table__.c.md5_digest.type.compile(dialect=dialect)


@pytest.mark.parametrize("dialect, expected", [
    # Existing SQLite/PostgreSQL databases must keep the exact column type they were created with.
    (sqlite.dialect(), "BLOB"),
    (postgresql.dialect(), "BYTEA"),
    # MySQL can't put a BLOB in a primary key without a prefix length (error 1170).
    (mysql.dialect(), "VARBINARY(255)"),
], ids=["sqlite", "postgresql", "mysql"])
def test_md5_digest_column_type(dialect, expected):
    assert _md5_digest_ddl(dialect) == expected
