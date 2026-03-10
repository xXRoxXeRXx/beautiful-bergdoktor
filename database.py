import sqlite3
import os
import pathlib
from datetime import datetime

_raw_db_path = os.getenv('DB_PATH', '/data/bergdoktorbot.db')
_db_resolved  = pathlib.Path(_raw_db_path).resolve()
_db_allowed   = pathlib.Path('/data').resolve()
if not str(_db_resolved).startswith(str(_db_allowed)):
    raise RuntimeError(
        f"DB_PATH '{_raw_db_path}' resolves outside the allowed directory '{_db_allowed}'"
    )
DB_PATH = str(_db_resolved)

# Allowed keys for the settings table – prevents arbitrary key injection
_ALLOWED_SETTINGS = frozenset({
    'telegram_bot_token',
    'telegram_chat_id',
    'upcoming_days',
    'notify_hourly',
    'interval_minutes',
})


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist and seed default settings."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS doctors (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT    NOT NULL,
            availabilities_url TEXT    NOT NULL,
            booking_url        TEXT    NOT NULL DEFAULT 'https://www.doctolib.de/',
            move_booking_url   TEXT,
            upcoming_days      INTEGER NOT NULL DEFAULT 15,
            active             INTEGER NOT NULL DEFAULT 1,
            created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS run_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
            doctor_name TEXT    NOT NULL,
            slots_found INTEGER NOT NULL DEFAULT 0,
            status      TEXT    NOT NULL,
            message     TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    ''')

    # Seed default settings (won't overwrite existing values)
    defaults = {
        'telegram_bot_token': '',
        'telegram_chat_id':   '',
        'upcoming_days':      '15',
        'notify_hourly':      'false',
        'interval_minutes':   '5',
    }
    for key, value in defaults.items():
        c.execute(
            'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
            (key, value)
        )

    conn.commit()

    # Migration: add upcoming_days column to doctors if it doesn't exist yet
    existing_cols = [row[1] for row in c.execute('PRAGMA table_info(doctors)').fetchall()]
    if 'upcoming_days' not in existing_cols:
        c.execute('ALTER TABLE doctors ADD COLUMN upcoming_days INTEGER NOT NULL DEFAULT 15')
        conn.commit()

    conn.close()


# ── Doctors ──────────────────────────────────────────────────────────────────

def get_all_doctors():
    conn = get_connection()
    doctors = conn.execute('SELECT * FROM doctors ORDER BY id').fetchall()
    conn.close()
    return [dict(d) for d in doctors]


def get_active_doctors():
    conn = get_connection()
    doctors = conn.execute(
        'SELECT * FROM doctors WHERE active = 1 ORDER BY id'
    ).fetchall()
    conn.close()
    return [dict(d) for d in doctors]


def get_doctor(doctor_id):
    conn = get_connection()
    doctor = conn.execute(
        'SELECT * FROM doctors WHERE id = ?', (doctor_id,)
    ).fetchone()
    conn.close()
    return dict(doctor) if doctor else None


def add_doctor(name, availabilities_url, booking_url, move_booking_url=None, upcoming_days=15):
    conn = get_connection()
    conn.execute(
        '''INSERT INTO doctors (name, availabilities_url, booking_url, move_booking_url, upcoming_days)
           VALUES (?, ?, ?, ?, ?)''',
        (name, availabilities_url, booking_url, move_booking_url or None, upcoming_days)
    )
    conn.commit()
    conn.close()


def update_doctor(doctor_id, name, availabilities_url, booking_url, active, upcoming_days=15):
    conn = get_connection()
    conn.execute(
        '''UPDATE doctors
           SET name = ?, availabilities_url = ?, booking_url = ?,
               active = ?, upcoming_days = ?, updated_at = ?
           WHERE id = ?''',
        (name, availabilities_url, booking_url,
         1 if active else 0, upcoming_days, datetime.now().isoformat(), doctor_id)
    )
    conn.commit()
    conn.close()


def delete_doctor(doctor_id):
    conn = get_connection()
    conn.execute('DELETE FROM doctors WHERE id = ?', (doctor_id,))
    conn.commit()
    conn.close()


# ── Run Logs ─────────────────────────────────────────────────────────────────

def add_log(doctor_name, slots_found, status, message=''):
    conn = get_connection()
    conn.execute(
        '''INSERT INTO run_logs (doctor_name, slots_found, status, message)
           VALUES (?, ?, ?, ?)''',
        (doctor_name, slots_found, status, message)
    )
    conn.commit()
    conn.close()


def get_logs(limit=200):
    conn = get_connection()
    logs = conn.execute(
        'SELECT * FROM run_logs ORDER BY id DESC LIMIT ?', (limit,)
    ).fetchall()
    conn.close()
    return [dict(l) for l in logs]


def clear_logs():
    conn = get_connection()
    conn.execute('DELETE FROM run_logs')
    conn.commit()
    conn.close()


# ── Settings ─────────────────────────────────────────────────────────────────

def get_settings():
    conn = get_connection()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def save_settings(data: dict):
    conn = get_connection()
    for key, value in data.items():
        if key not in _ALLOWED_SETTINGS:
            continue  # silently drop unknown keys (L-2)
        conn.execute(
            '''INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
               updated_at = excluded.updated_at''',
            (key, value, datetime.now().isoformat())
        )
    conn.commit()
    conn.close()
