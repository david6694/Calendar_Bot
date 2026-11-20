import os
import sys
import re
import uuid
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests
from icalendar import Calendar

EMAIL = os.getenv("TIMETREE_EMAIL")
PASSWORD = os.getenv("TIMETREE_PASSWORD")
CALENDAR_ID = os.getenv("TIMETREE_CALENDAR_ID")
SKELLO_URL = os.getenv("SKELLO_URL")
VERBOSE_LOGS = os.getenv("VERBOSE_LOGS", "false").lower() in ("1", "true", "yes")

PARIS_TZ = ZoneInfo("Europe/Paris")

# TimeTree label color ID (3 = Deep sky blue / #47b2f7)
LABEL_ID = int(os.getenv("TIMETREE_LABEL_ID", "3"))

TIMETREE_BASE_URL = "https://timetreeapp.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "X-Timetreea": "web/2.1.0/en",
    "Accept": "application/json",
    "Origin": "https://timetreeapp.com",
    "Referer": "https://timetreeapp.com/",
}


def fetch_skello_shifts(skello_url):
    """Download and extract upcoming Skello work shifts."""
    if skello_url.startswith("webcal://"):
        skello_url = skello_url.replace("webcal://", "https://", 1)

    print("Downloading Skello schedule...")
    res = requests.get(skello_url, timeout=30)
    res.raise_for_status()

    cal = Calendar.from_ical(res.text)
    now_utc = datetime.now(timezone.utc)
    # Look from yesterday up to 35 days ahead
    min_horizon = now_utc - timedelta(days=1)
    max_horizon = now_utc + timedelta(days=35)
    shifts = []

    for component in cal.walk():
        if component.name == "VEVENT":
            summary = str(component.get("summary", "Work")).strip()

            # Exclude rest days, leave, and absences (French & English Skello labels)
            if any(term in summary.lower() for term in ["repos", "congé", "conge", "absence", "day off", "leave"]):
                continue

            dtstart = component.get("dtstart")
            dtend = component.get("dtend")
            if not dtstart or not dtend:
                continue

            start = dtstart.dt
            end = dtend.dt

            # Normalize to timezone-aware datetime in Europe/Paris
            if not isinstance(start, datetime):
                start = datetime.combine(start, datetime.min.time(), tzinfo=PARIS_TZ)
            elif start.tzinfo is None:
                start = start.replace(tzinfo=PARIS_TZ)
            else:
                start = start.astimezone(PARIS_TZ)

            if not isinstance(end, datetime):
                end = datetime.combine(end, datetime.max.time(), tzinfo=PARIS_TZ)
            elif end.tzinfo is None:
                end = end.replace(tzinfo=PARIS_TZ)
            else:
                end = end.astimezone(PARIS_TZ)

            start_utc = start.astimezone(timezone.utc)
            if min_horizon <= start_utc <= max_horizon:
                shifts.append({
                    "title": summary,
                    "start": start,
                    "end": end
                })

    shifts.sort(key=lambda s: s["start"])
    return shifts


class TimeTreeAPI:
    """Client for TimeTree's internal Web API."""

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.csrf_token = None
        self.client_uuid = uuid.uuid4().hex

    def login(self):
        """Authenticate via PUT /auth/email/signin and extract the CSRF token."""
        print("Signing in to TimeTree via API...")
        auth_url = f"{TIMETREE_BASE_URL}/auth/email/signin"
        payload = {
            "uid": self.email,
            "password": self.password,
            "uuid": self.client_uuid
        }

        res = self.session.put(
            auth_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )

        if res.status_code not in (200, 201):
            raise RuntimeError(f"TimeTree authentication failed ({res.status_code}): {res.text}")

        print("Authentication successful.")

        # Fetch the CSRF token from the home page
        home_res = self.session.get("https://timetreeapp.com/", timeout=30)
        csrf_match = re.search(
            r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
            home_res.text,
            re.IGNORECASE
        )
        if csrf_match:
            self.csrf_token = csrf_match.group(1)
            self.session.headers.update({"x-csrf-token": self.csrf_token})
            print("CSRF token retrieved.")
        else:
            print("Warning: CSRF meta tag not found, continuing anyway...")

    def get_calendar(self, target_identifier):
        """Find the target calendar by numeric ID, alias code, or name."""
        print("Looking up target calendar...")
        url = f"{TIMETREE_BASE_URL}/calendars?since=0"
        res = self.session.get(url, timeout=30)
        res.raise_for_status()

        calendars = res.json().get("calendars", [])
        active_cals = [c for c in calendars if not c.get("deactivated_at")]

        print(f"Found {len(active_cals)} active calendar(s) on account.")
        if VERBOSE_LOGS:
            for c in active_cals:
                print(f"  - '{c.get('name')}' (numeric ID: {c.get('id')}, alias: {c.get('alias_code')})")

        target_clean = str(target_identifier).strip().lower()
        for c in active_cals:
            c_id = str(c.get("id", "")).strip().lower()
            c_alias = str(c.get("alias_code", "")).strip().lower()
            c_name = str(c.get("name", "")).strip().lower()

            if target_clean in (c_id, c_alias, c_name):
                if VERBOSE_LOGS:
                    print(f"-> Selected calendar: '{c.get('name')}' (ID: {c.get('id')})")
                else:
                    print("-> Target calendar selected.")
                return c

        if len(active_cals) == 1:
            if VERBOSE_LOGS:
                print(f"-> Auto-selected the only active calendar: '{active_cals[0].get('name')}'")
            else:
                print("-> Auto-selected the only active calendar.")
            return active_cals[0]

        raise ValueError("Could not find a calendar matching TIMETREE_CALENDAR_ID.")

    def get_existing_events(self, calendar_id):
        """Fetch all active events from the calendar."""
        print("Fetching existing events from TimeTree...")
        events = []
        since = 0

        while True:
            url = f"{TIMETREE_BASE_URL}/calendar/{calendar_id}/events/sync?since={since}"
            res = self.session.get(url, timeout=30)
            res.raise_for_status()
            data = res.json()

            chunk_events = data.get("events", [])
            events.extend(chunk_events)

            if data.get("chunk") and data.get("since", 0) > since:
                since = data.get("since")
            else:
                break

        active_events = [e for e in events if not e.get("deactivated_at")]
        print(f"Found {len(active_events)} active event(s) on TimeTree.")
        return active_events

    def create_event(self, calendar_id, title, start_dt, end_dt, label_id=3):
        """Create an event with exact start/end times, timezone, and color label."""
        url = f"{TIMETREE_BASE_URL}/calendar/{calendar_id}/event"
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)

        payload = {
            "title": title,
            "all_day": False,
            "start_at": start_ms,
            "start_timezone": "Europe/Paris",
            "end_at": end_ms,
            "end_timezone": "Europe/Paris",
            "category": 1,
            "label_id": label_id,
            "attendees": [],
            "recurrences": [],
            "alerts": [],
            "file_uuids": []
        }

        headers = {"Content-Type": "application/json"}
        if self.csrf_token:
            headers["x-csrf-token"] = self.csrf_token

        res = self.session.post(url, json=payload, headers=headers, timeout=30)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create event ({res.status_code}): {res.text}")

        return res.json().get("event")

    def delete_event(self, calendar_id, event_uuid):
        """Delete a calendar event by its UUID."""
        url = f"{TIMETREE_BASE_URL}/calendar/{calendar_id}/event/{event_uuid}"
        headers = {"Content-Type": "application/json"}
        if self.csrf_token:
            headers["x-csrf-token"] = self.csrf_token

        res = self.session.delete(url, headers=headers, timeout=30)
        if res.status_code not in (200, 204):
            raise RuntimeError(f"Failed to delete event {event_uuid} ({res.status_code}): {res.text}")
        return True


def is_bot_event(ev):
    """
    Determine whether a TimeTree event is a shift synced by this bot
    (so personal events in the shared calendar are NEVER touched).
    """
    title = (ev.get("title") or "").strip().lower()
    # Skello shifts typically start with "shift:" or "shift "
    if title.startswith("shift:") or title.startswith("shift "):
        return True
    # Or carry the bot's color label and contain the word "shift"
    if ev.get("label_id") == LABEL_ID and "shift" in title:
        return True
    return False


def matches_shift(shift, ev):
    """
    Check whether a TimeTree event matches a Skello shift
    (same day, same start/end times, and same title).
    """
    start_dt = shift["start"]
    end_dt = shift["end"]
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    summary = shift["title"].strip().lower()
    time_start_str = start_dt.strftime("%H:%M")
    time_end_str = end_dt.strftime("%H:%M")
    shift_date = start_dt.date()

    ev_title = (ev.get("title") or "").strip().lower()
    ev_start_ms = ev.get("start_at", 0)
    ev_end_ms = ev.get("end_at", 0)

    # 1. Exact match by start/end timestamps (within 5 minutes)
    if abs(ev_start_ms - start_ms) < 5 * 60 * 1000:
        if abs(ev_end_ms - end_ms) < 5 * 60 * 1000:
            if summary in ev_title or ev_title in summary:
                return True

    # 2. Fallback match for legacy events (approximate start_at with times in title)
    try:
        ev_dt = datetime.fromtimestamp(ev_start_ms / 1000, tz=PARIS_TZ)
        if ev_dt.date() == shift_date:
            if summary in ev_title and time_start_str in ev_title and time_end_str in ev_title:
                return True
    except Exception:
        pass

    return False


def main():
    if not EMAIL or not PASSWORD:
        print("Error: TIMETREE_EMAIL and TIMETREE_PASSWORD environment variables are required.")
        sys.exit(1)

    if not SKELLO_URL:
        print("Error: SKELLO_URL environment variable is required.")
        sys.exit(1)

    if not CALENDAR_ID:
        print("Error: TIMETREE_CALENDAR_ID environment variable is required.")
        sys.exit(1)

    # 1. Fetch Skello shifts
    shifts = fetch_skello_shifts(SKELLO_URL)
    print(f"Fetched {len(shifts)} work shift(s) from Skello for the next 35 days.")

    # 2. Sign in to TimeTree
    api = TimeTreeAPI(EMAIL, PASSWORD)
    api.login()

    # 3. Resolve target calendar
    calendar = api.get_calendar(CALENDAR_ID)
    cal_id = calendar["id"]

    # 4. Fetch existing events
    existing_events = api.get_existing_events(cal_id)

    # 5. Define sync window (today through +35 days)
    today_date = datetime.now(PARIS_TZ).date()
    max_sync_date = (datetime.now(PARIS_TZ) + timedelta(days=35)).date()

    # Step A: Match existing TimeTree events against the Skello schedule
    matched_event_keys = set()
    shifts_to_create = []

    for shift in shifts:
        matched_ev = None
        for ev in existing_events:
            ev_key = ev.get("uuid") or str(ev.get("id"))
            if ev_key in matched_event_keys:
                continue
            if matches_shift(shift, ev):
                matched_ev = ev
                matched_event_keys.add(ev_key)
                break

        if matched_ev:
            if VERBOSE_LOGS:
                date_str = shift["start"].strftime("%Y-%m-%d")
                time_start = shift["start"].strftime("%H:%M")
                time_end = shift["end"].strftime("%H:%M")
                print(f"[UNCHANGED] {shift['title']} ({time_start}-{time_end}) on {date_str}")
        else:
            shifts_to_create.append(shift)

    # Step B: Find TimeTree shift events that are obsolete or cancelled on Skello
    events_to_delete = []
    for ev in existing_events:
        ev_key = ev.get("uuid") or str(ev.get("id"))
        if ev_key in matched_event_keys:
            continue

        # Safety: never touch personal events
        if not is_bot_event(ev):
            continue

        # Safety: only inspect upcoming dates (today inclusive) up to +35 days
        try:
            ev_start_ms = ev.get("start_at", 0)
            ev_dt = datetime.fromtimestamp(ev_start_ms / 1000, tz=PARIS_TZ)
            if today_date <= ev_dt.date() <= max_sync_date:
                events_to_delete.append(ev)
        except Exception:
            pass

    # Step C: Delete obsolete shifts
    deleted_count = 0
    for ev in events_to_delete:
        ev_key = ev.get("uuid") or str(ev.get("id"))
        ev_title = ev.get("title", "Untitled")
        ev_date_str = ""
        try:
            ev_date_str = datetime.fromtimestamp(ev.get("start_at", 0) / 1000, tz=PARIS_TZ).strftime("%Y-%m-%d")
        except Exception:
            pass

        if VERBOSE_LOGS:
            print(f"[DELETE] {ev_title} on {ev_date_str} (shift cancelled or modified on Skello)...")
        try:
            api.delete_event(cal_id, ev_key)
            deleted_count += 1
            if VERBOSE_LOGS:
                print("  -> Deleted successfully.")
        except Exception as e:
            print(f"  -> Error deleting event: {e}")

    # Step D: Create new or updated shifts
    added_count = 0
    for shift in shifts_to_create:
        date_str = shift["start"].strftime("%Y-%m-%d")
        time_start = shift["start"].strftime("%H:%M")
        time_end = shift["end"].strftime("%H:%M")
        event_title = f"{shift['title']} ({time_start}-{time_end})"

        if VERBOSE_LOGS:
            print(f"[ADD] {event_title} on {date_str} ({time_start} - {time_end})...")
        try:
            api.create_event(cal_id, event_title, shift["start"], shift["end"], label_id=LABEL_ID)
            added_count += 1
            if VERBOSE_LOGS:
                print("  -> Added successfully.")
        except Exception as e:
            print(f"  -> Error creating event: {e}")

    print("\nSync complete:")
    print(f"  - {len(matched_event_keys)} shift(s) unchanged")
    print(f"  - {added_count} shift(s) added")
    print(f"  - {deleted_count} shift(s) deleted")


if __name__ == "__main__":
    main()
