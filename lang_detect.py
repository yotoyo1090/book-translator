"""Détection automatique de la langue d'un livre ou d'une image.

- Livres avec du texte (PDF texte, EPUB, DOCX, TXT) : on analyse un
  extrait pris à plusieurs endroits du livre.
- Scans et images : Tesseract identifie d'abord l'écriture (latine,
  coréenne, cyrillique…), puis on lit une page par OCR et on l'analyse.
Le détecteur (lingua) fonctionne hors ligne et est limité aux langues
proposées par la plateforme.
"""

import zipfile

import pymupdf
import pytesseract
from lingua import Language, LanguageDetectorBuilder
from PIL import Image

SAMPLE_CHARS = 3000
MIN_CONFIDENCE = 0.3

LINGUA = {
    "English": Language.ENGLISH, "French": Language.FRENCH,
    "Korean": Language.KOREAN, "Spanish": Language.SPANISH,
    "German": Language.GERMAN, "Italian": Language.ITALIAN,
    "Portuguese": Language.PORTUGUESE, "Dutch": Language.DUTCH,
    "Russian": Language.RUSSIAN, "Japanese": Language.JAPANESE,
    "Chinese": Language.CHINESE, "Arabic": Language.ARABIC,
}
NAME_OF = {v: k for k, v in LINGUA.items()}

# écriture détectée par Tesseract (OSD) → langues OCR candidates
SCRIPT_LANGS = {
    "Latin": ["eng", "fra", "deu", "spa", "ita", "por", "nld"],
    "Hangul": ["kor"], "Korean": ["kor"],
    "Cyrillic": ["rus"], "Arabic": ["ara"],
    "Han": ["chi_sim"], "Japanese": ["jpn"], "Katakana": ["jpn"],
    "Hiragana": ["jpn"],
}

_detector = None


def _get_detector():
    global _detector
    if _detector is None:
        _detector = LanguageDetectorBuilder.from_languages(*LINGUA.values()).build()
    return _detector


def detect_text(text):
    """Nom de la langue (« German »…) ou None si le texte est trop pauvre."""
    text = " ".join(text.split())
    if sum(c.isalpha() for c in text) < 20:
        return None
    best = _get_detector().compute_language_confidence_values(text[:SAMPLE_CHARS])[0]
    return NAME_OF.get(best.language) if best.value >= MIN_CONFIDENCE else None


# ------------------------------------------------------------
# Extraits de texte selon le format
# ------------------------------------------------------------

def _spread(items, n=8):
    """n éléments répartis dans toute la liste (début, milieu, fin)."""
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _pdf_sample(path):
    from pdf_engine import page_kind
    doc = pymupdf.open(path)
    try:
        texts, scans = [], []
        for i in _spread(range(len(doc)), 10):
            kind = page_kind(doc[i])
            if kind == "texte":
                texts.append(doc[i].get_text())
            elif kind in ("scan", "illisible"):
                scans.append(i)
        if sum(len(t) for t in texts) > 200:
            return " ".join(texts), None
        if scans:                      # livre scanné : image d'une page
            pix = doc[scans[len(scans) // 2]].get_pixmap(dpi=200)
            return None, Image.frombytes("RGB", (pix.width, pix.height),
                                         pix.samples)
        return " ".join(texts), None
    finally:
        doc.close()


def _epub_sample(path):
    from lxml import etree
    from doc_engines import epub_documents
    with zipfile.ZipFile(path) as z:
        docs = epub_documents(z)[0]
        parts = []
        for name in _spread(docs, 6):
            root = etree.fromstring(z.read(name), etree.XMLParser(recover=True))
            parts.append(" ".join(root.itertext())[:1000])
        return " ".join(parts)


def _docx_sample(path):
    from lxml import etree
    from doc_engines import W
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
        paras = ["".join(t.text or "" for t in p.iter(f"{{{W}}}t"))
                 for p in root.iter(f"{{{W}}}p")]
        return " ".join(_spread([p for p in paras if p.strip()], 40))


def _txt_sample(path):
    raw = open(path, "rb").read(200_000)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    chunk = len(text) // 4 or 1
    return " ".join(text[i * chunk:i * chunk + 800] for i in range(4))


def _zip_sample(path, pattern, tag):
    """Texte des éléments `tag` des fichiers XML de l'archive qui
    correspondent à `pattern` (Excel, PowerPoint, HWPX, ODF)."""
    import re
    from lxml import etree
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(pattern, n)]
        parts = []
        for name in _spread(sorted(names), 6):
            root = etree.fromstring(z.read(name), etree.XMLParser(recover=True, huge_tree=True))
            words = [t.text for t in root.iter(tag) if t.text and t.text.strip()]
            parts.append(" ".join(_spread(words, 200)))
        return " ".join(parts)


ZIP_SAMPLES = {
    "XLSX": (r"^xl/(sharedStrings|worksheets/sheet\d+)\.xml$", "{*}t"),
    "PPTX": (r"^ppt/slides/slide\d+\.xml$", "{*}t"),
    "HWPX": (r"^Contents/section\d+\.xml$", "{*}t"),
    "ODF": (r"^content\.xml$", None),
}


def _odf_sample(path):
    from lxml import etree
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("content.xml"), etree.XMLParser(recover=True, huge_tree=True))
        return " ".join(" ".join(root.itertext()).split())[:SAMPLE_CHARS * 3]


def _html_sample(path):
    import lxml.html
    doc = lxml.html.document_fromstring(open(path, "rb").read(400_000))
    for bad in doc.xpath("//script|//style"):
        bad.drop_tree()
    return " ".join(doc.text_content().split())


def _srt_sample(path):
    from office_engines import _read_text
    lines = [l for l in _read_text(path).splitlines()
             if l.strip() and "-->" not in l and not l.strip().isdigit()]
    return " ".join(_spread(lines, 120))


def _script(ch):
    o = ord(ch)
    if o < 0x250:
        return "latin" if ch.isalpha() else None
    for name, lo, hi in (("cyr", 0x400, 0x4FF), ("arab", 0x600, 0x6FF),
                         ("kana", 0x3040, 0x30FF), ("han", 0x4E00, 0x9FFF),
                         ("hangul", 0xAC00, 0xD7AF)):
        if lo <= o <= hi:
            return name
    return "autre"


def _one_script(text):
    """Vrai texte : surtout des lettres, presque toutes de la même écriture
    (des octets quelconques lus en UTF-16 mélangent les écritures)."""
    scripts = [s for s in map(_script, text) if s]
    if len(scripts) < len(text) * 0.6:
        return False
    top = max(set(scripts), key=scripts.count)
    if top == "autre" or scripts.count(top) < len(scripts) * 0.9:
        return False
    return top != "latin" or text.count(" ") >= 2


def _legacy_sample(path):
    """Ancien format binaire (HWP, DOC, XLS, PPT) sans le convertir.
    HWP : flux « PrvText » (aperçu du texte). Sinon : suites de lettres
    lisibles en UTF-16 (texte Office) ou en 8 bits."""
    import re
    if path.lower().endswith(".hwp"):
        try:
            import olefile
            with olefile.OleFileIO(path) as ole:
                if ole.exists("PrvText"):
                    return ole.openstream("PrvText").read().decode("utf-16-le", "ignore")
        except Exception:
            pass
    raw = open(path, "rb").read(3_000_000)
    found = []
    # texte UTF-16 : latin, cyrillique, arabe, kana, chinois, hangeul
    utf16 = rb"(?:[\x09\x0d\x20-\x7e\xa0-\xff]\x00|[\x00-\xff][\x04\x06\x30\x4e-\x9f\xac-\xd7]){16,}"
    for m in re.finditer(utf16, raw):
        text = m.group(0).decode("utf-16-le", "ignore")
        if _one_script(text):
            found.append(text)
    if sum(map(len, found)) < 300:            # texte en 8 bits (cp1252)
        found += [t for t in (m.group(0).decode("cp1252", "ignore") for m in
                  re.finditer(rb"[A-Za-z\xc0-\xff][A-Za-z\xc0-\xff ,.;:'()!?-]{24,}", raw))
                  if t.count(" ") >= 3]
    return " ".join(_spread(found, 200))


# ------------------------------------------------------------
# Scans et images : écriture puis OCR d'une page
# ------------------------------------------------------------

def _ocr_lang_for(image, installed):
    """UNE langue OCR pour l'écriture détectée (latine → anglais).
    Pour reconnaître la langue, une lecture approximative suffit : lire
    avec toutes les langues latines à la fois serait plusieurs fois plus lent."""
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0")
        script = next((l.split(":", 1)[1].strip() for l in osd.splitlines()
                       if l.startswith("Script:")), "Latin")
    except Exception:
        script = "Latin"               # OSD absent ou image trop pauvre
    for lang in SCRIPT_LANGS.get(script, ["eng"]):
        if lang in installed:
            return lang
    return "eng" if "eng" in installed else None


def _detection_band(image):
    """Bande du milieu de la page (un tiers de la hauteur) : assez de texte
    pour reconnaître la langue, ~2× plus rapide que la page entière."""
    w, h = image.size
    if h < 600:
        return image
    return image.crop((0, h // 3, w, 2 * h // 3))


def detect_image(image, installed):
    lang = _ocr_lang_for(image, installed)
    if not lang:
        return None
    text = pytesseract.image_to_string(_detection_band(image), lang=lang,
                                       config="--psm 3")
    if sum(c.isalpha() for c in text) < 40:      # bande vide : page entière
        text = pytesseract.image_to_string(image, lang=lang, config="--psm 3")
    return detect_text(text)


def detect_file(path, fmt, installed_ocr=None):
    """Langue d'un fichier du dossier, ou None si impossible à savoir.
    installed_ocr : langues Tesseract installées (None = pas d'OCR)."""
    try:
        if fmt == "PDF":
            text, image = _pdf_sample(path)
            if image is not None:
                return detect_image(image, installed_ocr) if installed_ocr else None
            return detect_text(text or "")
        if fmt == "EPUB":
            return detect_text(_epub_sample(path))
        if fmt == "DOCX":
            return detect_text(_docx_sample(path))
        if fmt == "TXT":
            return detect_text(_txt_sample(path))
        if fmt == "ODF":
            return detect_text(_odf_sample(path))
        if fmt in ZIP_SAMPLES:
            return detect_text(_zip_sample(path, *ZIP_SAMPLES[fmt]))
        if fmt == "HTML":
            return detect_text(_html_sample(path))
        if fmt == "RTF":
            from office_engines import rtf_text
            return detect_text(rtf_text(path))
        if fmt == "SRT":
            return detect_text(_srt_sample(path))
        if fmt in ("HWP", "DOC", "XLS", "PPT"):
            return detect_text(_legacy_sample(path))
        if fmt == "IMAGE" and installed_ocr:
            with Image.open(path) as im:
                img = im.convert("RGB")
            if max(img.size) < 1200:   # petite image : agrandie pour l'OCR
                img = img.resize((img.width * 2, img.height * 2))
            return detect_image(img, installed_ocr)
    except Exception:
        return None
    return None
