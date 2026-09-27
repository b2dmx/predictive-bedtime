# Predictive Bedtime

A Home Assistant integration that **learns when each person in the home goes to sleep** and predicts the next bedtime, so the house can get ready for sleep when sleep is actually expected, instead of on a fixed schedule.

It is built for irregular and rotating work schedules. It reads each person's work calendars, watches their side of the bed, and learns how their sleep relates to their shifts. When shifts change, predictions change with them.

## How it works

- **Learns from real nights.** Any stretch of 3+ hours in bed (while home) is recorded as a night's sleep. Shorter stretches are ignored.
- **Thinks in shifts, not weekdays.** Each night is described by hours until the next shift, hours since the last one ended, and time of day. A new rotation, a swapped shift or a schedule that changes every few months is handled by comparing it with similar situations, including ones never seen exactly before.
- **Starts sensible, then adapts.** Until enough nights are learned, predictions follow the schedule: a full night before early shifts, an unwinding buffer after late ones, and your usual bedtime on free days. As nights accumulate, actual behaviour takes over. **Confidence** shows how far along it is.
- **Bounded memory.** Nights older than the learning window (365 days by default) are discarded. Within the window, recent nights count more.
- **Nothing polls.** Calendars are re-read once a day, or immediately when one of that person's calendars changes. Sensors switch at the exact moments they are due.
- **Head start.** On first setup it learns from whatever bed history the recorder still holds.

## Entities (per person)

| Entity | What it is |
|---|---|
| Next bedtime | When this person is expected to go to bed next. Attributes include the schedule-only estimate and the shifts either side. |
| Next wake | A full night after bedtime, or earlier if a shift needs them up. |
| Confidence | 0–100 %, how much the prediction rests on learned nights rather than the schedule alone. |
| Last sleep | When the last recorded night began, with its wake time and length. |
| Expected asleep | On during the predicted sleep window, or once they have settled in bed. |
| Wind-down | On for a set time before the predicted bedtime. |

### Example

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.sam_predictive_bedtime_wind_down
    to: "on"
actions:
  - action: light.turn_on
    target:
      area_id: bedroom
    data:
      brightness_pct: 30
      color_temp_kelvin: 2200
```

## Requirements

- One or more **calendars** holding the person's shifts. Every timed event counts as a shift; all-day events are ignored.
- A **bed occupancy sensor** per person (per side of the bed), and a **person** entity.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/b2dmx/predictive-bedtime`, category **Integration**.
2. Download **Predictive Bedtime**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → Predictive Bedtime**, once per person.

### Manual

Copy `custom_components/predictive_bedtime` into your `config/custom_components` folder and restart.

## Settings

Setup asks for the person, their calendars, their bed sensor and a few starting habits. Everything can be changed later under **Configure**:

| Setting | Default |
|---|---|
| Usual bedtime with no shift nearby | 23:30 |
| Sleep needed before a shift | 7.5 h |
| Wake-up to shift start (getting ready + commute) | 75 min |
| Wind-down before bedtime | 60 min |
| Shortest time from shift end to bed | 90 min |
| Time in bed before it counts as a sleep attempt | 20 min |
| Time out of bed before it counts as awake | 30 min |
| Shortest stretch that counts as a night | 3 h |
| Learning window | 365 days |

## Privacy

Everything runs locally. Learned nights are stored in Home Assistant's `.storage` folder and never leave your system.

## License

MIT
