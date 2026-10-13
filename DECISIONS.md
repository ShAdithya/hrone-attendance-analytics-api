# DECISIONS.md

1. **Indexes.** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, and a unique compound index on `attendance_logs.emp_code` and `date` to guarantee one attendance record per employee per day. Other indexes support department and join-date filtering, attendance date sorting, employee monthly queries, and late-arrival analytics. I use compound indexes where they match the query patterns instead of adding a standalone status index without a demonstrated need.

2. **Punch-in race.** Both requests may check the employee before attempting insertion. The unique compound attendance index allows only one record for the same employee and date. The successful request returns 201; the request that encounters `DuplicateKeyError` returns 409.

3. **Ties.** The leaderboard uses MongoDB's `$rank`, then filters by rank. Employees tied at the cutoff share the same rank and are all returned. The next rank is skipped, and the final sort uses employee code to order tied rows consistently.

4. **Headcount.** The department summary starts from employees and joins attendance records. This lets employees with no logs contribute to headcount, while join-date filtering excludes employees who have not joined by the period end.

5. **100x scale.** I would benchmark representative workloads, inspect execution statistics, and review index selectivity. I would also consider workload-specific indexing, resource monitoring, and pagination or archival strategies based on measured bottlenecks.