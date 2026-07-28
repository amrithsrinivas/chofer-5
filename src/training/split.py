"""Pure market-level chronological splitting and purging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, cast

import pandas as pd

from .spec import SplitConfig, UtcRange

_SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class SplitResult:
    """Assignments are indexed exactly like the input rows."""

    assignments: pd.Series
    exclusion_reasons: pd.Series
    boundaries: Mapping[str, datetime]
    model_fit_at: datetime
    market_counts: Mapping[str, int]


def _as_utc(series: pd.Series, name: str) -> pd.Series:
    try:
        parsed = pd.Series(
            pd.to_datetime(series, utc=True, errors="raise"), index=series.index
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"column {name!r} contains an invalid timestamp") from exc
    if bool(parsed.isna().any()):
        raise ValueError(f"column {name!r} contains null timestamps")
    return parsed


def _range_for(split: str, config: SplitConfig) -> UtcRange:
    value = getattr(config, split)
    assert value is not None
    return value


def chronological_split(
    rows: pd.DataFrame,
    config: SplitConfig,
    *,
    market_column: str = "slug",
    time_column: str = "decision_at",
    window_start_column: str = "window_start",
    window_end_column: str = "window_end",
) -> SplitResult:
    """Split chronologically without ever separating rows from one market.

    Fraction mode allocates contiguous market groups, not individual rows. Purging
    removes an earlier block's markets when their outcome window extends into the
    next block, plus the configured pre-boundary embargo.
    """

    required = {
        market_column,
        time_column,
        window_start_column,
        window_end_column,
    }
    missing = sorted(required.difference(rows.columns))
    if missing:
        raise ValueError(f"cannot split rows; missing columns: {', '.join(missing)}")
    if rows.empty:
        raise ValueError("cannot split an empty dataset")
    markets_source = cast(pd.Series, rows[market_column])
    if bool(markets_source.isna().any()):
        raise ValueError(f"column {market_column!r} contains null market IDs")

    working = pd.DataFrame(index=rows.index)
    working["market"] = markets_source.astype(str)
    working["decision"] = _as_utc(cast(pd.Series, rows[time_column]), time_column)
    working["window_start"] = _as_utc(
        cast(pd.Series, rows[window_start_column]), window_start_column
    )
    working["window_end"] = _as_utc(
        cast(pd.Series, rows[window_end_column]), window_end_column
    )
    if bool(
        (
            cast(pd.Series, working["window_end"])
            <= cast(pd.Series, working["window_start"])
        ).any()
    ):
        raise ValueError("every window_end must be after window_start")

    grouped = (
        working.groupby("market", sort=False)
        .agg(
            decision_min=("decision", "min"),
            decision_max=("decision", "max"),
            window_start_min=("window_start", "min"),
            window_start_max=("window_start", "max"),
            window_end_min=("window_end", "min"),
            window_end_max=("window_end", "max"),
        )
        .reset_index()
    )
    inconsistent = grouped[
        (grouped["window_start_min"] != grouped["window_start_max"])
        | (grouped["window_end_min"] != grouped["window_end_max"])
    ]
    if not inconsistent.empty:
        inconsistent_markets = cast(pd.Series, inconsistent["market"])
        markets = ", ".join(inconsistent_markets.head(5).astype(str))
        raise ValueError(f"market IDs map to multiple windows: {markets}")

    grouped = grouped.sort_values(
        ["window_start_min", "decision_min", "market"], kind="mergesort"
    ).reset_index(drop=True)
    group_split = pd.Series("excluded", index=grouped.index, dtype="string")
    group_reason = pd.Series("outside_split_ranges", index=grouped.index, dtype="string")

    boundaries: dict[str, datetime]
    if config.uses_fractions:
        market_count = len(grouped)
        if market_count < 3:
            raise ValueError("fraction splitting requires at least three markets")
        assert config.train_fraction is not None
        assert config.validation_fraction is not None
        train_end = int(market_count * config.train_fraction)
        validation_end = int(
            market_count * (config.train_fraction + config.validation_fraction)
        )
        if train_end < 1 or validation_end <= train_end or validation_end >= market_count:
            raise ValueError(
                "split fractions produce an empty market fold; add markets or adjust fractions"
            )
        group_split.iloc[:train_end] = "train"
        group_split.iloc[train_end:validation_end] = "validation"
        group_split.iloc[validation_end:] = "test"
        group_reason.loc[:] = ""
        validation_boundary = pd.Timestamp(
            grouped.at[train_end, "decision_min"]
        ).to_pydatetime()
        test_boundary = pd.Timestamp(
            grouped.at[validation_end, "decision_min"]
        ).to_pydatetime()
        boundaries = {
            "validation_start": cast(datetime, validation_boundary),
            "test_start": cast(datetime, test_boundary),
        }
    else:
        for split in _SPLITS:
            split_range = _range_for(split, config)
            start = pd.Timestamp(split_range.start)
            end = pd.Timestamp(split_range.end)
            complete_market = (grouped["decision_min"] >= start) & (
                grouped["decision_max"] < end
            )
            group_split.loc[complete_market] = split
            group_reason.loc[complete_market] = ""
        validation_range = _range_for("validation", config)
        test_range = _range_for("test", config)
        boundaries = {
            "validation_start": cast(datetime, validation_range.start),
            "test_start": cast(datetime, test_range.start),
        }

    purge = pd.Timedelta(seconds=config.purge_seconds)
    for earlier, later, boundary_key in (
        ("train", "validation", "validation_start"),
        ("validation", "test", "test_start"),
    ):
        boundary = pd.Timestamp(boundaries[boundary_key])
        purge_mask = (group_split == earlier) & (
            grouped["window_end_max"] > boundary - purge
        )
        group_split.loc[purge_mask] = "excluded"
        group_reason.loc[purge_mask] = f"purged_before_{later}"

    grouped_markets = cast(pd.Series, grouped["market"])
    market_to_split = dict(zip(grouped_markets.astype(str), group_split, strict=True))
    market_to_reason = dict(zip(grouped_markets.astype(str), group_reason, strict=True))
    working_markets = cast(pd.Series, working["market"])
    assignments = working_markets.apply(
        lambda market: market_to_split[str(market)]
    ).astype("string")
    reasons = working_markets.apply(
        lambda market: market_to_reason[str(market)]
    ).astype("string")
    assignments.index = rows.index
    reasons.index = rows.index

    market_counts = {
        split: int((group_split == split).sum()) for split in (*_SPLITS, "excluded")
    }
    for split in _SPLITS:
        if market_counts[split] == 0:
            raise ValueError(f"chronological split has no {split} markets after purging")

    return SplitResult(
        assignments=cast(pd.Series, assignments),
        exclusion_reasons=cast(pd.Series, reasons),
        boundaries=boundaries,
        model_fit_at=boundaries["test_start"],
        market_counts=market_counts,
    )


split_rows = chronological_split
