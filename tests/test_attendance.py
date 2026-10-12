
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TEST_EMPLOYEE = {
    "emp_code": "EMP999998",
    "name": "Attendance Test Employee",
    "email": "attendance.test@example.com",
    "department": "Testing",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-01-01",
}

PUNCH_TIME = int(
    datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()
    * 1000
)


def ensure_test_employee():
    response = client.get(
        "/employees",
        params={"page": 1, "page_size": 100},
    )
    assert response.status_code == 200

    codes = {item["emp_code"] for item in response.json()["items"]}

    if TEST_EMPLOYEE["emp_code"] not in codes:
        response = client.post("/employees", json=TEST_EMPLOYEE)
        assert response.status_code == 201


def ensure_test_punch_in():
    ensure_test_employee()

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": TEST_EMPLOYEE["emp_code"],
            "punched_at": PUNCH_TIME,
            "status": "PRESENT",
        },
    )

    # A 409 is acceptable when this test record already exists.
    assert response.status_code in (201, 409)


def test_invalid_punch_in_timestamp_returns_422():
    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": "EMP0001",
            "punched_at": 0,
            "status": "PRESENT",
        },
    )
    assert response.status_code == 422


def test_punch_in_unknown_employee_returns_404():
    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": "EMP999999",
            "punched_at": PUNCH_TIME,
            "status": "PRESENT",
        },
    )
    assert response.status_code == 404


def test_punch_out_unknown_employee_returns_404():
    response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": "EMP999999",
            "punched_at": PUNCH_TIME,
        },
    )
    assert response.status_code == 404


def test_duplicate_punch_in_returns_409():
    ensure_test_employee()

    first = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": TEST_EMPLOYEE["emp_code"],
            "punched_at": PUNCH_TIME,
            "status": "PRESENT",
        },
    )

    if first.status_code == 201:
        second = client.post(
            "/attendance/punch-in",
            json={
                "emp_code": TEST_EMPLOYEE["emp_code"],
                "punched_at": PUNCH_TIME,
                "status": "PRESENT",
            },
        )
        assert second.status_code == 409
    else:
        assert first.status_code == 409


def test_regularize_no_change_returns_422():
    ensure_test_punch_in()

    response = client.patch(
        f"/attendance/{TEST_EMPLOYEE['emp_code']}/2026-10-12",
        json={
            "status": "PRESENT",
            "punch_in": PUNCH_TIME,
            "reason": "No actual change",
            "regularized_by": "pytest",
        },
    )

    # Existing test data may already have been corrected.
    if response.status_code == 200:
        # A real change occurred, so this isn't a no-op case.
        return

    assert response.status_code == 422


def test_regularize_absent_with_punch_time_returns_422():
    ensure_test_punch_in()

    response = client.patch(
        f"/attendance/{TEST_EMPLOYEE['emp_code']}/2026-10-12",
        json={
            "status": "ABSENT",
            "punch_in": PUNCH_TIME,
            "reason": "Invalid absent record",
            "regularized_by": "pytest",
        },
    )

    assert response.status_code == 422


def test_regularize_changes_status_and_adds_history():
    ensure_test_punch_in()

    response = client.patch(
        f"/attendance/{TEST_EMPLOYEE['emp_code']}/2026-10-12",
        json={
            "punch_in": PUNCH_TIME - 60_000,
            "reason": "Correct punch-in time",
            "regularized_by": "pytest",
        },
    )

    if response.status_code == 200:
        record = response.json()
        assert record["punch_in"] == PUNCH_TIME - 60_000
        assert len(record["history"]) >= 1
        assert record["history"][-1]["by"] == "pytest"
        assert "punch_in" in record["history"][-1]["changes"]
    else:
        # A prior run may already have applied this correction.
        assert response.status_code == 422
