import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "saas.db")))


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            nom TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            actif INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS google_tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            access_token  TEXT,
            refresh_token TEXT,
            expires_at    INTEGER,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS lead (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            city TEXT,
            activite TEXT,
            telephone TEXT,
            lien TEXT,
            email TEXT,
            nom TEXT,
            prenom TEXT,
            statut TEXT DEFAULT 'nouveau',
            statut_updated_at TEXT,
            note TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            status_log TEXT DEFAULT '[]',
            user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)


    # ── Migrations ─────────────────────────────────────────────────────────────
    # Ajouter ici les ALTER TABLE pour les nouvelles colonnes
    # Exemple :
    # conn = get_db()
    # existing = [r[1] for r in conn.execute("PRAGMA table_info(user)").fetchall()]
    # if "phone" not in existing:
    #     conn.execute("ALTER TABLE user ADD COLUMN phone TEXT")
    # conn.commit()
    # conn.close()

    # Table des Rendez-vous (Créer un RDV, statut et suivi)
    cursor = conn.cursor() # <-- On définit le cursor pour être sûr qu'il existe !
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom_client TEXT NOT NULL,
            email_client TEXT NOT NULL,
            telephone_client TEXT NOT NULL,
            objet TEXT NOT NULL,
            date_heure TEXT NOT NULL,
            notes TEXT,
            google_event_id TEXT,
            statut TEXT DEFAULT 'en_attente',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # ── Migration : colonnes de suivi des rappels automatiques ────────────────
    existing = [r[1] for r in conn.execute("PRAGMA table_info(appointment)").fetchall()]
    for col, typedef in [("rappel_24h_envoye", "INTEGER DEFAULT 0"), ("rappel_2h_envoye", "INTEGER DEFAULT 0"), ("lead_id", "INTEGER"), ("google_event_url", "TEXT"), ("duration", "INTEGER DEFAULT 60")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE appointment ADD COLUMN {col} {typedef}")

# Validation et ENFIN Fermeture (À laisser tout à la fin)
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()