"""Goal persistence shares the native task/event transaction, never its own engine."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.errors import DomainError
from app.db.base import Base
from app.models.goals import Goal, GoalMessage


class ConversationRow(Base):
    __tablename__ = "workspace_conversations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MessageRow(Base):
    __tablename__ = "workspace_messages"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_conversations.id"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GoalRow(Base):
    __tablename__ = "workspace_goals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_conversations.id"), unique=True
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def persist_goal(session, goal: Goal) -> None:
    session.add(
        ConversationRow(id=goal.conversationId, title=goal.title, created_at=goal.createdAt)
    )
    session.flush()
    session.add(
        GoalRow(
            id=goal.id,
            conversation_id=goal.conversationId,
            task_id=goal.taskId,
            created_at=goal.createdAt,
        )
    )
    for message in goal.messages:
        session.add(
            MessageRow(
                id=message.id,
                conversation_id=goal.conversationId,
                content=message.content,
                created_at=message.createdAt,
            )
        )
    session.flush()


class GoalRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def project(session, row):
        conversation = session.get(ConversationRow, row.conversation_id)
        messages = session.scalars(
            select(MessageRow)
            .where(MessageRow.conversation_id == row.conversation_id)
            .order_by(MessageRow.created_at, MessageRow.id)
        ).all()
        return Goal(
            id=row.id,
            conversationId=row.conversation_id,
            taskId=row.task_id,
            title=conversation.title,
            createdAt=row.created_at.replace(tzinfo=UTC),
            messages=[
                GoalMessage(
                    id=message.id,
                    content=message.content,
                    createdAt=message.created_at.replace(tzinfo=UTC),
                )
                for message in messages
            ],
        )

    def get(self, goal_id):
        with self.sessions() as session:
            row = session.get(GoalRow, goal_id)
            if row is None:
                raise DomainError("GOAL_NOT_FOUND", "The saved goal was not found.", 404)
            return self.project(session, row)

    def list(self, *, offset=0, limit=30):
        with self.sessions() as session:
            rows = session.scalars(
                select(GoalRow)
                .order_by(GoalRow.created_at.desc(), GoalRow.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return [self.project(session, row) for row in rows]
