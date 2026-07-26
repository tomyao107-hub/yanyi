from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from ..jobs.manager import job_manager
from ..models import utc_now

EventPayload = dict[str, Any]


@dataclass(slots=True)
class PublishedEvent:
    id: int
    type: str
    project_id: int
    timestamp: str
    data: EventPayload

    def as_dict(self) -> EventPayload:
        return {
            "id": self.id,
            "type": self.type,
            "project_id": self.project_id,
            "timestamp": self.timestamp,
            **self.data,
        }


class EventBroker:
    """Small in-process fan-out broker suitable for this local-only app."""

    def __init__(self, history_size: int = 500) -> None:
        self._history: dict[int, deque[PublishedEvent]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )
        self._subscribers: dict[int, set[asyncio.Queue[PublishedEvent]]] = defaultdict(set)
        self._sequence = 0

    async def publish(self, project_id: int, event_type: str, **data: Any) -> PublishedEvent:
        self._sequence += 1
        event = PublishedEvent(
            id=self._sequence,
            type=event_type,
            project_id=project_id,
            timestamp=utc_now(),
            data=data,
        )
        self._history[project_id].append(event)
        for queue in tuple(self._subscribers.get(project_id, ())):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return event

    def history(self, project_id: int, after_id: int | None = None) -> list[PublishedEvent]:
        events = list(self._history.get(project_id, ()))
        if after_id is not None:
            events = [event for event in events if event.id > after_id]
        return events

    async def clear(self, project_id: int) -> None:
        """Forget a deleted project and close any active project streams."""

        if project_id in self._subscribers:
            await self.publish(project_id, "project_deleted")
        self._history.pop(project_id, None)
        self._subscribers.pop(project_id, None)

    @contextlib.asynccontextmanager
    async def subscribe(self, project_id: int):
        queue: asyncio.Queue[PublishedEvent] = asyncio.Queue(maxsize=200)
        self._subscribers[project_id].add(queue)
        try:
            yield queue
        finally:
            subscribers = self._subscribers.get(project_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(project_id, None)


event_broker = EventBroker()
# Compatibility name retained for callers/tests. Durable state and duplicate
# prevention live in the Job table; the broker remains best-effort only.
translation_tasks = job_manager
