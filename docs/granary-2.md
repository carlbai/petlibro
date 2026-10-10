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
| Food in Bowl | `remainingGrain`, measured grams |
| Feedable Portions | `maxFeedable`, portions |
| Grams per Portion | `portionToGramRatio`, g/portion |
| Feeding Mode | `feedingMode`: Smart Refill (`FREE`) or scheduled feeding (`PLAN`) |
| Smart Refill Portions per Feed | `freePerGrainNum`, portions |
| Smart Refill Daily Portion Limit | `freeDailyMaxNum`, portions |
| Smart Refill Food Threshold | `freeLeftoverWeight`, grams |
| Smart Refill Wait Time | `freeWaitSeconds`, seconds |
| Enable Smart Refill | Sends feeding mode `FREE` |
| Enable Scheduled Feeding | Sends feeding mode `PLAN` |

Configure Smart Refill amounts and limits in the PETLIBRO app. Home Assistant
reads these settings; the two mode buttons change which feeding mode is active.
Enabling the feeding schedule and selecting `PLAN` are separate API actions.

Missing or invalid additional telemetry is unavailable, rather than reported
as zero. A failed optional request clears its previous values and leaves the
other refresh requests running. A real bowl weight of zero remains valid.

The inherited feeding-history weight and volume sensors remain **estimates**
using the integration's existing conversion factors. Food in Bowl is a separate
scale measurement. Grams per Portion exposes the reported calibration without
changing those inherited conversions.

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
contracts come from captured traffic on a sibling PLAF205 in
[upstream PR #274](https://github.com/jjjonesjr33/petlibro/pull/274).
They have **not yet been verified on a live PLAF105**. The tests use synthetic
responses based on those documented fields, without contacting a feeder or
dispensing food.

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
