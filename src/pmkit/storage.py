"""Tape storage. Provided complete.

ParquetSink buffers TapeRows and writes one Parquet file per flush, partitioned
by UTC date: <root>/date=YYYY-MM-DD/part-<epoch_ms>.parquet. Read the whole tape
back with `load_tape(root)`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .models import TapeRow


class MemorySink:
    """For tests and notebooks."""

    def __init__(self) -> None:
        self.rows: list[TapeRow] = []

    def write(self, rows: list[TapeRow]) -> None:
        self.rows.extend(rows)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class ParquetSink:
    def __init__(self, root: str | Path, flush_rows: int = 5_000, flush_seconds: float = 30.0):
        self.root = Path(root)
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self._buf: list[TapeRow] = []
        self._last_flush = time.monotonic()

    def write(self, rows: list[TapeRow]) -> None:
        self._buf.extend(rows)
        if (
            len(self._buf) >= self.flush_rows
            or time.monotonic() - self._last_flush >= self.flush_seconds
        ):
            self.flush()

    def flush(self) -> None:
        self._last_flush = time.monotonic()
        if not self._buf:
            return
        df = pd.DataFrame([_flat(r) for r in self._buf])
        self._buf = []
        for date, part in df.groupby(df["recv_ts"].dt.strftime("%Y-%m-%d")):
            out = self.root / f"date={date}"
            out.mkdir(parents=True, exist_ok=True)
            part.to_parquet(out / f"part-{time.time_ns() // 1_000_000}.parquet", index=False)

    def close(self) -> None:
        self.flush()


def _flat(row: TapeRow) -> dict:
    d = asdict(row)
    d["extra"] = json.dumps(d["extra"]) if d["extra"] else None
    return d


def load_tape(root: str | Path) -> pd.DataFrame:
    files = sorted(Path(root).glob("date=*/part-*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    return df.sort_values("recv_ts", kind="stable").reset_index(drop=True)
