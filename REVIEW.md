# Code Review — Employee Attendance & Analytics API

## Review scope and evidence

The review focused on API validation, attendance calculations, concurrency, analytics, query plans, and date-range handling.

**Review limitation:** The original starter version of `app/main.py` could not be recovered from the available Git history. Therefore, the findings below describe issues verified in the available implementation and do not claim to be a complete reconstruction of every original starter defect.

## Findings

### 1. Attendance-list explain filters did not match the API

- **Where:** `explain_endpoint`, `attendance_list` branch.
- **What is wrong:** The explain route did not reject invalid date ranges or unsupported attendance statuses. It also used a truthiness check for `emp_code`, unlike the list endpoint's explicit `None` check.
- **How to notice:** Compare `/attendance` and `/admin/explain/attendance_list` using the same filters, including an invalid date range or status.
- **How fixed:** Added matching date-range and status validation and used an explicit `emp_code is not None` check.

### 2. Department-trend explain accepted an oversized date range

- **Where:** `explain_endpoint`, `department_trend` branch.
- **What is wrong:** The maximum range check counted the difference between dates, not the number of calendar days. This allowed a 93-day inclusive range.
- **How to notice:** Request a range from `2026-01-01` through `2026-04-03`.
- **How fixed:** Validate `(date_end - date_start).days + 1 > 92`.

### 3. Department-trend explain counted half-days incorrectly

- **Where:** The `$group` stage in the department-trend explain pipeline.
- **What is wrong:** Every presence record contributed `1`, including half-day records.
- **How to notice:** Explain a range containing a `PRESENT`, `WFH`, or `ON_DUTY` record with `half_day: true`.
- **How fixed:** Count half-day records as `0.5` and other presence records as `1`.

### 4. Department-trend explain used inconsistent attendance-rate semantics

- **Where:** The `attendance_rate` stage in the department-trend explain pipeline.
- **What is wrong:** The rate was multiplied by `100` and returned `0` on weekends or when headcount was zero. The actual endpoint returns a fraction and uses `null` when the rate is undefined.
- **How to notice:** Compare the endpoint and explain pipelines for a weekday, weekend, or date with zero headcount.
- **How fixed:** Use `present_count / headcount` on working days with positive headcount; otherwise return `null`.

### 5. Department-trend explain used different date bounds

- **Where:** The `$densify` stage in the department-trend explain pipeline.
- **What is wrong:** The upper bound did not extend to the day after the requested end date, unlike the actual trend pipeline's exclusive upper bound.
- **How to notice:** Request a short date range and compare the generated date boundaries with the actual endpoint.
- **How fixed:** Use UTC-aware bounds from the start date through midnight after the end date.

## Additional behavior checked

- **Duplicate employee and attendance records:** Unique indexes enforce employee-code and employee/date uniqueness.
- **Concurrent punch-out:** The update filters for `punch_out: None`, so only one request can complete the punch-out successfully.
- **Concurrent regularization:** The update compares the existing history before applying a correction, preventing a concurrent update from silently overwriting another correction.
- **Leaderboard ties:** `$rank` preserves ties and the final sort makes the returned order deterministic.
- **Department headcount:** The summary begins with eligible employees, allowing employees without attendance logs to contribute to headcount.
- **Time rules:** Automated tests cover time calculations and half-day behavior.

These are checks of the available implementation. They should not be interpreted as proof that every behavior was correct in the original starter.

## Verification

Latest verified test command:

```bash
MONGO_DB=attendance_test_db python -m pytest -q
```

Result: **24 passed**, with three deprecation warnings.

The warnings concern the current FastAPI startup-event API and the installed Starlette test-client integration. They did not cause test failures.

The department-trend tests also passed independently. Query-plan claims should be supported by the actual output of `/admin/explain/{endpoint}` against the benchmark dataset rather than assumed from the index definitions alone.