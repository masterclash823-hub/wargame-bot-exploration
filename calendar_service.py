"""Atomic calendar configuration and recovery without paying any month twice."""
import math
from datetime import datetime, timedelta, timezone

import db
from economy_engine import _config, _set
from world_service import world_lock, tr


def utc(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime): return None
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def lock(c):
    c.execute("INSERT INTO economy_meta(key,value) VALUES('tick_lock','1') ON CONFLICT(key) DO NOTHING")
    c.execute("SELECT value FROM economy_meta WHERE key='tick_lock'" + (' FOR UPDATE' if db.USE_POSTGRES else ''))
    c.fetchone()
    world_lock(c)


def speed(value):
    try: hours = float(value)
    except (ValueError, TypeError): hours = 0
    if not math.isfinite(hours) or not 1/60 <= hours <= 8760:
        raise ValueError(tr('Ustaw od 1 minuty do 8760 godzin na miesiąc (/calendar set).',
                            'Set between 1 minute and 8760 hours per month (/calendar set).'))
    return hours


def recover_date(c):
    current = int(_config(c, 'current_year', '1'))*12 + int(_config(c, 'current_month', '1'))-1
    c.execute('SELECT MAX(month_index) AS last FROM economy_months')
    latest = c.fetchone()['last']
    if latest is not None and current < latest:
        # The ledger proves this date was already paid; only restore the clock.
        current = latest
        _set(c, 'current_year', current//12)
        _set(c, 'current_month', current%12+1)
    return current


def configure(hours, channel_id, start_month=None, start_year=None, now=None):
    hours = speed(hours)
    if start_month is not None and (type(start_month) is not int or not 1 <= start_month <= 12):
        raise ValueError(tr('Miesiąc musi być od 1 do 12.', 'Month must be between 1 and 12.'))
    if start_year is not None and (type(start_year) is not int or not 1 <= start_year <= 9999):
        raise ValueError(tr('Rok musi być od 1 do 9999.', 'Year must be between 1 and 9999.'))
    now = utc(now or datetime.now(timezone.utc))
    with db.atomic() as c:
        lock(c)
        current = recover_date(c)
        month = start_month if start_month is not None else current%12+1
        year = start_year if start_year is not None else current//12
        target = year*12+month-1
        c.execute('SELECT COUNT(*) AS n FROM economy_months')
        if c.fetchone()['n'] and target != current:
            raise ValueError(tr('Gra ma już rozliczone miesiące. Zmień prędkość lub kanał bez daty; do przesuwania czasu użyj /admineco tick.',
                                'Months have already been settled. Change speed/channel without a date; use /admineco tick to advance time.'))
        last = utc(_config(c, 'last_tick_ts'))
        try: old_hours = speed(_config(c, 'hours_per_month', '24'))
        except ValueError: old_hours = hours
        running = _config(c, 'calendar_running', '0') == '1'
        endpoint = now if running else utc(_config(c, 'calendar_paused_at')) or now
        if last:
            elapsed = max(0, (endpoint-last).total_seconds()) if target == current else 0
            _set(c, 'last_tick_ts', (endpoint-timedelta(seconds=elapsed*hours/old_hours)).isoformat())
        _set(c, 'hours_per_month', hours)
        _set(c, 'announce_channel_id', channel_id)
        _set(c, 'current_month', month)
        _set(c, 'current_year', year)
        return month, year


def start(now=None):
    now = utc(now or datetime.now(timezone.utc))
    with db.atomic() as c:
        lock(c); recover_date(c)
        speed(_config(c, 'hours_per_month', '24'))
        running = _config(c, 'calendar_running', '0') == '1'
        last = utc(_config(c, 'last_tick_ts'))
        paused = utc(_config(c, 'calendar_paused_at'))
        if not last or (not running and not paused): last = now
        elif not running: last += max(timedelta(), now-paused)
        _set(c, 'last_tick_ts', last.isoformat())
        _set(c, 'calendar_paused_at', '')
        _set(c, 'calendar_running', '1')
        return not running


def stop(now=None):
    now = utc(now or datetime.now(timezone.utc))
    with db.atomic() as c:
        lock(c)
        if _config(c, 'calendar_running', '0') == '1':
            _set(c, 'calendar_paused_at', now.isoformat())
        _set(c, 'calendar_running', '0')


def status(now=None):
    now = utc(now or datetime.now(timezone.utc))
    with db.cursor() as c:
        hours = speed(_config(c, 'hours_per_month', '24'))
        last = utc(_config(c, 'last_tick_ts'))
        return dict(month=int(_config(c, 'current_month', '1')), year=int(_config(c, 'current_year', '1')),
                    running=_config(c, 'calendar_running', '0') == '1', hours=hours,
                    due=(last+timedelta(hours=hours)) if last else None,
                    error=_config(c, 'calendar_last_error'), error_at=_config(c, 'calendar_error_at'))


def record_failure(exc):
    # Public status reports a failure class; full details stay in server logs.
    with db.atomic() as c:
        _set(c, 'calendar_last_error', type(exc).__name__)
        _set(c, 'calendar_error_at', datetime.now(timezone.utc).isoformat())
