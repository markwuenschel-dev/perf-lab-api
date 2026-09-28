# Apple Watch → Perf Lab with an iOS Shortcut

Apple keeps Watch data on the iPhone (HealthKit) and offers no cloud API. A daily Shortcut
reads last night's numbers from Health and posts them to Perf Lab. PDR-0006 (amendment
2026-09-28) records the decision.

## 1. Create a token
In Perf Lab, open **Settings → Wearable — Apple Watch → Set up Apple Watch sync**. Copy the
token (`plw_…`) and the URL. The token is shown once. It can only write wellness data, and
**Revoke** stops it immediately.

## 2. Build the Shortcut
Open **Shortcuts → Automation → + → Time of Day**, set it to 07:00, Daily, **Run Immediately**.
Then add these actions:

1. **Find Health Samples**: Type *Heart Rate Variability*, Start Date *is in the last 1 day*,
   sort by *Start Date*, latest first, limit 1. Then **Get Details of Health Sample →
   Value**. Name the result `HRV`.
2. Do the same for **Resting Heart Rate**. Name the result `RHR`.
3. **Find Health Samples**: Type *Sleep Analysis*, Start Date *is in the last 1 day*. Keep
   only the *asleep* samples (Core, Deep, REM, or "Asleep" on older watches). Then **Get
   Details → Duration**, **Calculate Statistics → Sum**, and divide by 3600. Name the
   result `SleepHours`.
4. **Format Date**: *Current Date*, custom format `yyyy-MM-dd`. Name the result `Day`.
5. **Get Contents of URL**:
   - URL: the one Settings shows (`https://…/v1/wellness/ingest`).
   - Method: `POST`.
   - Headers: `Authorization` = `Bearer plw_…`.
   - Request Body: JSON with these fields:

   | Key | Type | Value |
   |---|---|---|
   | `source` | Text | `apple_watch` |
   | `date` | Text | `Day` |
   | `hrv_ms` | Number | `HRV` |
   | `resting_hr` | Number | `RHR` |
   | `sleep_hours` | Number | `SleepHours` |

Run it once by hand. Settings should then show **last sync** with the current time.

## What the server does with it
- **One row per day.** Sending the same day again replaces that day, so a retry never
  double-counts.
- **HRV is stored as SDNN**, which is what Apple reports. It is never averaged with Oura's
  rMSSD, because baselines are kept per source and per metric.
- **Oura wins when it has a same-day reading.** The Watch fills any signal the ring did not
  report, and always outranks a slider value for HRV, resting HR and sleep.
- Soreness, motivation and stress are refused from the phone. Those come from the athlete.
- Leave out any field you don't have, but send at least one of `hrv_ms`, `resting_hr` or
  `sleep_hours`.

## When it stops
A locked or offline phone, or an automation switched off, fails silently. Settings flags a
token with no successful push in 36 h. The check-in never waits on it and asks by hand
instead.
