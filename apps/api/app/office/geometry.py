"""Pure interpolation of a server-selected, reviewed route."""

from datetime import UTC, datetime
from math import hypot

from app.models.office import OfficeMotion, OfficePoint

SPEED_PIXELS_PER_SECOND = 180
FOOTPRINT_DIAMETER = 68


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def distance(a: OfficePoint, b: OfficePoint) -> float:
    return hypot(a.x - b.x, a.y - b.y)


def path_length(points: list[OfficePoint]) -> float:
    return sum(distance(a, b) for a, b in zip(points, points[1:], strict=False))


def movement_progress(motion: OfficeMotion, now: datetime) -> float:
    instant = motion.stoppedAt or now
    return min(
        1.0,
        max(0.0, (utc(instant) - utc(motion.startedAt)).total_seconds() * 1000 / motion.durationMs),
    )


def split_motion(
    motion: OfficeMotion, now: datetime
) -> tuple[OfficePoint, list[OfficePoint], list[OfficePoint]]:
    remaining = path_length(motion.points) * movement_progress(motion, now)
    for index, (a, b) in enumerate(zip(motion.points, motion.points[1:], strict=False)):
        segment = distance(a, b)
        if remaining <= segment:
            fraction = remaining / segment if segment else 0
            point = OfficePoint(x=a.x + (b.x - a.x) * fraction, y=a.y + (b.y - a.y) * fraction)
            return (
                point,
                [point, *reversed(motion.points[: index + 1])],
                [point, *motion.points[index + 1 :]],
            )
        remaining -= segment
    return motion.points[-1], list(reversed(motion.points)), [motion.points[-1]]


def point_segment_distance(point: OfficePoint, a: OfficePoint, b: OfficePoint) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    denominator = dx * dx + dy * dy
    t = (
        max(0, min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / denominator))
        if denominator
        else 0
    )
    return hypot(point.x - a.x - t * dx, point.y - a.y - t * dy)
