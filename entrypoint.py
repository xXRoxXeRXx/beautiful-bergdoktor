"""
BergdoktorBot – Single-container entrypoint.

Starts the Flask web interface in a background thread and
runs the bot check loop in the main thread.
"""
import os
import sys
import time
import threading
from datetime import datetime

# Ensure /app is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db


def log(message):
    print(f"[{datetime.now()}] {message}", flush=True)


def run_web():
    """Start the Flask web interface (blocking)."""
    # Import here so Flask only loads in this thread
    from web.app import app
    port = int(os.getenv('WEB_PORT', '8080'))
    log(f"Starting web interface on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


def run_bot_loop():
    """Run the bot check loop in the main thread."""
    from notifyDoctolibDoctorsAppointment import main as bot_main

    log("Starting bot loop...")

    while True:
        log("Starting bot execution...")
        try:
            bot_main()
        except Exception as e:
            log(f"Bot error: {e}")

        # Read interval fresh from DB each cycle so web UI changes take effect immediately
        try:
            settings = db.get_settings()
            interval = int(settings.get('interval_minutes') or os.getenv('INTERVAL_MINUTES', '5'))
            if interval < 1:
                interval = 5
        except Exception:
            interval = 5

        log(f"Sleeping for {interval} minute(s)...")
        time.sleep(interval * 60)


if __name__ == '__main__':
    # Initialise DB before anything starts
    db.init_db()
    log("BergdoktorBot starting...")

    # Start Flask web interface in background thread
    web_thread = threading.Thread(target=run_web, daemon=True, name='web')
    web_thread.start()

    # Bot loop runs in main thread (handles KeyboardInterrupt / SIGTERM cleanly)
    run_bot_loop()
