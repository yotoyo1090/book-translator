"""Scan d'un dossier : trouve les livres et les images, et analyse
chaque fichier (pages, texte / scanné / illisible…, langue).

Pour aller vite :
- mémoire des scans (cache_scan.sqlite) : un fichier inchangé (même
  taille, même date) n'est pas réanalysé ;
- analyse en parallèle (plusieurs processus) quand il y a beaucoup de
  fichiers nouveaux ou modifiés ;
- progression transmise au fur et à mesure (on_progress).
"""

import hashlib
import json
import os
import sqlite3
import threading
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed

import pymupdf

import office_engines as office
from convert import converter_for
from doc_engines import docx_paragraph_count, epub_documents
from lang_detect import detect_file
from pdf_engine import damaged_page, page_kind

FORMATS = {
    ".pdf": "PDF",
    ".epub": "EPUB",
    ".docx": "DOCX",
    ".txt": "TXT",
    ".md": "TXT",
    ".png": "IMAGE", ".jpg": "IMAGE", ".jpeg": "IMAGE", ".webp": "IMAGE",
    ".bmp": "IMAGE", ".tif": "IMAGE", ".tiff": "IMAGE", ".gif": "IMAGE",
    ".xlsx": "XLSX", ".pptx": "PPTX", ".hwpx": "HWPX",
    ".odt": "ODF", ".ods": "ODF", ".odp": "ODF",
    ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML",
    ".rtf": "RTF", ".srt": "SRT", ".vtt": "SRT",
    # anciens formats binaires : convertis par le programme installé
    ".hwp": "HWP", ".doc": "DOC", ".xls": "XLS", ".ppt": "PPT",
}
SUPPORTED_NOW = {"PDF", "EPUB", "DOCX", "TXT", "IMAGE", "XLSX", "PPTX", "HWPX",
                 "ODF", "HTML", "RTF", "SRT", "HWP", "DOC", "XLS", "PPT"}
LEGACY_FORMATS = {"HWP", "DOC", "XLS", "PPT"}

# à changer si la façon d'analyser change : l'ancienne mémoire est ignorée
ANALYSE_VERSION = "3"
WORKERS = 4                # processus d'analyse en parallèle
PARALLEL_FROM = 4          # en dessous : un seul processus (démarrage coûteux)

HERE = os.path.dirname(os.path.abspath(__file__))


def analyse_pdf(path, samples=8):
    doc = pymupdf.open(path)
    try:
        n = len(doc)
        if n == 0:
            return {"pages": 0, "type": "vide"}
        step = max(1, n // samples)
        kinds, damaged = set(), False
        for i in range(0, n, step):
            kinds.add(page_kind(doc[i]))
            damaged = damaged or damaged_page(doc[i])
        kinds.discard("vide")
        if damaged:
            return {"pages": n, "type": "endommagé"}
        if kinds == {"texte"} or not kinds:
            kind = "texte"
        elif kinds == {"scan"}:
            kind = "scanné"
        elif kinds == {"illisible"}:
            kind = "texte illisible"
        else:
            kind = "mixte"
        return {"pages": n, "type": kind}
    finally:
        doc.close()


# codes des langues proposées (mêmes que LANGUES dans server.py)
KNOWN_CODES = {"en", "fr", "ko", "es", "de", "it", "pt", "nl", "ru", "ja",
               "zh", "ar"}


def translated_path(folder, relpath, code):
    """Fichier traduit à côté de l'original : livre.pdf → livre_fr.pdf"""
    base, ext = os.path.splitext(relpath)
    return os.path.join(folder, f"{base}_{code}{ext}")


def is_translation(path):
    """Vrai pour « livre_fr.pdf » si « livre.pdf » existe dans le même
    dossier : c'est une traduction, pas un nouveau livre à traduire."""
    base, ext = os.path.splitext(path)
    head, sep, code = base.rpartition("_")
    return bool(sep) and code in KNOWN_CODES and os.path.exists(head + ext)


# ------------------------------------------------------------
# Analyse d'un fichier (fonction de module : utilisable par un processus)
# ------------------------------------------------------------

def analyse_file(path, fmt, installed_ocr):
    """Ce qui ne dépend que du contenu du fichier : pages, type, langue."""
    info = {"supporte": fmt in SUPPORTED_NOW, "pages": None, "type": None}
    try:
        if fmt == "PDF":
            info.update(analyse_pdf(path))
        elif fmt == "EPUB":
            with zipfile.ZipFile(path) as z:
                info.update(pages=len(epub_documents(z)[0]), type="chapitres")
        elif fmt == "IMAGE":
            from PIL import Image
            with Image.open(path) as im:
                frames = getattr(im, "n_frames", 1)
                animated = im.format == "GIF" and frames > 1
            info.update(pages=frames, type="GIF animé" if animated else "image")
            if animated:
                info["supporte"] = False
        elif fmt == "DOCX":
            info.update(pages=docx_paragraph_count(path), type="paragraphes")
        elif fmt == "XLSX":
            info.update(pages=office.xlsx_sheet_count(path), type="feuilles")
        elif fmt == "PPTX":
            info.update(pages=office.pptx_slide_count(path), type="diapositives")
        elif fmt == "HWPX":
            info.update(pages=office.hwpx_paragraph_count(path), type="paragraphes")
        elif fmt == "ODF":
            info.update(pages=office.odf_paragraph_count(path), type="paragraphes")
        elif fmt == "HTML":
            info.update(pages=office.html_block_count(path), type="page web")
        elif fmt == "RTF":
            info.update(type="texte enrichi")
        elif fmt == "SRT":
            info.update(pages=office.subtitle_count(path), type="sous-titres")
        elif fmt in LEGACY_FORMATS:
            info.update(type="ancien format")
        else:
            info.update(type="texte brut")
    except Exception as e:                      # fichier corrompu / protégé
        info.update(supporte=False, type=f"erreur : {e}")
    # langue du livre (nom anglais, ex. « German »), None si inconnue
    info["langue"] = (detect_file(path, fmt, installed_ocr)
                      if info["supporte"] else None)
    return info


# ------------------------------------------------------------
# Mémoire des scans
# ------------------------------------------------------------

class ScanCache:

    def __init__(self, path=os.path.join(HERE, "cache_scan.sqlite")):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS analyses ("
                         " cle TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self._db.commit()

    @staticmethod
    def key(path, installed_ocr):
        """Change si le fichier change (taille, date), si la façon
        d'analyser change, ou si d'autres langues OCR sont installées."""
        st = os.stat(path)
        raw = "\x1f".join([ANALYSE_VERSION, os.path.abspath(path),
                           str(st.st_size), str(int(st.st_mtime)),
                           ",".join(sorted(installed_ocr or []))])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key):
        with self._lock:
            row = self._db.execute("SELECT data FROM analyses WHERE cle = ?",
                                   (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, data):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO analyses VALUES (?, ?)",
                             (key, json.dumps(data)))
            self._db.commit()


_cache = None


def _scan_cache():
    global _cache
    if _cache is None:
        _cache = ScanCache()
    return _cache


# ------------------------------------------------------------
# Scan du dossier
# ------------------------------------------------------------

def list_files(folder, recursive=True):
    """Fichiers à analyser (rapide : aucun fichier n'est ouvert)."""
    found = []
    for root, dirs, files in os.walk(folder):
        # anciens dossiers de sortie (versions précédentes) : ignorés
        dirs[:] = [d for d in dirs if not d.startswith("traduit_")]
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            path = os.path.join(root, name)
            if ext in FORMATS and not is_translation(path):
                found.append((path, FORMATS[ext]))
        if not recursive:
            break
    return found


def _book(folder, path, fmt, analysis):
    relpath = os.path.relpath(path, folder)
    if fmt in LEGACY_FORMATS and analysis.get("supporte"):
        # recalculé à chaque scan : 한글 / Office a pu être installé depuis
        tool = converter_for(os.path.splitext(path)[1])
        analysis = {**analysis, "outil": tool, "supporte": tool is not None,
                    "type": "conversion" if tool else "programme requis"}
    return {
        "path": path,
        "relpath": relpath,
        "format": fmt,
        "taille": os.path.getsize(path),
        **analysis,
        # recalculé à chaque scan : une traduction a pu apparaître
        "traductions": [c for c in sorted(KNOWN_CODES)
                        if os.path.exists(translated_path(folder, relpath, c))],
    }


def scan_folder(folder, recursive=True, on_progress=None, should_stop=None):
    """Liste et analyse les fichiers du dossier.
    on_progress(fait, total, livre) est appelé pour chaque fichier fini."""
    from ocr_engine import OcrUnavailable, configure_tesseract
    try:
        installed_ocr = sorted(configure_tesseract())
    except OcrUnavailable:
        installed_ocr = None           # pas d'OCR : scans/images non détectés

    files = list_files(folder, recursive)
    total, done, books = len(files), 0, []
    cache = _scan_cache()

    def finished(path, fmt, analysis):
        nonlocal done
        done += 1
        book = _book(folder, path, fmt, analysis)
        books.append(book)
        if on_progress:
            on_progress(done, total, book)

    # 1. fichiers déjà analysés et inchangés : instantané
    todo = []
    for path, fmt in files:
        key = cache.key(path, installed_ocr)
        hit = cache.get(key)
        if hit is not None:
            finished(path, fmt, hit)
        else:
            todo.append((path, fmt, key))

    # 2. nouveaux fichiers : en parallèle s'il y en a assez
    if len(todo) >= PARALLEL_FROM:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(analyse_file, p, f, installed_ocr): (p, f, k)
                       for p, f, k in todo}
            for fut in as_completed(futures):
                p, f, k = futures[fut]
                if should_stop and should_stop():
                    pool.shutdown(cancel_futures=True)
                    break
                try:
                    analysis = fut.result()
                    cache.put(k, analysis)
                except Exception as e:     # processus tombé sur ce fichier
                    analysis = {"supporte": False, "pages": None,
                                "type": f"erreur : {e}", "langue": None}
                finished(p, f, analysis)
    else:
        for p, f, k in todo:
            if should_stop and should_stop():
                break
            analysis = analyse_file(p, f, installed_ocr)
            cache.put(k, analysis)
            finished(p, f, analysis)

    books.sort(key=lambda b: b["relpath"].lower())
    return books
