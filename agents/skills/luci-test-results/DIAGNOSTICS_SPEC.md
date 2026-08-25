# Test History Diagnostic Specification

## 1. Metrics & Base Definitions
- AnyRunTotal: Number of runs
- RunPass: Number of individual test runs passed
- RunFail: Number of individual test runs failed
- RunInfraFail: Number of individual test runs failed due to infra
- RunSkip: Number of individual test runs skipped
- ValidRunTotal: RunPass + RunFail
- RunFailRate: RunFail / ValidRunTotal
- RunPassRate: RunPass / ValidRunTotal

- AnyBuildTotal: Number of builds
- BuildCleanPass: Number of builds passed on initial run without retry (1 run)
- BuildPassOnRetry: Number of builds with failed initial run, passed on retry run (at least 2 runs)
- BuildPass: BuildCleanPass + BuildPassOnRetry
- BuildFail: Number of builds with only run failures (at least 1 run)
- BuildSkip: Number of builds where all runs were skipped (RunSkip > 0 and RunPass == 0 and RunFail == 0)
- BuildNoRun: Number of builds where test did not run or was omitted
- ValidBuildTotal: BuildPass + BuildFail
- BuildFailRate: BuildFail / ValidBuildTotal
- BuildPassRate: BuildPass / ValidBuildTotal

### Streaks & Score Formulas (Evaluated over partition_time >= now - 5d)
Let ValidBuildHistory be the chronological sequence of valid builds [B_1, B_2, ..., B_N] (oldest to newest, skipping over isolated BuildSkip builds <= 48h) within the last 5 days.

- TailFailStreak: Number of consecutive BuildFail builds leading up to B_N backwards (0 if B_N is not BuildFail).
- TailPassStreak: Number of consecutive BuildPass builds leading up to B_N backwards (0 if B_N is not BuildPass).
- TailPassStreakRetries: Number of BuildPassOnRetry builds strictly within TailPassStreak.
- PreStreakPassCount: Number of consecutive BuildCleanPass builds immediately preceding TailFailStreak (BuildPassOnRetry breaks clean streak) (0 if TailFailStreak == 0).
- PreStreakFailCount: Number of consecutive BuildFail builds immediately preceding TailPassStreak (0 if TailPassStreak == 0).

#### RegressionScore Formula
RegressionScore = TailFailStreak * PreStreakPassCount

| Tail Failures (F) | Preceding Clean Passes (P) | RegressionScore (F * P) | Threshold (>= 20) | Status | Rationale |
| :---: | :---: | :---: | :---: | :--- | :--- |
| 1 | 49 | 49 | Pass | ACTIVE REGRESSION | Fresh breakage after 49 clean builds (Android arm dbg) |
| 1 | 33 | 33 | Pass | ACTIVE REGRESSION | Fresh breakage after 33 clean builds (android-arm64-proguard-rel) |
| 1 | 20 | 20 | Pass | ACTIVE REGRESSION | Boundary: 1 failure after 20 clean builds |
| 1 | 10 | 10 | Fail (< 20) | FLAKY | 1 failure after only 10 passes (waits for 2nd fail) |
| 2 | 48 | 96 | Pass | ACTIVE REGRESSION | Confirmed regression after 48 clean builds (android-10-x86-rel) |
| 2 | 10 | 20 | Pass | ACTIVE REGRESSION | 2 consecutive failures after 10 clean builds |
| 3 | 7 | 21 | Pass | ACTIVE REGRESSION | 3 consecutive failures after 7 clean builds |
| 4 | 5 | 20 | Pass | ACTIVE REGRESSION | 4 consecutive failures after 5 clean builds |
| 1 | 3 | 3 | Fail (< 20) | FLAKY | 1 failure after 3 passes (flaky noise) |
| 2 | 2 | 4 | Fail (< 20) | FLAKY | 2 failures after 2 passes (flaky oscillation) |

#### RecoveryScore Formula
RecoveryScore = TailPassStreak * PreStreakFailCount

| Tail Passes (P) | Preceding Failures (F) | RecoveryScore (P * F) | Threshold (>= 16) | Status | Rationale |
| :---: | :---: | :---: | :---: | :--- | :--- |
| 4 | 4 | 16 | Pass | PASSING (RECOVERED) | Clean 4-pass recovery after 4-failure streak |
| 3 | 6 | 18 | Pass | PASSING (RECOVERED) | Clean 3-pass recovery after 6-failure streak |
| 2 | 8 | 16 | Pass | PASSING (RECOVERED) | Clean 2-pass recovery after 8-failure streak |
| 5 | 4 | 20 | Pass | PASSING (RECOVERED) | Solid 5-pass recovery after 4 failures |
| 2 | 2 | 4 | Fail (< 16) | FLAKY | 2 passes after 2 failures (flaky oscillation) |
| 1 | 5 | 5 | Fail (< 16) | CONSISTENTLY FAILING | 1 unconfirmed pass after 5 failures |

---

## 2. Overall Builder Diagnostic Status

Evaluated by synthesizing active tail streaks (5-day lookback) with sliding 24-hour window rates.
24h metrics (ValidBuildTotal, BuildPass, BuildFail, BuildFailRate, RunFail, RunFailRate, BuildSkip) are computed over builds with partition_time >= now - 24h (falling back to the latest active date if that date is within the last 36 hours).

```python
# 1. Inactive or Skipped Test in Recent Window
if ValidBuildTotal == 0:
  if BuildSkip > 0:
    return "SKIPPED"
  else:
    return "NOT RUNNING"

# 2. Active Breakage Formula: RegressionScore = TailFailStreak * PreStreakPassCount
regression_score = TailFailStreak * PreStreakPassCount
if regression_score >= 20 and TailFailStreak >= 1 and PreStreakPassCount >= 2:
  return "ACTIVE REGRESSION"

# 3. Active Recovery Formula: RecoveryScore = TailPassStreak * PreStreakFailCount
recovery_score = TailPassStreak * PreStreakFailCount
if recovery_score >= 16 and TailPassStreak >= 2 and PreStreakFailCount >= 2:
  if TailPassStreakRetries == 0:
    return "PASSING (RECOVERED)"
  else:
    return "PASSING ON RETRY (RECOVERED)"

# 4. Steady-State Persistent Failure
if (
    (BuildPass == 0 and BuildFail > 0)
    or (BuildFailRate >= 0.70 and ValidBuildTotal >= 3)
    or TailFailStreak >= 10
):
  return "CONSISTENTLY FAILING"

# 5. Interleaved Failures (True Flakiness: both passes and failures present)
if BuildFailRate >= 0.20:
  return "HIGHLY FLAKY"
elif BuildFail > 0:
  return "FLAKY"

# 6. Steady-State Passing (Clean tail with 0 build failures)
if RunFail == 0:
  return "PASSING"
elif RunFailRate >= 0.30:
  return "PASSING ON HEAVY RETRY"
elif RunFailRate >= 0.08:
  return "PASSING WITH RETRIES"
else:
  return "MOSTLY PASSING"
```

| Status | Condition | Meaning |
| :--- | :--- | :--- |
| SKIPPED | ValidBuildTotal == 0 and BuildSkip > 0 | 100% of runs in recent window were skipped (@DisableIf, GTEST_SKIP) |
| NOT RUNNING | ValidBuildTotal == 0 and BuildSkip == 0 | 0 runs in recent window (@DisabledTest, DISABLED_, or inactive >36h) |
| ACTIVE REGRESSION | RegressionScore >= 20 (with TailFailStreak >= 1 and PreStreakPassCount >= 2) | Significant breakage relative to clean baseline |
| PASSING (RECOVERED) | RecoveryScore >= 16 and TailPassStreakRetries == 0 | Broken test recovered with clean tail passes |
| PASSING ON RETRY (RECOVERED) | RecoveryScore >= 16 and TailPassStreakRetries > 0 | Broken test recovered with retry passes |
| CONSISTENTLY FAILING | (BuildPass == 0 and BuildFail > 0) or (BuildFailRate >= 0.70 and ValidBuildTotal >= 3) or TailFailStreak >= 10 | 100% failures in window or persistent breakage |
| HIGHLY FLAKY | BuildPass > 0 and BuildFailRate >= 0.20 | Frequent intermittent build failures (>= 20%) |
| FLAKY | BuildPass > 0 and BuildFail > 0 (BuildFailRate < 0.20) | Low intermittent build failures (< 20%) |
| PASSING ON HEAVY RETRY | BuildFail == 0 and RunFailRate >= 0.30 | 0 build failures, heavy retries (>= 30% run fail rate) |
| PASSING WITH RETRIES | BuildFail == 0 and 0.08 <= RunFailRate < 0.30 | 0 build failures, moderate retries (8%–30% run fail rate) |
| MOSTLY PASSING | BuildFail == 0 and RunFailRate < 0.08 (RunFail > 0) | 0 build failures, rare retries (< 8% run fail rate) |
| PASSING | BuildFail == 0 and RunFail == 0 | 100% clean builds and runs |

---

## 3. Query Defaults
- DEFAULT_LIMIT: 50 verdicts (per builder when zooming)
- DEFAULT_SURVEY_LIMIT: 10 verdicts (per builder during initial survey)
- MAX_PAGES: 25 pages (max 2500 verdicts)
- MAX_STREAK_LOOKBACK: 5 days (120 hours)
