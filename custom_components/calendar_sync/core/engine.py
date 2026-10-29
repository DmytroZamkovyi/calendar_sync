"""Three-way sync engine: last-synced snapshot / calendar 1 / calendar 2.

One-way mirroring is the same algorithm with one direction disabled. No Home
Assistant imports; calendars are reached only through `CalendarAdapter`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .models import (
    CLOSED_REVERT,
    CONFLICT_ASK,
    CONFLICT_CALENDAR1,
    CONFLICT_CALENDAR2,
    FIELD_NAMES,
    CalendarAdapter,
    EventFields,
    PairConfig,
    overlaps,
)
from .rules import apply_rules
from .state import Link, State


@dataclass(slots=True)
class Operation:
    kind: str  # create | update | replace | delete | adopt
    side: int  # calendar the operation is applied to (1 or 2)
    event_id: str | None
    summary: str
    fields: tuple[str, ...] = ()


@dataclass(slots=True)
class Conflict:
    link_id: str
    field: str
    summary: str
    value1: Any
    value2: Any


@dataclass(slots=True)
class SyncResult:
    state: State
    created: int = 0
    updated: int = 0
    deleted: int = 0
    adopted: int = 0
    blocked_deletions: int = 0
    operations: list[Operation] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _Run:
    def __init__(
        self,
        cfg: PairConfig,
        state: State,
        events1: Mapping[str, EventFields],
        events2: Mapping[str, EventFields],
        adapter1: CalendarAdapter,
        adapter2: CalendarAdapter,
        *,
        dry: bool,
        allow_deletes: bool,
    ) -> None:
        self.cfg = cfg
        self.state = state.copy()
        self.events = {1: events1, 2: events2}
        self.adapters = {1: adapter1, 2: adapter2}
        self.dry = dry
        self.allow_deletes = allow_deletes
        self.deletes = 0
        # ids removed during this cycle; they are still present in the input snapshot
        self.gone: dict[int, set[str]] = {1: set(), 2: set()}
        self.result = SyncResult(state=self.state)

    # -- helpers -----------------------------------------------------------

    def _open(self, src: int, dst: int) -> bool:
        """True when changes may flow src -> dst."""
        enabled = self.cfg.direction_1to2 if src == 1 else self.cfg.direction_2to1
        return enabled and self.adapters[dst].capabilities.create

    def _transform(self, src: int, fields: EventFields) -> EventFields | None:
        rules = self.cfg.rules_1to2 if src == 1 else self.cfg.rules_2to1
        return apply_rules(rules, fields, self.cfg.tz)

    def _error(self, text: str) -> None:
        self.result.errors.append(text)

    def _new_link(self, src: int, src_id: str, src_fields: EventFields, dst_id: str, dst_fields: EventFields) -> None:
        link = Link(
            id1=src_id if src == 1 else dst_id,
            id2=dst_id if src == 1 else src_id,
            base1=src_fields if src == 1 else dst_fields,
            base2=dst_fields if src == 1 else src_fields,
        )
        self.state.links[f"l{self.state.next_id}"] = link
        self.state.next_id += 1

    # -- adapter operations (record in preview, execute otherwise) ----------

    async def _create(self, side: int, fields: EventFields) -> tuple[str, EventFields] | None:
        op = Operation("create", side, None, fields.summary)
        if self.dry:
            self.result.operations.append(op)
            self.result.created += 1
            return f"dry-{len(self.result.operations)}", fields
        try:
            created = await self.adapters[side].create(fields)
        except Exception as err:  # noqa: BLE001 - adapter boundary
            self._error(f"create on calendar {side} failed: {err}")
            return None
        self.result.operations.append(op)
        self.result.created += 1
        return created

    async def _update(
        self, side: int, event_id: str, current: EventFields, new: EventFields
    ) -> tuple[str, EventFields] | None:
        adapter = self.adapters[side]
        changed = tuple(sorted(new.diff(current)))
        replace = not adapter.capabilities.update
        if replace and not (adapter.capabilities.create and adapter.capabilities.delete):
            self._error(f"calendar {side} cannot modify events")
            return None
        op = Operation("replace" if replace else "update", side, event_id, new.summary, changed)
        if self.dry:
            self.result.operations.append(op)
            self.result.updated += 1
            if replace:
                self.gone[side].add(event_id)
            return event_id, new
        try:
            if replace:
                await adapter.delete(event_id)
                self.gone[side].add(event_id)
                created = await adapter.create(new)
                result = created
            else:
                stored = await adapter.update(event_id, new)
                result = (event_id, stored or new)
        except Exception as err:  # noqa: BLE001 - adapter boundary
            self._error(f"update on calendar {side} failed: {err}")
            return None
        self.result.operations.append(op)
        self.result.updated += 1
        return result

    async def _delete(self, side: int, event_id: str, summary: str) -> bool:
        self.deletes += 1
        if not self.allow_deletes:
            self.result.blocked_deletions += 1
            return False
        op = Operation("delete", side, event_id, summary)
        if not self.dry:
            try:
                await self.adapters[side].delete(event_id)
            except Exception as err:  # noqa: BLE001 - adapter boundary
                self._error(f"delete on calendar {side} failed: {err}")
                return False
        self.gone[side].add(event_id)
        self.result.operations.append(op)
        self.result.deleted += 1
        return True

    # -- main --------------------------------------------------------------

    async def run(self) -> None:
        for key, link in list(self.state.links.items()):
            await self._process_link(key, link)
        await self._create_unlinked(1, 2)
        await self._create_unlinked(2, 1)

    async def _process_link(self, key: str, link: Link) -> None:
        cur1 = self.events[1].get(link.id1) if link.id1 else None
        cur2 = self.events[2].get(link.id2) if link.id2 else None
        if link.detached:
            live_present = cur1 if link.id1 else cur2
            if live_present is None:
                del self.state.links[key]
            return
        if cur1 is None and cur2 is None:
            del self.state.links[key]
        elif cur1 is None:
            await self._handle_missing(key, link, 1, cur2)
        elif cur2 is None:
            await self._handle_missing(key, link, 2, cur1)
        else:
            await self._reconcile(key, link, cur1, cur2)

    async def _handle_missing(self, key: str, link: Link, missing: int, present: EventFields) -> None:
        other = 2 if missing == 1 else 1
        if not overlaps(link.get_base(missing), self.cfg.window, self.cfg.tz):
            return  # rolled out of the window, not a deletion
        caps_other = self.adapters[other].capabilities
        if self._open(missing, other) and self.cfg.propagate_deletes and caps_other.delete:
            if await self._delete(other, link.get_id(other), present.summary):  # type: ignore[arg-type]
                del self.state.links[key]
            return
        if (
            not self._open(missing, other)
            and self._open(other, missing)
            and self.cfg.closed_mode == CLOSED_REVERT
        ):
            desired = self._transform(other, present)
            if desired is None:
                return
            created = await self._create(missing, desired)
            if created:
                link.set_id(missing, created[0])
                link.set_base(missing, created[1])
                link.set_base(other, present)
            return
        link.set_id(missing, None)
        link.detached = True

    async def _reconcile(self, key: str, link: Link, cur1: EventFields, cur2: EventFields) -> None:
        open12, open21 = self._open(1, 2), self._open(2, 1)
        changed = {1: cur1.diff(link.base1), 2: cur2.diff(link.base2)}
        revert = self.cfg.closed_mode == CLOSED_REVERT and (open12 != open21)
        cur = {1: cur1, 2: cur2}

        def collect(src: int, dst: int) -> dict[str, Any]:
            if not self._open(src, dst):
                return {}
            desired = self._transform(src, cur[src])
            if desired is None:
                return {}
            names = FIELD_NAMES if revert else changed[src]
            return {
                name: desired.get(name)
                for name in names
                if desired.get(name) != cur[dst].get(name)
            }

        to2, to1 = collect(1, 2), collect(2, 1)
        unresolved: set[str] = set()
        for name in sorted(set(to1) & set(to2)):
            policy = self.cfg.conflict_policy
            if policy == CONFLICT_CALENDAR1:
                del to1[name]
            elif policy == CONFLICT_CALENDAR2:
                del to2[name]
            else:
                self.result.conflicts.append(
                    Conflict(key, name, cur1.summary, cur1.get(name), cur2.get(name))
                )
                unresolved.add(name)
                del to1[name], to2[name]

        new1, new2 = cur1.replace(to1), cur2.replace(to2)
        if to2:
            res = await self._update(2, link.id2, cur2, new2)  # type: ignore[arg-type]
            if res is None:
                return
            link.id2, new2 = res
        if to1:
            res = await self._update(1, link.id1, cur1, new1)  # type: ignore[arg-type]
            if res is None:
                return
            link.id1, new1 = res

        for side, new in ((1, new1), (2, new2)):
            old = link.get_base(side)
            link.set_base(side, new.replace({n: old.get(n) for n in unresolved}) if unresolved else new)

    async def _create_unlinked(self, src: int, dst: int) -> None:
        if not self._open(src, dst):
            return
        linked_src = {l.get_id(src) for l in self.state.links.values()}
        linked_dst = {l.get_id(dst) for l in self.state.links.values()}
        free_dst = {
            i: f
            for i, f in self.events[dst].items()
            if i not in linked_dst and i not in self.gone[dst]
        }
        for event_id, fields in self.events[src].items():
            if event_id in linked_src or event_id in self.gone[src]:
                continue
            desired = self._transform(src, fields)
            if desired is None:
                continue
            match = next(
                (
                    i
                    for i, f in free_dst.items()
                    if f.summary == desired.summary and f.get("when") == desired.get("when")
                ),
                None,
            )
            if match is not None:
                self.result.operations.append(Operation("adopt", dst, match, desired.summary))
                self.result.adopted += 1
                self._new_link(src, event_id, fields, match, free_dst.pop(match))
                continue
            created = await self._create(dst, desired)
            if created:
                self._new_link(src, event_id, fields, created[0], created[1])


async def sync_pair(
    cfg: PairConfig,
    state: State,
    events1: Mapping[str, EventFields],
    events2: Mapping[str, EventFields],
    adapter1: CalendarAdapter,
    adapter2: CalendarAdapter,
    *,
    confirm_deletions: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Run one cycle for a pair and return the new state plus what was done.

    `events1`/`events2` map event id -> fields for the configured window and must
    come from a successful read of both calendars; on a read error skip the cycle.
    Deletions above the guard threshold are blocked unless `confirm_deletions`.
    """
    plan = _Run(cfg, state, events1, events2, adapter1, adapter2, dry=True, allow_deletes=True)
    await plan.run()
    threshold = max(cfg.delete_guard_min, cfg.delete_guard_ratio * len(state.links))
    guarded = not confirm_deletions and plan.deletes > threshold
    if dry_run:
        if guarded:
            plan.result.blocked_deletions = plan.deletes
        return plan.result
    real = _Run(cfg, state, events1, events2, adapter1, adapter2, dry=False, allow_deletes=not guarded)
    await real.run()
    return real.result
