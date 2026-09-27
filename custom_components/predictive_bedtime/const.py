"""Constants for Predictive Bedtime."""
from datetime import timedelta

DOMAIN = "predictive_bedtime"
TITLE_SUFFIX = "Predictive Sleep"

CONF_PERSON = "person"
CONF_CALENDARS = "calendars"
# Replaced by the two signal lists in config version 2; kept for migration.
CONF_BED_SENSOR = "bed_sensor"
CONF_IN_BED = "in_bed_signals"
CONF_ASLEEP = "asleep_signals"
CONF_ASLEEP_STATES = "asleep_states"

CONF_TARGET_SLEEP = "target_sleep_hours"
CONF_PREP = "prep_minutes"
CONF_UNWIND = "unwind_minutes"
CONF_WIND_DOWN = "wind_down_minutes"
CONF_FREE_BEDTIME = "free_bedtime"
CONF_SETTLE = "settle_minutes"
CONF_WAKE_GAP = "wake_gap_minutes"
CONF_MIN_SLEEP = "min_sleep_hours"
CONF_RETENTION = "learning_window_days"

DEFAULT_OPTIONS = {
    CONF_TARGET_SLEEP: 7.5,
    CONF_PREP: 75,
    CONF_UNWIND: 90,
    CONF_WIND_DOWN: 60,
    CONF_FREE_BEDTIME: "23:30:00",
    CONF_SETTLE: 20,
    CONF_WAKE_GAP: 30,
    CONF_MIN_SLEEP: 3,
    CONF_RETENTION: 365,
}

STORAGE_VERSION = 1
# Recent nights count more: a night this fraction of the learning window old counts half.
HALF_LIFE_FRACTION = 1 / 3
# Shifts are only needed to describe nights as they are recorded.
SHIFT_RETENTION = timedelta(days=21)
# The recorder usually keeps 10 days; this is a one-off head start, not the learning window.
BACKFILL_DAYS = 30
# Calendars are re-read at least this often, and immediately whenever a calendar entity changes.
CALENDAR_MAX_AGE = timedelta(hours=24)
# After a failed calendar read.
CALENDAR_RETRY = timedelta(minutes=10)
