from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from math import ceil
from uuid import uuid4

from sqlalchemy import case, func, select, text

from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    AuditEventRow,
    IdentityAgentRow,
    OfficeCommandRow,
    OfficePlacementRow,
    OutboxEventRow,
    SystemStateRow,
)
from app.models.agent_runtime import AgentRunState
from app.models.domain import EventEnvelope
from app.models.office import (
    OfficeCatalog,
    OfficeCommand,
    OfficeCommandResult,
    OfficeMotion,
    OfficePlacement,
    OfficePoint,
    OfficeSnapshot,
)
from app.office.geometry import (
    FOOTPRINT_DIAMETER,
    SPEED_PIXELS_PER_SECOND,
    movement_progress,
    path_length,
    point_segment_distance,
    split_motion,
    utc,
)
from app.services.unit_of_work import UnitOfWork


class OfficeRepository:
    """Serialized local spatial commands with audit/outbox in the same transaction.

    These are trusted-loopback operator presentation commands. They do not grant
    identity permissions, control runtime execution, or expose runtime results.
    """

    def __init__(self, sessions, catalog: OfficeCatalog, now=None):
        self.sessions = sessions
        self.catalog = catalog
        self.now = now or (lambda: datetime.now(UTC))
        self.stations = {station.id: station for station in catalog.stations}
        self.routes = {(route.originId, route.destinationId): route for route in catalog.routes}

    @contextmanager
    def _write(self):
        with UnitOfWork(self.sessions) as unit:
            session = unit.session
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            # The shared sequence row also serializes cross-process reservations.
            system = session.scalar(
                select(SystemStateRow).where(SystemStateRow.id == 1).with_for_update()
            )
            if system is None:
                raise RuntimeError("System state is unavailable.")
            yield session, system

    @staticmethod
    def _motion(row):
        return OfficeMotion.model_validate(row.motion_json) if row.motion_json else None

    @staticmethod
    def _emergency_time(session, system):
        if not system.emergency_stop:
            return None
        # Other domain events can update system.updated_at after the stop. Use
        # its durable outbox timestamp, including after a crash before settling.
        instant = session.scalar(
            select(OutboxEventRow.created_at)
            .where(OutboxEventRow.event_type == "system.emergency_stop")
            .order_by(OutboxEventRow.created_at.desc())
            .limit(1)
        )
        return utc(instant or system.updated_at)

    def _effective_motion(self, row, identity, system, now, *, stop_all=False, emergency_at=None):
        """Freeze projections even between a lifecycle update and reconciliation."""
        motion = self._motion(row)
        if not motion or motion.stoppedAt:
            return motion
        inactive = not identity or not identity.is_enabled or identity.lifecycle_state != "active"
        if stop_all or system.emergency_stop or inactive:
            stop_time = now
            if inactive and identity:
                stop_time = min(stop_time, utc(identity.updated_at))
            if system.emergency_stop:
                stop_time = min(stop_time, emergency_at or utc(system.updated_at))
            motion.stoppedAt = max(utc(motion.startedAt), stop_time)
        return motion

    @staticmethod
    def _event(session, system, identity_id, action, now, payload):
        system.current_sequence_number += 1
        event_id = f"office-{uuid4().hex}"
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=f"office.{action}",
            timestamp=now,
            sequenceNumber=system.current_sequence_number,
            eventSessionId=system.event_session_id,
            correlationId=identity_id,
            source="office",
            payload={"identityId": identity_id, **payload},
        ).model_dump(mode="json")
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type=envelope["eventType"],
                envelope=envelope,
                correlation_id=identity_id,
                event_session_id=system.event_session_id,
                sequence_number=system.current_sequence_number,
                status="pending",
                created_at=now,
                publish_attempt_count=0,
            )
        )
        session.add(
            AuditEventRow(
                id=f"audit-{uuid4().hex}",
                event_type=envelope["eventType"],
                actor="local-operator",
                agent_id=None,
                task_id=None,
                approval_id=None,
                previous_state=None,
                new_state=action,
                correlation_id=identity_id,
                sequence_number=system.current_sequence_number,
                event_session_id=system.event_session_id,
                timestamp=now,
                payload={
                    "summary": f"Office {action}",
                    "payload": envelope["payload"],
                    "artifactIds": [],
                },
                schema_version="1.0",
            )
        )

    def _settle(self, session, system, rows, now, stop_all=False):
        changed = False
        emergency_at = self._emergency_time(session, system)
        for row in rows:
            original = self._motion(row)
            if not original or original.stoppedAt:
                continue
            identity = session.get(IdentityAgentRow, row.identity_id)
            motion = self._effective_motion(
                row, identity, system, now, stop_all=stop_all, emergency_at=emergency_at
            )
            if movement_progress(motion, now) >= 1:
                row.station_id = motion.destinationId
                row.position_json = self.stations[motion.destinationId].point.model_dump()
                row.motion_json = None
                action = "arrived"
            elif motion.stoppedAt:
                row.position_json = split_motion(motion, now)[0].model_dump()
                row.motion_json = motion.model_dump(mode="json")
                action = "stopped"
            else:
                continue
            row.version += 1
            row.updated_at = now
            self._event(session, system, row.identity_id, action, now, {"version": row.version})
            changed = True
        return changed

    def reconcile(self, *, stop_all=False):
        # Idle offices should not acquire a write lock twice a second. Commands
        # still re-read all reservations under the serialized write transaction.
        with self.sessions() as session:
            motions = session.scalars(
                select(OfficePlacementRow.motion_json).where(
                    OfficePlacementRow.station_id.is_not(None)
                )
            )
            if not any(motion and not motion.get("stoppedAt") for motion in motions):
                return False
        with self._write() as (session, system):
            rows = list(session.scalars(select(OfficePlacementRow)))
            return self._settle(session, system, rows, self.now(), stop_all)

    def snapshot(self):
        now = self.now()
        with self.sessions() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN"))
            system = session.get(SystemStateRow, 1)
            emergency_at = self._emergency_time(session, system)
            rows = list(
                session.scalars(select(OfficePlacementRow).order_by(OfficePlacementRow.identity_id))
            )
            assigned_ids = [row.identity_id for row in rows if row.station_id]
            identities = {
                row.id: row
                for row in session.scalars(
                    select(IdentityAgentRow).where(IdentityAgentRow.id.in_(assigned_ids))
                )
            }
            activities = self._activities(session, assigned_ids)
            placements = []
            for row in rows:
                if row.station_id is None:
                    continue
                identity = identities[row.identity_id]
                motion = self._effective_motion(
                    row, identity, system, now, emergency_at=emergency_at
                )
                position = (
                    split_motion(motion, now)[0]
                    if motion
                    else OfficePoint.model_validate(row.position_json)
                )
                moving = bool(
                    motion and not motion.stoppedAt and movement_progress(motion, now) < 1
                )
                station_id = (
                    motion.destinationId
                    if motion and movement_progress(motion, now) >= 1
                    else row.station_id
                )
                placements.append(
                    OfficePlacement(
                        identityId=row.identity_id,
                        displayName=identity.display_name,
                        lifecycleState=identity.lifecycle_state,
                        enabled=identity.is_enabled,
                        stationId=station_id,
                        spriteId=row.sprite_id,
                        position=position,
                        motion=motion,
                        movementState="moving"
                        if moving
                        else "stopped"
                        if motion and motion.stoppedAt
                        else "idle",
                        activity=activities.get(row.identity_id, "idle"),
                        version=row.version,
                        updatedAt=row.updated_at,
                    )
                )
            return OfficeSnapshot(
                serverTime=now,
                catalog=self.catalog,
                placements=placements,
                placementVersions={row.identity_id: row.version for row in rows},
                emergencyStop=system.emergency_stop,
            )

    @staticmethod
    def _activities(session, identity_ids):
        if not identity_ids:
            return {}
        # Project only identity/state columns. Private task specifications and
        # results are never loaded; rank in SQL so history cannot grow a snapshot.
        working = {
            AgentRunState.CLAIMED,
            AgentRunState.STARTING,
            AgentRunState.RUNNING,
            AgentRunState.PAUSE_REQUESTED,
            AgentRunState.CANCEL_REQUESTED,
            AgentRunState.CANCELLING,
        }
        waiting = {AgentRunState.PAUSED, AgentRunState.BLOCKED}
        priority = case(
            (AgentRuntimeRunRow.state.in_(working), 4),
            (AgentRuntimeRunRow.state.in_(waiting), 3),
            (AgentRuntimeRunRow.state == AgentRunState.QUEUED, 2),
            else_=1,
        )
        ranked = (
            select(
                AgentRuntimeRunRow.agent_id,
                AgentRuntimeRunRow.state,
                func.row_number()
                .over(
                    partition_by=AgentRuntimeRunRow.agent_id,
                    order_by=(
                        priority.desc(),
                        AgentRuntimeRunRow.updated_at.desc(),
                        AgentRuntimeRunRow.run_id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(AgentRuntimeRunRow.agent_id.in_(identity_ids))
            .subquery()
        )
        labels = {
            **dict.fromkeys(working, "working"),
            **dict.fromkeys(waiting, "waiting"),
            AgentRunState.QUEUED: "queued",
            AgentRunState.SUCCEEDED: "completed",
            AgentRunState.FAILED: "failed",
            AgentRunState.TIMED_OUT: "failed",
            AgentRunState.ABANDONED: "failed",
        }
        return {
            agent_id: labels.get(state, "idle")
            for agent_id, state in session.execute(
                select(ranked.c.agent_id, ranked.c.state).where(ranked.c.rank == 1)
            )
        }

    def command(self, identity_id: str, command: OfficeCommand):
        digest = sha256(
            json.dumps(
                {"identityId": identity_id, **command.model_dump(mode="json")}, sort_keys=True
            ).encode()
        ).hexdigest()
        with self._write() as (session, system):
            now = self.now()
            previous = session.get(OfficeCommandRow, command.commandId)
            if previous:
                if previous.request_hash != digest:
                    raise DomainError(
                        "OFFICE_COMMAND_CONFLICT",
                        "This command ID was used for different office work.",
                        409,
                    )
                return OfficeCommandResult.model_validate(previous.response_json)
            identity = session.get(IdentityAgentRow, identity_id)
            if identity is None:
                raise DomainError("AGENT_NOT_FOUND", "The identity was not found.", 404)
            rows = list(session.scalars(select(OfficePlacementRow)))
            self._settle(session, system, rows, now)
            row = next((item for item in rows if item.identity_id == identity_id), None)
            if command.expectedVersion != (row.version if row else 0):
                raise DomainError(
                    "OFFICE_VERSION_CONFLICT",
                    "Office state changed. Refresh before issuing this command again.",
                    409,
                )
            if command.action in {"assign", "move"}:
                if system.emergency_stop:
                    raise DomainError(
                        "EMERGENCY_STOP_ACTIVE", "Resume the system before office movement.", 423
                    )
                if not identity.is_enabled or identity.lifecycle_state != "active":
                    raise DomainError(
                        "AGENT_INACTIVE", "Only active identities can be placed or moved.", 409
                    )
                if command.stationId not in self.stations:
                    raise DomainError(
                        "OFFICE_STATION_UNAVAILABLE",
                        "This station is outside the verified office network.",
                        422,
                    )
                for other in rows:
                    if other.station_id is None:
                        continue
                    motion = self._motion(other)
                    if other.identity_id != identity_id and command.stationId in {
                        other.station_id,
                        motion.destinationId if motion else None,
                    }:
                        raise DomainError(
                            "OFFICE_STATION_OCCUPIED",
                            "Another identity occupies or reserves this station.",
                            409,
                        )
                    if motion and not motion.stoppedAt:
                        raise DomainError(
                            "OFFICE_AISLE_BUSY",
                            "An office move is in progress. Wait for arrival or stop it first.",
                            409,
                        )
            if command.action == "assign":
                if row is not None and row.station_id is not None:
                    raise DomainError(
                        "OFFICE_ALREADY_PLACED",
                        "Use movement to change stations, or release the placement first.",
                        409,
                    )
                if command.spriteId not in self.catalog.spriteIds:
                    raise DomainError(
                        "OFFICE_SPRITE_UNAVAILABLE", "Select an original supported sprite.", 422
                    )
                station = self.stations[command.stationId]
                for other in rows:
                    if other.station_id is None:
                        continue
                    point = OfficePoint.model_validate(other.position_json)
                    if (
                        point_segment_distance(point, station.point, station.point)
                        < FOOTPRINT_DIAMETER
                    ):
                        raise DomainError(
                            "OFFICE_SPACE_OCCUPIED",
                            "This station overlaps another identity's space.",
                            409,
                        )
                if row is None:
                    row = OfficePlacementRow(identity_id=identity_id, version=0)
                    session.add(row)
                row.station_id = station.id
                row.sprite_id = command.spriteId
                row.position_json = station.point.model_dump()
                row.motion_json = None
                row.version += 1
                row.updated_at = now
            elif row is None or row.station_id is None:
                raise DomainError(
                    "OFFICE_NOT_PLACED", "Assign this identity to a station first.", 409
                )
            elif command.action == "move":
                motion = self._motion(row)
                if motion and motion.stoppedAt:
                    _, _, forwards = split_motion(motion, now)
                    if command.stationId == motion.destinationId:
                        points = forwards
                    else:
                        raise DomainError(
                            "OFFICE_STOPPED_ROUTE",
                            "Continue to the original destination, or release this placement before assigning a different station.",
                            409,
                        )
                    door_ids = motion.doorIds
                else:
                    route = self.routes.get((row.station_id, command.stationId))
                    if route is None:
                        raise DomainError(
                            "OFFICE_ROUTE_UNAVAILABLE",
                            "No verified collision-free route connects these stations.",
                            409,
                        )
                    points, door_ids = route.points, route.doorIds
                if len(points) < 2 or path_length(points) < 0.01:
                    raise DomainError(
                        "OFFICE_ALREADY_AT_DESTINATION",
                        "The identity is already at that station.",
                        409,
                    )
                for other in rows:
                    if other.identity_id == identity_id or other.station_id is None:
                        continue
                    obstacle = OfficePoint.model_validate(other.position_json)
                    if any(
                        point_segment_distance(obstacle, a, b) < FOOTPRINT_DIAMETER
                        for a, b in zip(points, points[1:], strict=False)
                    ):
                        raise DomainError(
                            "OFFICE_ROUTE_OCCUPIED",
                            "A stationary identity blocks this route. Move or release it first.",
                            409,
                        )
                row.motion_json = OfficeMotion(
                    originId=row.station_id,
                    destinationId=command.stationId,
                    points=points,
                    doorIds=door_ids,
                    startedAt=now,
                    durationMs=max(1, ceil(path_length(points) / SPEED_PIXELS_PER_SECOND * 1000)),
                ).model_dump(mode="json")
                row.version += 1
                row.updated_at = now
            elif command.action == "stop":
                motion = self._motion(row)
                if motion and not motion.stoppedAt:
                    motion.stoppedAt = now
                    row.position_json = split_motion(motion, now)[0].model_dump()
                    row.motion_json = motion.model_dump(mode="json")
                row.version += 1
                row.updated_at = now
            elif command.action == "release":
                row.version += 1
                row.station_id = None
                row.motion_json = None
                row.updated_at = now
            result = OfficeCommandResult(
                commandId=command.commandId,
                identityId=identity_id,
                version=row.version,
                action=command.action,
            )
            session.add(
                OfficeCommandRow(
                    command_id=command.commandId,
                    identity_id=identity_id,
                    request_hash=digest,
                    response_json=result.model_dump(mode="json"),
                    created_at=now,
                )
            )
            self._event(
                session,
                system,
                identity_id,
                command.action,
                now,
                {"version": row.version, "stationId": command.stationId},
            )
            return result
