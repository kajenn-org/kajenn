# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Schedule parsing and next-run computation — the three task kinds.

- ``every``: ``"30s" | "15m" | "2h" | "1d"`` — next = after + interval.
- ``cron``: classic 5-field string (min hour dom month dow) with ``* , - /``,
  parsed in-house (~60 lines; croniter stays out — zero dependencies).
  System cron semantics: dow accepts 0-7 (0 and 7 = Sunday); when BOTH dom
  and dow are restricted a date matches if EITHER matches; evaluation is in
  LOCAL time, like system cron.
- ``at``: a list of ISO timestamps (naive = local time), each fires once;
  an exhausted list yields no next run (a DERIVED state — the record stays).

All computed instants are POSIX epoch seconds (the store's ``*_ts`` fields).
No replay: the next run is always computed forward from ``after`` (now),
never backfilled for downtime.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

__all__ = ["TaskCadence", "EverySpec", "CronSpec", "AtSpec"]

EVERY_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
CRON_SEARCH_DAYS = 366 * 4  # a valid spec matches well within 4 years


class EverySpec:
    """A fixed interval; every run is the previous instant plus ``seconds``."""

    def __init__(self, spec: Any) -> None:
        """Parse ``"30s" | "15m" | "2h" | "1d"`` into ``seconds``.

        Raises:
            ValueError: On a malformed spec, an unknown unit or a zero interval.
        """
        text = str(spec).strip()
        number, unit = text[:-1], text[-1:]
        if not number.isdigit() or unit not in EVERY_UNITS:
            raise ValueError(f"invalid every spec: {spec!r} (want <number><s|m|h|d>)")
        self.seconds = int(number) * EVERY_UNITS[unit]
        if self.seconds <= 0:
            raise ValueError(f"invalid every spec: {spec!r} (zero interval)")

    def get_next_run(self, after_ts: float) -> float | None:
        """The instant one interval after ``after_ts`` (epoch seconds)."""
        return after_ts + self.seconds


class AtSpec:
    """A list of one-shot instants; an exhausted list has no next run."""

    def __init__(self, spec: Any) -> None:
        """Parse a list of ISO timestamps into sorted ``instants`` (naive = local).

        Raises:
            ValueError: If ``spec`` is not a list or an entry is not ISO-parseable.
        """
        if not isinstance(spec, list):
            raise ValueError("invalid at spec: want a list of ISO timestamps")
        instants = []
        for entry in spec:
            try:
                instants.append(datetime.fromisoformat(str(entry)).timestamp())
            except ValueError as exc:
                raise ValueError(f"invalid at timestamp: {entry!r}") from exc
        self.instants = sorted(instants)

    def get_next_run(self, after_ts: float) -> float | None:
        """The first instant strictly after ``after_ts``, or None once past them all."""
        for instant in self.instants:
            if instant > after_ts:
                return instant
        return None


class CronSpec:
    """A parsed 5-field cron string; ``get_next_run`` walks to the next match."""

    def __init__(self, spec: str) -> None:
        """Parse ``"min hour dom month dow"`` into value sets.

        Raises:
            ValueError: On a wrong field count or a malformed/out-of-range field.
        """
        self.spec = spec
        fields = str(spec).split()
        if len(fields) != 5:
            raise ValueError(f"invalid cron spec: {spec!r} (want 5 fields)")
        self.minutes = self._parse_field(fields[0], 0, 59)
        self.hours = self._parse_field(fields[1], 0, 23)
        self.days = self._parse_field(fields[2], 1, 31)
        self.months = self._parse_field(fields[3], 1, 12)
        # dow: 0-7 on the wire, 7 folds onto Sunday=0
        self.weekdays = {v % 7 for v in self._parse_field(fields[4], 0, 7)}
        self.dom_restricted = fields[2] != "*"
        self.dow_restricted = fields[4] != "*"

    def _parse_field(self, field: str, lo: int, hi: int) -> set[int]:
        """One cron field -> the set of matching values (``* , - /``)."""
        values: set[int] = set()
        for part in field.split(","):
            step = 1
            body = part
            if "/" in part:
                body, step_text = part.split("/", 1)
                if not step_text.isdigit() or int(step_text) < 1:
                    raise ValueError(f"invalid cron step in {self.spec!r}: {part!r}")
                step = int(step_text)
            if body == "*":
                start, end = lo, hi
            elif "-" in body:
                a, _, b = body.partition("-")
                if not a.isdigit() or not b.isdigit():
                    raise ValueError(f"invalid cron range in {self.spec!r}: {part!r}")
                start, end = int(a), int(b)
            elif body.isdigit():
                # a bare value; with a step it opens a range to the top (vixie)
                start = int(body)
                end = hi if "/" in part else start
            else:
                raise ValueError(f"invalid cron field in {self.spec!r}: {part!r}")
            if not (lo <= start <= hi and lo <= end <= hi and start <= end):
                raise ValueError(f"cron value out of range in {self.spec!r}: {part!r}")
            values.update(range(start, end + 1, step))
        return values

    def get_next_run(self, after_ts: float) -> float | None:
        """The first matching instant strictly after ``after_ts`` (epoch seconds).

        Raises:
            ValueError: If nothing matches within ~4 years (an impossible date,
                e.g. ``"0 0 31 2 *"``).
        """
        candidate = datetime.fromtimestamp(after_ts).replace(second=0, microsecond=0)
        candidate += timedelta(minutes=1)
        for _ in range(CRON_SEARCH_DAYS):
            if candidate.month in self.months and self._day_matches(candidate):
                matched = self._first_time_from(candidate)
                if matched is not None:
                    return matched.timestamp()
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
        raise ValueError(f"cron spec {self.spec!r}: no occurrence within 4 years")

    def _day_matches(self, day: datetime) -> bool:
        """System cron date rule: dom OR dow when both are restricted."""
        dom_ok = day.day in self.days
        dow_ok = (day.weekday() + 1) % 7 in self.weekdays  # python Mon=0 -> cron Sun=0
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok if self.dom_restricted else dow_ok if self.dow_restricted else True

    def _first_time_from(self, candidate: datetime) -> datetime | None:
        """First (hour, minute) match within candidate's day, from its time on."""
        for hour in sorted(self.hours):
            if hour < candidate.hour:
                continue
            for minute in sorted(self.minutes):
                if hour == candidate.hour and minute < candidate.minute:
                    continue
                return candidate.replace(hour=hour, minute=minute)
        return None


class TaskCadence:
    """The cadence of a task record: its ``kind`` picks the spec that computes."""

    spec_classes: dict[str, type[EverySpec | CronSpec | AtSpec]] = {
        "every": EverySpec,
        "cron": CronSpec,
        "at": AtSpec,
    }

    def __init__(self, kind: str, spec: Any) -> None:
        """Build the spec of ``kind`` — the parse happens here, once.

        Args:
            kind: ``"every" | "cron" | "at"``.
            spec: The kind's spec — interval string, cron string, or ISO list.

        Raises:
            ValueError: On an unknown kind or a malformed spec.
        """
        spec_class = self.spec_classes.get(kind)
        if spec_class is None:
            raise ValueError(f"unknown schedule kind: {kind!r}")
        self.kind = kind
        self.spec = spec_class(spec)

    def get_next_run(self, after_ts: float) -> float | None:
        """The next due instant, or None (an exhausted ``at`` list)."""
        return self.spec.get_next_run(after_ts)
