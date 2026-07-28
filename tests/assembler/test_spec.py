from __future__ import annotations

import pytest
from pydantic import ValidationError

from assembler.spec import DatasetSpec


def test_spec_requires_explicit_utc_range_anchor_and_pmdata_policy() -> None:
    base = {
        "spec_version": "1",
        "assets": ["BTC"],
        "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
        "decision_schedules": [{"name": "d", "anchor": "window_start", "offset": "-30s"}],
        "features": ["time_of_day_sin"],
        "availability_policies": {"pmdata": "pmdata_local_timestamp_utc_clamped_v1"},
        "quote": {"max_staleness_seconds": 5, "band_min": 0.49, "band_max": 0.51},
        "sources": {"poly_l2": "required"},
        "execution_delay": "250ms",
        "output_path": "derived/datasets",
    }
    spec = DatasetSpec.model_validate(base)
    assert spec.decision_schedules[0].offset_seconds == -30
    assert spec.execution_delay_seconds == 0.25
    invalid = dict(base)
    invalid["time_range"] = {"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00Z"}
    with pytest.raises(ValidationError):
        DatasetSpec.model_validate(invalid)
