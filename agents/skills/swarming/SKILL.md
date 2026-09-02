---
name: swarming
description: >-
  Investigate LUCI Swarming task execution status (running, pending, completed, expired, timed out) and bot health status (busy, idle, dead, quarantined, maintenance). Use when diagnosing stuck or pending Swarming tasks, checking bot pool capacity, filtering tasks by tags (pool, cpu, cores, os) and time window (start/end timestamp), inspecting task logs and stdout, looking up bot dimensions, or querying Swarming v2 pRPC endpoints via the prpc CLI. Don't use for triggering new tasks (use led or test runner skills) or Buildbucket builds (use bb).
---

# Investigating Swarming Task & Bot Status

## 1. Overview & Common Instances

LUCI Swarming uses a **pRPC (Proto-over-HTTP)** v2 API encoding messages with `protojson`.

### Swarming Hostname
- **Chromium Swarming**: `chromium-swarm.appspot.com`

### Web RPC Explorer
Interactive API testing UI: `https://<swarming-instance>/rpcexplorer/`

### Authentication
Queries use LUCI credentials via the `prpc` tool (bundled with `depot_tools`):
```bash
luci-auth login
```

---

## 2. Parameter Types & Server Validation Rules

### `google.protobuf.Timestamp`
- **Format**: RFC 3339 formatted string (e.g., `"2026-09-01T12:00:00Z"`).
- **Dynamic shell generation**: `$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)`.

### `NullableBool` (for Bot filters)
- Unset / `NULL` (0): No filter applied (returns both true and false).
- `FALSE` (1): Match entities where the property is false.
- `TRUE` (2): Match entities where the property is true.

### `StateQuery` Enum (for Task queries)
| Enum String | Value | Description |
| :--- | :--- | :--- |
| `QUERY_PENDING` | 0 | **Default**. Only pending tasks. |
| `QUERY_RUNNING` | 1 | Currently running tasks (includes overhead phases). |
| `QUERY_PENDING_RUNNING` | 2 | All active tasks (both pending and running). |
| `QUERY_COMPLETED` | 3 | Completed tasks (independent of exit code). |
| `QUERY_COMPLETED_SUCCESS`| 4 | Completed with exit code 0. |
| `QUERY_COMPLETED_FAILURE`| 5 | Completed with non-zero exit code. |
| `QUERY_EXPIRED` | 6 | Expired before being assigned to a bot. |
| `QUERY_TIMED_OUT` | 7 | Exceeded execution or I/O timeout. |
| `QUERY_BOT_DIED` | 8 | Bot crashed or lost communication. |
| `QUERY_CANCELED` | 9 | Canceled while pending. |
| `QUERY_ALL` | 10 | All task states. |
| `QUERY_KILLED` | 12 | Canceled while running. |
| `QUERY_NO_RESOURCE` | 13 | Refused immediately (no bot capacity). |
| `QUERY_CLIENT_ERROR` | 14 | Bad CIPD or CAS package. |

### `limit` Parameter
- Allowed range: `1` to `1000`.
- Default: `100` (if set to `0` or omitted).

---

## 3. Investigating Task Status (`swarming.v2.Tasks`)

### A. `Tasks.ListTasks`
List detailed task results matching tags, state, and creation time range.

- **Required Parameters**:
  - `start` (`google.protobuf.Timestamp`): Beginning of the task creation time window.
- **Optional Parameters**:
  - `end` (`google.protobuf.Timestamp`): End of time window (defaults to unbounded/now).
  - `state` (`StateQuery`): Defaults to `QUERY_PENDING`. Must explicitly set to `QUERY_PENDING_RUNNING`, `QUERY_RUNNING`, or `QUERY_ALL` to see active/running tasks.
  - `tags` (`repeated string`): List of `"key:value"` filters (e.g. `["pool:luci.chromium.try", "cpu:x86-64", "cores:8"]`). All must match (logical AND).
  - `limit` (`int32`): Page size `[1..1000]` (default: 100).
  - `cursor` (`string`): Cursor from previous response for pagination.
  - `sort` (`SortQuery`): Only `QUERY_CREATED_TS` (0) is supported.

```bash
START_TIME=$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)

prpc call {swarming_host} swarming.v2.Tasks.ListTasks <<EOF
{
  "start": "${START_TIME}",
  "tags": [
    "pool:{pool_name}",
    "cpu:x86-64",
    "cores:8"
  ],
  "state": "QUERY_PENDING_RUNNING",
  "limit": 50
}
EOF
```

---

### B. `Tasks.CountTasks`
High-performance task counting. Aggregates server-side without fetching entity bodies.

- **Required Parameters**:
  - `start` (`google.protobuf.Timestamp`): Explicitly required by the server.
- **Optional Parameters**:
  - `end` (`google.protobuf.Timestamp`): Defaults to `clock.Now()`.
  - `state` (`StateQuery`): Defaults to `QUERY_PENDING`.
  - `tags` (`repeated string`): `"key:value"` filter list.

```bash
START_TIME=$(date -u -d '3 hours ago' +%Y-%m-%dT%H:%M:%SZ)

prpc call {swarming_host} swarming.v2.Tasks.CountTasks <<EOF
{
  "start": "${START_TIME}",
  "tags": [
    "pool:{pool_name}",
    "cores:8"
  ],
  "state": "QUERY_PENDING"
}
EOF
```

---

### C. `Tasks.GetResult`
Retrieve full result, execution state, assigned bot, and timing for a single task.

- **Required Parameters**:
  - `task_id` (`string`): Task ID (Summary ID ending in `0` or Run ID ending in `1`, `2`).

```bash
prpc call {swarming_host} swarming.v2.Tasks.GetResult <<EOF
{
  "task_id": "{task_id}"
}
EOF
```

---

### D. `Tasks.GetRequest`
Retrieve original task specification (task slices, requested dimensions, expiration, priority).

- **Required Parameters**:
  - `task_id` (`string`): Task ID.

```bash
prpc call {swarming_host} swarming.v2.Tasks.GetRequest <<EOF
{
  "task_id": "{task_id}"
}
EOF
```

---

### E. `Tasks.GetStdout`
Retrieve the task process stdout/stderr log output.

- **Required Parameters**:
  - `task_id` (`string`): Task ID.
- **Optional Parameters**:
  - `offset` (`int64`): Byte offset to start reading from (must be `>= 0`, default: `0`).
  - `length` (`int64`): Maximum bytes to read (must be `>= 0`, default & max: `16MB`).

```bash
prpc call {swarming_host} swarming.v2.Tasks.GetStdout <<EOF
{
  "task_id": "{task_id}",
  "offset": 0,
  "length": 1048576
}
EOF
```
*(Note: `.output` in the JSON response is base64-encoded. Decode with `jq -r .output | base64 -d`).*

---

### F. `Tasks.ListTaskStates` & `Tasks.BatchGetResult`
Check states or results for up to 1000 task IDs in a single RPC.

- **Required Parameters**:
  - `task_id` / `task_ids` (`repeated string`): List of task IDs (1 to 1000 items, no duplicates).

```bash
prpc call {swarming_host} swarming.v2.Tasks.ListTaskStates <<EOF
{
  "task_id": ["{task_id_1}", "{task_id_2}", "{task_id_3}"]
}
EOF
```

---

## 4. Investigating Bot Status (`swarming.v2.Bots`)

### A. `Bots.CountBots`
Returns counts of total, busy, dead, quarantined, and maintenance bots matching dimensions.

- **Required Parameters**:
  - None.
- **Optional Parameters**:
  - `dimensions` (`repeated StringPair`): Key/value dimensions to filter by (e.g. `[{"key": "pool", "value": "luci.chromium.try"}]`).

```bash
prpc call {swarming_host} swarming.v2.Bots.CountBots <<EOF
{
  "dimensions": [
    {"key": "pool", "value": "{pool_name}"},
    {"key": "cpu", "value": "x86-64"},
    {"key": "cores", "value": "8"}
  ]
}
EOF
```

---

### B. `Bots.ListBots`
List individual bot states with multidimensional and health status filters.

- **Required Parameters**:
  - None.
- **Optional Parameters**:
  - `dimensions` (`repeated StringPair`): Dimensions to match.
  - `is_busy` (`NullableBool`): `FALSE` (idle/available), `TRUE` (busy), or `NULL`.
  - `is_dead` (`NullableBool`): `FALSE` (alive), `TRUE` (dead), or `NULL`.
  - `quarantined` (`NullableBool`): `FALSE` (healthy), `TRUE` (quarantined), or `NULL`.
  - `in_maintenance` (`NullableBool`): `FALSE` (normal), `TRUE` (in maintenance), or `NULL`.
  - `limit` (`int32`): Page size `[1..1000]` (default: 100).
  - `cursor` (`string`): Pagination cursor.

#### Example: Find Idle, Healthy Bots for Given Specs
```bash
prpc call {swarming_host} swarming.v2.Bots.ListBots <<EOF
{
  "dimensions": [
    {"key": "pool", "value": "{pool_name}"},
    {"key": "cpu", "value": "x86-64"},
    {"key": "cores", "value": "8"}
  ],
  "is_busy": "FALSE",
  "is_dead": "FALSE",
  "quarantined": "FALSE",
  "in_maintenance": "FALSE",
  "limit": 50
}
EOF
```

---

### C. `Bots.GetBot`
Get complete details of a single bot. If currently deleted or disconnected, Swarming reconstructs its last known state from `BotEvent` history and sets `deleted: true`.

- **Required Parameters**:
  - `bot_id` (`string`): Bot identifier.

```bash
prpc call {swarming_host} swarming.v2.Bots.GetBot <<EOF
{
  "bot_id": "{bot_id}"
}
EOF
```

---

### D. `Bots.ListBotTasks`
List tasks that executed on a specific bot.

- **Required Parameters**:
  - `bot_id` (`string`): Bot identifier.
- **Optional Parameters & Server Constraints**:
  - `state` (`StateQuery`): Defaults to `QUERY_PENDING`.
    > ⚠️ **Server Validation Error**: The server will reject the request with `InvalidArgument` if `state` is set to `QUERY_PENDING`, `QUERY_PENDING_RUNNING`, `QUERY_EXPIRED`, `QUERY_DEDUPED`, `QUERY_NO_RESOURCE`, or `QUERY_CANCELED` because pending/unassigned tasks are never associated with bots. **Always set `state` to `QUERY_ALL`, `QUERY_RUNNING`, or `QUERY_COMPLETED`.**
  - `sort` (`SortQuery`):
    - `QUERY_CREATED_TS` (default)
    - `QUERY_STARTED_TS` (allowed only when `state: "QUERY_ALL"`)
    - `QUERY_COMPLETED_TS` (allowed only when `state: "QUERY_ALL"`)
  - `start` (`google.protobuf.Timestamp`): Filter start time.
  - `end` (`google.protobuf.Timestamp`): Filter end time.
  - `limit` (`int32`): `[1..1000]` (default: 100).
  - `cursor` (`string`): Pagination cursor.

```bash
START_TIME=$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)

prpc call {swarming_host} swarming.v2.Bots.ListBotTasks <<EOF
{
  "bot_id": "{bot_id}",
  "start": "${START_TIME}",
  "state": "QUERY_ALL",
  "limit": 20
}
EOF
```

---

### E. `Bots.ListBotEvents`
Inspect bot lifecycle events (heartbeats, restarts, task completions, error events).

- **Required Parameters**:
  - `bot_id` (`string`): Bot identifier.
- **Optional Parameters**:
  - `start` (`google.protobuf.Timestamp`): Filter events `>= start`.
  - `end` (`google.protobuf.Timestamp`): Filter events `< end`.
  - `limit` (`int32`): `[1..1000]` (default: 100).
  - `cursor` (`string`): Pagination cursor.

```bash
prpc call {swarming_host} swarming.v2.Bots.ListBotEvents <<EOF
{
  "bot_id": "{bot_id}",
  "limit": 25
}
EOF
```

---

### F. `Bots.GetBotDimensions`
Inspect all aggregated bot dimensions advertised in a pool.

- **Optional Parameters**:
  - `pool` (`string`): Pool name. If omitted, returns union of all pools visible to caller.

```bash
prpc call {swarming_host} swarming.v2.Bots.GetBotDimensions <<EOF
{
  "pool": "{pool_name}"
}
EOF
```

---

## 5. Summary Quick-Reference Table

| RPC Method | Required Parameters | Optional Parameters | Key Default Behaviors |
| :--- | :--- | :--- | :--- |
| **`Tasks.ListTasks`** | `start` | `end`, `state`, `tags`, `limit`, `cursor`, `sort` | `state` defaults to `QUERY_PENDING`; `limit` defaults to 100 |
| **`Tasks.CountTasks`** | `start` | `end`, `state`, `tags` | `end` defaults to now; `state` defaults to `QUERY_PENDING` |
| **`Tasks.GetResult`** | `task_id` | *(none)* | Supports summary ID (`...0`) or run ID (`...1`) |
| **`Tasks.GetRequest`** | `task_id` | *(none)* | Returns task slices & requested dimensions |
| **`Tasks.GetStdout`** | `task_id` | `offset`, `length` | `offset` defaults to 0; `length` defaults to 16MB |
| **`Tasks.ListTaskStates`**| `task_id` (list) | *(none)* | Up to 1000 IDs, no duplicates |
| **`Tasks.BatchGetResult`**| `task_ids` (list)| *(none)* | Up to 1000 IDs, no duplicates |
| **`Bots.CountBots`** | *(none)* | `dimensions` | Returns `count`, `busy`, `dead`, `quarantined`, `maintenance` |
| **`Bots.ListBots`** | *(none)* | `dimensions`, `is_busy`, `is_dead`, `quarantined`, `in_maintenance`, `limit`, `cursor` | `limit` defaults to 100; booleans default to `NULL` (unfiltered) |
| **`Bots.GetBot`** | `bot_id` | *(none)* | Reconstructs from event history if deleted (`deleted: true`) |
| **`Bots.ListBotTasks`** | `bot_id` | `state`, `sort`, `start`, `end`, `limit`, `cursor` | `state` **must not** be `QUERY_PENDING` (must use `QUERY_ALL` or `QUERY_RUNNING`) |
| **`Bots.ListBotEvents`**| `bot_id` | `start`, `end`, `limit`, `cursor` | `limit` defaults to 100 |
| **`Bots.GetBotDimensions`**| *(none)* | `pool` | Returns all visible pools if `pool` is omitted |
