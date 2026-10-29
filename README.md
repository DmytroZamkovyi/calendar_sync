# Calendar Sync

[🇺🇦 Українська](README.uk.md)

Two-way (or one-way) synchronisation between any two Home Assistant `calendar.*`
entities, with simple per-direction rules. Native Home Assistant UI only: no custom
panels, cards or styles.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DmytroZamkovyi&repository=calendar_sync&category=integration)
[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=calendar_sync)

> **Status: work in progress.** The sync core (`core/`) and the calendar entity
> adapter (`adapter.py`, `ha_calendar.py`) are done and tested. The Home Assistant
> integration layer (config flow, entities, services) is not written yet, so the
> buttons above will only work once it is released.

## Installation (once released)

1. HACS → ⋮ → **Custom repositories** → add this repository, category **Integration**
   (or use the HACS button above).
2. Download **Calendar Sync**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → Calendar Sync**
   (or use the second button above).

## How it works

- Pick calendar 1 and calendar 2, then enable the directions you need. Both enabled
  is two-way sync; one enabled is a mirror.
- Each cycle compares *last synced snapshot / calendar 1 / calendar 2* per event and
  moves only the fields that changed (summary, time, description, location).
- Rules (fixed set of conditions and actions) are defined per direction: skip events,
  rename with a template, clear description or location, shift time.
- In a mirror, edits made in the target calendar are either ignored or reverted.
- Conflicts (the same field changed on both sides) are resolved by policy:
  calendar 1 wins, calendar 2 wins, or ask.
- Safety: only linked events are ever modified, deletions above a threshold are held
  back until confirmed, adapter errors leave the link untouched and are retried.

### Limits

- Read-only calendars (CalDAV, holidays, other integrations' calendars) can only be a source.
- The built-in Google Calendar integration cannot edit events, so changes are written
  there as delete + create.
- Recurring events are synced as separate expanded occurrences inside the window,
  not as a series.
- Attendees, reminders and attachments are not synced.

## Development

```
python -m pytest
```

The core has no Home Assistant imports.

## License

MIT
