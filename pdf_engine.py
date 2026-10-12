"""Moteur PDF (PDF avec couche texte).

Principe : on garde le PDF d'origine, on efface seulement le texte
bloc par bloc et on réécrit la traduction dans le même cadre.
Les images, les dessins et la mise en page ne sont pas touchés.
Les pages scannées (sans texte) sont laissées telles quelles :
elles seront gérées par le moteur OCR (étape 3).
"""

import html
import re
from collections import Counter

import pymupdf

LETTERS = re.compile(r"[^\W\d_]{2,}")

# Les messages de MuPDF sur les PDF abîmés ne s'affichent plus dans la
# console : on les capte page par page (voir damaged_page).
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

# messages qui veulent dire « le texte de la page est mal lu »
DAMAGE_MARKERS = ("syntax error", "unknown keyword", "content stream",
                  "invalid character", "unterminated", "cannot parse")


def damaged_page(page):
    """Vrai si MuPDF a signalé, en lisant cette page, que ses instructions
    de texte sont corrompues (une partie du texte peut alors manquer).
    Les messages sans effet sur le texte (profil de couleur, calques,
    dégradés) sont ignorés."""
    pymupdf.TOOLS.reset_mupdf_warnings()
    try:
        page.get_texttrace()
    except Exception:                  # page illisible même pour MuPDF
        pymupdf.TOOLS.reset_mupdf_warnings()
        return True
    messages = pymupdf.TOOLS.mupdf_warnings().lower()
    pymupdf.TOOLS.reset_mupdf_warnings()
    return any(m in messages for m in DAMAGE_MARKERS)


# ------------------------------------------------------------
# Extraction des blocs de texte
# ------------------------------------------------------------

def _color_hex(value):
    return f"#{value:06x}"


def _join_lines(lines):
    """Recolle les lignes d'un bloc ; enlève la césure « exem- ple »."""
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


def extract_blocks(page):
    """Retourne les blocs de texte horizontaux avec leur style dominant."""
    blocks = []
    data = page.get_text("dict")
    for b in data["blocks"]:
        if b.get("type") != 0:
            continue
        lines, sizes, fonts, colors = [], Counter(), Counter(), Counter()
        bold = italic = 0
        horizontal = True
        line_boxes = []
        for line in b["lines"]:
            if abs(line["dir"][0] - 1) > 0.01:      # texte tourné : ignoré
                horizontal = False
            line_text = ""
            for span in line["spans"]:
                t = span["text"]
                n = len(t.strip())
                line_text += t
                if n:
                    sizes[round(span["size"], 1)] += n
                    fonts[span["font"]] += n
                    colors[span["color"]] += n
                    if span["flags"] & 16:
                        bold += n
                    if span["flags"] & 2:
                        italic += n
            lines.append(line_text)
            line_boxes.append(pymupdf.Rect(line["bbox"]))
        text = _join_lines(lines)
        if not horizontal or not text:
            continue
        total = sum(sizes.values()) or 1
        font = fonts.most_common(1)[0][0] if fonts else ""
        blocks.append({
            "rect": pymupdf.Rect(b["bbox"]),
            "lines": line_boxes,
            "text": text,
            "size": sizes.most_common(1)[0][0] if sizes else 10,
            "font": font,
            "color": _color_hex(colors.most_common(1)[0][0]) if colors else "#000000",
            "bold": bold / total > 0.5,
            "italic": italic / total > 0.5,
        })
    return blocks


def needs_translation(text):
    """Faux pour les numéros de page, puces, chiffres seuls."""
    return bool(LETTERS.search(text))


# ------------------------------------------------------------
# Réécriture du texte traduit
# ------------------------------------------------------------

def _alignment(block, page_rect):
    rect = block["rect"]
    center_page = (page_rect.x0 + page_rect.x1) / 2
    center_block = (rect.x0 + rect.x1) / 2
    if (rect.width < page_rect.width * 0.7
            and abs(center_block - center_page) < page_rect.width * 0.03):
        return "center"
    if len(block["lines"]) >= 3:
        return "justify"
    return "left"


def _font_family(font_name):
    name = font_name.lower()
    if any(k in name for k in ("times", "serif", "roman", "garamond",
                               "georgia", "book", "minion", "palatino")):
        if "sans" not in name:
            return "serif"
    if any(k in name for k in ("courier", "mono", "consol")):
        return "monospace"
    return "sans-serif"


def free_rect(block, obstacles, content, centered):
    """Agrandit le cadre du bloc dans l'espace libre (vers le bas, et sur
    les côtés pour une ligne centrée) sans toucher aux autres éléments,
    pour éviter que la police rétrécisse quand la traduction est longue."""
    r = pymupdf.Rect(block["rect"])
    others = [o for o in obstacles if o != block["rect"]]

    # Une seule ligne : on peut s'étendre jusqu'aux marges du texte
    # (des deux côtés si centrée, sinon seulement vers la droite)
    if len(block["lines"]) == 1:
        x0 = content.x0 if centered else r.x0
        wide = pymupdf.Rect(x0, r.y0, max(content.x1, r.x1), r.y1)
        if not any(wide.intersects(o) for o in others):
            r.x0, r.x1 = wide.x0, wide.x1

    # Vers le bas : jusqu'à l'élément suivant qui chevauche en largeur
    below = content.y1
    for o in others:
        if o.y0 >= r.y1 - 1 and o.x0 < r.x1 and o.x1 > r.x0:
            below = min(below, o.y0)
    r.y1 = max(r.y1, below - 2)
    return r


def write_block(page, block, translated, rect=None):
    css = (
        "* {"
        f" font-family: {_font_family(block['font'])};"
        f" font-size: {block['size']}px;"
        f" color: {block['color']};"
        f" font-weight: {'bold' if block['bold'] else 'normal'};"
        f" font-style: {'italic' if block['italic'] else 'normal'};"
        f" text-align: {_alignment(block, page.rect)};"
        " line-height: 1.15; margin: 0; padding: 0; }"
    )
    content = f"<p>{html.escape(translated)}</p>"
    # scale_low=0 : la police peut rétrécir autant que nécessaire
    page.insert_htmlbox(rect or block["rect"], content, css=css, scale_low=0)


# ------------------------------------------------------------
# Traduction d'une page / d'un document
# ------------------------------------------------------------

def page_kind(page):
    """« texte », « scan » (image, ou image + couche OCR invisible),
    « illisible » (texte à l'encodage cassé) ou « vide »."""
    visible = []
    for span in page.get_texttrace():
        if span["type"] != 3:                       # 3 = texte invisible
            visible.extend(chr(c[0]) for c in span["chars"] if c[0] > 32)
    has_images = bool(page.get_images(full=False))
    if len(visible) < 30:
        return "scan" if has_images else "vide"
    letters = sum(c.isalpha() for c in visible)
    if letters / len(visible) < 0.5:
        return "illisible"
    return "texte"


def translate_page(page, translator):
    """Traduit en place une page qui a une vraie couche texte.
    Retourne le nombre de blocs traduits."""
    blocks = extract_blocks(page)

    todo = []
    for block in blocks:
        if needs_translation(block["text"]):
            todo.append((block, translator.translate(block["text"])))

    if not todo:
        return 0

    # Obstacles = tous les blocs de texte + toutes les images de la page
    obstacles = [b["rect"] for b in blocks]
    obstacles += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    content = pymupdf.Rect(
        min(b["rect"].x0 for b in blocks),
        page.rect.y0 + 20,
        max(b["rect"].x1 for b in blocks),
        page.rect.y1 - 20,
    )
    targets = [
        free_rect(b, obstacles, content, _alignment(b, page.rect) == "center")
        for b, _ in todo
    ]

    for block, _ in todo:
        page.add_redact_annot(block["rect"], fill=False)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )
    for (block, translated), rect in zip(todo, targets):
        write_block(page, block, translated, rect)
    return len(todo)


def translate_page_images(doc, page, translator, ocr_lang, target_code, done):
    """Traduit le texte DANS les images de la page (schémas, légendes…).
    `done` : images déjà traitées (une image réutilisée sur plusieurs
    pages n'est traduite qu'une fois). Retourne le nb de zones traduites."""
    import io
    from PIL import Image
    from image_text import translate_pil

    count = 0
    page_area = page.rect.width * page.rect.height
    for item in page.get_images(full=True):
        xref, smask = item[0], item[1]
        if xref in done:
            continue
        done.add(xref)
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        shown = rects[0]
        # image pleine page (scan, fond) ou minuscule : ignorée
        if shown.width * shown.height > 0.8 * page_area or min(shown.width, shown.height) < 40:
            continue
        try:
            pix = pymupdf.Pixmap(doc, xref)
            if smask:
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
            if pix.colorspace and pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            mode = {1: "L", 3: "RGB"}.get(pix.n - pix.alpha, "RGB")
            img = Image.frombytes(mode + ("A" if pix.alpha else ""),
                                  (pix.width, pix.height), pix.samples)
            new, n = translate_pil(img, translator, ocr_lang, target_code)
        except Exception:
            continue                      # image exotique : laissée intacte
        if new is None:
            continue
        buf = io.BytesIO()
        if new.mode == "RGBA":
            new.save(buf, "PNG")
        else:
            new.convert("RGB").save(buf, "JPEG", quality=92)
        page.replace_image(xref, stream=buf.getvalue())
        count += n
    return count


def translate_pdf(src, dst, translator, ocr_lang=None, ocr_error=None,
                  translate_images=False, target_code=None,
                  on_progress=None, should_stop=None):
    """Traduit tout le PDF `src` et l'enregistre dans `dst`.

    ocr_lang : code Tesseract de la langue du livre (ex. « eng ») pour
    les pages scannées / illisibles ; None = OCR indisponible (ocr_error
    explique pourquoi), ces pages sont alors laissées telles quelles.
    on_progress(page_num, total, info) est appelé après chaque page.
    should_stop() -> True interrompt proprement (reprise via le cache).
    """
    from ocr_engine import translate_ocr_page

    doc = pymupdf.open(src)
    stats = {"pages": len(doc), "blocs": 0, "pages_ocr": 0,
             "pages_ignorees": [], "raison_ocr": ocr_error,
             "interrompu": False, "images": 0, "pages_endommagees": []}
    images_done = set()
    try:
        for i, page in enumerate(doc, start=1):
            if should_stop and should_stop():
                stats["interrompu"] = True
                break
            kind = page_kind(page)
            # page au texte corrompu : on lit ce qui s'affiche (OCR) plutôt
            # que le texte extrait, qui peut avoir des trous
            if kind == "texte" and damaged_page(page):
                stats["pages_endommagees"].append(i)
                if ocr_lang:
                    kind = "illisible"
            n_img = 0
            if translate_images and ocr_lang and kind in ("texte", "illisible"):
                n_img = translate_page_images(doc, page, translator, ocr_lang,
                                              target_code, images_done)
                stats["images"] += n_img
            if kind == "texte":
                n = translate_page(page, translator)
                stats["blocs"] += n
                info = f"{n} bloc(s) traduit(s)"
            elif kind in ("scan", "illisible") and ocr_lang:
                n = translate_ocr_page(page, translator, ocr_lang, kind)
                stats["blocs"] += n
                stats["pages_ocr"] += 1
                info = f"OCR ({kind}) : {n} paragraphe(s) traduit(s)"
            elif kind in ("scan", "illisible"):
                stats["pages_ignorees"].append(i)
                info = f"page {kind} non traduite : {ocr_error}"
            else:
                info = "page vide"
            if stats["pages_endommagees"][-1:] == [i]:
                info += " · page endommagée" + ("" if ocr_lang else
                                                 " (OCR indisponible : texte peut-être incomplet)")
            if n_img:
                info += f" · {n_img} zone(s) d'image"
            if on_progress:
                on_progress(i, len(doc), info)
        if not stats["interrompu"]:
            doc.save(dst, garbage=3, deflate=True)
    finally:
        doc.close()
    return stats
