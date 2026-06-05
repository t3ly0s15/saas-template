import os, json, logging, base64
from typing import Tuple
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import Request as HttpRequest, urlopen
from urllib.error import URLError
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from database import get_db
from auth import get_current_user

_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
_APP_URL       = os.environ.get("APP_URL", "http://localhost:8000")
_REDIRECT_URI  = f"{_APP_URL}/api/integrations/google/callback"
_SCOPES        = " ".join([
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
])

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


# ── Helpers Google Calendar ────────────────────────────────────────────────────

def _create_calendar_event(nom_client: str, email_client: str, date_heure: str, notes: str = "", duration: int = 60) -> Tuple[str, str]:
    """Crée un événement Google Calendar et invite le client.

    Retourne ``(event_id, event_url)`` — deux chaînes vides si Google n'est pas connecté
    ou si l'appel API échoue (non bloquant pour la création du RDV).
    """
    token = _get_valid_token()
    if not token:
        return "", ""
    try:
        start_dt = datetime.fromisoformat(date_heure)
        end_dt   = start_dt + timedelta(minutes=duration)
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return "", ""
    event = {
        "summary":     f"Owntime - RDV avec {nom_client}",
        "description": notes or "Rendez-vous planifié via Owntime",
        "start":       {"dateTime": fmt(start_dt), "timeZone": "Europe/Paris"},
        "end":         {"dateTime": fmt(end_dt),   "timeZone": "Europe/Paris"},
        "attendees":   [{"email": email_client}],
    }
    req = HttpRequest(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        data=json.dumps(event).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        data      = json.loads(urlopen(req, timeout=15).read())
        event_id  = data.get("id", "")
        event_url = data.get("htmlLink", "")
        logging.info("[CALENDAR] Événement créé : %s", event_id)
        return event_id, event_url
    except (URLError, json.JSONDecodeError) as exc:
        logging.error("[CALENDAR ERROR] Création : %s", exc)
        return "", ""

def _update_calendar_event(event_id: str, status: str) -> None:
    """Met à jour le statut d'un événement Google Calendar."""
    if not event_id:
        return
    token = _get_valid_token()
    if not token:
        return
    req = HttpRequest(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        data=json.dumps({"status": status}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        urlopen(req, timeout=10)
        logging.info("[CALENDAR] Événement %s → %s", event_id, status)
    except URLError as exc:
        logging.error("[CALENDAR ERROR] Update : %s", exc)

def _delete_calendar_event(event_id: str) -> None:
    """Supprime un événement Google Calendar."""
    if not event_id:
        return
    token = _get_valid_token()
    if not token:
        return
    req = HttpRequest(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    try:
        urlopen(req, timeout=10)
        logging.info("[CALENDAR] Événement %s supprimé.", event_id)
    except URLError as exc:
        logging.error("[CALENDAR ERROR] Delete : %s", exc)


# ── Helpers Gmail (importables depuis appointments.py) ─────────────────────────

def _get_valid_token() -> str:
    """Retourne un access_token Google valide, en le rafraîchissant si expiré.

    Retourne une chaîne vide si aucun token n'est stocké ou si le refresh échoue.
    """
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    row = conn.execute("SELECT * FROM google_tokens LIMIT 1").fetchone()
    conn.close()
    if not row:
        logging.warning("[GMAIL] Aucun token Google stocké.")
        return ""
    now = int(datetime.utcnow().timestamp())
    if row.get("expires_at", 0) > now + 60:
        return row["access_token"]
    if not row.get("refresh_token"):
        logging.warning("[GMAIL] Pas de refresh token disponible.")
        return ""
    payload = urlencode({
        "client_id":     _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "refresh_token": row["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = HttpRequest("https://oauth2.googleapis.com/token", data=payload,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        tokens     = json.loads(urlopen(req, timeout=10).read())
        new_access = tokens["access_token"]
        new_exp    = now + tokens.get("expires_in", 3600)
        conn2 = get_db()
        conn2.execute("UPDATE google_tokens SET access_token=?,expires_at=?,updated_at=datetime('now') WHERE id=?",
                      (new_access, new_exp, row["id"]))
        conn2.commit(); conn2.close()
        logging.info("[GMAIL] Token rafraîchi avec succès.")
        return new_access
    except (URLError, KeyError, json.JSONDecodeError) as exc:
        logging.error("[GMAIL] Échec rafraîchissement : %s", exc)
        return ""

def _send_via_gmail(to: str, subject: str, html: str) -> bool:
    """Envoie un email HTML via l'API Gmail. Retourne True si succès, False sinon."""
    token = _get_valid_token()
    if not token:
        return False
    msg = MIMEMultipart("alternative")
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    payload = json.dumps({"raw": raw}).encode()
    req = HttpRequest("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                      data=payload, headers={"Authorization": f"Bearer {token}",
                                             "Content-Type": "application/json"})
    try:
        urlopen(req, timeout=15)
        logging.info("[GMAIL] Email envoyé à %s", to)
        return True
    except URLError as exc:
        logging.error("[GMAIL ERROR] %s", exc)
        return False


# ── Publiques (Google redirige ici, pas de Bearer token possible) ──────────────

@router.get("/google/login")
def google_login():
    params = urlencode({
        "client_id":     _CLIENT_ID,
        "redirect_uri":  _REDIRECT_URI,
        "response_type": "code",
        "scope":         _SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/google/callback")
def google_callback(code: str = None, error: str = None):
    if error or not code:
        logging.warning("[GOOGLE OAUTH] Callback error: %s", error)
        return RedirectResponse("/appointments?google_error=1")

    payload = urlencode({
        "code":          code,
        "client_id":     _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "redirect_uri":  _REDIRECT_URI,
        "grant_type":    "authorization_code",
    }).encode()
    req = HttpRequest(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        resp   = urlopen(req, timeout=10)
        tokens = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as exc:
        logging.error("[GOOGLE OAUTH] Token exchange failed: %s", exc)
        return RedirectResponse("/appointments?google_error=2")

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_at    = int(datetime.utcnow().timestamp()) + tokens.get("expires_in", 3600)

    conn = get_db()
    conn.execute("DELETE FROM google_tokens")
    conn.execute(
        "INSERT INTO google_tokens (access_token, refresh_token, expires_at) VALUES (?, ?, ?)",
        (access_token, refresh_token, expires_at),
    )
    conn.commit()
    conn.close()
    logging.info("[GOOGLE OAUTH] Tokens stockés. Refresh token : %s", "oui" if refresh_token else "non")
    return RedirectResponse("/appointments")


# ── Protégées (requièrent le JWT de session) ───────────────────────────────────

@router.get("/google/freebusy")
def google_freebusy(date_heure: str, duration: int = 30, current_user: dict = Depends(get_current_user)):
    """Vérifie si un créneau est libre dans le Google Calendar principal.

    ``date_heure`` : format ISO sans timezone (ex. "2024-06-15T09:00"), interprété en Europe/Paris.
    Retourne ``{"connected": bool, "busy": bool}``.
    """
    token = _get_valid_token()
    if not token:
        return {"connected": False, "busy": False}
    try:
        paris_tz = timedelta(hours=2)
        start_dt = datetime.fromisoformat(date_heure).replace(tzinfo=timezone(paris_tz))
        end_dt = start_dt + timedelta(minutes=duration)
    except Exception:
        raise HTTPException(status_code=400, detail="Format de date invalide")
    body = json.dumps({
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "items": [{"id": "primary"}],
    }).encode()
    req = HttpRequest(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        resp = json.loads(urlopen(req, timeout=10).read())
        busy_slots = resp.get("calendars", {}).get("primary", {}).get("busy", [])
        return {"connected": True, "busy": len(busy_slots) > 0}
    except (URLError, json.JSONDecodeError) as exc:
        logging.error("[FREEBUSY ERROR] %s", exc)
        raise HTTPException(status_code=500, detail="Erreur lors de la vérification")


@router.get("/google/status")
def google_status(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    row = conn.execute("SELECT * FROM google_tokens LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {"connected": False}
    now_ts    = int(datetime.utcnow().timestamp())
    connected = bool(row.get("refresh_token")) or (row.get("expires_at", 0) > now_ts + 60)
    return {"connected": connected}


@router.post("/google/disconnect")
def google_disconnect(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM google_tokens")
    conn.commit()
    conn.close()
    logging.info("[GOOGLE OAUTH] Tokens supprimés par %s", current_user.get("email"))
    return {"message": "Google déconnecté"}
