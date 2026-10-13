from datetime import datetime, timedelta, timezone

from app.main import compute_late_minutes

IST = timezone(timedelta(hours=5, minutes=30))


def test_overnight_shift_late_minutes_uses_previous_day_start():
    # A 01:00 punch belongs to the shift that started at 22:00 yesterday.
    punch_in = datetime(2026, 10, 13, 1, 0, tzinfo=IST)

    assert compute_late_minutes(punch_in, "22:00", "06:00") == 180


def test_early_punch_for_daytime_shift_is_not_late():
    punch_in = datetime(2026, 10, 13, 8, 0, tzinfo=IST)
    assert compute_late_minutes(punch_in, "09:30", "18:30") == 0


def test_exactly_ten_minutes_late_is_not_counted():
    punch_in = datetime(2026, 10, 13, 9, 40, 0, tzinfo=IST)
    assert compute_late_minutes(punch_in, "09:30", "18:30") == 0


def test_ten_minutes_and_one_second_late_counts_whole_minutes():
    punch_in = datetime(2026, 10, 13, 9, 40, 1, tzinfo=IST)
    assert compute_late_minutes(punch_in, "09:30", "18:30") == 10


from app.main import compute_overtime


def test_overtime_of_29_minutes_is_zero():
    punch_out = datetime(2026, 10, 13, 18, 59, tzinfo=IST)
    assert compute_overtime(punch_out, "09:30", "18:30", "2026-10-13") == 0


def test_overtime_of_exactly_30_minutes_is_counted():
    punch_out = datetime(2026, 10, 13, 19, 0, tzinfo=IST)
    assert compute_overtime(punch_out, "09:30", "18:30", "2026-10-13") == 30


def test_overnight_shift_overtime_uses_next_day_end():
    punch_out = datetime(2026, 10, 14, 6, 30, tzinfo=IST)
    assert compute_overtime(punch_out, "22:00", "06:00", "2026-10-13") == 30


from app.main import compute_work_hours


def test_work_hours_round_half_up():
    punch_in = datetime(2026, 10, 13, 9, 0, 0, tzinfo=IST)
    punch_out = datetime(2026, 10, 13, 13, 30, 18, tzinfo=IST)
    assert compute_work_hours(punch_in, punch_out) == 4.51


def test_work_hours_rounds_to_two_decimal_places():
    punch_in = datetime(2026, 10, 13, 9, 0, 0, tzinfo=IST)
    punch_out = datetime(2026, 10, 13, 13, 30, 0, tzinfo=IST)
    assert compute_work_hours(punch_in, punch_out) == 4.5
