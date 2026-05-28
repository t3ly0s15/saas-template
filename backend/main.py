import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from database import init_db
from routers import auth_router, leads, appointments, integrations
from routers.appointments import reminder_loop, email_router as appointments_email_router

# ── Ajouter ici les imports de nouveaux routers ────────────────────────────────
# from routers import mon_module

app = FastAPI(title="Mon SaaS", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO prod: remplacer par ["https://votre-domaine.com"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(leads.router)
app.include_router(appointments.router)
app.include_router(appointments_email_router)
app.include_router(integrations.router)
# Ajouter ici les nouveaux routers :
# app.include_router(mon_module.router)

# ── Static files ───────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

init_db()


@app.on_event("startup")
async def start_reminder_task():
    asyncio.create_task(reminder_loop())


@app.get("/health")
def health():
    return {"status": "ok"}


# ── SPA routes — ajouter ici les routes URL de chaque page ────────────────────
@app.get("/")
@app.get("/dashboard")
@app.get("/leads")
@app.get("/settings")
@app.get("/appointments")
# @app.get("/ma-page")   ← décommenter pour chaque nouvelle page
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))
