"""File d'attente : un seul livre traduit à la fois (une seule carte
graphique), avec pause / reprise / annulation."""

import os
import threading
import time
import uuid

from cache import TranslationCache
from ocr_engine import OcrUnavailable, configure_tesseract, tesseract_code
from engines import translate_file
from scanner import translated_path
from translator import OllamaError, Translator


def output_path(job):
    return translated_path(job["dossier"], job["relpath"], job["cible_code"])


class JobQueue:

    def __init__(self, cache_path):
        self.cache = TranslationCache(cache_path)
        self.lock = threading.Lock()
        self.jobs = []                 # dans l'ordre d'ajout
        self.paused = False
        self._wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------- API utilisée par le serveur ----------------

    def add(self, dossier, books, cible, cible_code, model, images=False):
        """books : livres du scan, chacun avec sa langue source « source ».
        Un livre déjà dans la file pour la même langue de sortie (en
        attente, en cours ou en pause) n'est pas ajouté une 2e fois.
        Retourne le nombre de livres réellement ajoutés."""
        added = 0
        with self.lock:
            actifs = {(j["path"], j["cible"]) for j in self.jobs
                      if j["etat"] in ("en attente", "en cours", "en pause")}
            for b in books:
                if (b["path"], cible) in actifs:
                    continue
                added += 1
                self.jobs.append({
                    "id": uuid.uuid4().hex[:8],
                    "dossier": dossier,
                    "path": b["path"],
                    "relpath": b["relpath"],
                    "source": b["source"],
                    "cible": cible,
                    "cible_code": cible_code,
                    "modele": model,
                    "images": images,
                    "etat": "en attente",
                    "page": 0,
                    "pages": b.get("pages") or 0,
                    "info": "",
                    "sortie": None,
                    "debut": None,
                    "fin": None,
                    "annuler": False,
                })
        self._wake.set()
        return added

    def snapshot(self):
        with self.lock:
            jobs = [{k: v for k, v in j.items() if k != "annuler"}
                    for j in self.jobs]
            return {"pause": self.paused, "jobs": jobs}

    def set_paused(self, value):
        with self.lock:
            self.paused = value
        self._wake.set()

    def cancel(self, job_id):
        with self.lock:
            for j in self.jobs:
                if j["id"] == job_id:
                    if j["etat"] in ("en attente", "en pause"):
                        j["etat"] = "annulé"
                    elif j["etat"] == "en cours":
                        j["annuler"] = True

    def retry(self, job_id):
        with self.lock:
            for j in self.jobs:
                if j["id"] == job_id and j["etat"] in ("erreur", "annulé"):
                    j.update(etat="en attente", info="", page=0,
                             debut=None, annuler=False)
        self._wake.set()

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs
                         if j["etat"] in ("en attente", "en cours", "en pause")]

    # ---------------- travail en arrière-plan ----------------

    def _next_job(self):
        with self.lock:
            if self.paused:
                return None
            for state in ("en pause", "en attente"):   # reprise d'abord
                for j in self.jobs:
                    if j["etat"] == state:
                        j["etat"] = "en cours"
                        j["debut"] = j["debut"] or time.time()
                        return j
        return None

    def _worker(self):
        while True:
            job = self._next_job()
            if job is None:
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job):
        translator = Translator(job["source"], job["cible"], job["modele"],
                                cache=self.cache)
        try:
            ocr_lang = tesseract_code(job["source"], configure_tesseract())
            ocr_error = None
        except OcrUnavailable as e:
            ocr_lang, ocr_error = None, str(e)
        dst = output_path(job)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        def progress(i, total, info):
            with self.lock:
                job["page"], job["pages"], job["info"] = i, total, info

        def should_stop():
            with self.lock:
                return self.paused or job["annuler"]

        try:
            stats = translate_file(job["path"], dst, translator,
                                   cible_code=job["cible_code"],
                                   translate_images=job["images"],
                                   ocr_lang=ocr_lang, ocr_error=ocr_error,
                                  on_progress=progress,
                                  should_stop=should_stop)
        except OllamaError as e:
            # Ollama indisponible : inutile de faire échouer toute la file,
            # on met en pause ; « Reprendre » relance là où on en était.
            with self.lock:
                self.paused = True
                job["etat"] = "en pause"
                job["info"] = f"{e} — file mise en pause, clique sur Reprendre"
            return
        except Exception as e:
            with self.lock:
                job["etat"], job["info"] = "erreur", f"{type(e).__name__} : {e}"
            return

        with self.lock:
            if job["annuler"]:
                job["etat"], job["info"] = "annulé", ""
            elif stats["interrompu"]:
                # reprendra au début du fichier ; les pages déjà
                # traduites sortent instantanément du cache
                job["etat"], job["info"] = "en pause", "reprise via le cache"
            else:
                job["etat"], job["fin"], job["sortie"] = "terminé", time.time(), dst
                info = f"{stats['blocs']} blocs traduits"
                if stats.get("images"):
                    info += f" · {stats['images']} zone(s) de texte dans les images"
                if stats["pages_ocr"]:
                    info += f" · {stats['pages_ocr']} page(s) passée(s) en OCR"
                abimees = stats.get("pages_endommagees") or []
                if abimees:
                    liste = ", ".join(map(str, abimees[:10])) + ("…" if len(abimees) > 10 else "")
                    info += (f" · PDF endommagé : {len(abimees)} page(s) "
                             f"relue(s) par OCR (p. {liste})")
                if stats["pages_ignorees"]:
                    info += (f" · {len(stats['pages_ignorees'])} page(s) non "
                             f"traduite(s) : {stats['raison_ocr']}")
                job["info"] = info
