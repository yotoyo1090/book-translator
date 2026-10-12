"""Moteurs des formats bureautiques : Excel, PowerPoint, 한글 HWPX,
LibreOffice (ODT/ODS/ODP), HTML, RTF et sous-titres (SRT/VTT).

Même principe que pour DOCX : les fichiers Office, HWPX et ODF sont des
archives zip de XML. On ne réécrit que le texte ; images, styles,
formules, mise en page sont recopiés tels quels. Le gras, l'italique,
les couleurs… sont gardés grâce aux balises numérotées (<1>…</1>).
"""

import copy
import html as html_lib
import posixpath
import re
import zipfile

from lxml import etree

from doc_engines import (TAG_RE, XML_SPACE, _local, _stats,
                         _translate_archive_images, _translate_keep_spaces,
                         _translate_leaf, _write_zip, needs_translation)


class _Stop(Exception):
    pass


def _xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False,
                                                  huge_tree=True))


def _dump(root):
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True)


# ============================================================
# Paragraphe fait de « runs » (morceaux de même mise en forme)
# Commun à PowerPoint (a:r), Excel (r) et 한글 HWPX (hp:run).
# ============================================================

def _compose(groups, translator):
    """groups : ("text", clé de format, run, texte) ou ("void", objet).
    Retourne [("text", run, texte) | ("void", objet)] traduit,
    ou None si le modèle a abîmé les balises.
    Les éléments sans texte au début et à la fin (réglages de section,
    numéros…) restent à leur place : seuls ceux du milieu sont balisés."""
    head, tail = [], []
    while groups and groups[0][0] == "void":
        head.append(("void", groups.pop(0)[1]))
    while groups and groups[-1][0] == "void":
        tail.insert(0, ("void", groups.pop()[1]))
    middle = _compose_middle(groups, translator)
    return None if middle is None else head + middle + tail


def _compose_middle(groups, translator):
    weight = {}
    for g in groups:
        if g[0] == "text":
            weight[g[1]] = weight.get(g[1], 0) + len(g[3])
    base_key = max(weight, key=weight.get)
    base = next(g for g in groups if g[0] == "text" and g[1] == base_key)

    parts, tokens, n = [], {}, 0
    for g in groups:
        if g[0] == "text" and g[1] == base_key:
            parts.append(g[3])
        elif g[0] == "text" and needs_translation(g[3]):
            n += 1
            tokens[n] = ("pair", g)
            parts.append(f"<{n}>{g[3]}</{n}>")
        else:
            n += 1
            tokens[n] = ("void", g)
            parts.append(f"<{n}/>")
    source = " ".join("".join(parts).split())
    if not tokens:
        return [("text", base[2], translator.translate(source))]
    if "<" in TAG_RE.sub("", source) or ">" in TAG_RE.sub("", source) \
            or not hasattr(translator, "translate_tagged"):
        return None
    result = translator.translate_tagged(source)
    matches = list(TAG_RE.finditer(result))
    seen = sorted(int(m.group(1) or m.group(2)) for m in matches)
    if seen != sorted(tokens) or re.search(r"</?\d+/?>", TAG_RE.sub("", result)):
        return None
    for m in matches:
        if (tokens[int(m.group(1) or m.group(2))][0] == "void") != (m.group(1) is not None):
            return None

    out, pos = [], 0
    for m in matches:
        if result[pos:m.start()]:
            out.append(("text", base[2], result[pos:m.start()]))
        kind, g = tokens[int(m.group(1) or m.group(2))]
        if kind == "pair":
            out.append(("text", g[2], m.group(3)))
        elif g[0] == "text":
            out.append(("text", g[2], g[3]))
        else:
            out.append(("void", g[1]))
        pos = m.end()
    if result[pos:]:
        out.append(("text", base[2], result[pos:]))
    return out


class RunDialect:
    """Description d'un format à runs.
    run / text / props : balises du run, du texte, des propriétés ;
    keep : enfants du paragraphe qui restent à leur place (pPr…) ;
    drop : enfants à supprimer après traduction (cache de mise en page)."""

    def __init__(self, run, text, props=(), keep=(), drop=(), preserve=False):
        self.run, self.text = run, text
        self.props, self.keep, self.drop = set(props), set(keep), set(drop)
        self.preserve = preserve           # xml:space="preserve" sur le texte

    def key(self, run):
        attrs = tuple(sorted(run.attrib.items()))
        props = b"".join(etree.tostring(c) for c in run if c.tag in self.props)
        return attrs, props

    def new_run(self, model, children):
        r = etree.Element(model.tag, dict(model.attrib))
        for c in model:
            if c.tag in self.props:
                r.append(copy.deepcopy(c))
        for c in children:
            r.append(c)
        return r

    def text_el(self, text):
        t = etree.Element(self.text)
        t.text = text
        if self.preserve:
            t.set(XML_SPACE, "preserve")
        return t


def _translate_runs(p, translator, d):
    """Traduit un paragraphe à runs. Retourne 1 si traduit."""
    texts = [t for r in p if r.tag == d.run for t in r if t.tag == d.text]
    # saut de ligne DANS le texte (한글 <hp:lineBreak/>) : un espace
    full = "".join((t.text or "") + "".join(" " + (c.tail or "") for c in t)
                   for t in texts)
    if not needs_translation(full):
        return 0

    simple = all(len(t) == 0 for t in texts)
    pieces = None
    if simple:
        atoms, members = [], []
        for child in p:
            if child.tag in d.keep or child.tag in d.drop:
                continue
            members.append(child)
            if child.tag != d.run:
                atoms.append(("void", ("elem", child)))
                continue
            for c in child:
                if c.tag in d.props:
                    continue
                if c.tag == d.text:
                    atoms.append(("text", d.key(child), child, c.text or ""))
                else:
                    atoms.append(("void", ("child", child, c)))
        groups = []
        for a in atoms:                      # morceaux voisins de même format
            if a[0] == "text" and groups and groups[-1][0] == "text" \
                    and groups[-1][1] == a[1]:
                groups[-1] = ("text", a[1], groups[-1][2], groups[-1][3] + a[3])
            else:
                groups.append(a)
        pieces = _compose(groups, translator)

    if pieces is not None:
        index = list(p).index(members[0])
        new, sources = [], {}
        for piece in pieces:
            if piece[0] == "text":
                if piece[2]:
                    new.append(d.new_run(piece[1], [d.text_el(piece[2])]))
            elif piece[1][0] == "elem":
                new.append(piece[1][1])
            elif new and sources.get(new[-1]) is piece[1][1]:
                new[-1].append(piece[1][2])        # même run d'origine : ensemble
            else:
                run = d.new_run(piece[1][1], [piece[1][2]])
                new.append(run)
                sources[run] = piece[1][1]
        for m in members:
            p.remove(m)
        for k, el in enumerate(new):
            p.insert(index + k, el)
    else:
        # Repli : tout le texte traduit dans le premier morceau
        first = texts[0]
        for c in list(first):
            first.remove(c)
        first.text = translator.translate(" ".join(full.split()))
        if d.preserve:
            first.set(XML_SPACE, "preserve")
        for t in texts[1:]:
            for c in list(t):
                t.remove(c)
            t.text = ""
    for c in list(p):
        if c.tag in d.drop:
            p.remove(c)
    return 1


def _translate_plain(el, translator, preserve=False):
    """Élément qui ne contient que du texte (cellule Excel simple…)."""
    if not needs_translation(el.text):
        return 0
    el.text = _translate_keep_spaces(translator, el.text)
    if preserve:
        el.set(XML_SPACE, "preserve")
    return 1


# ------------------------------------------------------------
# Archive zip à parties XML (commun Excel / PowerPoint / HWPX / ODF)
# ------------------------------------------------------------

def _translate_package(src, dst, translator, parts, translate_part, unit,
                       translate_images, ocr_lang, target_code,
                       on_progress, should_stop):
    """parts : noms des fichiers XML à traduire, dans l'ordre.
    translate_part(nom, octets, tick) -> (octets, nb de blocs)."""
    with zipfile.ZipFile(src) as z:
        total = len(parts) or 1
        stats = _stats(total)
        replaced = {}

        def tick():
            if should_stop and should_stop():
                raise _Stop

        try:
            for k, name in enumerate(parts, start=1):
                tick()
                replaced[name], n = translate_part(name, z.read(name), tick)
                stats["blocs"] += n
                if on_progress:
                    on_progress(k, total, unit)
        except _Stop:
            stats["interrompu"] = True
            return stats
        if translate_images and ocr_lang:
            if on_progress:
                on_progress(total, total, "texte dans les images…")
            _translate_archive_images(z, replaced, translator, ocr_lang,
                                      target_code, stats, should_stop)
            if stats["interrompu"]:
                return stats
        if on_progress:
            on_progress(total, total, f"{stats['blocs']} paragraphe(s) traduit(s)")
        _write_zip(z, dst, replaced)
    return stats


def _num_key(name):
    m = re.search(r"(\d+)\.xml$", name)
    return (re.sub(r"\d+\.xml$", "", name), int(m.group(1)) if m else 0)


# ============================================================
# DrawingML (PowerPoint, graphiques et zones de texte Excel)
# ============================================================

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
DRAWING = RunDialect(run=f"{{{A}}}r", text=f"{{{A}}}t", props=[f"{{{A}}}rPr"],
                     keep=[f"{{{A}}}pPr", f"{{{A}}}endParaRPr"])


def _translate_drawingml(data, translator, tick=None):
    root = _xml(data)
    count = 0
    for p in reversed(list(root.iter(f"{{{A}}}p"))):
        if tick:
            tick()
        count += _translate_runs(p, translator, DRAWING)
    return _dump(root), count


# ============================================================
# PowerPoint (PPTX)
# ============================================================

PPTX_PARTS = re.compile(
    r"^ppt/(slides/slide|notesSlides/notesSlide|charts/chart|"
    r"diagrams/(data|drawing))\d*\.xml$")


def pptx_slide_count(path):
    with zipfile.ZipFile(path) as z:
        return sum(1 for n in z.namelist()
                   if re.match(r"^ppt/slides/slide\d+\.xml$", n))


def translate_pptx(src, dst, translator, translate_images=False,
                   ocr_lang=None, target_code=None,
                   on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        parts = sorted((n for n in z.namelist() if PPTX_PARTS.match(n)),
                       key=lambda n: (not n.startswith("ppt/slides/"), _num_key(n)))
    return _translate_package(
        src, dst, translator, parts,
        lambda name, data, tick: _translate_drawingml(data, translator, tick),
        "diapositives", translate_images, ocr_lang, target_code,
        on_progress, should_stop)


# ============================================================
# Excel (XLSX)
# ============================================================

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CELL_RUNS = RunDialect(run=f"{{{S}}}r", text=f"{{{S}}}t", props=[f"{{{S}}}rPr"],
                       keep=[f"{{{S}}}rPh", f"{{{S}}}phoneticPr"], preserve=True)
XLSX_PARTS = re.compile(
    r"^xl/(sharedStrings|worksheets/sheet\d+|comments\d*|"
    r"threadedComments/threadedComment\d*|drawings/drawing\d+|charts/chart\d+)\.xml$")


def _translate_rich(el, translator):
    """<si>, <is> ou <text> d'un commentaire : texte simple ou runs."""
    plain = el.find(f"{{{S}}}t")
    if plain is not None and el.find(f"{{{S}}}r") is None:
        return _translate_plain(plain, translator, preserve=True)
    return _translate_runs(el, translator, CELL_RUNS)


def _translate_xlsx_part(name, data, translator, tick):
    if "/drawings/" in name or "/charts/" in name:
        return _translate_drawingml(data, translator, tick)
    root = _xml(data)
    count = 0
    if "threadedComment" in name:
        for t in root.iter("{*}text"):
            tick()
            count += _translate_plain(t, translator)
    elif name.endswith("sharedStrings.xml"):
        for si in root.iter(f"{{{S}}}si"):
            tick()
            count += _translate_rich(si, translator)
    elif "/worksheets/" in name:
        for c in root.iter(f"{{{S}}}c"):      # textes saisis dans la cellule
            if c.get("t") == "inlineStr":
                tick()
                for is_ in c.iter(f"{{{S}}}is"):
                    count += _translate_rich(is_, translator)
    else:                                     # commentaires (notes)
        for t in root.iter(f"{{{S}}}text"):
            tick()
            count += _translate_rich(t, translator)
    return _dump(root), count


def xlsx_sheet_count(path):
    with zipfile.ZipFile(path) as z:
        return sum(1 for n in z.namelist()
                   if re.match(r"^xl/worksheets/sheet\d+\.xml$", n))


def translate_xlsx(src, dst, translator, translate_images=False,
                   ocr_lang=None, target_code=None,
                   on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        parts = sorted((n for n in z.namelist() if XLSX_PARTS.match(n)),
                       key=lambda n: (not n.endswith("sharedStrings.xml"), _num_key(n)))
    return _translate_package(
        src, dst, translator, parts,
        lambda name, data, tick: _translate_xlsx_part(name, data, translator, tick),
        "feuilles", translate_images, ocr_lang, target_code,
        on_progress, should_stop)


# ============================================================
# 한글 HWPX
# ============================================================

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HWPX_RUNS = RunDialect(run=f"{{{HP}}}run", text=f"{{{HP}}}t",
                       drop=[f"{{{HP}}}linesegarray"])
HWPX_PARTS = re.compile(r"^Contents/section\d+\.xml$")


def _translate_hwpx_section(data, translator, tick):
    root = _xml(data)
    count = 0
    # paragraphes intérieurs (cellules de tableau…) avant ceux qui les contiennent
    for p in reversed(list(root.iter(f"{{{HP}}}p"))):
        tick()
        count += _translate_runs(p, translator, HWPX_RUNS)
    return _dump(root), count


def hwpx_paragraph_count(path):
    with zipfile.ZipFile(path) as z:
        return sum(sum(1 for _ in _xml(z.read(n)).iter(f"{{{HP}}}p"))
                   for n in z.namelist() if HWPX_PARTS.match(n))


def translate_hwpx(src, dst, translator, translate_images=False,
                   ocr_lang=None, target_code=None,
                   on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        parts = sorted((n for n in z.namelist() if HWPX_PARTS.match(n)),
                       key=_num_key)
    return _translate_package(
        src, dst, translator, parts,
        lambda name, data, tick: _translate_hwpx_section(data, translator, tick),
        "sections", translate_images, ocr_lang, target_code,
        on_progress, should_stop)


# ============================================================
# LibreOffice / OpenOffice (ODT, ODS, ODP)
# ============================================================

TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ODF_BLOCKS = {f"{{{TEXT_NS}}}p", f"{{{TEXT_NS}}}h"}


def _translate_odf_xml(data, translator, tick):
    root = _xml(data)
    count = 0
    blocks = [el for el in root.iter() if el.tag in ODF_BLOCKS]
    for el in reversed(blocks):
        tick()
        nested = any(d.tag in ODF_BLOCKS for d in el.iterdescendants())
        if not nested:
            count += _translate_leaf(el, translator)
            continue
        # paragraphe qui contient une note, un cadre… : seulement son propre
        # texte (le texte de la note est traduit avec ses paragraphes)
        own = False
        if needs_translation(el.text):
            el.text = _translate_keep_spaces(translator, el.text)
            own = True
        for child in el:
            if child.tail and needs_translation(child.tail):
                child.tail = _translate_keep_spaces(translator, child.tail)
                own = True
            if _local(child.tag) in ("span", "a") and len(child) == 0 \
                    and needs_translation(child.text):
                child.text = _translate_keep_spaces(translator, child.text)
                own = True
        count += own
    return _dump(root), count


def odf_paragraph_count(path):
    with zipfile.ZipFile(path) as z:
        root = _xml(z.read("content.xml"))
        return sum(1 for el in root.iter() if el.tag in ODF_BLOCKS)


def translate_odf(src, dst, translator, translate_images=False,
                  ocr_lang=None, target_code=None,
                  on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        # styles.xml : en-têtes et pieds de page (texte, tableur). Pour une
        # présentation, il ne contient que les modèles (« Cliquez pour… »)
        wanted = ("content.xml",) if src.lower().endswith(".odp") \
            else ("content.xml", "styles.xml")
        parts = [n for n in wanted if n in z.namelist()]
    return _translate_package(
        src, dst, translator, parts,
        lambda name, data, tick: _translate_odf_xml(data, translator, tick),
        "parties", translate_images, ocr_lang, target_code,
        on_progress, should_stop)


# ============================================================
# HTML
# ============================================================

def translate_html(src, dst, translator, lang_code=None,
                   on_progress=None, should_stop=None):
    import lxml.html
    from doc_engines import SKIP, _is_leaf_block
    raw = open(src, "rb").read()
    doc = lxml.html.document_fromstring(raw)
    blocks = [el for el in doc.iter()
              if isinstance(el.tag, str) and _local(el.tag) not in SKIP
              and _is_leaf_block(el) and _local(el.tag) not in ("html", "head", "body")
              and not any(_local(a.tag) in SKIP for a in el.iterancestors())]
    stats = _stats(len(blocks))
    for k, el in enumerate(blocks, start=1):
        if should_stop and should_stop():
            stats["interrompu"] = True
            return stats
        stats["blocs"] += _translate_leaf(el, translator)
        if on_progress and (k % 10 == 0 or k == len(blocks)):
            on_progress(k, len(blocks), "paragraphes")
    for meta in doc.iter("meta"):                 # description de la page
        if meta.get("name") in ("description", "keywords") \
                and needs_translation(meta.get("content")):
            meta.set("content", translator.translate(meta.get("content")))
    if lang_code:
        doc.set("lang", lang_code)
    out = lxml.html.tostring(doc, encoding="utf-8", doctype="<!DOCTYPE html>",
                             method="html")
    # la page est réécrite en UTF-8 : on met la déclaration à jour
    out = re.sub(rb'(<meta[^>]+charset=["\']?)[\w-]+', rb"\1utf-8", out, count=1, flags=re.I)
    with open(dst, "wb") as f:
        f.write(out)
    return stats


def html_block_count(path):
    import lxml.html
    doc = lxml.html.document_fromstring(open(path, "rb").read())
    return sum(1 for el in doc.iter() if _local(el.tag) in ("p", "h1", "h2", "h3", "li", "td"))


# ============================================================
# Sous-titres (SRT, VTT)
# ============================================================

def _split_lines(text, n):
    """Recoupe une traduction en n lignes à peu près égales (aux espaces)."""
    if n <= 1 or " " not in text:
        return [text]
    words, lines, size = text.split(), [], len(text) / n
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > size * (len(lines) + 1) - sum(map(len, lines)) \
                and len(lines) < n - 1:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    lines.append(cur)
    return lines


def _read_text(src):
    raw = open(src, "rb").read()
    for enc in ("utf-8-sig", "cp949", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue


def translate_subtitles(src, dst, translator, on_progress=None, should_stop=None):
    text = _read_text(src).replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text.strip("\n"))
    stats = _stats(len(blocks))
    out = []
    for k, block in enumerate(blocks, start=1):
        if should_stop and should_stop():
            stats["interrompu"] = True
            return stats
        lines = block.split("\n")
        cue = next((i for i, l in enumerate(lines) if "-->" in l), None)
        body = lines[cue + 1:] if cue is not None else []
        if cue is None or not needs_translation(" ".join(body)):
            out.append(block)                 # en-tête WEBVTT, NOTE, numéros…
            continue
        translated = translator.translate(" ".join(" ".join(body).split()))
        out.append("\n".join(lines[: cue + 1] + _split_lines(translated, len(body))))
        stats["blocs"] += 1
        if on_progress and (k % 10 == 0 or k == len(blocks)):
            on_progress(k, len(blocks), "sous-titres")
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join("\n\n".join(out).split("\n")) + "\r\n")
    return stats


def subtitle_count(path):
    return _read_text(path).count("-->")


# ============================================================
# RTF
# ============================================================

RTF_TOKEN = re.compile(
    rb"(\{|\}|\\'[0-9a-fA-F]{2}|\\u-?\d+ ?|\\[a-zA-Z]+-?\d* ?|\\[^a-zA-Z]|"
    rb"[\r\n]+|[^\\{}\r\n]+)")
# groupes dont le texte n'est pas affiché (tables, images, champs…)
RTF_HIDDEN = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict",
              b"object", b"fldinst", b"themedata", b"colorschememapping",
              b"latentstyles", b"datastore", b"listtable", b"listoverridetable",
              b"rsidtbl", b"generator", b"xmlnstbl", b"mmathPr", b"header",
              b"footer", b"headerl", b"headerr", b"footerl", b"footerr",
              b"filetbl", b"revtbl", b"pgdsctbl", b"bkmkstart", b"bkmkend"}
RTF_BREAK = {b"par", b"sect", b"cell", b"row", b"line", b"page"}
RTF_SPECIAL = {b"\\~": "\u00a0", b"\\_": "\u2011", b"\\-": ""}


def _rtf_encode(text):
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif 32 <= o < 128:
            out.append(ch)
        elif o > 0xFFFF:
            o -= 0x10000
            for u in (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)):
                out.append(f"\\u{u - 65536}?")
        else:
            out.append(f"\\u{o if o < 32768 else o - 65536}?")
    return "".join(out).encode("ascii")


def translate_rtf(src, dst, translator, on_progress=None, should_stop=None):
    data = open(src, "rb").read()
    tokens = RTF_TOKEN.findall(data)
    m = re.search(rb"\\ansicpg(\d+)", data[:2000])
    codepage = f"cp{m.group(1).decode()}" if m else "cp1252"

    # 1. repérer les paragraphes : [(indices des jetons de texte, morceaux)]
    paragraphs, cur = [], []
    stack, hidden, skip_next, uc = [], False, 0, 1
    first_in_group = False
    for i, tok in enumerate(tokens):
        if tok == b"{":
            stack.append((hidden, uc))
            first_in_group = True
            continue
        if tok == b"}":
            hidden, uc = stack.pop() if stack else (False, 1)
            continue
        starts = first_in_group
        first_in_group = False
        if tok.startswith(b"\\*"):
            hidden = True
            continue
        if tok.startswith(b"\\") and re.match(rb"\\[a-zA-Z]", tok):
            word = re.match(rb"\\([a-zA-Z]+)", tok).group(1)
            if word in RTF_HIDDEN and starts:
                hidden = True
            elif word == b"uc":
                uc = int(re.search(rb"\d+", tok).group(0))
            elif word == b"u" and not hidden:
                n = int(re.search(rb"-?\d+", tok).group(0))
                cur.append((i, chr(n + 65536 if n < 0 else n)))
                skip_next = uc
            elif word in RTF_BREAK and not hidden:
                if cur:
                    paragraphs.append(cur)
                cur = []
            continue
        if hidden or tok.startswith((b"\r", b"\n")):
            continue
        if skip_next:                          # caractère de secours après \uN
            skip_next -= 1
            cur.append((i, ""))
            continue
        if tok.startswith(b"\\'"):
            cur.append((i, bytes([int(tok[2:4], 16)])))
        elif tok in RTF_SPECIAL:
            cur.append((i, RTF_SPECIAL[tok]))
        elif tok.startswith(b"\\"):
            if tok[1:2] in (b"\\", b"{", b"}"):
                cur.append((i, tok[1:2].decode()))
        else:
            cur.append((i, tok))               # texte brut (octets)
    if cur:
        paragraphs.append(cur)

    def decode(pieces):
        text, buf = [], b""
        for _, p in pieces:
            if isinstance(p, bytes):
                buf += p
            else:
                text.append(buf.decode(codepage, errors="replace"))
                buf = b""
                text.append(p)
        text.append(buf.decode(codepage, errors="replace"))
        return "".join(text)

    # 2. traduire chaque paragraphe : tout le texte va dans le premier jeton
    stats = _stats(len(paragraphs))
    for k, pieces in enumerate(paragraphs, start=1):
        if should_stop and should_stop():
            stats["interrompu"] = True
            return stats
        text = decode(pieces)
        if not needs_translation(text):
            continue
        lead = " " if text[:1] == " " else ""
        trail = " " if text[-1:] == " " else ""
        new = lead + translator.translate(" ".join(text.split())) + trail
        for j, (i, _) in enumerate(pieces):
            if j:
                tokens[i] = b""
                continue
            enc = _rtf_encode(new)
            b = i - 1
            while b >= 0 and not tokens[b]:
                b -= 1
            prev = tokens[b] if b >= 0 else b""
            if re.search(rb"\\[a-zA-Z]+-?\d*$", prev):   # « \b » collé : délimiteur
                enc = b" " + enc
            tokens[i] = enc
        stats["blocs"] += 1
        if on_progress and (k % 10 == 0 or k == len(paragraphs)):
            on_progress(k, len(paragraphs), "paragraphes")
    with open(dst, "wb") as f:
        f.write(b"".join(tokens))
    return stats


def rtf_text(path, limit=200_000):
    """Texte d'un RTF (pour détecter la langue) : on « traduit » à vide."""
    import os
    import tempfile

    class Grab:
        def __init__(self):
            self.out = []

        def translate(self, t):
            self.out.append(t)
            return t

    g = Grab()
    fd, tmp = tempfile.mkstemp(suffix=".rtf")
    os.close(fd)
    try:
        translate_rtf(path, tmp, g)
    finally:
        os.remove(tmp)
    return " ".join(g.out)[:limit]
