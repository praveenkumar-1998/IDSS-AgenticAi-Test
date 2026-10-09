import os
from pathlib import Path
from typing import Generator
from sqlalchemy import create_engine, Engine, event
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase


class Base(DeclarativeBase):
    pass


_engines: dict[str, Engine] = {}
_session_factory: sessionmaker | None = None
SessionLocal: sessionmaker | None = None


def get_data_dir() -> Path:
    if custom_dir := os.getenv("DB_DIR"):
        path = Path(custom_dir).resolve()
    else:
        root_dir = Path(__file__).resolve().parents[2]
        path = root_dir / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_engine(service_name: str) -> Engine:
    if service_name not in _engines:
        data_dir = get_data_dir()
        db_file = data_dir / f"{service_name}.db"
        url = f"sqlite:///{db_file.as_posix()}"
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        _engines[service_name] = engine
    return _engines[service_name]


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(engine: Engine) -> None:
    global _session_factory, SessionLocal
    Base.metadata.create_all(bind=engine)
    _session_factory = make_session_factory(engine)
    SessionLocal = _session_factory


def get_db() -> Generator[Session, None, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialised – call init_db() first.")
    db: Session = _session_factory()
    try:
        yield db
    finally:
        db.close()
