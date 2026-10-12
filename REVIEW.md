# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for employee management, attendance tracking, attendance corrections, and analytics.

## Features

- Employee creation and paginated employee listing.
- Attendance punch-in and punch-out.
- Attendance listing with filters and pagination.
- Attendance corrections with an audit history.
- Monthly employee analytics and department summaries.
- Late-arrival leaderboard and department attendance trends.
- MongoDB query execution statistics through the admin explain endpoint.
- Input validation, duplicate protection, and database indexes.

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Dependencies listed in `requirements.txt`

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Configure a `.env` file in the project root:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

Ensure MongoDB is running, then load the provided sample data:

```bash
python sample_seed.py
```

**Note:** The seed script reloads the sample collections. Use it only when you intend to reset the sample data.

Start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

## API documentation

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Tests

Run the automated tests:

```bash
python -m pytest -q
```

The current local test suite contains 11 tests. Run the suite again after making changes.

## Project files

- `app/main.py` — API implementation
- `openapi.yaml` — API contract and business rules
- `DATA_MODEL.md` — MongoDB data model
- `sample_data/` — sample employee and attendance records
- `sample_seed.py` — sample data loader
- `tests/` — automated tests
- `REVIEW.md` — review of starter-code defects
- `DECISIONS.md` — implementation decisions

## Security

Do not commit `.env`, database credentials, API keys, or other secrets. The `.gitignore` file excludes `.env` and the local virtual environment.