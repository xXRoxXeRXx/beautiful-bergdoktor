"""
Shared availability checking logic used by both the bot and the web interface.
"""
from datetime import date, datetime, timedelta
import html
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/114.0.0.0 Safari/537.36'
)

# Doctolib API hard limit
DOCTOLIB_MAX_LIMIT = 15

# URL constraints (H-1 / SSRF)
_ALLOWED_SCHEMES   = {'https'}
_ALLOWED_HOSTNAMES = {'.doctolib.de', '.doctolib.fr', '.doctolib.it'}
_MAX_URL_LENGTH    = 2048


def _validate_doctolib_url(url: str, field: str = 'URL') -> None:
    """Raise ValueError if *url* is not a safe, Doctolib-owned HTTPS URL."""
    if len(url) > _MAX_URL_LENGTH:
        raise ValueError(f"{field} exceeds maximum length of {_MAX_URL_LENGTH} characters")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"{field} must use HTTPS (got '{parsed.scheme}')")
    hostname = (parsed.hostname or '').lower()
    if not any(hostname == h.lstrip('.') or hostname.endswith(h)
               for h in _ALLOWED_HOSTNAMES):
        raise ValueError(f"{field} hostname '{hostname}' is not an allowed Doctolib domain")


def sanitize_booking_url(url: str) -> str:
    """Return an HTML-safe, validated booking URL; falls back to doctolib.de root."""
    try:
        _validate_doctolib_url(url, 'booking_url')
        return html.escape(url, quote=True)
    except ValueError:
        return 'https://www.doctolib.de/'


def run_check(doctor: dict, upcoming_days: int) -> dict:
    """
    Check appointment availability for a single doctor on Doctolib.

    Args:
        doctor: dict with keys: name, availabilities_url, booking_url, move_booking_url
        upcoming_days: how many days ahead to consider "near future"

    Returns:
        dict with keys: doctor, slots_total, slots_exist, earlier_slots,
                        availabilities, success, [error]
    """
    doctor_name = doctor.get('name', '?')

    try:
        avail_url = doctor['availabilities_url']

        # H-1: validate URL is a safe Doctolib HTTPS endpoint before fetching
        _validate_doctolib_url(avail_url, 'availabilities_url')

        url_parts = urllib.parse.urlparse(avail_url)
        query = dict(urllib.parse.parse_qsl(url_parts.query))
        query.update({
            'limit': DOCTOLIB_MAX_LIMIT,
            'start_date': date.today(),
        })
        url = url_parts._replace(query=urllib.parse.urlencode(query)).geturl()

        req = urllib.request.Request(url)
        req.add_header('User-Agent', USER_AGENT)
        req.add_header('Accept', 'application/json, text/javascript, */*; q=0.01')
        req.add_header('Accept-Language', 'de-DE,de;q=0.9,en;q=0.8')
        req.add_header('Referer', 'https://www.doctolib.de/')
        req.add_header('X-Requested-With', 'XMLHttpRequest')

        try:
            raw = urllib.request.urlopen(req, timeout=10).read()
        except urllib.error.HTTPError as http_err:
            raw = http_err.read()
            body_preview = raw[:200].decode('utf-8', errors='replace')
            logger.error(
                "run_check HTTP %s for doctor '%s'. Response preview: %s",
                http_err.code, doctor_name, body_preview,
            )
            user_msg = _http_error_to_user_message(http_err.code)
            return {'doctor': doctor, 'success': False, 'error': user_msg}

        response_text = raw.decode('utf-8')

        if not response_text.strip():
            logger.error("run_check: empty response for doctor '%s'", doctor_name)
            return {'doctor': doctor, 'success': False,
                    'error': 'Leere Antwort von Doctolib (möglicherweise blockiert)'}

        try:
            availabilities = json.loads(response_text)
        except json.JSONDecodeError as json_err:
            preview = response_text[:300]
            logger.error(
                "run_check: invalid JSON for doctor '%s'. Error: %s. Preview: %s",
                doctor_name, json_err, preview,
            )
            # Detect common non-JSON responses
            if '<html' in preview.lower() or '<!doctype' in preview.lower():
                user_msg = 'Doctolib hat eine HTML-Seite zurückgegeben (Rate-Limit oder veraltete URL)'
            else:
                user_msg = 'Ungültige Antwort von Doctolib (kein JSON)'
            return {'doctor': doctor, 'success': False, 'error': user_msg}

        slots_total = availabilities['total']

        # Filter slots to only those within the doctor's configured upcoming_days window.
        # upcoming_days=0 means "today only"; otherwise slots up to N days from now qualify.
        today = datetime.today().date()
        if upcoming_days == 0:
            cutoff_date = today
        else:
            cutoff_date = today + timedelta(days=upcoming_days)

        slots_in_window = 0
        earlier_slots = False
        for day in availabilities['availabilities']:
            if not day['slots']:
                continue
            slot_date = datetime.fromisoformat(day['date']).date()
            if slot_date <= cutoff_date:
                slots_in_window += len(day['slots'])
                earlier_slots = True

        slots_exist = slots_in_window > 0

        return {
            'doctor':         doctor,
            'slots_total':    slots_in_window,
            'slots_exist':    slots_exist,
            'earlier_slots':  earlier_slots,
            'availabilities': availabilities,
            'upcoming_days':  upcoming_days,
            'success':        True,
        }

    except ValueError as e:
        # URL validation errors – safe to show as-is (no secrets)
        logger.error("run_check validation error for doctor '%s': %s", doctor_name, e)
        return {'doctor': doctor, 'success': False, 'error': str(e)}

    except Exception as e:
        # H-2: log full detail server-side only
        logger.error("run_check unexpected error for doctor '%s': %s", doctor_name, e, exc_info=True)
        return {'doctor': doctor, 'success': False,
                'error': f'Unerwarteter Fehler: {type(e).__name__}'}


def _http_error_to_user_message(status_code: int) -> str:
    """Map HTTP status codes to human-readable German error messages."""
    messages = {
        403: 'Zugriff verweigert (HTTP 403) – URL möglicherweise veraltet oder IP blockiert',
        404: 'Arzt-URL nicht gefunden (HTTP 404) – bitte URL aus DevTools neu kopieren',
        406: 'Nicht akzeptabel (HTTP 406) – Doctolib lehnt die Anfrage ab; bitte URL aus DevTools neu kopieren',
        429: 'Zu viele Anfragen (HTTP 429) – Doctolib hat die IP vorübergehend blockiert',
        503: 'Doctolib nicht erreichbar (HTTP 503) – bitte später erneut versuchen',
    }
    return messages.get(status_code, f'HTTP-Fehler {status_code} von Doctolib')
