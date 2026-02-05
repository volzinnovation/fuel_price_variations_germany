#!/usr/bin/env python3
"""Build a compact management summary from existing data2 JSON files.

This is useful if data2/ already exists and you don't want to re-download
and recompute everything via generate_data.py.

Output: data2/YYYY/MM/DD/management_boxplots.json (snapshot for yesterday)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pytz
from tqdm import tqdm

TZ = pytz.timezone("Europe/Berlin")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data2 = root / "data2"
    if not data2.exists():
        raise SystemExit("data2/ not found. Run generate_data.py first.")

    fuels = ("diesel", "e10", "e5")
    mgmt_values: Dict[str, Dict[int, List[float]]] = {
        fuel: {hour: [] for hour in range(24)} for fuel in fuels
    }
    station_counts: Dict[str, int] = {fuel: 0 for fuel in fuels}

    for fuel in fuels:
        files = list(data2.rglob(f"{fuel}.json"))
        station_counts[fuel] = len(files)
        for path in tqdm(files, desc=f"Reading {fuel}", unit="file"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                hourly = payload.get("hourly") or []
                for row in hourly:
                    hour = int(row.get("hour"))
                    price = float(row.get("price"))
                    if 0 <= hour <= 23:
                        mgmt_values[fuel][hour].append(price)
            except Exception:
                continue

    snapshot_date = date.today() - timedelta(days=1)
    summary = {
        "snapshot_date": str(snapshot_date),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "station_counts": station_counts,
        "fuels": {},
    }

    for fuel in fuels:
        fuel_stats = []
        for hour in range(24):
            values = mgmt_values[fuel][hour]
            if values:
                s = pd.Series(values, dtype="float64")
                fuel_stats.append(
                    {
                        "hour": hour,
                        "count": int(s.count()),
                        "min": float(s.min()),
                        "q1": float(s.quantile(0.25)),
                        "median": float(s.quantile(0.5)),
                        "q3": float(s.quantile(0.75)),
                        "max": float(s.max()),
                    }
                )
            else:
                fuel_stats.append(
                    {
                        "hour": hour,
                        "count": 0,
                        "min": 0.0,
                        "q1": 0.0,
                        "median": 0.0,
                        "q3": 0.0,
                        "max": 0.0,
                    }
                )
        summary["fuels"][fuel] = fuel_stats

    out = (
        root
        / "data2"
        / f"{snapshot_date:%Y}"
        / f"{snapshot_date:%m}"
        / f"{snapshot_date:%d}"
        / "management_boxplots.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
