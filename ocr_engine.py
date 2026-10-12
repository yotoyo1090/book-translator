"""Moteur OCR (étape 3) : pages scannées et pages au texte illisible.

Principe : on lit la page avec Tesseract, qui donne chaque paragraphe
avec sa position. Pour chaque paragraphe :
  - page scannée   : on le recouvre d'un rectangle de la couleur du
                     papier, puis on écrit la traduction par-dessus ;
  - texte illisible : on efface le texte d'origine (comme le moteur
                     texte), puis on écrit la traduction.
Les illustrations de la page ne sont jamais recouvertes : on ne touche
qu'aux zones où Tesseract a trouvé du texte, et le texte traduit ne
s'étend que dans de l'espace vide.
"""

import html
import os
import shutil
import statistics

import numpy as np
import pymupdf
import pytesseract
from PIL import Image, ImageStat

DPI = 300
MIN_CONFIDENCE = 45          # paragraphes en dessous : bruit (dessins…)

# Nom de langue (celui du prompt) → code Tesseract
TESSERACT_LANG = {
    "English": "eng", "French": "fra", "Korean": "kor", "Spanish": "spa",
    "German": "deu", "Italian": "ita", "Portuguese": "por", "Dutch": "nld",
    "Russian": "rus", "Japanese": "jpn", "Chinese": "chi_sim",
    "Arabic": "ara",
}


class OcrUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------
# Tesseract
# ------------------------------------------------------------

def configure_tesseract():
    """Trouve tesseract.exe (Windows) ou tesseract dans le PATH."""
    for path in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break
    else:
        if not shutil.which("tesseract"):
            raise OcrUnavailable(
                "Tesseract OCR n'est pas installé "
                "(https://github.com/UB-Mannheim/tesseract/wiki)")
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception as e:
        raise OcrUnavailable(f"Tesseract ne démarre pas : {e}")


def tesseract_code(lang_name, available):
    code = TESSERACT_LANG.get(lang_name)
    if code is None:
        raise OcrUnavailable(f"Pas de modèle OCR connu pour « {lang_name} »")
    if code not in available:
        raise OcrUnavailable(
            f"La langue OCR « {code} » n'est pas installée dans Tesseract "
            f"(installées : {', '.join(sorted(available))})")
    return code


# ------------------------------------------------------------
# Lecture de la page
# ------------------------------------------------------------

def render(page, dpi=DPI):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                          alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _join_lines(lines):
    text = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        elif text:
            text += " " + line
        else:
            text = line
    return text


def _font_size(g, text):
    """Corps de la police (en pixels), mesuré sur des pages réelles :
    - plusieurs lignes : interligne / 1,2 ;
    - une seule ligne  : hauteur max des mots / 0,93, ou / 0,72 si le
      texte est en majuscules (pas de lettres montantes/descendantes)."""
    tops = [t for _, t in sorted(g["tops"].items())]
    gaps = [b - a for a, b in zip(tops, tops[1:]) if b > a]
    if gaps:
        return statistics.median(gaps) / 1.2
    # seulement les mots faits de lettres (pas « [vi] », « 12. »…)
    words = [(w, h) for w, h in g["words"] if w.isalpha()] or g["words"]
    caps = [h for w, h in words if w.isupper()]
    if len(caps) >= len(words) * 0.8:
        return statistics.median(caps) / 0.72
    return max(h for _, h in words) / 0.93


def ocr_paragraphs(image, lang, dpi=DPI):
    """Paragraphes trouvés par Tesseract, en coordonnées PDF (points)."""
    data = pytesseract.image_to_data(image, lang=lang, config="--psm 3",
                                     output_type=pytesseract.Output.DICT)
    k = 72 / dpi
    groups = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i])
        g = groups.setdefault(key, {"lines": {}, "boxes": [], "conf": [],
                                    "heights": [], "tops": {}, "words": []})
        g["lines"].setdefault(data["line_num"][i], []).append(word)
        x, y, w, h = (data[c][i] for c in ("left", "top", "width", "height"))
        g["boxes"].append((x, y, x + w, y + h))
        g["conf"].append(float(data["conf"][i]))
        g["heights"].append(h)
        g["words"].append((word, h))
        line = data["line_num"][i]
        g["tops"][line] = min(g["tops"].get(line, y), y)

    paragraphs = []
    for g in groups.values():
        text = _join_lines(" ".join(ws) for _, ws in sorted(g["lines"].items()))
        conf = statistics.mean(g["conf"])
        if conf < MIN_CONFIDENCE:
            continue
        x0 = min(b[0] for b in g["boxes"]); y0 = min(b[1] for b in g["boxes"])
        x1 = max(b[2] for b in g["boxes"]); y1 = max(b[3] for b in g["boxes"])
        paragraphs.append({
            "rect": pymupdf.Rect(x0 * k, y0 * k, x1 * k, y1 * k),
            "px": (x0, y0, x1, y1),
            "text": text,
            "lines": len(g["lines"]),
            "size": round(_font_size(g, text) * k, 1),
            "conf": conf,
        })
    paragraphs.sort(key=lambda p: (p["rect"].y0, p["rect"].x0))
    return paragraphs


# ------------------------------------------------------------
# Couleurs et espace libre (sur l'image rendue)
# ------------------------------------------------------------

def _border_pixels(image, box, pad=4):
    x0, y0, x1, y1 = box
    W, H = image.size
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W, x1 + pad), min(H, y1 + pad)
    strips = [(x0, y0, x1, min(y0 + pad, y1)), (x0, max(y1 - pad, y0), x1, y1),
              (x0, y0, min(x0 + pad, x1), y1), (max(x1 - pad, x0), y0, x1, y1)]
    pixels = []
    for s in strips:
        if s[2] > s[0] and s[3] > s[1]:
            pixels.extend(image.crop(s).getdata())
    return pixels


def paper_color(image, box):
    """Couleur du papier autour du paragraphe (médiane du contour)."""
    pixels = _border_pixels(image, box)
    if not pixels:
        return (1, 1, 1)
    return tuple(statistics.median(p[c] for p in pixels) / 255 for c in range(3))


def ink_color(image, box, paper):
    """Couleur de l'encre du paragraphe : médiane des pixels nettement
    différents du papier (garde les titres bleus, rouges…)."""
    px = np.asarray(image.crop(box), dtype=np.int16).reshape(-1, 3)
    ref = np.array([round(c * 255) for c in paper], dtype=np.int16)
    ink = px[np.abs(px - ref).max(axis=1) > 60]
    if len(ink) < 20:
        return "#1a1a1a"
    r, g, b = (int(v) for v in np.median(ink, axis=0))
    return f"#{r:02x}{g:02x}{b:02x}"


def _is_blank(image, box, paper, max_ink=0.02):
    """Vrai si la zone est du papier vide : moins de 2 % de pixels
    nettement différents de la couleur du papier (tolère le grain du
    scan et le bord anti-aliasé des lettres voisines)."""
    region = image.crop(box)
    if region.width < 1 or region.height < 1:
        return False
    px = np.asarray(region, dtype=np.int16)
    ref = np.array([round(c * 255) for c in paper], dtype=np.int16)
    ink = np.abs(px - ref).max(axis=2) > 60
    return ink.mean() <= max_ink


def grow_into_blank(image, para, others, page_rect, dpi=DPI, step_pt=4):
    """Agrandit le cadre vers le bas tant que la zone est vide (papier
    uni) et ne touche aucun autre paragraphe — jamais sur une image."""
    k = dpi / 72
    r = pymupdf.Rect(para["rect"])
    paper = paper_color(image, para["px"])

    def free(area):
        if any(area.intersects(o) for o in others):
            return False
        box = tuple(int(v) for v in (area.x0 * k, area.y0 * k,
                                     area.x1 * k, area.y1 * k))
        return _is_blank(image, box, paper)

    # Une seule ligne (titre…) : s'élargir sur les côtés dans le vide,
    # des deux côtés si elle est centrée, sinon vers la droite
    if para["lines"] == 1:
        centered = _alignment(r, page_rect, 1) == "center"
        while r.x1 + step_pt < page_rect.x1 - 18:
            right = pymupdf.Rect(r.x1, r.y0, r.x1 + step_pt, r.y1)
            left = pymupdf.Rect(r.x0 - step_pt, r.y0, r.x0, r.y1)
            if not free(right) or (centered and (r.x0 - step_pt < page_rect.x0 + 18
                                                 or not free(left))):
                break
            r.x1 += step_pt
            if centered:
                r.x0 -= step_pt

    # Une ligne doit avoir la hauteur de son interligne (la boîte OCR
    # ne couvre que les lettres) : on complète vers le bas si c'est vide
    need = para["lines"] * para["size"] * 1.2 - r.height
    if need > 0 and free(pymupdf.Rect(r.x0, r.y1, r.x1, r.y1 + need)):
        r.y1 += need

    limit = page_rect.y1 - 18
    while r.y1 + step_pt < limit:
        if not free(pymupdf.Rect(r.x0, r.y1, r.x1, r.y1 + step_pt)):
            break
        r.y1 += step_pt
    return r


# ------------------------------------------------------------
# Traduction d'une page
# ------------------------------------------------------------

def _needs_translation(text):
    return sum(c.isalpha() for c in text) >= 2


def _alignment(rect, page_rect, lines):
    center_page = (page_rect.x0 + page_rect.x1) / 2
    if (rect.width < page_rect.width * 0.7
            and abs((rect.x0 + rect.x1) / 2 - center_page) < page_rect.width * 0.03):
        return "center"
    return "justify" if lines >= 3 else "left"


def translate_ocr_page(page, translator, ocr_lang, mode):
    """mode = "scan" (image) ou "illisible" (texte à l'encodage cassé).
    Retourne le nombre de paragraphes traduits."""
    image = render(page)
    paragraphs = [p for p in ocr_paragraphs(image, ocr_lang)
                  if _needs_translation(p["text"])]
    if not paragraphs:
        return 0

    todo = [(p, translator.translate(p["text"])) for p in paragraphs]
    rects = [p["rect"] for p in paragraphs]

    targets = []
    for p, _ in todo:
        others = [r for r in rects if r is not p["rect"]]
        targets.append(grow_into_blank(image, p, others, page.rect))

    if page.get_text().strip():
        # retirer le texte d'origine sous chaque paragraphe : encodage
        # cassé (illisible) ou couche OCR invisible d'un scan
        for p, _ in todo:
            page.add_redact_annot(p["rect"], fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)

    for (p, translated), target in zip(todo, targets):
        bg = paper_color(image, p["px"])
        if mode == "scan":
            cover = pymupdf.Rect(target.x0 - 1.5, target.y0 - 1.5,
                                 target.x1 + 1.5, target.y1 + 1.5)
            page.draw_rect(cover, color=None, fill=bg, overlay=True)
        css = (
            "* {"
            " font-family: serif;"
            f" font-size: {max(p['size'], 6)}px;"
            f" color: {ink_color(image, p['px'], bg)};"
            f" text-align: {_alignment(p['rect'], page.rect, p['lines'])};"
            " line-height: 1.15; margin: 0; padding: 0; }"
        )
        page.insert_htmlbox(target, f"<p>{html.escape(translated)}</p>",
                            css=css, scale_low=0)
    return len(todo)
