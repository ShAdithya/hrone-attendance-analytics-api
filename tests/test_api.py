
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"



def test_employee_pagination_returns_two_records():
    response = client.get("/employees?page=1&page_size=2")

    assert response.status_code == 200

    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] >= 6
    assert data["page"] == 1
    assert data["page_size"] == 2



def test_employee_pagination_second_page():
    response = client.get("/employees?page=2&page_size=2")

    assert response.status_code == 200

    data = response.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["emp_code"] == "EMP0003"
    assert data["items"][1]["emp_code"] == "EMP0004"


def test_employee_pagination_rejects_invalid_page():
    response = client.get("/employees?page=0&page_size=2")

    assert response.status_code == 422
def test_employee_monthly_explain_uses_full_aggregation():
    response = client.get(
        "/admin/explain/employee_monthly",
        params={"emp_code": "EMP0001", "month": "2026-10"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["endpoint"] == "employee_monthly"
    assert data["collection"] == "attendance_logs"

    pipeline = data["explain"]["command"]["pipeline"]
    stages = {next(iter(stage)) for stage in pipeline}

    assert "$group" in stages
