import hmac
import os
import sys
import json
import secrets
import urllib.request
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify)

# Shared modules live in /app (parent of /app/web)
sys.path.insert(0, '/app')
import database as db
from checker import run_check, sanitize_booking_url, _validate_doctolib_url

app = Flask(__name__)

# C-3: use a stable secret from the environment; generate a one-time fallback
# with a loud warning so admins know sessions won't survive restarts.
_secret = os.getenv('FLASK_SECRET_KEY', '')
if not _secret:
    _secret = secrets.token_hex(32)
    print(
        "[SECURITY WARNING] FLASK_SECRET_KEY is not set. "
        "A temporary key has been generated – all sessions will be lost on restart. "
        "Set FLASK_SECRET_KEY in your .env file.",
        flush=True,
    )
app.secret_key = _secret

# L-1: sessions expire after 8 hours
app.permanent_session_lifetime = timedelta(hours=8)

# C-1: no default credentials – refuse to start if unset
WEB_USER = os.getenv('WEB_USER', '')
WEB_PASS = os.getenv('WEB_PASS', '')
if not WEB_USER or not WEB_PASS:
    raise RuntimeError(
        "WEB_USER and WEB_PASS environment variables must be set. "
        "The web interface will not start with default or empty credentials."
    )

db.init_db()

# ── Simple in-process login-attempt rate limiter (H-3) ───────────────────────
import threading
import time as _time

_login_lock   = threading.Lock()
_login_attempts: dict[str, list[float]] = {}   # ip -> [timestamps]
_MAX_ATTEMPTS = 5
_WINDOW_SECS  = 60


def _is_rate_limited(ip: str) -> bool:
    now = _time.monotonic()
    with _login_lock:
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < _WINDOW_SECS]
        _login_attempts[ip] = attempts
        return len(attempts) >= _MAX_ATTEMPTS


def _record_attempt(ip: str) -> None:
    now = _time.monotonic()
    with _login_lock:
        bucket = [t for t in _login_attempts.get(ip, []) if now - t < _WINDOW_SECS]
        bucket.append(now)
        _login_attempts[ip] = bucket


# ── CSRF helpers (M-1) ────────────────────────────────────────────────────────

def _generate_csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def _validate_csrf() -> bool:
    token = request.form.get('csrf_token', '')
    expected = session.get('csrf_token', '')
    return bool(token and expected and hmac.compare_digest(token, expected))


# Expose the generator to Jinja2 templates
app.jinja_env.globals['csrf_token'] = _generate_csrf_token


def csrf_protect(f):
    """Decorator: validate CSRF token on every state-changing POST."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'POST':
            if not _validate_csrf():
                flash('Ungültiger CSRF-Token. Bitte Seite neu laden.', 'error')
                return redirect(request.referrer or url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Telegram helpers ──────────────────────────────────────────────────────────

def send_telegram_message(token, chat_id, message):
    """Send a Telegram message via HTTP POST."""
    payload = json.dumps({
        'chat_id':                  chat_id,
        'text':                     message,
        'parse_mode':               'HTML',
        'disable_web_page_preview': True,
    }).encode('utf-8')
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True, None
    except Exception as e:
        return False, str(e)


def build_and_send_telegram(results, upcoming_days):
    """Build a Telegram message from check results and send it. Returns (sent, error)."""
    settings = db.get_settings()
    token   = settings.get('telegram_bot_token') or os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = settings.get('telegram_chat_id')   or os.getenv('TELEGRAM_CHAT_ID', '')

    if not (token and chat_id):
        return False, 'Telegram-Token oder Chat-ID nicht konfiguriert'

    available = [r for r in results if r.get('success') and r.get('slots_exist')]
    if not available:
        return False, 'Keine Slots gefunden – keine Nachricht gesendet'

    now = datetime.now()
    date_str = now.strftime('%d.%m.%Y %H:%M')
    divider = '\u2500' * 28

    message = (
        '\U0001f3e5 <b>Arzttermin verf\u00fcgbar!</b>\n'
        f'<i>{date_str} Uhr</i>\n'
        f'{divider}\n\n'
    )

    for r in available:
        doctor = r['doctor']
        slots  = r['slots_total']
        urgency = '\U0001f534 Kurzfristig' if r.get('earlier_slots') else '\U0001f7e1 Verf\u00fcgbar'
        slot_word = f'{slots} freier Termin' if slots == 1 else f'{slots} freie Termine'
        # M-4: sanitize booking_url before embedding in Telegram HTML
        safe_url = sanitize_booking_url(doctor.get('booking_url', ''))

        message += (
            f'{urgency} \u00b7 <b>{doctor["name"]}</b>\n'
            f'\U0001f4c5 {slot_word} in den n\u00e4chsten {upcoming_days} Tagen\n'
            f'\U0001f449 <a href="{safe_url}">Jetzt buchen</a>\n\n'
        )

    message += f'{divider}\n\U0001f916 <i>Der BergdoktorBot</i>'
    return send_telegram_message(token, chat_id, message)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip = request.remote_addr or '0.0.0.0'

        # H-3: rate limiting
        if _is_rate_limited(ip):
            flash('Zu viele Anmeldeversuche. Bitte warte eine Minute.', 'error')
            return render_template('login.html'), 429

        _record_attempt(ip)

        # C-2: timing-safe credential comparison
        user_ok = hmac.compare_digest(
            request.form.get('username', ''), WEB_USER
        )
        pass_ok = hmac.compare_digest(
            request.form.get('password', ''), WEB_PASS
        )
        if user_ok and pass_ok:
            session.clear()           # L-1: invalidate any prior session
            session.permanent = True  # L-1: apply the 8-hour lifetime
            session['logged_in'] = True
            return redirect(url_for('dashboard'))

        flash('Falscher Benutzername oder Passwort', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── URL validation helper (H-1 / M-3) ────────────────────────────────────────

_MAX_NAME_LEN = 200
_MAX_URL_LEN  = 2048


def _validate_doctor_form(name, availabilities_url, booking_url):
    """Return an error string, or None if all fields are valid."""
    if not name:
        return 'Name ist ein Pflichtfeld'
    if len(name) > _MAX_NAME_LEN:
        return f'Name zu lang (max. {_MAX_NAME_LEN} Zeichen)'
    if not availabilities_url:
        return 'Availabilities-URL ist ein Pflichtfeld'
    if len(availabilities_url) > _MAX_URL_LEN:
        return f'Availabilities-URL zu lang (max. {_MAX_URL_LEN} Zeichen)'
    try:
        _validate_doctolib_url(availabilities_url, 'Availabilities-URL')
    except ValueError as exc:
        return str(exc)
    if booking_url:
        if len(booking_url) > _MAX_URL_LEN:
            return f'Booking-URL zu lang (max. {_MAX_URL_LEN} Zeichen)'
        try:
            _validate_doctolib_url(booking_url, 'Booking-URL')
        except ValueError as exc:
            return str(exc)
    return None


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    doctors = db.get_all_doctors()
    logs = db.get_logs(limit=10)
    settings = db.get_settings()
    return render_template('dashboard.html',
                           doctors=doctors, logs=logs, settings=settings)


# ── Doctors ───────────────────────────────────────────────────────────────────

@app.route('/doctors')
@login_required
def doctors():
    all_doctors = db.get_all_doctors()
    return render_template('doctors.html', doctors=all_doctors)


@app.route('/doctors/add', methods=['GET', 'POST'])
@login_required
@csrf_protect
def add_doctor():
    if request.method == 'POST':
        name               = request.form.get('name', '').strip()
        availabilities_url = request.form.get('availabilities_url', '').strip()
        booking_url        = request.form.get('booking_url', '').strip()

        err = _validate_doctor_form(name, availabilities_url, booking_url)
        if err:
            flash(err, 'error')
        else:
            db.add_doctor(name, availabilities_url, booking_url)
            flash(f'Arzt "{name}" wurde hinzugef\u00fcgt', 'success')
            return redirect(url_for('doctors'))

    return render_template('doctor_form.html', doctor=None, action='add')


@app.route('/doctors/<int:doctor_id>/edit', methods=['GET', 'POST'])
@login_required
@csrf_protect
def edit_doctor(doctor_id):
    doctor = db.get_doctor(doctor_id)
    if not doctor:
        flash('Arzt nicht gefunden', 'error')
        return redirect(url_for('doctors'))

    if request.method == 'POST':
        name               = request.form.get('name', '').strip()
        availabilities_url = request.form.get('availabilities_url', '').strip()
        booking_url        = request.form.get('booking_url', '').strip()
        active             = request.form.get('active') == 'on'

        err = _validate_doctor_form(name, availabilities_url, booking_url)
        if err:
            flash(err, 'error')
        else:
            db.update_doctor(doctor_id, name, availabilities_url, booking_url, active)
            flash(f'Arzt "{name}" wurde aktualisiert', 'success')
            return redirect(url_for('doctors'))

    return render_template('doctor_form.html', doctor=doctor, action='edit')


@app.route('/doctors/<int:doctor_id>/delete', methods=['POST'])
@login_required
@csrf_protect
def delete_doctor(doctor_id):
    doctor = db.get_doctor(doctor_id)
    if doctor:
        db.delete_doctor(doctor_id)
        flash(f'Arzt "{doctor["name"]}" wurde gel\u00f6scht', 'success')
    return redirect(url_for('doctors'))


@app.route('/doctors/<int:doctor_id>/test', methods=['POST'])
@login_required
@csrf_protect
def test_doctor(doctor_id):
    doctor = db.get_doctor(doctor_id)
    if not doctor:
        return jsonify({'error': 'Arzt nicht gefunden'}), 404

    settings = db.get_settings()
    try:
        upcoming_days = int(settings.get('upcoming_days', '15'))
    except ValueError:
        upcoming_days = 15

    result = run_check(doctor, upcoming_days)

    status = 'success' if result['success'] else 'error'
    slots  = result.get('slots_total', 0)
    # H-2: only log sanitized error (run_check already returns a safe string)
    msg = f"Found {slots} slots" if result['success'] else result.get('error', '')
    db.add_log(doctor['name'], slots, status, msg)

    tg_sent, tg_error = build_and_send_telegram([result], upcoming_days)

    # H-2: strip raw availabilities blob and internal doctor dict from API response
    return jsonify({
        'success':        result['success'],
        'slots_total':    result.get('slots_total', 0),
        'slots_exist':    result.get('slots_exist', False),
        'earlier_slots':  result.get('earlier_slots', False),
        'error':          result.get('error', ''),
        'telegram_sent':  tg_sent,
        'telegram_error': tg_error,
    })


# ── Test All ──────────────────────────────────────────────────────────────────

@app.route('/run-test', methods=['POST'])
@login_required
@csrf_protect
def run_test():
    """Trigger a full check run, log results and send Telegram if slots found."""
    settings = db.get_settings()
    try:
        upcoming_days = int(settings.get('upcoming_days', '15'))
    except ValueError:
        upcoming_days = 15

    doctors = db.get_active_doctors()
    results = []
    for doctor in doctors:
        result = run_check(doctor, upcoming_days)
        status = 'success' if result['success'] else 'error'
        slots  = result.get('slots_total', 0)
        msg    = f"Found {slots} slots" if result['success'] else result.get('error', '')
        db.add_log(doctor['name'], slots, status, msg)
        results.append(result)

    tg_sent, tg_error = build_and_send_telegram(results, upcoming_days)

    return jsonify({
        'results': [{
            'name':          r['doctor']['name'],
            'slots':         r.get('slots_total', 0),
            'success':       r['success'],
            'earlier_slots': r.get('earlier_slots', False),
            'error':         r.get('error', ''),
        } for r in results],
        'telegram_sent':  tg_sent,
        'telegram_error': tg_error,
        'timestamp':      datetime.now().isoformat(),
    })


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.route('/logs')
@login_required
def logs():
    all_logs = db.get_logs(limit=200)
    return render_template('logs.html', logs=all_logs)


@app.route('/logs/clear', methods=['POST'])
@login_required
@csrf_protect
def clear_logs():
    db.clear_logs()
    flash('Logs wurden geleert', 'success')
    return redirect(url_for('logs'))


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@csrf_protect
def settings():
    if request.method == 'POST':
        # Only pass the token if the user actually typed a new one (H-4)
        new_token = request.form.get('telegram_bot_token', '').strip()
        current   = db.get_settings()
        token_to_save = new_token if new_token else current.get('telegram_bot_token', '')

        db.save_settings({
            'telegram_bot_token': token_to_save,
            'telegram_chat_id':   request.form.get('telegram_chat_id', '').strip(),
            'upcoming_days':      request.form.get('upcoming_days', '15').strip(),
            'interval_minutes':   request.form.get('interval_minutes', '5').strip(),
        })
        flash('Einstellungen gespeichert', 'success')
        return redirect(url_for('settings'))

    current = db.get_settings()
    return render_template('settings.html', settings=current)


if __name__ == '__main__':
    port = int(os.getenv('WEB_PORT', '8080'))
    app.run(host='0.0.0.0', port=port, debug=False)
