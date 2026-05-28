import os, logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from auth import get_current_user

_APP_URL = os.environ.get("APP_URL", "http://localhost:8000")

router       = APIRouter(prefix="/appointments",     tags=["appointments"])
email_router = APIRouter(prefix="/api/appointments", tags=["appointments-public"])

# Modèle de données pour valider ce que l'interface nous envoie
class AppointmentCreate(BaseModel):
    nom_client: str
    email_client: str
    telephone_client: str
    objet: str
    date_heure: str
    notes: Optional[str] = None
    statut: str = "en_attente"
    lead_id: Optional[int] = None
    duration: int = 60

class AppointmentUpdate(BaseModel):
    statut: Optional[str] = None

# ── Helpers email ──────────────────────────────────────────────────────────────

def _send_confirmation_email(apt: dict, confirm_url: str, cancel_url: str, label: str = "", event_url: str = "") -> None:
    """Envoie l'email de confirmation/rappel au client via Gmail API.

    Si l'envoi échoue (Google non connecté), logue les URLs en fallback.
    ``label`` (ex. "J-1", "J-2h") personnalise l'objet pour les rappels.
    ``event_url`` ajoute un bouton "Ouvrir dans Google Calendar" si fourni.
    """
    date_fmt   = apt["date_heure"].replace("T", " ")
    duration   = apt.get("duration", 60)
    titre      = f"Rappel {label} — votre rendez-vous approche" if label else "Rendez-vous en attente de confirmation"
    subject    = f"Rappel {label} — votre RDV : {apt['objet']}" if label else f"Confirmation de votre RDV : {apt['objet']}"
    cal_block  = f'<div style="margin-top:20px;padding-top:16px;border-top:1px solid #e5e7eb;text-align:center"><a href="{event_url}" target="_blank" style="color:#6b7280;font-size:13px;text-decoration:none;">📅 Retrouvez ce créneau dans votre agenda : <span style="color:#3b82f6;text-decoration:underline">Ouvrir dans Google Calendar</span></a></div>' if event_url else ''

    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:30px;margin:0">
<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:12px;padding:36px;border:1px solid #e5e7eb">
  <h2 style="color:#111827;margin:0 0 6px;font-size:18px">{titre}</h2>
  <p style="color:#6b7280;margin:0 0 24px;font-size:14px">Bonjour <strong>{apt["nom_client"]}</strong>, voici le récapitulatif de votre rendez-vous :</p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:28px;font-size:14px">
    <tr><td style="padding:5px 0;color:#9ca3af;width:110px">Objet</td><td style="padding:5px 0;font-weight:600;color:#111827">{apt["objet"]}</td></tr>
    <tr><td style="padding:5px 0;color:#9ca3af">Date &amp; Heure</td><td style="padding:5px 0;font-weight:600;color:#111827">{date_fmt}</td></tr>
    <tr><td style="padding:5px 0;color:#9ca3af">Durée</td><td style="padding:5px 0;font-weight:600;color:#111827">{duration} min</td></tr>
    {'<tr><td style="padding:5px 0;color:#9ca3af">Note</td><td style="padding:5px 0;color:#374151">' + apt["notes"] + '</td></tr>' if apt.get("notes") else ''}
  </table>
  <p style="color:#374151;margin:0 0 20px;font-size:14px">Merci de confirmer ou d'annuler votre présence :</p>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
    <a href="{confirm_url}" style="display:inline-block;padding:14px 28px;background:#16a34a;color:#fff;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700">✅ Confirmer mon rendez-vous</a>
    <a href="{cancel_url}"  style="display:inline-block;padding:14px 28px;background:#dc2626;color:#fff;border-radius:10px;text-decoration:none;font-size:15px;font-weight:700">❌ Annuler le rendez-vous</a>
  </div>
  {cal_block}
</div></body></html>"""

    from routers.integrations import _send_via_gmail
    sent = _send_via_gmail(apt["email_client"], subject, html)
    if not sent:
        logging.info("[EMAIL MOCK] To:%s | Confirm:%s | Cancel:%s", apt["email_client"], confirm_url, cancel_url)

# ── Robot de rappels automatiques ─────────────────────────────────────────────

def _run_reminders() -> None:
    """Scanne tous les RDV actifs et envoie les rappels J-1 (23h30–24h30) et J-2h (1h30–2h30)."""
    now = datetime.now()
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    rows = conn.execute(
        "SELECT * FROM appointment WHERE statut != 'annule'"
        " AND (rappel_24h_envoye = 0 OR rappel_2h_envoye = 0)"
    ).fetchall()
    conn.close()
    logging.debug("[ROBOT AUTOMATIQUE] Scan : %d RDV éligibles.", len(rows))

    for apt in rows:
        try:
            rdv_dt = datetime.fromisoformat(apt["date_heure"])
        except (ValueError, TypeError):
            continue
        delta_h = (rdv_dt - now).total_seconds() / 3600

        def _send_and_mark(col: str, label: str) -> None:
            logging.info("[ROBOT AUTOMATIQUE] Envoi rappel %s pour %s (RDV le %s)",
                         label, apt["nom_client"], apt["date_heure"])
            _send_confirmation_email(
                apt,
                f"{_APP_URL}/api/appointments/{apt['id']}/confirm",
                f"{_APP_URL}/api/appointments/{apt['id']}/cancel",
                label,
            )
            upd = get_db()
            upd.execute(f"UPDATE appointment SET {col} = 1 WHERE id = ?", (apt["id"],))
            upd.commit()
            upd.close()

        if apt["rappel_24h_envoye"] == 0 and 23.5 <= delta_h <= 24.5:
            _send_and_mark("rappel_24h_envoye", "J-1")
        elif apt["rappel_2h_envoye"] == 0 and 1.5 <= delta_h <= 2.5:
            _send_and_mark("rappel_2h_envoye", "J-2h")

async def reminder_loop() -> None:
    """Boucle de fond lancée au démarrage : appelle _run_reminders() toutes les 60 secondes."""
    import asyncio
    while True:
        await asyncio.sleep(60)
        try:
            _run_reminders()
        except Exception as exc:
            logging.error("[ROBOT AUTOMATIQUE] Erreur inattendue : %s", exc)

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_appointment(data: AppointmentCreate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO appointment (nom_client, email_client, telephone_client, objet, date_heure, notes, statut, lead_id, duration)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data.nom_client, data.email_client, data.telephone_client, data.objet, data.date_heure, data.notes, data.statut, data.lead_id, data.duration))
    
    conn.commit()
    apt_id      = cursor.lastrowid
    if data.lead_id:
        conn.execute("UPDATE lead SET statut = 'rdv_pris' WHERE id = ?", (data.lead_id,))
        conn.commit()
    conn.close()
    confirm_url = f"{_APP_URL}/api/appointments/{apt_id}/confirm"
    cancel_url  = f"{_APP_URL}/api/appointments/{apt_id}/cancel"
    from routers.integrations import _create_calendar_event
    event_id, event_url = _create_calendar_event(data.nom_client, data.email_client, data.date_heure, data.notes or "", data.duration)
    if event_id:
        upd = get_db()
        upd.execute("UPDATE appointment SET google_event_id = ?, google_event_url = ? WHERE id = ?",
                    (event_id, event_url, apt_id))
        upd.commit(); upd.close()
    _send_confirmation_email(
        {"id": apt_id, "nom_client": data.nom_client, "email_client": data.email_client,
         "objet": data.objet, "date_heure": data.date_heure, "notes": data.notes or ""},
        confirm_url, cancel_url, event_url=event_url,
    )
    return {"message": "Rendez-vous enregistré et email envoyé", "confirm_url": confirm_url, "cancel_url": cancel_url}

@router.get("/")
def get_appointments(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.row_factory = lambda cursor, row: dict(zip([col[0] for col in cursor.description], row))
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM appointment ORDER BY date_heure DESC")
    appointments = cursor.fetchall()
    conn.close()
    for apt in appointments:
        apt["confirm_url"] = f"{_APP_URL}/api/appointments/{apt['id']}/confirm"
        apt["cancel_url"]  = f"{_APP_URL}/api/appointments/{apt['id']}/cancel"
    return appointments

# Route de simulation des rappels — utile en recette, désactiver en prod si souhaité
@router.post("/test-reminders")
def test_reminders(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    rows = conn.execute("SELECT * FROM appointment WHERE statut != 'annule'").fetchall()
    conn.close()
    sent = []
    for apt in rows:
        for col, label in (("rappel_24h_envoye", "J-1"), ("rappel_2h_envoye", "J-2h")):
            logging.info("[ROBOT SIMULÉ] Envoi rappel %s pour %s <%s>", label, apt["nom_client"], apt["email_client"])
            _send_confirmation_email(
                apt,
                f"{_APP_URL}/api/appointments/{apt['id']}/confirm",
                f"{_APP_URL}/api/appointments/{apt['id']}/cancel",
                label,
            )
            sent.append({"client": apt["nom_client"], "rappel": label})
    return {"simulated": len(sent), "detail": sent}

# ── Routes publiques client (confirmation / annulation par lien email) ──────────

def _public_page(title: str, message: str, is_confirm: bool, cal_url: str = "") -> str:
    if is_confirm:
        icon_svg = '<svg width="72" height="72" viewBox="0 0 72 72" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="36" cy="36" r="36" fill="#dcfce7"/><path d="M22 36L31 45L50 26" stroke="#16a34a" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        cal_block = f'<a href="{cal_url}" target="_blank" style="display:inline-block;margin-top:20px;padding:10px 20px;border:1px solid #e5e7eb;border-radius:8px;font-size:13px;color:#4b5563;text-decoration:none;transition:background .15s">📅 Ouvrir dans mon Google Calendar</a>' if cal_url else ''
    else:
        icon_svg = '<svg width="72" height="72" viewBox="0 0 72 72" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="36" cy="36" r="36" fill="#fee2e2"/><path d="M24 24L48 48M48 24L24 48" stroke="#dc2626" stroke-width="4.5" stroke-linecap="round"/></svg>'
        cal_block = ''
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9fafb;min-height:100vh;display:flex;align-items:center;justify-content:center;margin:0;padding:20px;box-sizing:border-box">
<div style="max-width:420px;width:100%;background:#fff;border-radius:20px;padding:48px 40px;border:1px solid #e5e7eb;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.06)">
  <div style="margin-bottom:24px">{icon_svg}</div>
  <h1 style="color:#111827;font-size:22px;font-weight:700;margin:0 0 12px;letter-spacing:-.3px">{title}</h1>
  <p style="color:#6b7280;font-size:14px;line-height:1.6;margin:0">{message}</p>
  {cal_block}
</div></body></html>"""

@router.get("/{appointment_id}/confirm", response_class=HTMLResponse)
def public_confirm(appointment_id: int):
    conn = get_db()
    conn.execute("UPDATE appointment SET statut = 'confirme' WHERE id = ?", (appointment_id,))
    conn.commit(); conn.close()
    return HTMLResponse(_public_page("Présence confirmée !", "Merci, votre présence a bien été confirmée. À très bientôt.", True))

@router.get("/{appointment_id}/cancel", response_class=HTMLResponse)
def public_cancel(appointment_id: int):
    conn = get_db()
    conn.execute("UPDATE appointment SET statut = 'annule' WHERE id = ?", (appointment_id,))
    conn.commit(); conn.close()
    return HTMLResponse(_public_page("Annulation enregistrée", "Votre annulation a bien été prise en compte. N'hésitez pas à nous recontacter.", False))

@router.patch("/{appointment_id}")
def patch_appointment(appointment_id: int, data: AppointmentUpdate, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    fields = {k: v for k, v in data.dict().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Aucun champ à mettre à jour")
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [appointment_id]
    conn.execute(f"UPDATE appointment SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return {"message": "Rendez-vous mis à jour"}

@router.delete("/{appointment_id}", status_code=204)
def delete_appointment(appointment_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    conn.execute("DELETE FROM appointment WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()

# ── Routes publiques email (préfixe /api/appointments pour les boutons d'email) ─

@email_router.get("/{appointment_id}/confirm", response_class=HTMLResponse)
def api_public_confirm(appointment_id: int):
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    apt = conn.execute("SELECT google_event_id, google_event_url, lead_id FROM appointment WHERE id = ?", (appointment_id,)).fetchone()
    conn.execute("UPDATE appointment SET statut = 'confirme' WHERE id = ?", (appointment_id,))
    if apt and apt.get("lead_id"):
        conn.execute("UPDATE lead SET statut = 'rdv_confirme' WHERE id = ?", (apt["lead_id"],))
    conn.commit(); conn.close()
    if apt and apt.get("google_event_id"):
        from routers.integrations import _update_calendar_event
        _update_calendar_event(apt["google_event_id"], "confirmed")
    return HTMLResponse(_public_page("Présence confirmée !", "Merci, votre présence a bien été confirmée. À très bientôt.", True, apt.get("google_event_url", "") if apt else ""))

@email_router.get("/{appointment_id}/cancel", response_class=HTMLResponse)
def api_public_cancel(appointment_id: int):
    conn = get_db()
    conn.row_factory = lambda c, row: dict(zip([col[0] for col in c.description], row))
    apt = conn.execute("SELECT google_event_id, lead_id FROM appointment WHERE id = ?", (appointment_id,)).fetchone()
    conn.execute("UPDATE appointment SET statut = 'annule' WHERE id = ?", (appointment_id,))
    if apt and apt.get("lead_id"):
        conn.execute("UPDATE lead SET statut = 'rdv_annule' WHERE id = ?", (apt["lead_id"],))
    conn.commit(); conn.close()
    if apt and apt.get("google_event_id"):
        from routers.integrations import _delete_calendar_event
        _delete_calendar_event(apt["google_event_id"])
    return HTMLResponse(_public_page("Annulation enregistrée", "Votre annulation a bien été prise en compte. N'hésitez pas à nous recontacter.", False))