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
    # In bed 22:00, briefly out 22:05 (not settled), back 22:10.
    assert det.step(at(26, 22, 5), True, at(26, 22), False, P) is None
    assert not det.asleep
    assert det.step(at(26, 22, 40), True, at(26, 22, 10), False, P) is None
    assert det.asleep and local(det.onset) == "26 22:10"
    # Up for 10 minutes at 02:00: still asleep.
    assert det.step(at(27, 2, 10), False, at(27, 2), False, P) is None
    assert det.asleep
    # Out of bed at 06:00 for good.
    done = det.step(at(27, 6, 45), False, at(27, 6), False, P)
    assert done and local(done[0]) == "26 22:10" and local(done[1]) == "27 06:00"
    assert done[2] == model.SOURCE_IN_BED
    # A 1-hour nap is not a night.
    det.step(at(27, 16, 30), True, at(27, 16), False, P)
    assert det.step(at(27, 17, 45), False, at(27, 17), False, P) is None


def test_tracker_counts_immediately_and_marks_the_night():
    det = model.SleepDetector()
    det.step(at(26, 23, 1), True, at(26, 23), True, P)
    assert det.asleep and local(det.onset) == "26 23:00"
    done = det.step(at(27, 7, 31), False, at(27, 7), False, P)
    assert done[2] == model.SOURCE_TRACKER


VALUES = model.DEFAULT_ASLEEP_VALUES


def test_combine_needs_someone_home_for_in_bed_signals():
    assert model.combine([], ["on"], True, VALUES) == (True, False, True)
    assert model.combine([], ["on"], False, VALUES) == (False, False, True)
    # A tracker still counts away from home (travel is still sleep).
    assert model.combine(["asleep"], [], False, VALUES) == (True, True, True)


def test_combine_fills_gaps_between_signals():
    # Flaky bed sensor unavailable, tracker still reporting deep sleep.
    assert model.combine(["deep"], ["unavailable"], True, VALUES) == (True, True, True)
    # mmWave sees someone, tracker says awake: in bed, not sure.
    assert model.combine(["awake"], ["detected"], True, VALUES) == (True, False, True)
    # Nothing reporting at all is unknown, not awake.
    assert model.combine(["unavailable"], [None], True, VALUES)[2] is False


def test_combine_custom_states_are_case_insensitive():
    assert model.combine(["Sleeping"], [], True, ["sleeping"])[1] is True
    assert model.combine(["core"], [], True, ["Core"])[1] is True


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

