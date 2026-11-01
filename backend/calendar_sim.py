"""
The simulation calendar. A campaign run counts days (round 1 = the start date, one round a day); a governing season
counts quarters (round 1 = Q1 of year one, starting the January after the election). Every round carries an ISO date
and a label, and every prompt is told the date, so nobody hashtags a year that is sixteen years gone.
"""
from datetime import date, timedelta

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def make(start_year, start_month=1, unit="day", start_day=1, step_days=1):
    """unit 'day' with step_days: a campaign spreads itself over the time it has (see pacing); 'quarter' for a term."""
    return {"unit": unit, "start": f"{int(start_year):04d}-{int(start_month):02d}-{int(start_day):02d}", "step_days": int(step_days)}


def pacing(rounds, election):
    """How many days a campaign round covers: an election campaign fills about three months to the vote, otherwise a week a round."""
    if election:
        return max(3, round(90 / max(1, rounds)))
    return 7


def _start(cal):
    y, m, d = (int(x) for x in cal["start"].split("-"))
    return date(y, m, d)


def round_date(cal, rnum):
    """ISO date of a round (round 0 is the seed day / inauguration month)."""
    s = _start(cal)
    if cal.get("unit") == "quarter":
        q = max(0, rnum - 1)
        m = s.month - 1 + 3 * q
        return date(s.year + m // 12, m % 12 + 1, 1)
    return s + timedelta(days=max(0, rnum - 1) * int(cal.get("step_days", 1) or 1))


def label(cal, rnum):
    d = round_date(cal, rnum)
    if cal.get("unit") == "quarter":
        return f"{MONTHS[d.month - 1]} {d.year}"
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def prompt_line(cal, rnum):
    d = round_date(cal, rnum)
    if cal.get("unit") == "quarter":
        return f"TODAY'S DATE: {MONTHS[d.month - 1]} {d.year} (quarter {max(1, rnum)} of the term). Reference only this year and the years before it as the past; never treat earlier years as the present."
    step = int(cal.get("step_days", 1) or 1)
    gap = "" if rnum <= 1 else f" ({step} days since the last round)" if step > 1 else ""
    return f"TODAY'S DATE: {MONTHS[d.month - 1]} {d.day}, {d.year}{gap}. Reference only this year and the years before it as the past; never treat earlier years as the present."


def ts(cal, rnum, hour=8, minute=0):
    d = round_date(cal, rnum)
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}"


def end_year(cal, rounds):
    return round_date(cal, rounds).year


def next_season_start(cal, rounds):
    """The January after the election that ended this run."""
    return make(end_year(cal, rounds) + 1, 1, "quarter", 20)
