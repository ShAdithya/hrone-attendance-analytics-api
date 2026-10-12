"""HROne Employee Attendance & Analytics API, contract v2.0."""
import os
import re
import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import json_util

load_dotenv()  # Existing environment variables take precedence over .env.
client = MongoClient(os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017"), tz_aware=False, serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
MIN_EPOCH = 100_000_000_000
MAX_EPOCH = 4_102_444_800_000
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def ensure_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employee_department_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="ix_employee_department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)], name="ix_attendance_employee_date_status")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)], name="ix_attendance_date_status_late")


@app.on_event("startup")
def startup():
    ensure_indexes()


def valid_epoch(value: Any) -> int:
    if type(value) is not int or not MIN_EPOCH <= value <= MAX_EPOCH:
        raise ValueError("timestamp must be an integer epoch-millisecond value in the supported range")
    return value


def from_epoch_ms(value: int) -> datetime:
    valid_epoch(value)
    # Truncate to whole seconds before storage/calculation.
    return datetime.fromtimestamp(value // 1000, tz=UTC).replace(microsecond=0).astimezone(IST)


def to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)  # PyMongo naive dates represent UTC.
    return int(value.timestamp() * 1000)


def mongo_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).replace(tzinfo=None)
    return value.astimezone(UTC).replace(tzinfo=None)


def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    local = punch_in.astimezone(IST)
    sh, sm = map(int, shift_start.split(":")); eh, em = map(int, shift_end.split(":"))
    if (eh, em) <= (sh, sm) and (local.hour, local.minute) < (eh, em):
        local -= timedelta(days=1)
    return local.date().isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    local = punch_in.astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    start = local.replace(hour=h, minute=m, second=0, microsecond=0)
    seconds = (local - start).total_seconds()
    return int(seconds // 60) if seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    return float((seconds / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    sh, sm = map(int, shift_start.split(":")); eh, em = map(int, shift_end.split(":"))
    end_day = date.fromisoformat(date_str)
    if (eh, em) <= (sh, sm): end_day += timedelta(days=1)
    end = datetime.combine(end_day, time(eh, em), tzinfo=IST)
    seconds = (punch_out.astimezone(IST) - end).total_seconds()
    mins = int(seconds // 60)
    return mins if mins >= 30 else 0


def round_half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def serialize_history(history: list) -> list:
    result = []
    for entry in history or []:
        changes = {}
        for key, change in entry.get("changes", {}).items():
            changes[key] = {"from": to_epoch_ms(change.get("from")) if key in {"punch_in", "punch_out"} else change.get("from"),
                            "to": to_epoch_ms(change.get("to")) if key in {"punch_in", "punch_out"} else change.get("to")}
        result.append({"at": to_epoch_ms(entry.get("at")), "by": entry.get("by"), "reason": entry.get("reason"), "changes": changes})
    return result


def serialize_attendance(doc: dict) -> dict:
    return {"emp_code": doc["emp_code"], "date": doc["date"], "status": doc["status"],
            "punch_in": to_epoch_ms(doc.get("punch_in")), "punch_out": to_epoch_ms(doc.get("punch_out")),
            "work_hours": doc.get("work_hours"), "late_minutes": int(doc.get("late_minutes", 0)),
            "overtime_minutes": int(doc.get("overtime_minutes", 0)), "half_day": bool(doc.get("half_day", False)),
            "history": serialize_history(doc.get("history", []))}


def serialize_employee(doc: dict) -> dict:
    out = {k: doc.get(k) for k in ("emp_code", "name", "email", "department", "shift_start", "shift_end", "joined_on")}
    out["created_at"] = to_epoch_ms(doc.get("created_at"))
    return out


def month_bounds(month: str) -> tuple[str, str, date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(422, "month must be YYYY-MM")
    y, m = map(int, month.split("-")); first = date(y, m, 1); last = date(y, m, calendar.monthrange(y, m)[1])
    return first.isoformat(), last.isoformat(), first, last


def weekdays_between(start: date, end: date) -> int:
    if end < start: return 0
    count = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5: count += 1
        cur += timedelta(days=1)
    return count


def validate_date_string(value: str) -> str:
    try: return date.fromisoformat(value).isoformat()
    except Exception: raise HTTPException(422, "date must be YYYY-MM-DD")


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str
    @field_validator("joined_on")
    @classmethod
    def joined_date(cls, v):
        date.fromisoformat(v); return v
    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end: raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"
    @field_validator("punched_at")
    @classmethod
    def timestamp(cls, v): return None if v is None else valid_epoch(v)


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[int] = None
    @field_validator("punched_at")
    @classmethod
    def timestamp(cls, v): return None if v is None else valid_epoch(v)


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    @field_validator("punch_in", "punch_out")
    @classmethod
    def timestamps(cls, v): return None if v is None else valid_epoch(v)


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump(); doc["created_at"] = mongo_dt(datetime.now(UTC).replace(microsecond=0))
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {} if department is None else {"department": department}
    total = db.employees.count_documents(q)
    docs = db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size)
    return {"items": [serialize_employee(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "unknown employee")
    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0).astimezone(IST)
    ts = ts.replace(microsecond=0)
    day = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": mongo_dt(ts), "punch_out": None,
           "work_hours": None, "late_minutes": compute_late_minutes(ts, emp["shift_start"]), "overtime_minutes": 0, "half_day": False, "history": []}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "already punched in for this date")
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "unknown employee")
    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else datetime.now(UTC).replace(microsecond=0).astimezone(IST)
    ts = ts.replace(microsecond=0)
    # Select the most recent punch-in not later than the requested instant, including overnight shifts.
    ts_utc = mongo_dt(ts)
    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$ne": None, "$lte": ts_utc}}, sort=[("punch_in", DESCENDING)])
    if not record: raise HTTPException(404, "no punch-in found")
    pin = record.get("punch_in")
    if pin.tzinfo is None: pin = pin.replace(tzinfo=UTC)
    else: pin = pin.astimezone(UTC)
    pin = pin.astimezone(IST)
    delta = (ts.astimezone(UTC) - pin.astimezone(UTC)).total_seconds()
    if delta <= 0 or delta > 86400: raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    work = compute_work_hours(pin, ts)
    overtime = compute_overtime(ts, emp["shift_start"], emp["shift_end"], record["date"])
    update = {"punch_out": ts_utc, "work_hours": work, "overtime_minutes": overtime, "half_day": work < 4.50}
    result = db.attendance_logs.update_one({"_id": record["_id"], "punch_out": None}, {"$set": update})
    if result.modified_count != 1: raise HTTPException(409, "record already punched out")
    record.update(update)
    return serialize_attendance(record)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                   status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if emp_code is not None: q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from: q["date"]["$gte"] = validate_date_string(date_from)
        if date_to: q["date"]["$lte"] = validate_date_string(date_to)
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must not exceed date_to")
    if status:
        if status not in STATUSES: raise HTTPException(422, "invalid status")
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    docs = db.attendance_logs.find(q).sort("date", DESCENDING).skip((page-1)*page_size).limit(page_size)
    return {"items": [serialize_attendance(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    date = validate_date_string(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "unknown employee")
    old = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not old: raise HTTPException(404, "attendance record not found")
    requested = body.model_dump(exclude={"reason", "regularized_by"}, exclude_unset=True)
    status = requested.get("status", old["status"])
    if status in {"ABSENT", "LEAVE"} and ("punch_in" in requested or "punch_out" in requested):
        raise HTTPException(422, "ABSENT/LEAVE cannot include punch times")
    if status in PRESENCE:
        if "punch_in" in requested: pin = from_epoch_ms(requested["punch_in"])
        else:
            pin = old.get("punch_in")
            if pin is not None and pin.tzinfo is None: pin = pin.replace(tzinfo=UTC).astimezone(IST)
        if pin is None: raise HTTPException(422, "presence status requires punch_in")
        if attendance_date(pin, emp["shift_start"], emp["shift_end"]) != date: raise HTTPException(422, "punch_in must belong to this attendance date")
        if "punch_out" in requested: pout = from_epoch_ms(requested["punch_out"])
        else:
            pout = old.get("punch_out")
            if pout is not None and pout.tzinfo is None: pout = pout.replace(tzinfo=UTC).astimezone(IST)
        if pout is not None:
            if (pout.astimezone(UTC)-pin.astimezone(UTC)).total_seconds() <= 0 or (pout.astimezone(UTC)-pin.astimezone(UTC)).total_seconds() > 86400:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        computed = {"late_minutes": compute_late_minutes(pin, emp["shift_start"]),
                    "work_hours": compute_work_hours(pin, pout) if pout else None,
                    "overtime_minutes": compute_overtime(pout, emp["shift_start"], emp["shift_end"], date) if pout else 0,
                    "half_day": bool(pout and compute_work_hours(pin, pout) < 4.50),
                    "punch_in": mongo_dt(pin), "punch_out": mongo_dt(pout)}
    else:
        computed = {"punch_in": None, "punch_out": None, "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    computed["status"] = status
    changes = {}
    for key, new in computed.items():
        oldv = old.get(key, False if key == "half_day" else 0 if key in {"late_minutes", "overtime_minutes"} else None)
        if oldv != new:
            changes[key] = {"from": oldv, "to": new}
    if not changes: raise HTTPException(422, "request does not change the record")
    entry = {"at": mongo_dt(datetime.now(UTC).replace(microsecond=0)), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    # Optimistic compare-and-set prevents concurrent corrections losing history.
    result = db.attendance_logs.update_one({"_id": old["_id"], "history": old.get("history", [])},
                                           {"$set": computed, "$push": {"history": entry}})
    if result.modified_count != 1: raise HTTPException(409, "record changed concurrently; retry correction")
    old.update(computed); old["history"] = old.get("history", []) + [entry]
    return serialize_attendance(old)


def month_match(month: str) -> dict:
    first, last, _, _ = month_bounds(month)
    return {"date": {"$gte": first, "$lte": last}}


def present_value_stage():
    return {"$addFields": {"_present_value": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]},
                           "_is_weekday": {"$and": [{"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}]}}}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "unknown employee")
    first_s, last_s, first, last = month_bounds(month)
    joined = max(first, date.fromisoformat(emp["joined_on"]))
    working = weekdays_between(joined, last) if joined <= last else 0
    pipeline = [{"$match": {"emp_code": emp_code, **month_match(month)}}, present_value_stage(),
                {"$group": {"_id": None,
                 "present_days": {"$sum": {"$cond": [{"$and": ["$_is_weekday", {"$in": ["$status", list(PRESENCE)]}]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                 "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                 "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                 "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                 "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}}]
    row = next(db.attendance_logs.aggregate(pipeline), {})
    pct = round_half_up(row.get("present_days", 0) / working * 100, 4) if working else None
    return {"emp_code": emp_code, "month": month, "working_days": working, "present_days": row.get("present_days", 0),
            "leave_days": row.get("leave_days", 0), "late_count": row.get("late_count", 0),
            "total_late_minutes": row.get("total_late_minutes", 0), "total_overtime_minutes": row.get("total_overtime_minutes", 0), "attendance_pct": pct}


@app.get("/analytics/departments/summary")
def department_summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: Optional[str] = None):
    first_s, last_s, first, last = month_bounds(month)
    emp_match = {"joined_on": {"$lte": last_s}}
    if department is not None: emp_match["department"] = department
    pipeline = [{"$match": emp_match}, {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", first_s]}, {"$lte": ["$date", last_s]}]}}},
        {"$addFields": {"_present": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}}}], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$department", "headcount": {"$addToSet": "$emp_code"},
         "present_days": {"$sum": {"$cond": [{"$and": [{"$ne": ["$logs", None]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 2]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 6]}]}, {"$ifNull": ["$logs._present", 0]}, 0]}},
         "work_hours": {"$push": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", "$$REMOVE"]}},
         "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
         "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
         "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
         "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$headcount"}, "present_days": 1,
         "avg_work_hours": {"$cond": [{"$gt": [{"$size": "$work_hours"}, 0]}, {"$avg": "$work_hours"}, None]},
         "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}}, {"$sort": {"department": 1}}]
    rows = list(db.employees.aggregate(pipeline))
    # $round uses banker's rounding in MongoDB; apply half-up to reported average.
    for r in rows:
        if r.get("avg_work_hours") is not None: r["avg_work_hours"] = round_half_up(r["avg_work_hours"], 2)
    return {"month": month, "items": rows}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    first_s, last_s, _, _ = month_bounds(month)
    pipeline = [{"$match": {**month_match(month), "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": 1}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}}, {"$unwind": "$employee"}]
    if department is not None: pipeline.append({"$match": {"employee.department": department}})
    pipeline += [{"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
                 {"$match": {"rank": {"$lte": limit}}}, {"$sort": {"total_late_minutes": -1, "_id": 1}},
                 {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}}]
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    from_s = validate_date_string(from_date); to_s = validate_date_string(to_date)
    start = date.fromisoformat(from_s); end = date.fromisoformat(to_s)
    if end < start or (end-start).days + 1 > 92: raise HTTPException(422, "invalid date range; maximum is 92 days")
    if not db.employees.find_one({"department": department}): raise HTTPException(404, "unknown department")
    # Gap filling and moving window happen in MongoDB. Join employee headcount by date after densifying.
    start_dt = datetime.combine(start, time.min, tzinfo=UTC); end_dt = datetime.combine(end, time.min, tzinfo=UTC)
    pipeline = [{"$match": {"date": {"$gte": from_s, "$lte": to_s}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"}, {"$match": {"employee.department": department}},
        {"$group": {"_id": "$date", "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
                     "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$project": {"_id": 0, "date": {"$dateFromString": {"dateString": "$_id"}}, "present_count": 1, "late_count": 1}},
        {"$densify": {"field": "date", "range": {"step": 1, "unit": "day", "bounds": [start_dt, end_dt + timedelta(days=1)]}}},
        {"$set": {"date_str": {"$dateToString": {"date": "$date", "format": "%Y-%m-%d", "timezone": "UTC"}}, "present_count": {"$ifNull": ["$present_count", 0]}, "late_count": {"$ifNull": ["$late_count", 0]}}},
        {"$lookup": {"from": "employees", "let": {"day": "$date_str"}, "pipeline": [{"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}}, {"$count": "n"}], "as": "hc"}},
        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]}, "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": "$date"}, [1, 7]]}]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$divide": ["$present_count", "$headcount"]}, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$project": {"_id": 0, "date": "$date_str", "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}},
        {"$sort": {"date": 1}}]
    items = list(db.attendance_logs.aggregate(pipeline, allowDiskUse=True))
    for item in items:
        for key in ("attendance_rate", "moving_avg_7d"):
            if item.get(key) is not None: item[key] = round_half_up(item[key], 4)
    # Empty-log departments still need one row per date; densify needs at least one input document.
    if not items:
        cur = start; items = []
        while cur <= end:
            working = cur.weekday() < 5
            headcount = db.employees.count_documents({"department": department, "joined_on": {"$lte": cur.isoformat()}})
            items.append({"date": cur.isoformat(), "is_working_day": working, "headcount": headcount, "present_count": 0, "late_count": 0,
                          "attendance_rate": 0.0 if working and headcount else None, "moving_avg_7d": None})
            cur += timedelta(days=1)
        for i, item in enumerate(items):
            vals = [x["attendance_rate"] for x in items[max(0, i-6):i+1] if x["attendance_rate"] is not None]
            item["moving_avg_7d"] = round_half_up(sum(vals)/len(vals), 4) if vals else None
    return {"department": department, "items": items}


def bson_explain(collection, pipeline=None, query=None, sort=None, skip=0, limit=None):
    if pipeline is not None:
        return db.command({"explain": {"aggregate": collection.name, "pipeline": pipeline, "cursor": {}, "allowDiskUse": True}, "verbosity": "executionStats"})
    find_cmd = {"find": collection.name, "filter": query or {}, "sort": sort or {}}
    if skip: find_cmd["skip"] = skip
    if limit: find_cmd["limit"] = limit
    return db.command({"explain": find_cmd, "verbosity": "executionStats"})


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
                     emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
                     limit: int = Query(10, ge=1, le=50), date_from: Optional[str] = None, date_to: Optional[str] = None,
                     status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     from_date: Optional[str] = Query(None, alias="from"), to_date: Optional[str] = Query(None, alias="to")):
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from: q["date"]["$gte"] = validate_date_string(date_from)
            if date_to: q["date"]["$lte"] = validate_date_string(date_to)
        if status: q["status"] = status
        exp = bson_explain(db.attendance_logs, query=q, sort={"date": -1}, skip=(page-1)*page_size, limit=page_size)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": json_util.loads(json_util.dumps(exp))}
    if endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "emp_code and month are required")
        month_bounds(month); pipe = [{"$match": {"emp_code": emp_code, **month_match(month)}}]
        exp = bson_explain(db.attendance_logs, pipeline=pipe); coll = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "month is required")
        first_s, last_s, _, last = month_bounds(month); q = {"joined_on": {"$lte": last_s}}
        if department: q["department"] = department
        exp = bson_explain(db.employees, query=q, sort={"department": 1}); coll = "employees"
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "month is required")
        pipe = [{"$match": {**month_match(month), "late_minutes": {"$gt": 0}}}, {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}}]
        exp = bson_explain(db.attendance_logs, pipeline=pipe); coll = "attendance_logs"
    else:
        if not department or not from_date or not to_date: raise HTTPException(422, "department, from and to are required")
        validate_date_string(from_date); validate_date_string(to_date)
        pipe = [{"$match": {"date": {"$gte": from_date, "$lte": to_date}}}]
        exp = bson_explain(db.attendance_logs, pipeline=pipe); coll = "attendance_logs"
    return {"endpoint": endpoint, "collection": coll, "explain": json_util.loads(json_util.dumps(exp))}
