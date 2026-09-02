from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def create_database(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine_options = {}
    if database_url.startswith("mysql"):
        # READ COMMITTED avoids InnoDB gap-lock contention while workers use
        # FOR UPDATE SKIP LOCKED to claim independent batches.
        engine_options["isolation_level"] = "READ COMMITTED"
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=1800,
        **engine_options,
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)

