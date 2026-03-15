# NYU Meal Swipe Bot

A bot that monitors your NYU meal plan swipe balance and sends real-time Telegram alerts when the count changes. Uses Selenium to authenticate via NYU SSO (Microsoft + Duo MFA) and polls the balance page on a schedule, keeping a persistent browser session to avoid repeated logins.

> **Note**
> Headless Chrome leaks memory over time. At 2-minute polling intervals, expect a crash after ~2 hours (~62 polls). Increase the interval to 5–10 minutes for longer uptime.

## Features

### Balance Monitoring
- Polls [mealplans.nyu.edu](https://mealplans.nyu.edu) on a configurable interval
- Extracts swipe count via regex from the Balances page
- Tracks session key (`skey`) changes to verify fresh data each poll

### Telegram Notifications
- Sends alerts only when the swipe count changes — no spam
- Async delivery via `httpx` and the Telegram Bot API
- Reports session expiry and errors

### NYU SSO Authentication
- Handles Microsoft SSO login, account picker, and Duo MFA push
- Persistent Chrome profile to reuse SSO cookies across polls
- Auto-recovery: if Chrome crashes, the driver is killed and recreated on the next poll

### API
- FastAPI server exposing health check and current status endpoints

## Tech Stack

| Layer          | Technology                                          |
|----------------|-----------------------------------------------------|
| Backend        | FastAPI, APScheduler, Python                        |
| Scraper        | Selenium, undetected-chromedriver, Chrome (headless) |
| Notifications  | Telegram Bot API, httpx (async)                     |
| Auth           | NYU SSO (Microsoft + Duo MFA), persistent Chrome profile |

## Architecture

```
app.py          → FastAPI server + APScheduler polling loop
scraper.py      → Selenium-based NYU SSO login + balance scraper
notifier.py     → Async Telegram Bot API notifications via httpx
```

### Polling Flow

1. `app.py` triggers `check_and_alert` on a scheduled interval
2. `scraper.py` revisits the NYU meal plan portal to obtain a fresh `skey`
3. Navigates to the Balances page and extracts the swipe count
4. If the count changed, `notifier.py` sends a Telegram alert
5. Status is exposed via the `/status` endpoint

### SSO Login Flow

1. Chrome navigates to `mealplans.nyu.edu`
2. Redirected to Microsoft SSO → enters NetID credentials
3. Duo MFA push sent → user approves on phone
4. Session cookies stored in persistent Chrome profile for reuse

## Setup & Installation

### Prerequisites

- Python 3.10+
- Google Chrome (v145+)
- A Telegram bot token (via [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID

### Backend

```bash
git clone https://github.com/shubhvn-dev/meal-swipe-bot.git
cd meal-swipe-bot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Configure environment variables (`.env`):

```env
NYU_NETID=your_netid
NYU_PASSWORD=your_password
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
HEADLESS=true
CHROME_PROFILE_DIR=./chrome_profile
```

Run the server:

```bash
python app.py
```

On first run, approve the **Duo MFA push** on your phone. After that, the persistent Chrome profile keeps you logged in.

## Usage

### Endpoints

| Endpoint       | Description                                    |
|----------------|------------------------------------------------|
| `GET /`        | Health check                                   |
| `GET /status`  | Current swipe count, uptime, last check time   |

### Telegram Alerts

Alerts are sent when the balance changes:

```
🍽️ Swipe count changed: 4 → 3
```

Session expiry and scraper errors are also reported.

## Known Limitations

- **Chrome memory leak** — headless Chrome accumulates memory with each page load. The browser crashed after ~2 hours at 2-min intervals during testing.
- **Duo MFA on restart** — if Chrome crashes or the app restarts, a new Duo push is required.
- **SSO session expiry** — the portal issues a new `skey` every page load, but the underlying SSO cookie can expire after extended periods.

## Future Improvements

- Navigate to `about:blank` between polls to free DOM memory
- Operating hours restriction (7 AM – 9 PM) to skip overnight polling
- CDP garbage collection (`HeapProfiler.collectGarbage`) between polls
- Memory usage logging via `psutil`
- Stress testing mode with configurable intervals

## License

MIT

## Contact

Shubhan Kadam
Email: [dev.shubhankadam@gmail.com](mailto:dev.shubhankadam@gmail.com)
