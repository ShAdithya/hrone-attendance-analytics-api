from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app, db, ensure_indexes

client = TestClient(app)
UTC = timezone.utc


def epoch_ms(value):
    return int(value.timestamp() * 1000)


def test_half_day_is_based_on_rounded_work_hours():
    ensure_indexes()

    emp_code = "EMP999997"
    employee = {
        "emp_code": emp_code,
        "name": "Half Day Test Employee",
        "email": "halfday.test@example.com",
        "department": "Testing",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-01-01",
    }

    existing = client.get(
        "/employees",
        params={"page": 1, "page_size": 100},
    )
    codes = {item["emp_code"] for item in existing.json()["items"]}

    if emp_code not in codes:
        response = client.post("/employees", json=employee)
        assert response.status_code == 201

    test_cases = [
        ("2026-10-13", 8, 29, 40, 4.49, True),
        ("2026-10-14", 8, 30, 0, 4.5, False),
    ]

    for day, out_hour, out_minute, out_second, expected_hours, expected_half_day in test_cases:
        db.attendance_logs.delete_one({"emp_code": emp_code, "date": day})

        punch_in = datetime.fromisoformat(day).replace(
            hour=4, minute=0, second=0, tzinfo=UTC
        )
        punch_out = punch_in.replace(
            hour=out_hour, minute=out_minute, second=out_second
        )

        response = client.post(
            "/attendance/punch-in",
            json={
                "emp_code": emp_code,
                "punched_at": epoch_ms(punch_in),
                "status": "PRESENT",
            },
        )
        assert response.status_code == 201

        response = client.post(
            "/attendance/punch-out",
            json={
                "emp_code": emp_code,
                "punched_at": epoch_ms(punch_out),
            },
        )
        assert response.status_code == 200

        result = response.json()
        assert result["work_hours"] == expected_hours
        assert result["half_day"] is expected_half_day
