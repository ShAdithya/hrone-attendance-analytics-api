
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient

load_dotenv(Path(__file__).parent / ".env")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017")
BENCHMARK_DB = "attendance_benchmark"

EMPLOYEE_COUNT = 1000
ATTENDANCE_COUNT = 100_000
START_DATE = date(2026, 1, 1)

DEPARTMENTS = ["Engineering", "Sales", "Support", "HR", "Finance"]
STATUSES = ["PRESENT", "WFH", "ABSENT", "LEAVE", "ON_DUTY"]

def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

    try:
        client.admin.command("ping")
        db = client[BENCHMARK_DB]

        # Refuse to overwrite existing benchmark collections.
        if db.employees.count_documents({}) or db.attendance_logs.count_documents({}):
            raise SystemExit(
                f"Database '{BENCHMARK_DB}' already contains data. "
                "Nothing was deleted or inserted."
            )

        print("Generating synthetic employees...")
        employees = []
        for i in range(1, EMPLOYEE_COUNT + 1):
            joined = START_DATE + timedelta(days=i % 180)
            employees.append({
                "emp_code": f"BENCH{i:05d}",
                "name": f"Benchmark Employee {i}",
                "email": f"bench{i}@example.test",
                "department": DEPARTMENTS[i % len(DEPARTMENTS)],
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": joined.isoformat(),
                "created_at": datetime.combine(
                    joined, time(4, 30), tzinfo=timezone.utc
                ),
            })

        db.employees.insert_many(employees, ordered=False)
        print(f"Inserted {len(employees):,} employees.")

        print("Generating 100,000 attendance records...")
        batch = []
        inserted = 0

        for i in range(ATTENDANCE_COUNT):
            employee_number = (i % EMPLOYEE_COUNT) + 1
            employee = employees[employee_number - 1]
            day = START_DATE + timedelta(days=i // EMPLOYEE_COUNT)

            status = STATUSES[i % len(STATUSES)]
            late_minutes = 15 if i % 7 == 0 else 0
            half_day = status == "WFH" and i % 2 == 0

            punch_in = None
            punch_out = None
            work_hours = None

            if status in ("PRESENT", "WFH", "ON_DUTY"):
                punch_in = datetime.combine(
                    day, time(4, 0), tzinfo=timezone.utc
                )
                punch_out = punch_in + timedelta(hours=8)
                work_hours = 4.0 if half_day else 8.0

            batch.append({
                "emp_code": employee["emp_code"],
                "date": day.isoformat(),
                "status": status,
                "punch_in": punch_in,
                "punch_out": punch_out,
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": 0,
                "half_day": half_day,
                "history": [],
            })

            if len(batch) >= 2000:
                db.attendance_logs.insert_many(batch, ordered=False)
                inserted += len(batch)
                batch.clear()

                if inserted % 20_000 == 0:
                    print(f"Inserted {inserted:,} attendance records...")

        if batch:
            db.attendance_logs.insert_many(batch, ordered=False)
            inserted += len(batch)

        print("\nBenchmark dataset created successfully.")
        print("Database:", BENCHMARK_DB)
        print("Employees:", db.employees.count_documents({}))
        print("Attendance records:", db.attendance_logs.count_documents({}))

        # Create the same indexes defined by the application.
        db.employees.create_index(
            [("emp_code", ASCENDING)],
            unique=True,
            name="uq_employee_code",
        )
        db.employees.create_index(
            [("department", ASCENDING), ("emp_code", ASCENDING)],
            name="ix_employee_department_code",
        )
        db.employees.create_index(
            [("department", ASCENDING), ("joined_on", ASCENDING)],
            name="ix_employee_department_joined",
        )
        db.employees.create_index(
            [("joined_on", ASCENDING)],
            name="ix_employee_joined",
        )
        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            name="uq_attendance_employee_date",
        )
        db.attendance_logs.create_index(
            [("date", DESCENDING), ("emp_code", ASCENDING)],
            name="ix_attendance_date_employee",
        )
        db.attendance_logs.create_index(
            [("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)],
            name="ix_attendance_employee_date_status",
        )
        db.attendance_logs.create_index(
            [("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)],
            name="ix_attendance_date_status_late",
        )

        print("Application indexes created in benchmark database.")

    finally:
        client.close()

if __name__ == "__main__":
    main()
