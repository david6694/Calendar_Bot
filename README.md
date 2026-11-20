# Skello to TimeTree Calendar Bot

A lightweight Python bot that automatically synchronizes upcoming work shifts from a **Skello** iCalendar feed (`.ics` / `webcal://`) to a **TimeTree** calendar via GitHub Actions.
Also courtesy of https://github.com/ehs208/TimeTree-MCP for his work that saved me a lot of time.

## Features
- Fetches upcoming Skello shifts over the next 35 days (ignoring rest days / leave).
- Creates missing shifts on TimeTree with exact start/end times and a configurable color label.
- Automatically deletes obsolete shifts (cancelled or modified on Skello) without touching personal events.
- Runs daily via GitHub Actions (`.github/workflows/bot.yml`) or on demand (`workflow_dispatch`).
- Privacy-safe logs by default (no shift titles, hours, or calendar names printed unless `VERBOSE_LOGS=true`).

## Setup

> [!TIP]
> If you plan to run this bot with GitHub Actions, **use a private repository** so your secrets and workflow runs stay private.

### Environment Variables / GitHub Secrets

Configure the following secrets in **Settings → Secrets and variables → Actions** (or export them locally):

| Variable | Required | Description |
| --- | :---: | --- |
| `TIMETREE_EMAIL` | Yes | Your TimeTree account email |
| `TIMETREE_PASSWORD` | Yes | Your TimeTree account password |
| `TIMETREE_CALENDAR_ID` | Yes | Target TimeTree calendar ID, alias code, or name |
| `SKELLO_URL` | Yes | Your Skello iCalendar subscription URL (`https://...` or `webcal://...`) |
| `TIMETREE_LABEL_ID` | No | TimeTree color label ID (default: `3`) |
| `VERBOSE_LOGS` | No | Set to `true` to print detailed calendar and shift logs locally (default: `false`) |

### Local Usage

```bash
pip install -r requirements.txt
export TIMETREE_EMAIL="you@example.com"
export TIMETREE_PASSWORD="your-password"
export TIMETREE_CALENDAR_ID="your-calendar-id"
export SKELLO_URL="https://..."
python sync_bot.py
```

## Disclaimer

This project interacts with TimeTree's undocumented internal web API and is not affiliated with or endorsed by Skello or TimeTree. Use at your own risk.

## License

[MIT](LICENSE)
