from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from training.spec import SplitConfig
from training.split import chronological_split


def _rows() -> pd.DataFrame:
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for market in range(8):
        start = origin + timedelta(hours=market)
        for decision in range(2):
            records.append(
                {
                    "row_id": f"{market}-{decision}",
                    "slug": f"market-{market}",
                    "decision_at": start + timedelta(minutes=30 + decision),
                    "window_start": start,
                    "window_end": start + timedelta(minutes=45),
                }
            )
    return pd.DataFrame(records[::-1])


def test_fraction_split_is_chronological_and_keeps_markets_together() -> None:
    rows = _rows()
    config = SplitConfig.model_validate(
        {
            "train_fraction": 0.5,
            "validation_fraction": 0.25,
            "test_fraction": 0.25,
            "purge_seconds": 0,
        }
    )
    result = chronological_split(rows, config)

    assignments = pd.DataFrame(
        {"slug": rows["slug"], "split": result.assignments}
    ).groupby("slug")["split"].nunique()
    assert assignments.max() == 1
    assert result.market_counts == {
        "train": 4,
        "validation": 2,
        "test": 2,
        "excluded": 0,
    }
    train_times = rows.loc[result.assignments == "train", "decision_at"]
    validation_times = rows.loc[result.assignments == "validation", "decision_at"]
    test_times = rows.loc[result.assignments == "test", "decision_at"]
    assert max(train_times) < min(validation_times)
    assert max(validation_times) < min(test_times)


def test_purge_excludes_whole_market_whose_outcome_reaches_boundary() -> None:
    rows = _rows()
    # The last base-train market ends 15 minutes after validation's first decision.
    rows.loc[rows["slug"] == "market-3", "window_end"] = datetime(
        2026, 1, 1, 4, 40, tzinfo=timezone.utc
    )
    config = SplitConfig.model_validate(
        {
            "train_fraction": 0.5,
            "validation_fraction": 0.25,
            "test_fraction": 0.25,
            "purge_seconds": 0,
        }
    )
    result = chronological_split(rows, config)
    market_mask = rows["slug"] == "market-3"
    assert set(result.assignments.loc[market_mask]) == {"excluded"}
    assert set(result.exclusion_reasons.loc[market_mask]) == {
        "purged_before_validation"
    }
