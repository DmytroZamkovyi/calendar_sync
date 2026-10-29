# Calendar Sync

Two-way (or one-way) synchronisation between any two Home Assistant `calendar.*`
entities, with simple per-direction rules. Native Home Assistant UI only.

**Status: work in progress.** This branch contains the pure sync core and its tests
(`custom_components/calendar_sync/core/`). The Home Assistant integration layer
(config flow, entities, services) is not written yet, so it cannot be installed.

## How it works

- Pick calendar 1 and calendar 2, then enable the directions you need. Both enabled
  is two-way sync; one enabled is a mirror.
- Each cycle compares *last synced snapshot / calendar 1 / calendar 2* per event and
  moves only the fields that changed (summary, time, description, location).
- Rules (fixed set of conditions and actions) are defined per direction: skip events,
  rename, clear description or location, shift time.
- Safety: only linked events are ever modified, deletions above a threshold are held
  back until confirmed, adapter errors leave the link untouched and are retried.

## Development

```
python -m pytest
```

The core has no Home Assistant imports.

## License

MIT
