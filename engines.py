"""Choisit le bon moteur selon le format du fichier."""

import os

import office_engines as office
from convert import LEGACY, translate_legacy
from doc_engines import translate_docx, translate_epub, translate_text_file
from image_text import IMAGE_EXT, translate_image_file
from pdf_engine import translate_pdf


def translate_file(src, dst, translator, cible_code=None, ocr_lang=None,
                   ocr_error=None, translate_images=False,
                   on_progress=None, should_stop=None):
    """translate_images : traduire aussi le texte DANS les images des
    livres (les fichiers image du dossier, eux, sont toujours traduits)."""
    ext = os.path.splitext(src)[1].lower()
    common = {"on_progress": on_progress, "should_stop": should_stop}
    if ext == ".pdf":
        return translate_pdf(src, dst, translator, ocr_lang=ocr_lang,
                             ocr_error=ocr_error,
                             translate_images=translate_images,
                             target_code=cible_code, **common)
    if ext == ".epub":
        return translate_epub(src, dst, translator, lang_code=cible_code,
                              translate_images=translate_images,
                              ocr_lang=ocr_lang, **common)
    if ext == ".docx":
        return translate_docx(src, dst, translator,
                              translate_images=translate_images,
                              ocr_lang=ocr_lang, target_code=cible_code,
                              **common)
    if ext in (".txt", ".md"):
        return translate_text_file(src, dst, translator,
                                   markdown=ext == ".md", **common)
    zipped = {"translate_images": translate_images, "ocr_lang": ocr_lang,
              "target_code": cible_code, **common}
    if ext == ".xlsx":
        return office.translate_xlsx(src, dst, translator, **zipped)
    if ext == ".pptx":
        return office.translate_pptx(src, dst, translator, **zipped)
    if ext == ".hwpx":
        return office.translate_hwpx(src, dst, translator, **zipped)
    if ext in (".odt", ".ods", ".odp"):
        return office.translate_odf(src, dst, translator, **zipped)
    if ext in (".html", ".htm", ".xhtml"):
        return office.translate_html(src, dst, translator, lang_code=cible_code,
                                     **common)
    if ext == ".rtf":
        return office.translate_rtf(src, dst, translator, **common)
    if ext in (".srt", ".vtt"):
        return office.translate_subtitles(src, dst, translator, **common)
    if ext in LEGACY:
        # HWP, DOC, XLS, PPT : converti, traduit, puis reconverti
        return translate_legacy(
            src, dst,
            lambda a, b: translate_file(a, b, translator, cible_code, ocr_lang,
                                        ocr_error, translate_images,
                                        on_progress, should_stop),
            on_progress)
    if ext in IMAGE_EXT:
        if not ocr_lang:
            raise RuntimeError(f"Image non traduite : {ocr_error}")
        return translate_image_file(src, dst, translator, ocr_lang,
                                    target_code=cible_code, **common)
    raise ValueError(f"Format non pris en charge : {ext}")
