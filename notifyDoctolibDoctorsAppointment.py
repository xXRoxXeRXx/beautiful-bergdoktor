"""
BergdoktorBot – Main bot runner.
Reads configuration from SQLite DB (managed via web interface).
Falls back to environment variables if DB has no doctors configured.
"""
from datetime import datetime, timedelta
import json
import os
import re
import sys
import urllib.request

# Allow running from /app directory in Docker
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checker import run_check, sanitize_booking_url
import database as db


def log(message):
    """Print a timestamped log message."""
    print(f"[{datetime.now()}] {message}")


def get_doctors_from_env():
    """Fallback: read doctor configs from environment variables."""
    doctors = []
    doctor_numbers = set()
    for key in os.environ:
        match = re.match(r'DOCTOR_(\d+)_', key)
        if match:
            doctor_numbers.add(int(match.group(1)))
    for num in sorted(doctor_numbers):
        prefix = f'DOCTOR_{num}_'
        url = os.getenv(f'{prefix}AVAILABILITIES_URL', '')
        if url:
            doctors.append({
                'name': os.getenv(f'{prefix}NAME', f'Doctor {num}'),
                'availabilities_url': url,
                'booking_url': os.getenv(f'{prefix}BOOKING_URL', 'https://www.doctolib.de/'),
            })
    # Legacy single-doctor fallback
    if not doctors:
        legacy_url = os.getenv('AVAILABILITIES_URL', '')
        if legacy_url:
            doctors.append({
                'name': os.getenv('APPOINTMENT_NAME') or 'Legacy Doctor',
                'availabilities_url': legacy_url,
                'booking_url': os.getenv('BOOKING_URL', 'https://www.doctolib.de/'),
            })
    return doctors


def extract_slot_entries(availabilities, upcoming_days):
    """Build a list of (slot_key, slot_date) for all slots within the window."""
    today = datetime.today().date()
    cutoff = today if upcoming_days == 0 else today + timedelta(days=upcoming_days)
    entries = []
    for day in (availabilities or {}).get('availabilities', []):
        slots = day.get('slots') or []
        if not slots:
            continue
        try:
            slot_date = datetime.fromisoformat(day['date']).date()
        except (ValueError, KeyError, TypeError):
            continue
        if slot_date > cutoff:
            continue
        date_str = slot_date.isoformat()
        for s in slots:
            if isinstance(s, str):
                key = s
            elif isinstance(s, dict):
                key = s.get('start_date') or json.dumps(s, sort_keys=True)
            else:
                key = str(s)
            entries.append((key, date_str))
    return entries


def send_telegram_message(token, chat_id, message):
    """Send a Telegram message via HTTP POST."""
    payload = json.dumps({
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }).encode('utf-8')
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    log(f"Sending Telegram message: {message[:80]}...")
    try:
        urllib.request.urlopen(req, timeout=10)
        log("Message sent successfully!")
        return True
    except Exception as e:
        log(f"Error sending message: {e}")
        return False


def main():
    log("BergdoktorBot starting...")

    # ── Load config (DB preferred, env fallback) ──────────────────────────────
    db.init_db()
    settings = db.get_settings()

    telegram_token = settings.get('telegram_bot_token') or os.getenv('TELEGRAM_BOT_TOKEN', '')
    telegram_chat_id = settings.get('telegram_chat_id') or os.getenv('TELEGRAM_CHAT_ID', '')

    try:
        default_days = int(settings.get('upcoming_days') or os.getenv('UPCOMING_DAYS', '15'))
        if default_days < 0:
            default_days = 15
    except ValueError:
        default_days = 15

    doctors = db.get_active_doctors()
    if not doctors:
        log("No doctors in DB – falling back to environment variables")
        doctors = get_doctors_from_env()

    if not (telegram_token and telegram_chat_id):
        log("Configuration error – TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID required")
        return

    if not doctors:
        log("Configuration error – no doctors configured")
        return

    # ── Check all doctors ─────────────────────────────────────────────────────
    log(f"Checking {len(doctors)} doctor(s)...")
    results = []
    for doctor in doctors:
        log(f"Checking {doctor['name']}...")
        raw_days = doctor.get('upcoming_days')
        upcoming_days = int(raw_days) if raw_days is not None else default_days
        result = run_check(doctor, upcoming_days)
        results.append(result)

        status = 'success' if result['success'] else 'error'
        slots = result.get('slots_total', 0)
        msg = f"Found {slots} slots" if result['success'] else result.get('error', '')
        db.add_log(doctor['name'], slots, status, msg)

        if result['success']:
            log(f"{doctor['name']}: Found {slots} slots")
        else:
            log(f"{doctor['name']}: Error – {result['error']}")

    # ── Notify only when slots are available ──────────────────────────────────
    available_doctors = [r for r in results if r['success'] and r['slots_exist']]
    if not available_doctors:
        total_slots = sum(r.get('slots_total', 0) for r in results if r['success'])
        log(f"No slots found (total checked: {total_slots}) – no notification sent")
        return

    # ── Deduplication: only notify about slots we haven't notified before ─────
    notify_only_new = (settings.get('notify_only_new', 'true').strip().lower()
                       not in ('false', '0', 'no'))
    db.cleanup_expired_slots()

    doctors_to_notify = []
    for result in available_doctors:
        doctor = result['doctor']
        doctor_key = str(doctor.get('id') or doctor.get('name', '?'))
        entries = extract_slot_entries(
            result.get('availabilities'), result.get('upcoming_days', default_days)
        )
        current_keys = {key for key, _ in entries}
        if notify_only_new:
            known = db.get_notified_slot_keys(doctor_key)
            new_keys = current_keys - known
        else:
            new_keys = current_keys
        if new_keys:
            result['_doctor_key'] = doctor_key
            result['_slot_entries'] = entries
            result['_new_count'] = len(new_keys)
            doctors_to_notify.append(result)

    if not doctors_to_notify:
        log("Slots vorhanden, aber nichts Neues seit der letzten Meldung – übersprungen")
        return

    log(f"{len(doctors_to_notify)} Arzt/Aerzte mit neuen Slots – Benachrichtigung wird vorbereitet...")

    # ── Build message ────────────────────────────────────────────
    now = datetime.now()
    date_str = now.strftime('%d.%m.%Y %H:%M')
    divider = '─' * 28
    message = (
        '🏥 <b>Arzttermin verfügbar!</b>\n'
        f'<i>{date_str} Uhr</i>\n'
        f'{divider}\n\n'
    )

    for result in doctors_to_notify:
        doctor = result['doctor']
        slots = result['slots_total']
        days = result.get('upcoming_days', default_days)
        urgency = '🔴 Kurzfristig' if result['earlier_slots'] else '🟡 Verfügbar'
        slot_word = f'{slots} freier Termin' if slots == 1 else f'{slots} freie Termine'
        # M-4: sanitize booking_url before embedding in Telegram HTML
        safe_url = sanitize_booking_url(doctor.get('booking_url', ''))
        message += (
            f'{urgency} · <b>{doctor["name"]}</b>\n'
            f'📅 {slot_word} in den nächsten {days} Tagen\n'
            f'👉 <a href="{safe_url}">Jetzt buchen</a>\n\n'
        )

    message += f'{divider}\n🤖 <i>Der BergdoktorBot</i>'

    sent = send_telegram_message(telegram_token, telegram_chat_id, message)

    # ── Remember slots only after a successful send (so failures retry) ───────
    if sent:
        for result in doctors_to_notify:
            db.add_notified_slots(result['_doctor_key'], result['_slot_entries'])
    else:
        log("Versand fehlgeschlagen – Slots NICHT gemerkt, naechster Lauf versucht es erneut")


if __name__ == '__main__':
    main()
