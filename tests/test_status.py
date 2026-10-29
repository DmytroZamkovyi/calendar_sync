"""Cycle status bookkeeping and consistency of translations with code."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from custom_components.calendar_sync import status as st
from custom_components.calendar_sync.core.engine import Conflict, SyncResult
from custom_components.calendar_sync.core.state import State

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)


def result(**kw):
    return SyncResult(state=State(), **kw)


def test_status_counts_and_out_of_sync():
    s = st.status_from_result(
        result(created=2, conflicts=[Conflict("1", "summary", "Meet", "a", "b")]),
        NOW,
        None,
        dry_run=False,
    )
    assert s.created == 2 and s.conflicts == ["Meet (summary)"] and s.out_of_sync
    assert not st.status_from_result(result(), NOW, s, dry_run=False).out_of_sync


def test_failures_count_consecutive_cycles():
    s = st.status_from_result(result(errors=["x"]), NOW, None, dry_run=False)
    s = st.status_from_result(result(errors=["x"]), NOW, s, dry_run=False)
    assert s.failures == 2
    s = st.status_from_failure("unavailable", NOW, s)
    assert s.failures == 3 and s.errors == ["unavailable"]
    assert st.status_from_result(result(), NOW, s, dry_run=False).failures == 0


def test_short_list_truncates():
    text = st.short_list([str(i) for i in range(8)])
    assert text.endswith("(+3)") and text.count(";") == 5


def test_translations_cover_entities_services_and_issues():
    en = json.loads((ROOT / "translations/en.json").read_text("utf-8"))
    code = "".join(
        (ROOT / f).read_text("utf-8")
        for f in ("sensor.py", "binary_sensor.py", "button.py", "coordinator.py", "services.py")
    )
    for key in re.findall(r'(?<!translation_)key="(\w+)"', code):
        assert any(key in group for group in en["entity"].values()), key
    for kind in ("conflicts", "deletions_blocked", "sync_failing"):
        assert kind in en["issues"]
    yaml_services = re.findall(r"^(\w+):$", (ROOT / "services.yaml").read_text(), re.M)
    assert set(yaml_services) == set(en["services"])
    raised = "".join((ROOT / f).read_text("utf-8") for f in ("services.py", "coordinator.py"))
    for key in re.findall(r'translation_key="(\w+)"', raised):
        assert key in en["exceptions"] or key in en["issues"], key
