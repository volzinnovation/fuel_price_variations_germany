#!/usr/bin/env python3
"""Verify deployed noon-reference artifacts after data generation.

This script is meant to run after the daily data workflow has pushed generated
artifacts to ``master``. It performs three checks for a selected Berlin-local
date:

1. Local integrity: delayed noon references in dated ``noon.csv`` must match
   the first cycle bucket in the corresponding station ``fuel.json`` payload.
2. Live parity: the deployed dated ``noon.csv`` and
   ``management_boxplots.json`` must match the repository checkout.
3. Live samples: a deterministic sample of delayed noon references served by
   the live site must still match the selected noon reference price.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import certifi
import requests


BERLIN = ZoneInfo("Europe/Berlin")
FUELS: tuple[str, ...] = ("diesel", "e10", "e5")
NOON = dt_time(12, 0)
PRICE_TOLERANCE = 0.0005


class VerificationError(RuntimeError):
    """Raised when generated or deployed artifacts violate invariants."""


@dataclass(frozen=True)
class NoonReference:
    station_uuid: str
    fuel: str
    price: float
    last_update: datetime
    selection_method: str

    @property
    def delay_minutes(self) -> int:
        local_ts = self.last_update.astimezone(BERLIN)
        return local_ts.hour * 60 + local_ts.minute - 12 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--origin",
        default="https://tankzeit.de",
        help="Site origin to verify. Defaults to https://tankzeit.de.",
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        help="Berlin-local snapshot date in YYYY-MM-DD. Defaults to yesterday in Berlin.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root containing data/ and data2/. Defaults to the current repo.",
    )
    parser.add_argument(
        "--wait-live-seconds",
        type=int,
        default=900,
        help="How long to wait for live dated artifacts to match the checked-in files.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=30,
        help="Polling interval while waiting for the live site to catch up.",
    )
    parser.add_argument(
        "--live-samples-per-fuel",
        type=int,
        default=10,
        help="How many delayed live station/fuel payloads to verify per fuel.",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds for live fetches.",
    )
    return parser.parse_args()


def default_target_date(now: datetime | None = None) -> date:
    current = now.astimezone(BERLIN) if now else datetime.now(BERLIN)
    return current.date() - timedelta(days=1)


def parse_target_date(raw: str | None) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date() if raw else default_target_date()


def dated_dir(base: Path, target_day: date) -> Path:
    return base / "data2" / f"{target_day:%Y}" / f"{target_day:%m}" / f"{target_day:%d}"


def station_json_path(base: Path, station_uuid: str, fuel: str) -> Path:
    return base / "data2" / Path(*station_uuid.split("-")) / f"{fuel}.json"


def station_json_url(origin: str, station_uuid: str, fuel: str) -> str:
    return f"{origin.rstrip('/')}/data2/{'/'.join(station_uuid.split('-'))}/{fuel}.json"


def management_json_url(origin: str, target_day: date) -> str:
    return (
        f"{origin.rstrip('/')}/data2/{target_day:%Y}/{target_day:%m}/{target_day:%d}/"
        "management_boxplots.json"
    )


def noon_csv_url(origin: str, target_day: date) -> str:
    return f"{origin.rstrip('/')}/data2/{target_day:%Y}/{target_day:%m}/{target_day:%d}/noon.csv"


def management_page_url(origin: str, target_day: date) -> str:
    return f"{origin.rstrip('/')}/management.html?date={target_day:%Y-%m-%d}"


def _request_session() -> requests.Session:
    session = requests.Session()
    session.verify = certifi.where()
    return session


def fetch_text(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_json(session: requests.Session, url: str, timeout: int) -> object:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_csv_text(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(text.splitlines()))


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(BERLIN)


def iter_delayed_noon_references(
    rows: Iterable[dict[str, str]],
    fuels: Iterable[str] = FUELS,
) -> list[NoonReference]:
    references: list[NoonReference] = []
    for row in rows:
        station_uuid = str(row.get("station_uuid") or "").strip()
        if not station_uuid:
            continue
        for fuel in fuels:
            price_raw = row.get(fuel)
            method = str(row.get(f"{fuel}_selection_method") or "").strip()
            ts_raw = str(row.get(f"{fuel}_last_update") or row.get("last_update") or "").strip()
            if not price_raw or not ts_raw or method != "increase":
                continue
            try:
                price = float(price_raw)
                last_update = _parse_iso_timestamp(ts_raw)
            except ValueError:
                continue
            if price <= 0:
                continue
            if last_update.timetz().replace(tzinfo=None) <= NOON:
                continue
            references.append(
                NoonReference(
                    station_uuid=station_uuid,
                    fuel=fuel,
                    price=price,
                    last_update=last_update,
                    selection_method=method,
                )
            )
    references.sort(
        key=lambda item: (
            item.fuel,
            -item.delay_minutes,
            item.station_uuid,
        )
    )
    return references


def sample_references(
    references: Iterable[NoonReference],
    per_fuel: int,
) -> list[NoonReference]:
    selected: list[NoonReference] = []
    counts = {fuel: 0 for fuel in FUELS}
    for reference in references:
        if counts.get(reference.fuel, 0) >= per_fuel:
            continue
        selected.append(reference)
        counts[reference.fuel] = counts.get(reference.fuel, 0) + 1
    return selected


def cycle_anchor_price(payload: dict[str, object]) -> float:
    cycle_rows = payload.get("cycle_hourly")
    if not isinstance(cycle_rows, list) or not cycle_rows:
        raise VerificationError("Missing cycle_hourly data.")
    first_row = cycle_rows[0]
    if not isinstance(first_row, dict):
        raise VerificationError("Invalid first cycle row.")
    price = first_row.get("price_median")
    if price is None:
        raise VerificationError("First cycle row is missing price_median.")
    return float(price)


def cycle_anchor_delta(payload: dict[str, object]) -> float:
    cycle_rows = payload.get("cycle_hourly")
    if not isinstance(cycle_rows, list) or not cycle_rows:
        raise VerificationError("Missing cycle_hourly data.")
    first_row = cycle_rows[0]
    if not isinstance(first_row, dict):
        raise VerificationError("Invalid first cycle row.")
    delta = first_row.get("delta_median")
    if delta is None:
        raise VerificationError("First cycle row is missing delta_median.")
    return float(delta)


def verify_station_payload(
    reference: NoonReference,
    payload: dict[str, object],
    source_label: str,
) -> None:
    try:
        anchor_price = cycle_anchor_price(payload)
        anchor_delta = cycle_anchor_delta(payload)
    except VerificationError as exc:
        raise VerificationError(
            f"{source_label} {reference.fuel} {reference.station_uuid}: {exc}"
        ) from exc
    if abs(anchor_price - reference.price) > PRICE_TOLERANCE:
        raise VerificationError(
            f"{source_label} {reference.fuel} {reference.station_uuid} anchor mismatch: "
            f"cycle price {anchor_price:.3f} vs noon reference {reference.price:.3f} "
            f"at {reference.last_update.isoformat(timespec='seconds')}"
        )
    if abs(anchor_delta) > PRICE_TOLERANCE:
        raise VerificationError(
            f"{source_label} {reference.fuel} {reference.station_uuid} first cycle delta is "
            f"{anchor_delta:.3f} instead of 0.000"
        )


def verify_local_delayed_references(
    repo_root: Path,
    references: list[NoonReference],
) -> dict[str, int]:
    checked = 0
    per_fuel = {fuel: 0 for fuel in FUELS}
    failures: list[str] = []
    for reference in references:
        payload_path = station_json_path(repo_root, reference.station_uuid, reference.fuel)
        if not payload_path.exists():
            failures.append(f"Missing local station payload: {payload_path}")
            continue
        payload = load_json(payload_path)
        try:
            verify_station_payload(reference, payload, "local")
        except VerificationError as exc:
            failures.append(str(exc))
            continue
        checked += 1
        per_fuel[reference.fuel] += 1
    if failures:
        preview = "\n".join(f"- {message}" for message in failures[:10])
        suffix = "" if len(failures) <= 10 else f"\n- ... {len(failures) - 10} more"
        raise VerificationError(
            f"Local delayed-reference verification failed for {len(failures)} station/fuel payloads:\n"
            f"{preview}{suffix}"
        )
    return {"checked": checked, **{f"{fuel}_checked": count for fuel, count in per_fuel.items()}}


def verify_live_samples(
    session: requests.Session,
    origin: str,
    references: list[NoonReference],
    timeout: int,
    repo_root: Path | None = None,
) -> dict[str, int]:
    checked = 0
    per_fuel = {fuel: 0 for fuel in FUELS}
    failures: list[str] = []
    for reference in references:
        url = station_json_url(origin, reference.station_uuid, reference.fuel)
        payload = fetch_json(session, url, timeout)
        if not isinstance(payload, dict):
            failures.append(f"Live payload at {url} is not a JSON object.")
            continue
        try:
            verify_station_payload(reference, payload, "live")
        except VerificationError as exc:
            failures.append(str(exc))
            continue
        if repo_root is not None:
            local_path = station_json_path(repo_root, reference.station_uuid, reference.fuel)
            local_payload = load_json(local_path)
            if payload != local_payload:
                failures.append(f"Live payload {url} does not match the checked-in file {local_path}")
                continue
        checked += 1
        per_fuel[reference.fuel] += 1
    if failures:
        preview = "\n".join(f"- {message}" for message in failures[:10])
        suffix = "" if len(failures) <= 10 else f"\n- ... {len(failures) - 10} more"
        raise VerificationError(
            f"Live delayed-reference verification failed for {len(failures)} sampled payloads:\n"
            f"{preview}{suffix}"
        )
    return {"checked": checked, **{f"{fuel}_checked": count for fuel, count in per_fuel.items()}}


def wait_for_live_artifacts(
    session: requests.Session,
    origin: str,
    target_day: date,
    expected_management: object,
    expected_noon_rows: list[dict[str, str]],
    timeout_seconds: int,
    poll_interval_seconds: int,
    timeout: int,
) -> None:
    management_url = management_json_url(origin, target_day)
    noon_url = noon_csv_url(origin, target_day)
    deadline = time.monotonic() + max(0, timeout_seconds)
    last_error: str | None = None

    while True:
        try:
            live_management = fetch_json(session, management_url, timeout)
            live_noon_rows = parse_csv_text(fetch_text(session, noon_url, timeout))
            if live_management == expected_management and live_noon_rows == expected_noon_rows:
                return
            last_error = "live artifacts do not match checked-in data yet"
        except Exception as exc:  # pragma: no cover - exercised in integration
            last_error = str(exc)

        if time.monotonic() >= deadline:
            raise VerificationError(
                "Timed out waiting for live artifacts to match checked-in data: "
                f"{last_error or 'unknown mismatch'}"
            )
        time.sleep(max(1, poll_interval_seconds))


def verify_management_page_smoke(
    session: requests.Session,
    origin: str,
    target_day: date,
    timeout: int,
) -> None:
    html = fetch_text(session, management_page_url(origin, target_day), timeout)
    if "Preisänderung zur 12:00-Referenz" not in html:
        raise VerificationError("Live management.html is missing the noon-reference chart heading.")


def print_summary(title: str, values: dict[str, int]) -> None:
    print(title)
    for key, value in values.items():
        print(f"  {key}: {value}")


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    target_day = parse_target_date(args.target_date)
    target_dir = dated_dir(repo_root, target_day)
    local_management_path = target_dir / "management_boxplots.json"
    local_noon_path = target_dir / "noon.csv"

    if not local_management_path.exists():
        raise VerificationError(f"Missing local management summary: {local_management_path}")
    if not local_noon_path.exists():
        raise VerificationError(f"Missing local noon snapshot: {local_noon_path}")

    local_management = load_json(local_management_path)
    local_noon_rows = load_csv_rows(local_noon_path)
    delayed_references = iter_delayed_noon_references(local_noon_rows)

    if delayed_references:
        local_summary = verify_local_delayed_references(repo_root, delayed_references)
    else:
        local_summary = {"checked": 0, **{f"{fuel}_checked": 0 for fuel in FUELS}}
    print_summary(f"Local delayed-reference verification for {target_day:%Y-%m-%d}", local_summary)

    session = _request_session()
    wait_for_live_artifacts(
        session,
        args.origin,
        target_day,
        local_management,
        local_noon_rows,
        timeout_seconds=args.wait_live_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        timeout=args.request_timeout,
    )
    verify_management_page_smoke(session, args.origin, target_day, args.request_timeout)

    live_sample_references = sample_references(delayed_references, args.live_samples_per_fuel)
    if live_sample_references:
        live_summary = verify_live_samples(
            session,
            args.origin,
            live_sample_references,
            args.request_timeout,
            repo_root=repo_root,
        )
    else:
        live_summary = {"checked": 0, **{f"{fuel}_checked": 0 for fuel in FUELS}}
    print_summary(f"Live delayed-reference verification for {target_day:%Y-%m-%d}", live_summary)
    print(
        "Verified live noon-reference artifacts:",
        management_json_url(args.origin, target_day),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(str(exc))
        raise SystemExit(1)
