from __future__ import annotations

from pathlib import Path

from derive import cli


class _Progress:
    def __init__(self, iterable, **kwargs):
        self.iterable = iterable
        self.kwargs = kwargs
        self.postfixes: list[str] = []

    def __iter__(self):
        return iter(self.iterable)

    def set_postfix_str(self, value: str, *, refresh: bool) -> None:
        self.postfixes.append(value)


def test_build_reports_partition_progress(
    monkeypatch, tmp_path: Path
) -> None:
    progress: list[_Progress] = []

    def fake_tqdm(iterable, **kwargs):
        item = _Progress(iterable, **kwargs)
        progress.append(item)
        return item

    monkeypatch.setattr(cli, "tqdm", fake_tqdm)
    monkeypatch.setattr(
        cli,
        "_one",
        lambda task: (
            "canonical/underlying_inputs/asset=BTC/date=2026-01-01",
            "complete",
            42,
            False,
        ),
    )

    result = cli.main(
        [
            "build",
            "--data-dir",
            str(tmp_path),
            "--kind",
            "underlying",
            "--asset",
            "BTC",
            "--from",
            "2026-01-01",
            "--to",
            "2026-01-01",
        ]
    )

    assert result == 0
    assert progress[0].kwargs["total"] == 1
    assert progress[0].kwargs["unit"] == "partition"
    assert progress[0].postfixes == [
        "canonical/underlying_inputs/asset=BTC/date=2026-01-01 "
        "complete rows=42 skipped=False"
    ]
