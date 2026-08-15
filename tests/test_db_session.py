from app.db.session import build_connect_args


def test_sqlite_url_gets_no_connect_args():
    assert build_connect_args("sqlite+aiosqlite:///./gateway.db", 5.0, 10.0) == {}


def test_postgres_url_gets_asyncpg_timeout_kwargs():
    args = build_connect_args(
        "postgresql+asyncpg://gateway:changeme@localhost:5432/gateway", 5.0, 10.0
    )
    assert args == {"timeout": 5.0, "command_timeout": 10.0}
