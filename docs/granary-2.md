# Granary 2 (PLAF105, no camera)

This fork adds experimental support for the camera-free Granary 2. Discovery
recognizes `productIdentifier: "PLAF105"` even if the API's product name differs,
and also recognizes the exact product name `Granary 2`.

The device inherits the existing Granary Smart Feeder's manual feeding, feeding
schedules, indicator controls, desiccant reminders, connectivity, battery,
feeding history, and firmware entities. It does not inherit camera entities or
request camera credentials. Granary 2 Vision, Vision Duo, and X are separate
models and are not added by this change.

Additional entities:

| Entity | Source / unit |
| --- | --- |
| Total Consumed Today | Sum of measured `MEAL_RECORD.intake`, grams |
| Meals Today | Count of measured meal records |
| Eating Duration Today | Sum of eating intervals, seconds |
| Last Meal Time | Most recent measured meal in the past 30 days |
| Maximum Bowl Weight | `autoFeedMaxWeight`, grams, read-only |
| Automatic Feed Stop | `autoStopFeedSwitch`, read-only |
| Food in Bowl | `remainingGrain`, measured grams |
| Feedable Portions | `maxFeedable`, portions |
| Grams per Portion | `portionToGramRatio`, g/portion |
| Feeding Mode | `feedingMode`: Smart Refill (`FREE`) or scheduled feeding (`PLAN`) |
| Smart Refill Portions per Feed | `freePerGrainNum`, portions |
| Smart Refill Daily Portion Limit | `freeDailyMaxNum`, portions |
| Smart Refill Food Threshold | `freeLeftoverWeight`, grams |
| Smart Refill Wait Time | `freeWaitSeconds`, seconds |
| Meal Call | Enable playback of the existing recording |
| Meal Call Repetitions | 1–5 plays |
| Meal Call for Scheduled Feeding / Smart Refill / Manual Feeding | Playback application modes |
| Enable Smart Refill | Sends feeding mode `FREE` |
| Enable Scheduled Feeding | Sends feeding mode `PLAN` |

Smart Refill also has editable number entities: 1–10 portions per feed, a daily
limit of 1–100 portions, and a food threshold of 3–10 g. The wait interval remains
read-only. Meal Call controls change playback settings for the existing recording;
recording audio stays in the PETLIBRO app. Paired settings are read fresh before
each write and edits are serialized to preserve companion values. Failed writes
raise a service error.

Maximum Bowl Weight and Automatic Feed Stop remain read-only: the inspected app
code has no verified write contract, and these controls are absent from the
PLAF105 app UI. The two mode buttons change the active feeding mode. Enabling
the feeding schedule and selecting `PLAN` are separate API actions.

Missing or invalid additional telemetry is unavailable, rather than reported
as zero. A failed optional request clears its previous values and leaves the
other refresh requests running. A real bowl weight of zero remains valid.

Dispensed-weight sensors, schedule mass attributes, and manual mass quantities
use the reported Grams per Portion calibration. They remain **estimates**; Food
in Bowl and Total Consumed Today are separate measurements. Missing or zero
calibration makes mass estimates unavailable. Volume estimates keep the existing
conversion, because grams alone cannot establish food volume.

Single-bowl PLAF105 devices default the redundant left/right food statuses to
disabled. Buttons Lock is unavailable and disabled by default when its setting
is absent. Existing installations retain registry entries and history; disable
these three unsupported entities in the device UI if they were already enabled.

Total Consumed Today sums the meal records reported by the feeder, matching the
app's measured intake rather than estimating consumption from dispensed food
minus bowl weight. It uses midnight in Home Assistant's configured timezone
(also sent to the PETLIBRO API), deduplicates overlapping history pages, and
excludes other event types and other days. The daily total permits corrections
and reports its reset time for Home Assistant statistics. A complete day with
no meals reports 0 g; a failed, invalid, or incomplete history fetch reports
unavailable. History is bounded to ten pages of 25 records per request. Meals
Today and Eating Duration Today share the same midnight reset. Duration uses
millisecond eating start/end timestamps, rather than the rounded minute field;
missing intervals make duration unavailable without invalidating valid intake
or meal count. Last Meal Time looks back up to 30 days when today has no meals.

## Installation

After this branch is pushed to your fork, add `https://github.com/carlbai/petlibro`
to HACS as a custom integration repository and select the version containing this
change. If HACS only offers upstream releases, use the manual installation:

1. Download this branch's source ZIP from your fork.
2. Copy `custom_components/petlibro` into Home Assistant's
   `config/custom_components/petlibro`, replacing the existing integration files.
3. Restart Home Assistant and configure PETLIBRO under Settings → Devices & Services.
4. Check that the device model is PLAF105, Food in Bowl matches the app, and
   Feeding Mode matches the app before using the mode controls.

## Validation status

The device model and camera-free capabilities are documented by
[PETLIBRO](https://petlibro.com/products/granary-2-series). The additional API
read contracts initially came from captured traffic on a sibling PLAF205 in
[upstream PR #274](https://github.com/jjjonesjr33/petlibro/pull/274).
Bowl weight, feeding mode, and Smart Refill settings have been read from a live
PLAF105. A measured meal's top-level `intake` also matched the app's Total
Consumed value. Smart Refill and Meal Call write contracts and bounds were
verified against the PETLIBRO Android app code. Mode-changing and dispensing controls have not been exercised
on the live feeder. The tests use synthetic responses without contacting a
feeder or dispensing food.

CI runs the device tests, Hassfest, and HACS validation. On forks, it skips HACS's
repository topics and issue-tracker checks, which are publication metadata for
the default directory. The integration structure checks remain enabled.

If additional entities are unavailable, enable debug logging from the integration's
menu in Settings → Devices & Services, reload the integration, and inspect the
responses for `/data/data/realInfo` and `/device/device/getFreeFeedingSetting`.
Before sharing logs, remove email, passwords, tokens, device serials, MAC
addresses, Wi-Fi names, and personal pet information. Relevant telemetry fields
and the error codes are sufficient to check this adapter.

## Running the tests

Use Python 3.14 and install `tests/requirements.txt` in a virtual environment.
Run `python -m pytest tests` from the repository root. The test dependencies pin
Home Assistant 2026.10.1, the version used for validation.
