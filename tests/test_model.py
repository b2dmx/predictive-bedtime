"""Model tests. Run: py -3 tests/test_model.py (or pytest)."""
from __future__ import annotations

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

# Load model.py directly so Home Assistant is not needed.
_path = Path(__file__).parents[1] / "custom_components" / "predictive_bedtime" / "model.py"
_spec = importlib.util.spec_from_file_location("model", _path)
model = importlib.util.module_from_spec(_spec)
sys.modules["model"] = model
_spec.loader.exec_module(model)

TZ = ZoneInfo("America/New_York")
P = model.Params()


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ).astimezone(model.UTC)


def shift(day: int, start: int, end: int) -> model.Shift:
    s = at(day, start)
    e = at(day, end) if end > start else at(day + 1, end)
    return model.Shift(s, e)


# A backward-rotating week: evenings drifting earlier, then a quick turn to days.
ROTATION = [shift(24, 15, 23), shift(25, 13, 21), shift(26, 13, 21), shift(27, 7, 15), shift(28, 6, 14)]


def local(t: datetime) -> str:
    return t.astimezone(TZ).strftime("%d %H:%M")


def test_quick_turn_is_floored_by_unwind():
    # 13-21 then 07-15: a full night is impossible, so bed right after unwinding.
    assert local(model.schedule_bedtime(at(26, 21, 30), ROTATION, P, TZ)) == "26 22:30"


def test_late_shift_pushes_bedtime_past_unwind():
    assert local(model.schedule_bedtime(at(24, 23, 10), ROTATION, P, TZ)) == "25 00:30"


def test_early_shift_pulls_bedtime_earlier():
    # 07-15 then 06-14: latest bedtime for 7.5h + 75min prep is 21:15.
    assert local(model.schedule_bedtime(at(27, 16), ROTATION, P, TZ)) == "27 21:15"


def test_free_day_uses_usual_bedtime():
    assert local(model.schedule_bedtime(at(20, 12), [], P, TZ)) == "20 23:30"


def test_no_history_follows_schedule_with_zero_confidence():
    pred = model.predict(at(27, 16), at(27, 16), ROTATION, [], P, TZ)
    assert local(pred.bedtime) == "27 21:10" or local(pred.bedtime) == "27 21:20"
    assert pred.confidence == 0
    # 7.5 h after 21:10, which is just inside the 04:45 deadline for a 06:00 shift.
    assert local(pred.wake) == "28 04:40"


def test_learns_later_habit_before_early_shifts():
    # Schedule says 21:15 before a 06:00 shift; this person actually goes down at 22:30.
    shifts, episodes = [], []
    for d in range(60):
        base = datetime(2026, 6, 1, tzinfo=TZ) + timedelta(days=d)
        s = model.Shift(base.replace(hour=6).astimezone(model.UTC), base.replace(hour=14).astimezone(model.UTC))
        shifts.append(s)
        onset = (base - timedelta(days=1)).replace(hour=22, minute=30).astimezone(model.UTC)
        episodes.append((onset, s.start - timedelta(hours=1)))
    episodes = [model.make_episode(o, w, shifts) for o, w in episodes]
    now = shifts[-1].end + timedelta(hours=2)
    upcoming = shifts + [model.Shift(shifts[-1].start + timedelta(days=1), shifts[-1].end + timedelta(days=1))]
    pred = model.predict(now, now, upcoming, episodes, P, TZ)
    assert local(pred.schedule_bedtime).endswith("21:15")
    assert pred.bedtime.astimezone(TZ).strftime("%H:%M") in ("22:20", "22:30", "22:40")
    assert pred.confidence > 0.9


def test_interpolates_unseen_shift_time():
    # Learned 22:30 before 06:00 shifts only; a never-seen 08:00 shift should land later.
    shifts, episodes = [], []
    for d in range(30):
        base = datetime(2026, 6, 1, tzinfo=TZ) + timedelta(days=d)
        s = model.Shift(base.replace(hour=6).astimezone(model.UTC), base.replace(hour=14).astimezone(model.UTC))
        shifts.append(s)
        onset = (base - timedelta(days=1)).replace(hour=22, minute=30).astimezone(model.UTC)
        episodes.append((onset, s.start - timedelta(hours=1)))
    episodes = [model.make_episode(o, w, shifts) for o, w in episodes]
    now = shifts[-1].end + timedelta(hours=2)
    nxt = (datetime(2026, 7, 1, tzinfo=TZ)).replace(hour=8).astimezone(model.UTC)
    pred = model.predict(now, now, shifts + [model.Shift(nxt, nxt + timedelta(hours=8))], episodes, P, TZ)
    h = pred.bedtime.astimezone(TZ)
    assert h.hour * 60 + h.minute > 22 * 60 + 30, local(pred.bedtime)


def test_detector_debounces_and_drops_naps():
    det = model.SleepDetector()
    home_since = at(1, 0)
    # In bed 22:00, briefly out 22:05 (not settled), back 22:10.
    assert det.step(at(26, 22, 5), True, at(26, 22), True, home_since, P) is None
    assert not det.asleep
    assert det.step(at(26, 22, 40), True, at(26, 22, 10), True, home_since, P) is None
    assert det.asleep and local(det.onset) == "26 22:10"
    # Up for 10 minutes at 02:00: still asleep.
    assert det.step(at(27, 2, 10), False, at(27, 2), True, home_since, P) is None
    assert det.asleep
    # Out of bed at 06:00 for good.
    done = det.step(at(27, 6, 45), False, at(27, 6), True, home_since, P)
    assert done and local(done[0]) == "26 22:10" and local(done[1]) == "27 06:00"
    # A 1-hour nap is not a night.
    det.step(at(27, 16, 30), True, at(27, 16), True, home_since, P)
    assert det.step(at(27, 17, 45), False, at(27, 17), True, home_since, P) is None


def test_detector_needs_person_home():
    det = model.SleepDetector()
    det.step(at(26, 23), True, at(26, 22), False, at(26, 20), P)
    assert not det.asleep


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as err:
                failures += 1
                print(f"FAIL {name}: {err}")
    sys.exit(1 if failures else 0)

