"""Persistent sync state of one pair: links and last-synced snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .models import EventFields


def _dump_when(value: date | datetime) -> str:
    return value.isoformat()


def _load_when(text: str) -> date | datetime:
    return datetime.fromisoformat(text) if "T" in text else date.fromisoformat(text)


def fields_to_json(fields: EventFields) -> dict[str, Any]:
    return {
        "summary": fields.summary,
        "start": _dump_when(fields.start),
        "end": _dump_when(fields.end),
        "description": fields.description,
        "location": fields.location,
    }


def fields_from_json(data: dict[str, Any]) -> EventFields:
    return EventFields(
        summary=data["summary"],
        start=_load_when(data["start"]),
        end=_load_when(data["end"]),
        description=data.get("description", ""),
        location=data.get("location", ""),
    )


@dataclass(slots=True)
class Link:
    """One event on each side plus what each side looked like at the last sync.

    `detached` means one side was deleted by the user and the pair is configured
    to ignore that: the link is kept (so the event is not re-created) until the
    surviving event disappears too.
    """

    id1: str | None
    id2: str | None
    base1: EventFields
    base2: EventFields
    detached: bool = False

    def get_id(self, side: int) -> str | None:
        return self.id1 if side == 1 else self.id2

    def set_id(self, side: int, value: str | None) -> None:
        if side == 1:
            self.id1 = value
        else:
            self.id2 = value

    def get_base(self, side: int) -> EventFields:
        return self.base1 if side == 1 else self.base2

    def set_base(self, side: int, value: EventFields) -> None:
        if side == 1:
            self.base1 = value
        else:
            self.base2 = value


@dataclass(slots=True)
class State:
    links: dict[str, Link] = field(default_factory=dict)
    next_id: int = 1

    def copy(self) -> State:
        return State(
            links={
                key: Link(l.id1, l.id2, l.base1, l.base2, l.detached)
                for key, l in self.links.items()
            },
            next_id=self.next_id,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "next_id": self.next_id,
            "links": {
                key: {
                    "id1": l.id1,
                    "id2": l.id2,
                    "base1": fields_to_json(l.base1),
                    "base2": fields_to_json(l.base2),
                    "detached": l.detached,
                }
                for key, l in self.links.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> State:
        return cls(
            links={
                key: Link(
                    l["id1"],
                    l["id2"],
                    fields_from_json(l["base1"]),
                    fields_from_json(l["base2"]),
                    l.get("detached", False),
                )
                for key, l in data.get("links", {}).items()
            },
            next_id=data.get("next_id", 1),
        )
