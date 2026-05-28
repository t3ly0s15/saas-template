import csv
import io
import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Security, UploadFile, File
from pydantic import BaseModel
from database import get_db
from auth import get_current_user

router = APIRouter(prefix="/leads", dependencies=[Depends(get_current_user)], tags=["leads"])


class LeadUpdate(BaseModel):
    statut: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[str] = None
    nom: Optional[str] = None
    prenom: Optional[str] = None
    email: Optional[str] = None


@router.get("/")
def list_leads(q: Optional[str] = None, statut: Optional[str] = None, page: int = 1, page_size: int = 25, current_user=Security(get_current_user)):
    conn = get_db()
    base = "FROM lead WHERE 1=1"
    params = []
    if current_user["role"] != "admin":
        base += " AND (user_id = ? OR user_id IS NULL)"
        params.append(current_user["id"])
    if statut and statut != "tous":
        base += " AND statut = ?"
        params.append(statut)
    if q:
        base += " AND (title LIKE ? OR telephone LIKE ? OR city LIKE ?)"
        params.extend([f"%{q}%"] * 3)
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows = conn.execute(f"SELECT * {base} ORDER BY id DESC LIMIT ? OFFSET ?", params + [page_size, (page-1)*page_size]).fetchall()
    lead_ids = [r["id"] for r in rows]
    rdv_map = {}
    if lead_ids:
        ph = ",".join("?" * len(lead_ids))
        for a in conn.execute(f"SELECT id,lead_id,statut,objet,date_heure,duration,rappel_24h_envoye,rappel_2h_envoye FROM appointment WHERE lead_id IN ({ph}) ORDER BY created_at DESC", lead_ids).fetchall():
            a = dict(a)
            if a["lead_id"] not in rdv_map:
                rdv_map[a["lead_id"]] = a
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        apt = rdv_map.get(d["id"])
        d.update({
            "has_rdv": apt is not None,
            "rdv_id": apt["id"] if apt else None,
            "rdv_statut": apt["statut"] if apt else None,
            "rdv_objet": apt["objet"] if apt else None,
            "rdv_date": apt["date_heure"] if apt else None,
            "rdv_duration": apt["duration"] if apt else None,
            "rappel_24h_envoye": apt["rappel_24h_envoye"] if apt else 0,
            "rappel_2h_envoye": apt["rappel_2h_envoye"] if apt else 0,
        })
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/", status_code=201)
def create_lead(data: dict, current_user=Security(get_current_user)):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO lead (title, city, telephone, email, activite, lien, statut, note, tags, user_id) VALUES (?, ?, ?, ?, ?, ?, 'nouveau', '', '[]', ?)",
        (data.get("title",""), data.get("city",""), data.get("telephone",""), data.get("email",""), data.get("activite",""), data.get("lien",""), current_user["id"])
    )
    conn.commit()
    row = conn.execute("SELECT * FROM lead WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


@router.patch("/{lead_id}")
def update_lead(lead_id: int, body: LeadUpdate, current_user=Security(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM lead WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Lead not found")
    if current_user["role"] != "admin" and row["user_id"] and row["user_id"] != current_user["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Accès refusé")
    fields = body.model_dump(exclude_unset=True)
    if fields:
        if "statut" in fields:
            from datetime import datetime
            current = conn.execute("SELECT statut, status_log FROM lead WHERE id = ?", (lead_id,)).fetchone()
            old_statut = current["statut"] if current else "nouveau"
            if old_statut != fields["statut"]:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                fields["statut_updated_at"] = now
                old_log = json.loads(current["status_log"] or "[]") if current else []
                old_log.append({"ts": now, "from": old_statut, "to": fields["statut"]})
                fields["status_log"] = json.dumps(old_log, ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE lead SET {sets} WHERE id = ?", (*fields.values(), lead_id))
        conn.commit()
    row = conn.execute("SELECT * FROM lead WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row)


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, current_user=Security(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT user_id FROM lead WHERE id = ?", (lead_id,)).fetchone()
    if row and current_user["role"] != "admin" and row["user_id"] and row["user_id"] != current_user["id"]:
        conn.close()
        raise HTTPException(status_code=403, detail="Accès refusé")
    conn.execute("DELETE FROM lead WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), current_user=Security(get_current_user)):
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    conn = get_db()
    inserted = skipped = 0
    for row in reader:
        title = (row.get("title") or row.get("Nom") or row.get("Entreprise") or "").strip()
        telephone = (row.get("telephone") or row.get("Tél") or row.get("phone") or "").strip()
        email = (row.get("email") or row.get("Email") or "").strip()
        city = (row.get("city") or row.get("Ville") or row.get("ville") or "").strip()
        activite = (row.get("activite") or row.get("Activité") or row.get("categoryName") or "").strip()
        lien = (row.get("lien") or row.get("url") or row.get("Lien Google") or "").strip()
        if not title:
            skipped += 1; continue
        if conn.execute("SELECT id FROM lead WHERE title = ? AND telephone = ?", (title, telephone)).fetchone():
            skipped += 1; continue
        conn.execute("INSERT INTO lead (title, city, telephone, email, activite, lien, statut, note, tags, user_id) VALUES (?, ?, ?, ?, ?, ?, 'nouveau', '', '[]', ?)",
                     (title, city, telephone, email, activite, lien, current_user["id"]))
        inserted += 1
    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped}
