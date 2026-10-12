"""Traduction du texte qui se trouve DANS les images (schémas, légendes,
couvertures, captures, fichiers image du dossier).

Étapes : OCR des mots avec leur position → traduction → effacement du
texte d'origine (inpainting OpenCV, qui reconstruit le fond) → écriture
de la traduction dans la couleur d'origine. Si rien de lisible n'est
trouvé, l'image n'est pas modifiée.
"""

import io
import os
import statistics
import textwrap

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

MIN_SIDE = 60             # images plus petites (icônes, puces) : ignorées
MIN_PARA_CONF = 70        # confiance moyenne minimale d'un paragraphe
MIN_WORD_CONF = 50        # mots en dessous : ignorés (bruit sur les photos)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
             ".gif"}

# Polices : Windows d'abord, puis Linux. Langues à idéogrammes à part.
FONTS = {
    "default": [r"C:\Windows\Fonts\arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    "ko": [r"C:\Windows\Fonts\malgun.ttf",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
    "ja": [r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\msgothic.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
    "zh": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
    "ar": [r"C:\Windows\Fonts\arial.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


FONTS_BOLD = {
    "default": [r"C:\Windows\Fonts\arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "ko": [r"C:\Windows\Fonts\malgunbd.ttf"],
}


def _font_path(lang_code, bold=False):
    paths = FONTS.get(lang_code, []) + FONTS["default"]
    if bold and lang_code not in ("ja", "zh"):
        paths = FONTS_BOLD.get(lang_code, []) + FONTS_BOLD["default"] + paths
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def _is_bold(arr, boxes):
    """Gras si l'encre couvre plus de 32,5 % des boîtes des mots
    (mesuré sur boîtes Tesseract : 24–31 % en normal, 33–48 % en gras)."""
    ink = total = 0
    for x0, y0, x1, y1 in boxes:
        region = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
        if len(region) == 0:
            continue
        dist = np.abs(region - np.median(region, axis=0)).sum(axis=1)
        if dist.max() < 60:
            continue
        ink += int((dist >= max(60, 0.5 * dist.max())).sum())
        total += len(region)
    return total > 0 and ink / total > 0.325


def _font(path, size):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


# ------------------------------------------------------------
# OCR
# ------------------------------------------------------------

def _overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _paragraphs(rgb, ocr_lang):
    """Deux lectures fusionnées : en couleur (texte clair sur photo) et en
    niveaux de gris contrastés (texte coloré sur fond coloré)."""
    gray = ImageOps.autocontrast(ImageOps.grayscale(rgb))
    result = _paragraphs_of(rgb, ocr_lang)
    for p in _paragraphs_of(gray, ocr_lang):
        if not any(_overlap(p["bbox"], q["bbox"]) for q in result):
            result.append(p)
    return result


def _size_from_word(word, h):
    """Corps de police estimé à partir de la hauteur d'un mot :
    avec lettres descendantes (g, p…) la boîte ≈ 0,93 em, avec
    majuscules ou montantes ≈ 0,75 em, sinon (x, e, a…) ≈ 0,53 em."""
    if any(c in "gjpqyQ" for c in word):
        return h / 0.93
    if any(c.isupper() or c in "bdfhklt" for c in word):
        return h / 0.75
    return h / 0.53


def _paragraphs_of(img, ocr_lang):
    """Paragraphes fiables d'une image : texte, boîtes des mots, taille."""
    # petites images : agrandies pour que Tesseract lise mieux
    scale = 2 if max(img.size) < 1200 else 1
    big = img.resize((img.width * scale, img.height * scale),
                     Image.LANCZOS) if scale > 1 else img
    data = pytesseract.image_to_data(big, lang=ocr_lang, config="--psm 3",
                                     output_type=pytesseract.Output.DICT)
    groups = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = float(data["conf"][i])
        if not word or conf < MIN_WORD_CONF:
            continue
        letters = sum(c.isalpha() for c in word)
        if letters == 0 or letters / len(word) < 0.6:
            continue
        key = (data["block_num"][i], data["par_num"][i])
        g = groups.setdefault(key, {"lines": {}, "boxes": [], "conf": []})
        x, y, w, h = (data[c][i] // scale for c in ("left", "top", "width", "height"))
        g["lines"].setdefault(data["line_num"][i], []).append((word, x, y, w, h, conf))
        g["boxes"].append((x, y, x + w, y + h))
        g["conf"].append(conf)

    # Des mots très espacés sur une même ligne (étiquettes d'un schéma)
    # sont des textes différents : on les sépare.
    split = []
    for g in groups.values():
        clusters = {ln: _clusters(ws) for ln, ws in g["lines"].items()}
        if all(len(c) == 1 for c in clusters.values()):
            split.append(g)
            continue
        for ln, cs in clusters.items():
            for ws in cs:
                split.append({"lines": {ln: ws},
                              "boxes": [(x, y, x + w, y + h) for _, x, y, w, h, _ in ws],
                              "conf": [w[5] for w in ws]})

    paras = []
    for g in split:
        words = [w for line in g["lines"].values() for w in line]
        if statistics.mean(g["conf"]) < MIN_PARA_CONF:
            continue
        if not any(sum(c.isalpha() for c in w[0]) >= 3 for w in words):
            continue
        lines = [" ".join(w[0] for w in line)
                 for _, line in sorted(g["lines"].items())]
        tops = [min(w[2] for w in line) for _, line in sorted(g["lines"].items())]
        gaps = [b - a for a, b in zip(tops, tops[1:]) if b > a]
        size = (statistics.median(gaps) / 1.2 if gaps
                else statistics.median(_size_from_word(w[0], w[4]) for w in words))
        paras.append({
            "text": _join(lines),
            "boxes": g["boxes"],
            "bbox": (min(b[0] for b in g["boxes"]), min(b[1] for b in g["boxes"]),
                     max(b[2] for b in g["boxes"]), max(b[3] for b in g["boxes"])),
            "size": max(int(size), 8),
            "lines": len(lines),
        })
    return paras


def _clusters(words):
    """Coupe une ligne là où l'écart entre deux mots dépasse 1,5 × leur hauteur."""
    words = sorted(words, key=lambda w: w[1])
    h = statistics.median(w[4] for w in words)
    out = [[words[0]]]
    for prev, w in zip(words, words[1:]):
        if w[1] - (prev[1] + prev[3]) > 1.5 * h:
            out.append([w])
        else:
            out[-1].append(w)
    return out


def _join(lines):
    text = ""
    for line in lines:
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text = f"{text} {line}".strip()
    return text


def _ink_color(arr, bbox):
    """Couleur de l'encre = pixels les plus éloignés de la couleur du fond."""
    x0, y0, x1, y1 = bbox
    region = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    if len(region) == 0:
        return (0, 0, 0)
    bg = np.median(region, axis=0)
    dist = np.abs(region - bg).sum(axis=1)
    ink = region[dist >= max(60, 0.6 * dist.max())]
    if len(ink) < 5:
        return tuple(int(v) for v in (255 - bg))
    return tuple(int(v) for v in np.median(ink, axis=0))


# ------------------------------------------------------------
# Réécriture
# ------------------------------------------------------------

def _fit_text(draw, text, font_path, box_w, box_h, start_size, lines_hint):
    """Plus grande taille ≤ start_size pour que le texte tienne dans la zone."""
    size = start_size
    while size >= 7:
        font = _font(font_path, size)
        avg = max(draw.textlength("abcdefghijklmnopqrstuvwxyz", font=font) / 26, 1)
        width_chars = max(int(box_w / avg), 1)
        wrapped = textwrap.fill(text, width=width_chars) if lines_hint > 1 or \
            draw.textlength(text, font=font) > box_w else text
        left, top, right, bottom = draw.multiline_textbbox(
            (0, 0), wrapped, font=font, spacing=int(size * 0.2))
        if right - left <= box_w * 1.02 and bottom - top <= box_h * 1.15:
            return font, wrapped
        size -= 1
    font = _font(font_path, 7)
    return font, textwrap.fill(text, width=max(int(box_w / 4), 1))


def _widen(arr, bbox, others, centered, max_factor=1.6, step=4):
    """Élargit la zone tant que le fond (déjà nettoyé) est uni et qu'on
    ne touche aucun autre texte. Ligne seule : des deux côtés."""
    x0, y0, x1, y1 = bbox
    H, W = arr.shape[:2]
    limit = (x1 - x0) * max_factor

    def uniform(a, b):
        if a < 0 or b > W or b <= a:
            return False
        strip = arr[y0:y1, a:b].reshape(-1, 3)
        return len(strip) and strip.std(axis=0).max() < 12

    def hits(a, b):
        return any(not (b <= o[0] or a >= o[2] or y1 <= o[1] or y0 >= o[3])
                   for o in others)

    while (x1 - x0) + (2 * step if centered else step) <= limit:
        if centered:
            if not (uniform(x0 - step, x0) and uniform(x1, x1 + step)) \
                    or hits(x0 - step, x1 + step):
                break
            x0, x1 = x0 - step, x1 + step
        else:
            if not uniform(x1, x1 + step) or hits(x0, x1 + step):
                break
            x1 += step
    return x0, y0, x1, y1


def translate_pil(image, translator, ocr_lang, target_code=None):
    """Retourne (nouvelle image, nb de paragraphes) ou (None, 0)."""
    if min(image.size) < MIN_SIDE:
        return None, 0
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
    rgb = image.convert("RGB")
    paras = _paragraphs(rgb, ocr_lang)
    if not paras:
        return None, 0

    arr = np.array(rgb)
    todo = []
    for p in paras:
        # Ollama renvoie parfois plusieurs lignes : on remet sur une seule
        # (Pillow ne sait pas mesurer un texte multiligne ; textwrap
        # recoupe ensuite à la largeur de la zone)
        translated = " ".join(translator.translate(p["text"]).split())
        if translated and translated.strip() != p["text"].strip():
            p["bold"] = _is_bold(arr, p["boxes"])
            todo.append((p, translated, _ink_color(arr, p["bbox"])))
    if not todo:
        return None, 0

    # 1. effacer le texte d'origine : masque des mots + inpainting
    mask = np.zeros(arr.shape[:2], dtype=np.uint8)
    for p, _, _ in todo:
        pad = max(2, p["size"] // 6)
        for x0, y0, x1, y1 in p["boxes"]:
            mask[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = 255
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    clean = cv2.inpaint(bgr, mask, 5, cv2.INPAINT_TELEA)
    out = Image.fromarray(cv2.cvtColor(clean, cv2.COLOR_BGR2RGB))

    # 2. écrire la traduction dans la même zone (élargie si le fond
    #    autour est uni, pour ne pas rétrécir la police)
    draw = ImageDraw.Draw(out)
    clean_arr = np.array(out)
    boxes = [p["bbox"] for p, _, _ in todo]
    for p, translated, color in todo:
        others = [b for b in boxes if b is not p["bbox"]]
        x0, y0, x1, y1 = _widen(clean_arr, p["bbox"], others, p["lines"] == 1)
        box_w = max(x1 - x0, 10)
        box_h = max(y1 - y0, p["size"])
        font_path = _font_path(target_code, p["bold"])
        font, wrapped = _fit_text(draw, translated, font_path, box_w, box_h,
                                  p["size"], p["lines"])
        spacing = int(font.size * 0.2)
        l, t, r, b = draw.multiline_textbbox((0, 0), wrapped, font=font,
                                             spacing=spacing)
        if p["lines"] == 1:            # ligne seule : centrée sur l'original
            cx = (p["bbox"][0] + p["bbox"][2]) / 2
            cy = (p["bbox"][1] + p["bbox"][3]) / 2
            pos = (cx - (r - l) / 2 - l, cy - (b - t) / 2 - t)
            align = "center"
        else:
            pos, align = (x0 - l, y0 - t), "left"
        draw.multiline_text(pos, wrapped, font=font, fill=color,
                            spacing=spacing, align=align)

    if alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha)
    return out, len(todo)


# ------------------------------------------------------------
# Octets d'image (EPUB, DOCX, PDF) et fichiers image
# ------------------------------------------------------------

def _save(img, fmt, original):
    buf = io.BytesIO()
    fmt = (fmt or "PNG").upper()
    if fmt in ("JPEG", "JPG"):
        img.convert("RGB").save(buf, "JPEG", quality=95,
                                dpi=original.info.get("dpi", (72, 72)))
    elif fmt == "GIF":
        img.convert("P", palette=Image.ADAPTIVE).save(buf, "GIF")
    else:
        try:
            img.save(buf, fmt)
        except (KeyError, OSError):
            img.save(buf, "PNG")
    return buf.getvalue()


def translate_image_bytes(data, translator, ocr_lang, target_code=None):
    """Pour une image dans une archive. Retourne (octets, n) ou (None, 0)."""
    try:
        original = Image.open(io.BytesIO(data))
        if getattr(original, "n_frames", 1) > 1:     # GIF animé : on n'y touche pas
            return None, 0
        new, n = translate_pil(original, translator, ocr_lang, target_code)
    except Exception:
        return None, 0                               # image illisible : intacte
    if new is None:
        return None, 0
    return _save(new, original.format, original), n


def translate_image_file(src, dst, translator, ocr_lang, target_code=None,
                         on_progress=None, should_stop=None):
    """Fichier image du dossier (une page par image, ou plusieurs pour un
    TIFF multipage). Si rien n'est lisible, la copie est identique."""
    original = Image.open(src)
    frames = [f.copy() for f in ImageSequence.Iterator(original)]
    animated = original.format == "GIF" and len(frames) > 1
    stats = {"pages": len(frames), "blocs": 0, "pages_ocr": 0,
             "pages_ignorees": [], "raison_ocr": None, "interrompu": False}
    if animated:
        frames = frames[:0]
        stats["pages_ignorees"] = [1]
        stats["raison_ocr"] = "GIF animé non traité"

    out_frames = []
    for i, frame in enumerate(frames, start=1):
        if should_stop and should_stop():
            stats["interrompu"] = True
            return stats
        new, n = translate_pil(frame, translator, ocr_lang, target_code)
        out_frames.append(new if new is not None else frame)
        stats["blocs"] += n
        stats["pages_ocr"] += 1
        if on_progress:
            on_progress(i, len(frames), f"{n} zone(s) de texte traduite(s)")

    if not out_frames:                 # rien à faire : copie identique
        with open(src, "rb") as f, open(dst, "wb") as g:
            g.write(f.read())
        return stats
    fmt = original.format or "PNG"
    if len(out_frames) > 1:
        out_frames[0].save(dst, fmt, save_all=True, append_images=out_frames[1:])
    else:
        with open(dst, "wb") as g:
            g.write(_save(out_frames[0], fmt, original))
    return stats
