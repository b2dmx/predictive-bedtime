"""Coordinator: watches the sleep signals, reads the calendars, remembers recent nights, runs the model.

Nothing polls. Work happens when something changes (a sleep signal, presence, a calendar entity), at
the moments a prediction says something is due (wind-down, bedtime, wake), and at most a
day after the calendars were last read.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_HOME
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BACKFILL_DAYS,
    CALENDAR_MAX_AGE,
    CONF_ASLEEP,
    CONF_ASLEEP_STATES,
    CONF_CALENDARS,
    CONF_FREE_BEDTIME,
    CONF_IN_BED,
    CONF_MIN_SLEEP,
    CONF_PERSON,
    CONF_PREP,
    CONF_RETENTION,
    CONF_SETTLE,
    CONF_TARGET_SLEEP,
    CONF_UNWIND,
    CONF_WAKE_GAP,
    CONF_WIND_DOWN,
    DEFAULT_OPTIONS,
    DOMAIN,
    HALF_LIFE_FRACTION,
    SHIFT_RETENTION,
    STORAGE_VERSION,
)
from .model import (
    DEFAULT_ASLEEP_VALUES,
    Episode,
    Params,
    Prediction,
    Shift,
    SleepDetector,
    combine,
    make_episode,
    predict,
)

_LOGGER = logging.getLogger(__name__)

type BedtimeConfigEntry = ConfigEntry[BedtimeCoordinator]


def params_from_options(options: dict[str, Any]) -> Params:
    o = {**DEFAULT_OPTIONS, **options}
    hour, minute = (int(x) for x in str(o[CONF_FREE_BEDTIME]).split(":")[:2])
    return Params(
        target_sleep=timedelta(hours=float(o[CONF_TARGET_SLEEP])),
        prep=timedelta(minutes=float(o[CONF_PREP])),
        unwind=timedelta(minutes=float(o[CONF_UNWIND])),
        wind_down=timedelta(minutes=float(o[CONF_WIND_DOWN])),
        free_bedtime=time(hour, minute),
        settle=timedelta(minutes=float(o[CONF_SETTLE])),
        wake_gap=timedelta(minutes=float(o[CONF_WAKE_GAP])),
        min_sleep=timedelta(hours=float(o[CONF_MIN_SLEEP])),
        half_life_days=float(o[CONF_RETENTION]) * HALF_LIFE_FRACTION,
    )


class BedtimeCoordinator(DataUpdateCoordinator[Prediction]):
    """One per person."""

    config_entry: BedtimeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BedtimeConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {entry.title}", update_interval=None
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.episodes: list[Episode] = []
        self.shifts: list[Shift] = []
        self.detector = SleepDetector()
        self.last_wake: datetime | None = None
        # Once wind-down starts the prediction is held until the expected wake.
        self.committed: Prediction | None = None
        self._last_fetch: datetime | None = None
        self._calendars_changed = True
        self._unsub_detector: Callable[[], None] | None = None
        self._unsub_next: Callable[[], None] | None = None
        self._needs_backfill = False
        # Combined sleep reading and when it last flipped.
        self._on: bool | None = None
        self._since: datetime | None = None

    # --- configuration -------------------------------------------------

    def _conf(self, key: str) -> Any:
        return self.config_entry.options.get(key, self.config_entry.data.get(key))

    @property
    def person(self) -> str:
        return self.config_entry.data[CONF_PERSON]

    @property
    def in_bed_signals(self) -> list[str]:
        return list(self._conf(CONF_IN_BED) or [])

    @property
    def asleep_signals(self) -> list[str]:
        return list(self._conf(CONF_ASLEEP) or [])

    @property
    def asleep_values(self) -> list[str]:
        return list(self._conf(CONF_ASLEEP_STATES) or DEFAULT_ASLEEP_VALUES)

    @property
    def signals(self) -> list[str]:
        return [*self.in_bed_signals, *self.asleep_signals]

    def _reading(self, state_of: Callable[[str], str | None]) -> tuple[bool, bool, bool]:
        person = state_of(self.person)
        return combine(
            (state_of(e) for e in self.asleep_signals),
            (state_of(e) for e in self.in_bed_signals),
            person is None or person == STATE_HOME,
            self.asleep_values,
        )

    @property
    def calendars(self) -> list[str]:
        return list(self._conf(CONF_CALENDARS))

    @property
    def params(self) -> Params:
        return params_from_options(dict(self.config_entry.options))

    @property
    def retention(self) -> timedelta:
        return timedelta(days=float(self.config_entry.options.get(CONF_RETENTION, DEFAULT_OPTIONS[CONF_RETENTION])))

    # --- lifecycle -----------------------------------------------------

    async def _async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored is None:
            # Reading history can take minutes on slow hardware; do it after setup.
            self._needs_backfill = True
            return
        self.episodes = [Episode.from_dict(e) for e in stored.get("episodes", [])]
        self._forget_old_nights()
        self.shifts = [
            Shift(dt_util.parse_datetime(s), dt_util.parse_datetime(e))
            for s, e in stored.get("shifts", [])
        ]
        self.detector.restore(stored.get("detector", {}))
        self.last_wake = dt_util.parse_datetime(stored["last_wake"]) if stored.get("last_wake") else None
        if stored.get("committed"):
            self.committed = Prediction.from_dict(stored["committed"])

    @callback
    def async_start_tracking(self) -> None:
        entry = self.config_entry
        entry.async_on_unload(
            async_track_state_change_event(
                self.hass, [*self.signals, self.person], self._async_on_signal
            )
        )
        entry.async_on_unload(
            async_track_state_change_event(self.hass, self.calendars, self._async_on_calendar)
        )
        entry.async_on_unload(self._cancel_timers)
        self._async_evaluate()
        if self._needs_backfill:
            self._needs_backfill = False
            entry.async_create_background_task(
                self.hass, self._async_run_backfill(), f"{DOMAIN} backfill {entry.title}"
            )

    async def _async_run_backfill(self) -> None:
        await self._async_backfill()
        self._save()
        await self.async_request_refresh()

    @callback
    def _cancel_timers(self) -> None:
        for unsub in (self._unsub_detector, self._unsub_next):
            if unsub:
                unsub()
        self._unsub_detector = self._unsub_next = None

    def _save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 10)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "episodes": [e.as_dict() for e in self.episodes],
            "shifts": [[s.start.isoformat(), s.end.isoformat()] for s in self.shifts],
            "detector": self.detector.as_dict(),
            "last_wake": self.last_wake.isoformat() if self.last_wake else None,
            "committed": self.committed.as_dict() if self.committed else None,
        }

    def _forget_old_nights(self) -> None:
        cutoff = dt_util.utcnow() - self.retention
        self.episodes = [e for e in self.episodes if e.onset >= cutoff]

    # --- sleep detection -----------------------------------------------

    @callback
    def _async_on_signal(self, event: Event[EventStateChangedData]) -> None:
        self._async_evaluate()

    @callback
    def _async_evaluate(self, *_: Any) -> None:
        if self._unsub_detector:
            self._unsub_detector()
            self._unsub_detector = None

        def state_of(entity_id: str) -> str | None:
            state = self.hass.states.get(entity_id)
            return state.state if state else None

        on, sure, known = self._reading(state_of)
        # Signals dropping off WiFi say nothing about whether anyone is asleep.
        if not known:
            return
        now = dt_util.utcnow()
        if self._on is None:
            self._on, self._since = on, self._initial_since(on)
        elif on != self._on:
            self._on, self._since = on, now
        since = self._since or now
        p = self.params

        was_asleep = self.detector.asleep
        finished = self.detector.step(now, on, since, sure, p)
        if finished:
            self._record(*finished)
        if finished or was_asleep != self.detector.asleep:
            self._save()
            self.async_update_listeners()
            self.hass.async_create_task(self.async_request_refresh())

        # Check again the moment a pending stretch would cross its threshold.
        due: datetime | None = None
        if not self.detector.asleep and on and not sure:
            due = since + p.settle
        elif self.detector.asleep and not on:
            due = since + p.wake_gap
        if due:
            self._unsub_detector = async_track_point_in_utc_time(
                self.hass, self._async_evaluate, max(due, now) + timedelta(seconds=1)
            )

    def _initial_since(self, on: bool) -> datetime:
        """Best guess at when the current reading began, from the signals' own history."""
        changed = [
            state.last_changed
            for entity_id in self.signals
            if (state := self.hass.states.get(entity_id)) is not None
        ]
        if not changed:
            return dt_util.utcnow()
        return min(changed) if on else max(changed)

    def _record(self, onset: datetime, wake: datetime, source: str) -> None:
        self.episodes = [*self.episodes, make_episode(onset, wake, self.shifts, source)]
        self._forget_old_nights()
        self.last_wake = wake
        self.committed = None
        _LOGGER.debug("%s: recorded sleep %s -> %s", self.config_entry.title, onset, wake)

    # --- calendars -----------------------------------------------------

    @callback
    def _async_on_calendar(self, event: Event[EventStateChangedData]) -> None:
        # The entity changes when a shift starts or ends, or its upcoming shift is edited.
        self._calendars_changed = True
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_fetch_shifts(self, start: datetime, end: datetime) -> None:
        response = await self.hass.services.async_call(
            "calendar",
            "get_events",
            {ATTR_ENTITY_ID: self.calendars, "start_date_time": start, "end_date_time": end},
            blocking=True,
            return_response=True,
        )
        fresh: set[Shift] = set()
        for calendar in (response or {}).values():
            for event in calendar.get("events", []):
                # All-day entries (holidays, notes) are not shifts.
                if "T" not in str(event.get("start")):
                    continue
                s = dt_util.parse_datetime(event["start"])
                e = dt_util.parse_datetime(event["end"])
                if s and e and e > s:
                    fresh.add(Shift(dt_util.as_utc(s), dt_util.as_utc(e)))
        # The calendar is authoritative inside the window it was asked about, so edited
        # and cancelled shifts disappear here. Older shifts are kept to describe past nights.
        keep = [s for s in self.shifts if s.end <= start or s.start >= end]
        cutoff = dt_util.utcnow() - SHIFT_RETENTION
        self.shifts = sorted(
            (s for s in {*keep, *fresh} if s.end >= cutoff), key=lambda s: s.start
        )

    # --- prediction ----------------------------------------------------

    async def _async_update_data(self) -> Prediction:
        now = dt_util.utcnow()
        if (
            self._calendars_changed
            or self._last_fetch is None
            or now - self._last_fetch >= CALENDAR_MAX_AGE
        ):
            try:
                await self._async_fetch_shifts(now - timedelta(days=2), now + timedelta(days=3))
                self._last_fetch = now
                self._calendars_changed = False
                self._save()
            except HomeAssistantError as err:
                if not self.shifts:
                    raise UpdateFailed(f"Could not read calendars: {err}") from err
                _LOGGER.warning("Could not read calendars, using cached shifts: %s", err)

        prediction = await self._async_predict(now)
        self._schedule_next(prediction, now)
        return prediction

    async def _async_predict(self, now: datetime) -> Prediction:
        if self.committed and now < self.committed.wake:
            return self.committed
        self.committed = None

        p = self.params
        start = now
        if self.detector.asleep and self.detector.onset:
            start = max(start, self.detector.onset + p.target_sleep + timedelta(hours=2))
        elif self.last_wake:
            start = max(start, self.last_wake + timedelta(hours=2))

        prediction = await self.hass.async_add_executor_job(
            predict,
            now,
            start,
            tuple(self.shifts),
            tuple(self.episodes),
            p,
            dt_util.get_default_time_zone(),
        )
        if not self.detector.asleep and prediction.bedtime - now <= p.wind_down:
            self.committed = prediction
            self._save()
        return prediction

    def _schedule_next(self, prediction: Prediction, now: datetime) -> None:
        if self._unsub_next:
            self._unsub_next()
        moments = [
            prediction.bedtime - self.params.wind_down,
            prediction.bedtime,
            prediction.wake,
            (self._last_fetch or now) + CALENDAR_MAX_AGE,
        ]
        due = min(m for m in moments if m > now) if any(m > now for m in moments) else now + CALENDAR_MAX_AGE
        self._unsub_next = async_track_point_in_utc_time(
            self.hass, self._async_on_due, due + timedelta(seconds=1)
        )

    @callback
    def _async_on_due(self, _now: datetime) -> None:
        self._unsub_next = None
        self.hass.async_create_task(self.async_request_refresh())

    # --- what the entities show ----------------------------------------

    @property
    def expected_asleep(self) -> bool:
        """In the predicted sleep window, or already settled in bed."""
        if self.detector.asleep:
            return True
        if self.data is None:
            return False
        return self.data.bedtime <= dt_util.utcnow() < self.data.wake

    @property
    def winding_down(self) -> bool:
        if self.detector.asleep or self.data is None:
            return False
        now = dt_util.utcnow()
        return self.data.bedtime - self.params.wind_down <= now < self.data.bedtime

    # --- one-off head start from the recorder --------------------------

    async def _async_backfill(self) -> None:
        now = dt_util.utcnow()
        start = now - timedelta(days=BACKFILL_DAYS)
        try:
            await self._async_fetch_shifts(start - timedelta(days=2), now + timedelta(days=3))
            self._last_fetch = now
            self._calendars_changed = False
        except HomeAssistantError as err:
            _LOGGER.warning("Backfill could not read calendars: %s", err)

        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415

            history = await get_instance(self.hass).async_add_executor_job(
                self._read_history, start, now
            )
        except Exception:  # noqa: BLE001 - best effort; learning simply starts from today
            _LOGGER.warning("Backfill could not read the recorder", exc_info=True)
            self._announce_backfill()
            return

        events = sorted(
            (state.last_changed, entity_id, state.state)
            for entity_id, states in history.items()
            for state in states
        )
        p = self.params
        detector = SleepDetector()
        current: dict[str, str] = {}
        on: bool | None = None
        since = start
        sure = False
        for when, entity_id, state in events:
            if on is not None and (done := detector.step(when, on, since, sure, p)):
                self._record(*done)
            current[entity_id] = state
            new_on, sure, known = self._reading(current.get)
            if known and new_on != on:
                on, since = new_on, when
        if on is not None and (done := detector.step(now, on, since, sure, p)):
            self._record(*done)
        self.episodes.sort(key=lambda e: e.onset)
        # Live detection has been running meanwhile; only adopt the replayed state if idle.
        if not self.detector.asleep:
            self.detector = detector
        self._announce_backfill()

    def _announce_backfill(self) -> None:
        count = len(self.episodes)
        name = self.config_entry.title
        if count:
            found = f"Found **{count} nights** of sleep for {name} in recent history and learned from them."
        else:
            found = f"No past nights were found for {name}, so learning starts tonight."
        persistent_notification.async_create(
            self.hass,
            f"{found}\n\nUntil more nights are learned, predictions follow the work schedule "
            "and the starting habits. Watch **Confidence** rise as it learns.",
            title="Predictive Bedtime is set up",
            notification_id=f"{DOMAIN}_{self.config_entry.entry_id}_setup",
        )

    def _read_history(self, start: datetime, end: datetime) -> dict[str, list[State]]:
        from homeassistant.components.recorder import history  # noqa: PLC0415

        out: dict[str, list[State]] = {}
        for entity_id in (*self.signals, self.person):
            out[entity_id] = history.state_changes_during_period(
                self.hass,
                start,
                end,
                entity_id=entity_id,
                no_attributes=True,
                include_start_time_state=True,
            ).get(entity_id, [])
        return out
