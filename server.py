"""Dashboard de traduction de livres.

    python server.py            → ouvre http://127.0.0.1:8765
"""

import os
import string
import threading
import time
import webbrowser

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jobs import JobQueue
from ocr_engine import TESSERACT_LANG, OcrUnavailable, configure_tesseract
from scanner import scan_folder
from translator import DEFAULT_MODEL, OLLAMA_URL, list_models
from version import code_version

VERSION = code_version()       # empreinte des fichiers au démarrage

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", 8765

# Nom anglais (pour le prompt) → (code, nom affiché)
LANGUES = {
    "English": ("en", "Anglais"),
    "French": ("fr", "Français"),
    "Korean": ("ko", "Coréen"),
    "Spanish": ("es", "Espagnol"),
    "German": ("de", "Allemand"),
    "Italian": ("it", "Italien"),
    "Portuguese": ("pt", "Portugais"),
    "Dutch": ("nl", "Néerlandais"),
    "Russian": ("ru", "Russe"),
    "Japanese": ("ja", "Japonais"),
    "Chinese": ("zh", "Chinois"),
    "Arabic": ("ar", "Arabe"),
}

app = FastAPI(title="Traducteur de livres")
queue = JobQueue(os.path.join(HERE, "cache_traductions.sqlite"))
last_scan = {"dossier": None, "livres": [], "recursif": True}


# ------------------------------------------------------------
# Fermeture : quand plus aucune page n'est ouverte, l'application
# s'arrête proprement (rien ne reste en arrière-plan).
# ------------------------------------------------------------

PAGE_GONE_AFTER = 30       # s sans page connectée (laisse le temps d'actualiser)
FIRST_PAGE_WAIT = 120      # s pour que la première page s'ouvre au lancement

pages = {"ouvertes": 0, "vue": time.time(), "deja_vue": False}
pages_lock = threading.Lock()
started = time.time()


def page_seen(delta=0):
    with pages_lock:
        pages["ouvertes"] += delta
        pages["vue"] = time.time()
        pages["deja_vue"] = True


def shutdown():
    """Ferme tout de suite : les passages déjà traduits sont dans le cache,
    seul le passage en cours sera refait. Libère la carte graphique."""
    queue.set_paused(True)
    models = {DEFAULT_MODEL} | {j["modele"] for j in queue.jobs if j.get("modele")}
    for m in models:                          # décharger le modèle de la mémoire vidéo
        try:
            requests.post(f"{OLLAMA_URL}/api/generate",
                          json={"model": m, "keep_alive": 0}, timeout=2)
        except requests.RequestException:
            pass
    os._exit(0)


def queue_busy():
    """Un livre est traduit, ou attend son tour (file pas en pause)."""
    with queue.lock:
        return any(j["etat"] == "en cours" or
                   (j["etat"] == "en attente" and not queue.paused)
                   for j in queue.jobs)


def watchdog():
    while True:
        time.sleep(5)
        with pages_lock:
            ouvertes, vue, deja = pages["ouvertes"], pages["vue"], pages["deja_vue"]
        now = time.time()
        # une traduction en cours continue même si la page se tait (onglet
        # mis en veille par le navigateur) : arrêt seulement par « 종료 »
        # ou quand la file n'a plus rien à faire
        if ouvertes > 0 or queue_busy():
            continue
        if (deja and now - vue > PAGE_GONE_AFTER) or \
                (not deja and now - started > FIRST_PAGE_WAIT):
            shutdown()


threading.Thread(target=watchdog, daemon=True).start()


# ------------------------------------------------------------
# Explorateur de dossiers (le navigateur ne donne pas les chemins)
# ------------------------------------------------------------

@app.get("/api/browse")
def browse(path: str = ""):
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase
                      if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": None, "dirs": drives}
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise HTTPException(404, "Dossier introuvable")
    try:
        dirs = sorted(
            (d for d in os.listdir(path)
             if os.path.isdir(os.path.join(path, d)) and not d.startswith(".")),
            key=str.lower,
        )
    except PermissionError:
        dirs = []
    parent = os.path.dirname(path)
    if parent == path:                      # racine d'un disque
        parent = "" if os.name == "nt" else None
    return {"path": path, "parent": parent,
            "dirs": [os.path.join(path, d) for d in dirs]}


# ------------------------------------------------------------
# Scan, langues, modèles
# ------------------------------------------------------------

class ScanRequest(BaseModel):
    dossier: str
    recursif: bool = True
    # « Traduire au fur et à mesure du scan » : chaque livre scanné part
    # tout de suite dans la file avec ces réglages
    auto_traduire: bool = False
    source: str = "auto"
    cible: str | None = None
    modele: str | None = None
    images: bool = False


def source_of(book, mode, corrections=None):
    """Langue du livre : correction > détection (auto) > choix manuel."""
    if mode == "auto":
        return (corrections or {}).get(book["path"]) or book.get("langue")
    return mode


# Le scan tourne en arrière-plan ; le dashboard suit sa progression.
scan_state = {"etat": "inactif", "fait": 0, "total": 0, "dossier": None}
scan_lock = threading.Lock()


@app.post("/api/scan")
def scan(req: ScanRequest):
    if not os.path.isdir(req.dossier):
        raise HTTPException(404, "Dossier introuvable")
    with scan_lock:
        if scan_state["etat"] == "en cours":
            raise HTTPException(409, "Un scan est déjà en cours")
        if req.auto_traduire and req.cible not in LANGUES:
            raise HTTPException(400, "Langue de sortie inconnue")
        scan_state.update(etat="en cours", fait=0, total=0, dossier=req.dossier,
                          envoyes=0)
        last_scan.update(dossier=req.dossier, livres=[], recursif=req.recursif)
    code = LANGUES[req.cible][0] if req.auto_traduire else None

    def progress(fait, total, livre):
        envoye = 0
        if req.auto_traduire:
            source = source_of(livre, req.source)
            # mêmes règles que la sélection par défaut : langue connue, pas
            # déjà traduit, pas déjà dans la langue de sortie
            if (livre["supporte"] and source in LANGUES and source != req.cible
                    and code not in livre["traductions"]):
                envoye = queue.add(req.dossier, [{**livre, "source": source}],
                                   req.cible, code, req.modele, req.images)
        with scan_lock:
            scan_state.update(fait=fait, total=total)
            scan_state["envoyes"] += envoye
            last_scan["livres"].append(livre)

    def run():
        try:
            livres = scan_folder(req.dossier, req.recursif, on_progress=progress)
            with scan_lock:
                last_scan["livres"] = livres
                scan_state.update(etat="terminé", fait=len(livres),
                                  total=len(livres))
        except Exception as e:
            with scan_lock:
                scan_state.update(etat="erreur", erreur=str(e))

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/scan")
def scan_status():
    with scan_lock:
        return {**scan_state, "livres": list(last_scan["livres"])}


@app.get("/api/langues")
def langues():
    return [{"nom": k, "code": c, "affiche": a} for k, (c, a) in LANGUES.items()]


@app.get("/api/modeles")
def modeles():
    try:
        return {"ok": True, "modeles": list_models(), "defaut": DEFAULT_MODEL}
    except requests.RequestException:
        return {"ok": False, "modeles": [], "defaut": DEFAULT_MODEL,
                "erreur": f"Ollama ne répond pas sur {OLLAMA_URL}"}


@app.get("/api/ocr")
def ocr():
    try:
        installees = configure_tesseract()
    except OcrUnavailable as e:
        return {"ok": False, "erreur": str(e), "langues": []}
    langues = [nom for nom, code in TESSERACT_LANG.items() if code in installees]
    return {"ok": True, "langues": langues}


# ------------------------------------------------------------
# File d'attente
# ------------------------------------------------------------

class JobRequest(BaseModel):
    chemins: list[str]
    source: str                      # "auto" ou une langue (mode manuel)
    cible: str
    modele: str
    images: bool = False
    langues: dict[str, str] = {}     # chemin → langue corrigée à la main


@app.post("/api/jobs")
def add_jobs(req: JobRequest):
    if req.cible not in LANGUES or (req.source != "auto" and req.source not in LANGUES):
        raise HTTPException(400, "Langue inconnue")
    par_chemin = {b["path"]: b for b in last_scan["livres"]}
    livres, inconnue, deja = [], 0, 0
    for c in req.chemins:
        b = par_chemin.get(c)
        if not b or not b["supporte"]:
            continue
        source = source_of(b, req.source, req.langues)
        if source not in LANGUES:
            inconnue += 1
        elif source == req.cible:
            deja += 1
        else:
            livres.append({**b, "source": source})
    if not livres:
        raise HTTPException(400, "Aucun livre à traduire : langue inconnue "
                                 "ou déjà dans la langue choisie")
    ajoutes = queue.add(last_scan["dossier"], livres, req.cible,
                        LANGUES[req.cible][0], req.modele, req.images)
    return {"ajoutes": ajoutes, "deja_en_file": len(livres) - ajoutes,
            "langue_inconnue": inconnue,
            "deja_dans_la_langue": deja}


@app.get("/api/status")
def status(version: int | None = None, liste: int | None = None,
           montrer: int | None = None):
    """Sans `version` : réponse immédiate. Avec `version` : attend qu'il se
    passe quelque chose dans la file (au plus 30 s) avant de répondre."""
    if version is None:
        page_seen()
        return queue.snapshot(liste, montrer)
    page_seen(+1)                 # une page attend : elle est ouverte
    try:
        queue.wait(version, timeout=30)
        return queue.snapshot(liste, montrer)
    finally:
        page_seen(-1)


@app.get("/api/version")
def version():
    return {"version": VERSION}


@app.post("/api/quitter")
def quitter():
    threading.Thread(target=shutdown, daemon=True).start()
    return {"ok": True}


@app.post("/api/annuler_attente")
def annuler_attente():
    queue.cancel_waiting()
    return {"ok": True}


@app.post("/api/pause")
def pause():
    queue.set_paused(True)
    return {"pause": True}


@app.post("/api/reprendre")
def reprendre():
    queue.set_paused(False)
    return {"pause": False}


@app.post("/api/annuler/{job_id}")
def annuler(job_id: str):
    queue.cancel(job_id)
    return {"ok": True}


@app.post("/api/reessayer/{job_id}")
def reessayer(job_id: str):
    queue.retry(job_id)
    return {"ok": True}


@app.post("/api/supprimer/{job_id}")
def supprimer(job_id: str):
    queue.remove(job_id)
    return {"ok": True}


@app.post("/api/nettoyer")
def nettoyer():
    queue.clear_finished()
    return {"ok": True}


@app.post("/api/ouvrir")
def ouvrir(chemin: str):
    """Ouvre le dossier d'un fichier traduit dans l'explorateur."""
    folder = os.path.dirname(chemin)
    if not os.path.isdir(folder):
        raise HTTPException(404, "Dossier introuvable")
    if os.name == "nt":
        os.startfile(folder)
    return {"ok": True}


# ------------------------------------------------------------
# Page web
# ------------------------------------------------------------

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")),
          name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    print(f"Dashboard : {url}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
