# SaaS Template

Boilerplate prêt à l'emploi pour construire un SaaS interne rapidement.

**Stack :** FastAPI · SQLite · JWT · Alpine.js · Tailwind CDN

**Inclus :** Auth complète · Gestion équipe · Module Leads (CRUD + import CSV) · Module Rendez-vous (Google Calendar + Gmail) · SPA avec sidebar

---

## Démarrage en 4 étapes

### 1. Copier les variables d'environnement

```bash
cp backend/.env.example backend/.env
```

Ouvre `backend/.env` et génère une clé secrète :

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Colle le résultat comme valeur de `JWT_SECRET_KEY` dans `.env`.

> ⚠️ Ne jamais committer `.env` — il est dans `.gitignore`

---

### 2. Lancer le serveur

```bash
bash start.sh
# → http://localhost:8000
```

Le venv est créé automatiquement au premier lancement (~2 min). Les suivants sont instantanés.

---

### 3. Créer le premier compte admin

```bash
cd backend && .venv/bin/python3 -c "
from database import get_db
from auth import hash_password
conn = get_db()
conn.execute(\"INSERT INTO user (email, password_hash, nom, role) VALUES (?, ?, ?, 'admin')\",
             ('admin@mondomaine.fr', hash_password('motdepasse'), 'Admin'))
conn.commit()
print('Admin créé')
"
```

> ⚠️ Utiliser `.venv/bin/python3` et non `python3` — seul le venv a les packages installés.

---

### 4. Se connecter

Ouvre [http://localhost:8000](http://localhost:8000) et connecte-toi.

---

## Variables d'environnement

| Variable | Obligatoire | Description |
|---|---|---|
| `JWT_SECRET_KEY` | ✅ | Clé secrète JWT (64 hex chars) |
| `DB_PATH` | ✅ | Chemin SQLite (ex. `/data/saas.db`) |
| `APP_URL` | ✅ | URL publique de l'app (ex. `https://app.exemple.com`) |
| `GOOGLE_CLIENT_ID` | Optionnel | OAuth2 Client ID — active Calendar + Gmail |
| `GOOGLE_CLIENT_SECRET` | Optionnel | OAuth2 Client Secret |

Sans `GOOGLE_CLIENT_ID`/`SECRET`, le module Rendez-vous fonctionne mais sans synchronisation Calendar et sans envoi d'emails (les liens confirm/cancel sont loggués en console).

### Configurer Google OAuth2

1. Créer un projet sur [console.cloud.google.com](https://console.cloud.google.com/)
2. Activer **Google Calendar API** et **Gmail API**
3. Créer des identifiants OAuth2 → type **"Application Web"**
4. Ajouter l'URI de redirection : `{APP_URL}/api/integrations/google/callback`
5. Copier Client ID et Client Secret dans `.env`
6. Dans l'app → onglet **Rendez-vous** → bouton **"Connecter Google Calendar & Gmail"**

---

## Flux Leads → Rendez-vous

```
Lead (tableau)
  └─ bouton 📅 Planifier
       └─ Modal "Planifier un RDV"
            ├─ Sélection date (input[type=date])
            ├─ Grille de créneaux 30 min (09:00–16:30)
            │    └─ Si Google connecté → freeBusy API → créneaux grisés si occupés
            └─ Soumission
                 ├─ Création appointment en base
                 ├─ Statut lead → "RDV pris" (verrouillé)
                 ├─ Événement créé dans Google Calendar (invite client)
                 └─ Email client (boutons Confirmer / Annuler)
                      └─ Clic Confirmer → statut → "RDV Confirmé"
                      └─ Clic Annuler  → statut → "RDV Annulé" + suppression Calendar
```

---

## Structure

```
saas_template/
├── Dockerfile            ← déploiement Docker (Coolify, Railway...)
├── .gitignore
├── start.sh              ← lancement local
└── backend/
    ├── main.py           ← FastAPI : routers + static files
    ├── database.py       ← SQLite : tables + migrations
    ├── auth.py           ← JWT : tokens + get_current_user
    ├── requirements.txt
    ├── .env.example      ← variables à copier dans .env
    ├── static/
    │   └── index.html    ← SPA Alpine.js (login + sidebar + pages)
    └── routers/
        ├── auth_router.py    ← login, /me, CRUD users, change password
        ├── leads.py          ← CRUD leads + import CSV
        ├── appointments.py   ← CRUD RDV + emails + robot de rappels
        └── integrations.py   ← Google OAuth2, Calendar, Gmail, freeBusy
```

---

## Ajouter un nouveau module

### 1. Créer la table (database.py)

```python
# Dans init_db() → conn.executescript("""...""")
CREATE TABLE IF NOT EXISTS commande (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    titre TEXT,
    statut TEXT DEFAULT 'nouveau',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 2. Créer le router (routers/commandes.py)

```python
from fastapi import APIRouter, Depends
from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/commandes", dependencies=[Depends(get_current_user)], tags=["commandes"])

@router.get("/")
def list_commandes(current_user=...):
    conn = get_db()
    rows = conn.execute("SELECT * FROM commande WHERE user_id = ?", (current_user["id"],)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
```

### 3. Enregistrer dans main.py

```python
from routers import auth_router, leads, commandes   # ← ajouter
app.include_router(commandes.router)                # ← ajouter
@app.get("/commandes")                              # ← ajouter route SPA
def root(): ...
```

### 4. Ajouter le menu dans index.html

```html
<!-- Dans <nav> sidebar -->
<button @click="page='commandes'" ...>Commandes</button>

<!-- Dans le contenu principal -->
<div x-show="page === 'commandes'">
  ...contenu...
</div>
```

---

## Import CSV Leads

Colonnes supportées (noms flexibles) :

| Colonne | Alias acceptés |
|---------|---------------|
| `title` | `Nom`, `Entreprise` |
| `telephone` | `Tél`, `phone` |
| `email` | `Email` |
| `city` | `Ville`, `ville` |
| `activite` | `Activité`, `categoryName` |
| `lien` | `url`, `Lien Google` |

Doublon détecté sur `title + telephone` — les doublons sont ignorés.

---

## Rôles

Par défaut : `admin` et `user`.  
Modifier le `<select>` dans les modals + les vérifications `current_user["role"] === "admin"`.

---

## Déploiement (Coolify / Docker)

1. Push sur GitHub (repo privé recommandé)
2. Coolify → New Resource → Private Repository → **Dockerfile**
3. Port : `8000`
4. Variables d'env : `JWT_SECRET_KEY`, `DB_PATH=/data/saas.db`, `APP_URL=https://...`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
5. Volume persistant : Source `/data/saas` → Destination `/data`
6. Deploy → créer le compte admin via le Terminal Coolify

---

## Pages disponibles

| URL | Page |
|-----|------|
| `/` · `/dashboard` | Dashboard |
| `/leads` | Leads (CRUD + import CSV + planification RDV) |
| `/appointments` | Rendez-vous (consultation + filtres) |
| `/settings` | Paramètres compte |
| `/team` | Équipe (admin) |
