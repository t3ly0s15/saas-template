from collections import defaultdict
from time import time
from fastapi import APIRouter, HTTPException, Security, Request
from pydantic import BaseModel
from database import get_db
from auth import hash_password, verify_password, create_token, get_current_user

# ── Rate limiting login ────────────────────────────────────────────────────────
_login_attempts = defaultdict(list)
_MAX_ATTEMPTS = 5
_WINDOW = 15 * 60  # 15 minutes


def _check_rate_limit(ip: str):
    now = time()
    attempts = [t for t in _login_attempts[ip] if now - t < _WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        wait = int(_WINDOW - (now - attempts[0]))
        raise HTTPException(status_code=429, detail=f"Trop de tentatives. Réessaie dans {wait // 60}m{wait % 60:02d}s.")
    _login_attempts[ip].append(now)


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateUserRequest(BaseModel):
    email: str
    password: str
    nom: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Auth ───────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(body: LoginRequest, request: Request):
    ip = request.client.host
    _check_rate_limit(ip)
    conn = get_db()
    user = conn.execute("SELECT * FROM user WHERE email = ? AND actif = 1", (body.email,)).fetchone()
    conn.close()
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    _login_attempts.pop(ip, None)
    token = create_token(user["id"], user["role"])
    return {
        "token": token,
        "user": {"id": user["id"], "nom": user["nom"], "email": user["email"], "role": user["role"]}
    }


@router.get("/me")
def me(current_user=Security(get_current_user)):
    return {"id": current_user["id"], "nom": current_user["nom"], "email": current_user["email"], "role": current_user["role"]}


@router.patch("/me/password")
def change_password(body: ChangePasswordRequest, current_user=Security(get_current_user)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mot de passe trop court (6 caractères min)")
    conn = get_db()
    user = conn.execute("SELECT password_hash FROM user WHERE id = ?", (current_user["id"],)).fetchone()
    if not verify_password(body.current_password, user["password_hash"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")
    conn.execute("UPDATE user SET password_hash = ? WHERE id = ?",
                 (hash_password(body.new_password), current_user["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Admin : gestion utilisateurs ──────────────────────────────────────────────

@router.get("/users")
def list_users(current_user=Security(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    conn = get_db()
    rows = conn.execute("SELECT id, email, nom, role, actif, created_at FROM user ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/users", status_code=201)
def create_user(body: CreateUserRequest, current_user=Security(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    conn = get_db()
    if conn.execute("SELECT id FROM user WHERE email = ?", (body.email,)).fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email déjà utilisé")
    conn.execute("INSERT INTO user (email, password_hash, nom, role) VALUES (?, ?, ?, ?)",
                 (body.email, hash_password(body.password), body.nom, body.role))
    conn.commit()
    user = conn.execute("SELECT id, email, nom, role, actif FROM user WHERE email = ?", (body.email,)).fetchone()
    conn.close()
    return dict(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, current_user=Security(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Impossible de supprimer son propre compte")
    conn = get_db()
    conn.execute("DELETE FROM user WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: dict, current_user=Security(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin uniquement")
    conn = get_db()
    allowed = {k: v for k, v in body.items() if k in ("actif", "role", "nom")}
    if "password" in body:
        allowed["password_hash"] = hash_password(body["password"])
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        conn.execute(f"UPDATE user SET {sets} WHERE id=?", (*allowed.values(), user_id))
        conn.commit()
    row = conn.execute("SELECT id, email, nom, role, actif FROM user WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}
