from datetime import date, datetime, time, timezone

from fastapi.testclient import TestClient

from app.main import app, db

client = TestClient(app)


def test_department_trend_fills_missing_days_and_weekends():
    department = "TREND_TEST"
    emp_code = "EMP999998"

    db.attendance_logs.delete_many({"emp_code": emp_code})
    db.employees.delete_many({"emp_code": emp_code})

    db.employees.insert_one({
        "emp_code": emp_code,
        "name": "Trend Test Employee",
        "email": "trend.test@example.com",
        "department": department,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-12",
    })

    response = client.get(
        f"/analytics/departments/{department}/trend",
        params={"from": "2026-10-12", "to": "2026-10-18"},
    )

    assert response.status_code == 200
    items = response.json()["items"]

    assert len(items) == 7
    assert [item["date"] for item in items] == [
        f"2026-10-{day}" for day in range(12, 19)
    ]
    assert [item["is_working_day"] for item in items] == [
        True, True, True, True, True, False, False
    ]
    assert all(item["headcount"] == 1 for item in items)
    assert all(item["present_count"] == 0 for item in items)
    assert items[0]["moving_avg_7d"] == 0.0
    assert items[5]["moving_avg_7d"] == 0.0
    assert items[6]["attendance_rate"] is None

    db.attendance_logs.delete_many({"emp_code": emp_code})
    db.employees.delete_many({"emp_code": emp_code})


def test_department_trend_rejects_ranges_over_92_days():
    response = client.get(
        "/analytics/departments/TREND_TEST/trend",
        params={"from": "2026-01-01", "to": "2026-04-03"},
    )

    assert response.status_code == 422


def test_department_trend_with_no_attendance_records():
    department = "TREND_EMPTY_TEST"
    emp_code = "EMP999997"

    db.attendance_logs.delete_many({"emp_code": emp_code})
    db.employees.delete_many({"emp_code": emp_code})
    db.employees.delete_many({"department": department})

    db.employees.insert_one({
        "emp_code": emp_code,
        "name": "Empty Trend Employee",
        "email": "empty.trend@example.com",
        "department": department,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-12",
    })

    try:
        response = client.get(
            f"/analytics/departments/{department}/trend",
            params={"from": "2026-10-12", "to": "2026-10-18"},
        )

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 7
        assert [item["date"] for item in items] == [
            f"2026-10-{day}" for day in range(12, 19)
        ]
        assert all(item["headcount"] == 1 for item in items)
        assert all(item["present_count"] == 0 for item in items)
        assert all(item["late_count"] == 0 for item in items)
        assert items[0]["attendance_rate"] == 0.0
        assert items[0]["moving_avg_7d"] == 0.0
        assert items[5]["attendance_rate"] is None
        assert items[5]["moving_avg_7d"] == 0.0
    finally:
        db.attendance_logs.delete_many({"emp_code": emp_code})
        db.employees.delete_many({"emp_code": emp_code})
