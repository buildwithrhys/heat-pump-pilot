from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from heat_pump_pilot.integrations.homeassistant.client import HAState

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def pilot_states() -> list[HAState]:
    raw = json.loads((FIXTURES / "home_assistant" / "pilot_states.json").read_text())
    out = []
    for r in raw:
        s = HAState(entity_id=r["entity_id"], state=r["state"], attributes=r.get("attributes", {}),
                    last_updated=datetime(2026, 10, 25, 22, 45, tzinfo=timezone.utc))
        s.platform = r.get("platform")
        out.append(s)
    return out


@pytest.fixture
def tmp_db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.sqlite'}"
