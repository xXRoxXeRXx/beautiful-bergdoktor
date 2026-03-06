# Beautiful Bergdoktor — A Doctolib Appointment Telegram Notifier

[![License](https://img.shields.io/github/license/MarcelWMeyer/beautiful-bergdoktor)](LICENSE.md)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

![Beautiful Bergdoktor banner](images/Der_Bergdoktor_banner_with_working_title_and_project_description.jpg)

Monitor multiple [Doctolib.de](https://www.doctolib.de/) doctors for appointment availability and get instant **Telegram notifications** — managed via a built-in **web dashboard**.

> **Based on** [Der BergdoktorBot](https://github.com/MarcelWMeyer/der-bergdoktorbot-a-doctolib-doctors-appointment-telegram-notifier) by [@MarcelWMeyer](https://github.com/MarcelWMeyer).  
> This fork adds a hardened security layer (CSRF protection, SSRF prevention, rate limiting, secret management) and improved error diagnostics on top of the original bot architecture.

---

## Features

- Monitor as many doctors as you like
- Manage doctors, settings and logs via browser dashboard
- All configuration stored in a persistent SQLite database
- Instant Telegram alerts when appointments open up
- Single Docker container — bot loop + web interface in one image
- Update the check interval without restarting the container
- Dashboard secured with username / password + CSRF + rate limiting
- SSRF-safe: only validates and fetches from `*.doctolib.de` URLs
- Structured error messages (HTTP 403 / 404 / 406 / 429 / 503 mapped to German hints)

## How It Works

```
Docker Container
├── Bot Loop  (every N minutes)
│   └── Checks each doctor, logs result, sends Telegram if slots found
└── Flask Web UI  (:8080)
    ├── Dashboard   – overview and quick test
    ├── Doctors     – add / edit / delete / test individual doctors
    ├── Logs        – full run history
    └── Settings    – Telegram credentials, check interval, upcoming days
```

### Telegram Message Preview

```
🏥 Arzttermin verfügbar!
06.03.2026 22:13 Uhr
────────────────────────────

🔴 Kurzfristig · Dr. Müller Orthopädie
📅 3 freie Termine in den nächsten 15 Tagen
👉 Jetzt buchen

────────────────────────────
🤖 Der BergdoktorBot
```

---

## Quick Start

### Step 1 — Create a Telegram Bot

1. Open [@BotFather](https://web.telegram.org/k/#@BotFather) and send `/newbot` — note the **Token**
2. Create a **private Telegram group**
3. Temporarily enable *Allow Groups* on the bot, add it to the group, then disable again
4. Post any message starting with `/` into the group
5. Visit `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` and note the **chat_id** (negative number for groups)
6. Test with: `https://api.telegram.org/bot<BOT_TOKEN>/sendMessage?chat_id=<CHAT_ID>&text=Hello`

### Step 2 — Get Doctolib URLs

You do **not** need to be logged in to Doctolib.

1. Go to your doctor's profile on doctolib.de
2. Open browser DevTools (`F12`) → Network tab → filter for `Fetch/XHR`
3. Click **TERMIN BUCHEN** and go through the booking wizard
4. Find the `availabilities.json` request and copy the full URL (**Availabilities URL**)
5. Copy the browser URL of the booking page (**Booking URL**)

Both URLs are entered per doctor in the web dashboard.

### Step 3 — Start the Bot

```bash
git clone https://github.com/MarcelWMeyer/beautiful-bergdoktor.git
cd beautiful-bergdoktor

# Copy and edit the environment file
cp .env.example .env
nano .env   # set WEB_USER, WEB_PASS, FLASK_SECRET_KEY

docker compose up -d
```

### Step 4 — Open the Dashboard

Open **http://localhost:8080** and log in with the credentials you set in `.env`.

Go to **Settings** to enter your Telegram credentials, then add your doctors under **Doctors**.

---

## Configuration

### `.env` file

The `.env` file covers deployment secrets only. Everything else is configured in the web dashboard.

| Variable | Required | Description |
|---|---|---|
| `WEB_USER` | ✅ | Dashboard login username (no default — must be set) |
| `WEB_PASS` | ✅ | Dashboard login password (no default — must be set) |
| `FLASK_SECRET_KEY` | ✅ | Random 64-hex-char string for session signing — generate once with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `WEB_PORT` | — | Port to expose the dashboard on (default: `8080`) |

```bash
# .env example
WEB_USER=yourname
WEB_PASS=a_strong_password
FLASK_SECRET_KEY=76f34caf50ba0ddcc5ee123c443eafe71ab031953d7898b96e6132f2d28d3bf0
WEB_PORT=8080
```

> ⚠️ **Never commit `.env` to git.** It is listed in `.gitignore`.

### Settings in the Web Dashboard

| Setting | Description | Default |
|---|---|---|
| Telegram Bot Token | Token from @BotFather | — |
| Telegram Chat ID | Group chat ID (negative number) | — |
| Tage vorausschauen | Days ahead to check for slots (Doctolib API max: 15) | 15 |
| Check-Intervall | Minutes between checks — applied after the next cycle, no restart needed | 5 |

### Doctor fields in the Web Dashboard

| Field | Description | Required |
|---|---|---|
| Name | Display name shown in Telegram and logs | ✅ |
| Availabilities URL | Full `availabilities.json` URL from DevTools Network tab | ✅ |
| Booking URL | Booking page URL on Doctolib | — |
| Aktiv | Enable or disable without deleting | — |

---

## Docker Commands

```bash
# Start in background
docker compose up -d

# View live logs
docker compose logs -f bergdoktorbot

# Stop
docker compose down

# Rebuild after code changes
docker compose build --no-cache && docker compose up -d
```

---

## Project Structure

```
.
├── entrypoint.py                        Single-container entrypoint (bot + web threads)
├── notifyDoctolibDoctorsAppointment.py  Bot runner: checks doctors, sends Telegram
├── checker.py                           Shared Doctolib availability logic + URL validation
├── database.py                          SQLite layer (doctors, logs, settings)
├── web/
│   ├── app.py                           Flask web application (CSRF, rate limiting, auth)
│   └── templates/                       Jinja2 HTML templates (dark mode UI)
├── Dockerfile                           Single image for bot and web
├── docker-compose.yml                   One service, one volume
├── .env                                 Local config — not committed to git
└── .env.example                         Template for new deployments
```

---

## Security

This project has been security-audited and hardened against:

| Risk | Mitigation |
|---|---|
| Default credentials | App refuses to start if `WEB_USER`/`WEB_PASS` are empty |
| CSRF | All state-changing routes protected with `hmac.compare_digest` token |
| SSRF | Only HTTPS URLs to `*.doctolib.de/.fr/.it` are accepted and fetched |
| Timing attacks | Login uses `hmac.compare_digest` for both username and password |
| Session fixation | Session is cleared and re-created on every successful login |
| Secret exposure | Bot token is never pre-filled in HTML; only replaced if user submits a new value |
| Brute force | 5 login attempts per IP per 60 seconds; HTTP 429 on excess |
| Key persistence | `FLASK_SECRET_KEY` read from env — sessions survive container restarts |
| SQL injection | All DB queries use parameterized statements via `sqlite3` |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Dashboard not reachable | Check `WEB_PORT` in `.env` and that the container is running |
| App refuses to start | `WEB_USER`, `WEB_PASS`, and `FLASK_SECRET_KEY` must all be set in `.env` |
| No Telegram notifications | Verify Token and Chat ID in the dashboard Settings |
| HTTP 406 / 403 error for a doctor | The `availabilities.json` URL has expired — re-capture from DevTools |
| HTTP 429 error | Doctolib is rate-limiting your IP — increase the check interval |
| Bot loop not starting | Run `docker compose logs bergdoktorbot` and check for Python errors |

---

## Credits & Source

**Beautiful Bergdoktor** is a hardened fork of **Der BergdoktorBot**:

- 🔗 **Original project:** [MarcelWMeyer/der-bergdoktorbot-a-doctolib-doctors-appointment-telegram-notifier](https://github.com/MarcelWMeyer/der-bergdoktorbot-a-doctolib-doctors-appointment-telegram-notifier)
- 👤 **Original author:** [@MarcelWMeyer](https://github.com/MarcelWMeyer)
- 📄 **License:** MIT — see [LICENSE.md](LICENSE.md)

---

## Disclaimer

This tool is for personal use only. Please respect Doctolib's terms of service and do not overload their servers. The authors are not responsible for any misuse.

---

*Topics: doctolib · telegram-bot · appointment-notifier · docker · python · flask · sqlite · healthcare · germany · security*


![Der BergdoktorBot banner](images/Der_Bergdoktor_banner_with_working_title_and_project_description.jpg)

Monitor multiple [Doctolib.de](https://www.doctolib.de/) doctors for appointment availability and get instant **Telegram notifications** - managed via a built-in **web dashboard**.

## Features

- Monitor as many doctors as you like
- Manage doctors, settings and logs via browser dashboard
- All configuration stored in a persistent SQLite database
- Instant Telegram alerts when appointments open up
- Single Docker container: bot loop + web interface in one image
- Update the check interval without restarting the container
- Dashboard secured with username and password

## How It Works

`
Docker Container
+-- Bot Loop  (every N minutes)
|   +-- Checks each doctor, logs result, sends Telegram if slots found
+-- Flask Web UI  (:8080)
    +-- Dashboard   - overview and quick test
    +-- Doctors     - add / edit / delete / test individual doctors
    +-- Logs        - full run history
    +-- Settings    - Telegram credentials, check interval, upcoming days
`

### Telegram Message Preview

`
Hospital Arzttermin verfuegbar!
06.03.2026 22:13 Uhr
----------------------------

Red Kurzfristig - Dr. Mueller Orthopaedie
Calendar 3 freie Termine in den naechsten 15 Tagen
Pointing Jetzt buchen

----------------------------
Robot Der BergdoktorBot
`

---

## Quick Start

### Step 1 - Create a Telegram Bot

1. Open [@BotFather](https://web.telegram.org/k/#@BotFather) and send /newbot - note the **Token**
2. Create a **private Telegram group**
3. Temporarily enable Allow Groups on the bot, add it to the group, then disable again
4. Post any message starting with / into the group
5. Visit https://api.telegram.org/bot<BOT_TOKEN>/getUpdates and note the **chat_id** (negative number for groups)
6. Test with: https://api.telegram.org/bot<BOT_TOKEN>/sendMessage?chat_id=<CHAT_ID>&text=Hello

### Step 2 - Get Doctolib URLs

You do **not** need to be logged in to Doctolib.

1. Go to your doctors profile on doctolib.de
2. Open browser DevTools (F12) - Network tab - filter for Fetch/XHR
3. Click TERMIN BUCHEN and go through the booking wizard
4. Find the availabilities.json request and copy the full URL (Availabilities URL)
5. Copy the browser URL of the booking page (Booking URL)

You enter both URLs per doctor in the web dashboard.

### Step 3 - Start the Bot

`ash
git clone https://github.com/xXRoxXeR/der-bergdoktorbot-a-doctolib-doctors-appointment-telegram-notifier.git
cd der-bergdoktorbot-a-doctolib-doctors-appointment-telegram-notifier
cp .env.example .env
docker-compose up -d
`

### Step 4 - Open the Dashboard

Open **http://localhost:8080** and log in (default: admin / admin).

Go to **Settings** to enter your Telegram credentials, then add your doctors under **Doctors**.

---

## Configuration

The .env file only needs three values. Everything else is configured in the web dashboard:

`
WEB_USER=admin
WEB_PASS=changeme
WEB_PORT=8080
`

### Settings in the Web Dashboard

| Setting | Description | Default |
|---------|-------------|---------|
| Telegram Bot Token | Token from @BotFather | - |
| Telegram Chat ID | Group chat ID (negative number) | - |
| Tage vorausschauen | Days ahead to check for slots (Doctolib API max 15 for query) | 15 |
| Check-Intervall | Minutes between checks, applied after next cycle without restart | 5 |

### Doctors in the Web Dashboard

| Field | Description | Required |
|-------|-------------|----------|
| Name | Display name | Yes |
| Availabilities URL | Full availabilities.json URL from DevTools Network tab | Yes |
| Booking URL | Booking page URL on Doctolib | Yes |
| Aktiv | Enable or disable without deleting | Yes |

---

## Docker Commands

`ash
# Start in background
docker-compose up -d

# View live logs
docker-compose logs -f bergdoktorbot

# Stop
docker-compose down

# Rebuild after code changes
docker-compose build --no-cache && docker-compose up -d
`

---

## Project Structure

`
.
+-- entrypoint.py                       Single-container entrypoint (bot + web threads)
+-- notifyDoctolibDoctorsAppointment.py Bot runner: checks doctors, sends Telegram
+-- checker.py                          Shared Doctolib availability logic
+-- database.py                         SQLite layer (doctors, logs, settings)
+-- web/
|   +-- app.py                          Flask web application
|   +-- templates/                      Jinja2 HTML templates (dark mode UI)
+-- Dockerfile                          Single image for bot and web
+-- docker-compose.yml                  One service, one volume
+-- .env                                Local config (web credentials only)
+-- .env.example                        Template for new deployments
`

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Dashboard not reachable | Check WEB_PORT in .env and that the container is running |
| No Telegram notifications | Verify Token and Chat ID in the dashboard Settings |
| API errors for a doctor | The availabilities.json URL may have expired, re-capture from DevTools |
| Bot loop not starting | Check docker-compose logs bergdoktorbot for Python errors |

---

## License

MIT License - see [LICENSE.md](LICENSE.md) for details.

## Disclaimer

This tool is for personal use only. Please respect Doctolibs terms of service and do not overload their servers. The authors are not responsible for any misuse.

---

Topics: doctolib - telegram-bot - appointment-notifier - docker - python - flask - sqlite - healthcare - germany
