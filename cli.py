"""Test en ligne de commande du moteur PDF (étape 1).

    python cli.py livre.pdf
    python cli.py livre.pdf --source English --cible French --modele translategemma:12b
    python cli.py livre.pdf --test     (sans Ollama : vérifie seulement la mise en page)
"""

import argparse
import os
import sys

from cache import TranslationCache
from ocr_engine import OcrUnavailable, configure_tesseract, tesseract_code
from engines import translate_file
from translator import DEFAULT_MODEL, OllamaError, Translator


class FakeTranslator:
    """Remplace Ollama pour tester la mise en page : renvoie le texte
    rallongé de 20 % (le français est plus long que l'anglais)."""

    def translate(self, text):
        return "[FR] " + text + " " + text[: len(text) // 5]

    def translate_tagged(self, text):
        return "[FR] " + text           # garde les balises <n>…</n>


def main():
    p = argparse.ArgumentParser(description="Traduction PDF (étape 1)")
    p.add_argument("pdf", help="fichier PDF, EPUB, DOCX, TXT, MD ou image")
    p.add_argument("--source", default="auto",
                   help="langue du livre (ex. German) ou auto (défaut)")
    p.add_argument("--cible", default="French")
    p.add_argument("--modele", default=DEFAULT_MODEL)
    p.add_argument("--sortie", help="fichier de sortie")
    p.add_argument("--images", action="store_true",
                   help="traduire aussi le texte dans les images des livres")
    p.add_argument("--test", action="store_true",
                   help="sans Ollama, pour vérifier la mise en page")
    a = p.parse_args()

    if not os.path.exists(a.pdf):
        sys.exit(f"Fichier introuvable : {a.pdf}")

    base, _ = os.path.splitext(a.pdf)
    dst = a.sortie or f"{base}_{a.cible}{os.path.splitext(a.pdf)[1]}"

    if a.source == "auto":
        from lang_detect import detect_file
        from ocr_engine import configure_tesseract as _cfg
        from scanner import FORMATS
        try:
            installed = _cfg()
        except OcrUnavailable:
            installed = None
        fmt = FORMATS.get(os.path.splitext(a.pdf)[1].lower())
        a.source = detect_file(a.pdf, fmt, installed)
        if not a.source:
            sys.exit("Langue non détectée : indique-la avec --source (ex. German).")
        print(f"Langue détectée : {a.source}")

    cache = None
    if a.test:
        translator = FakeTranslator()
    else:
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "cache_traductions.sqlite")
        cache = TranslationCache(cache_path)
        translator = Translator(a.source, a.cible, a.modele, cache=cache)

    def progress(i, total, info):
        print(f"[{i}/{total}] {info}", flush=True)

    try:
        ocr_lang, ocr_error = tesseract_code(a.source, configure_tesseract()), None
    except OcrUnavailable as e:
        ocr_lang, ocr_error = None, str(e)
        print(f"OCR désactivé : {e}")

    print(f"{a.pdf}  →  {dst}")
    try:
        stats = translate_file(a.pdf, dst, translator, ocr_lang=ocr_lang,
                               ocr_error=ocr_error, translate_images=a.images,
                               cible_code=None, on_progress=progress)
    except OllamaError as e:
        sys.exit(f"\nERREUR : {e}\nRelance la même commande : "
                 f"les pages déjà traduites viendront du cache.")
    finally:
        if cache:
            cache.close()

    print(f"\nTerminé : {stats['blocs']} blocs traduits ({stats['pages']} pages ou parties).")
    if stats.get("images"):
        print(f"Zones de texte traduites dans les images : {stats['images']}")
    if stats["pages_ocr"]:
        print(f"Pages passées en OCR : {stats['pages_ocr']}")
    if stats["pages_ignorees"]:
        print(f"Pages non traduites ({stats['raison_ocr']}) : "
              f"{stats['pages_ignorees']}")
    print(f"Fichier : {dst}")


if __name__ == "__main__":
    main()
