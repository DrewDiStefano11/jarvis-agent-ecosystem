from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker


class UnitOfWork:
    """One explicit commit boundary for a durable command."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self.session_factory()
        self.session.begin()
        return self

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                try:
                    self.commit()
                except Exception:
                    self.session.rollback()
                    raise
            else:
                self.session.rollback()
        finally:
            self.session.close()
