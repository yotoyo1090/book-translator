"""Moteurs EPUB, DOCX et TXT/MD (étape 4).

EPUB et DOCX sont des archives zip de fichiers XML. On ne réécrit que
le texte à l'intérieur des balises ; tout le reste (images, polices,
CSS, mise en forme, table des matières) est recopié octet pour octet.
"""

import posixpath
import re
import zipfile

from lxml import etree

LETTERS = re.compile(r"[^\W\d_]{2,}")


def needs_translation(text):
    return bool(text and LETTERS.search(text))


def _stats(units):
    return {"pages": units, "blocs": 0, "pages_ocr": 0, "pages_ignorees": [],
            "raison_ocr": None, "interrompu": False, "images": 0}


IMAGE_NAMES = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?)$", re.I)


def _translate_archive_images(z, replaced, translator, ocr_lang, target_code,
                              stats, should_stop=None):
    """Remplace dans l'archive chaque image dont le texte a été traduit
    (même nom, même format : les liens vers l'image restent valides)."""
    from image_text import translate_image_bytes
    for name in z.namelist():
        if not IMAGE_NAMES.search(name) or "thumbnail" in name.lower():
            continue                   # aperçu du fichier : inutile
        if should_stop and should_stop():
            stats["interrompu"] = True
            return
        data, n = translate_image_bytes(z.read(name), translator, ocr_lang,
                                        target_code)
        if data is not None:
            replaced[name] = data
            stats["images"] += n


def _translate_keep_spaces(translator, text):
    """Traduit en gardant les espaces de début et de fin du nœud."""
    if not needs_translation(text):
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + translator.translate(" ".join(text.split())) + trail


def _write_zip(src_zip, dst, replaced):
    """Recopie l'archive ; `replaced` = {nom: nouveaux octets}.
    Le fichier « mimetype » d'un EPUB doit rester premier et non compressé."""
    with zipfile.ZipFile(dst, "w") as out:
        for info in src_zip.infolist():
            data = replaced.get(info.filename, src_zip.read(info.filename))
            if info.filename == "mimetype":
                out.writestr(info, data, compress_type=zipfile.ZIP_STORED)
            else:
                out.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


# ============================================================
# EPUB
# ============================================================

XHTML = "http://www.w3.org/1999/xhtml"
BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td",
          "th", "dt", "dd", "figcaption", "caption", "div", "section",
          "article", "aside", "header", "footer", "title", "nav", "table",
          "tr", "ul", "ol", "dl", "figure", "body", "html", "head"}
SKIP = {"script", "style", "code", "pre", "math", "svg"}
WRAPPERS = {"em", "i", "strong", "b", "span", "small", "u", "cite"}


def _local(tag):
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _is_leaf_block(el):
    if _local(el.tag) not in BLOCKS:
        return False
    return not any(_local(d.tag) in BLOCKS for d in el.iterdescendants())


VOID = {"img", "image", "br", "hr", "input", "object", "audio", "video",
        "svg", "math", "wbr"}
TAG_RE = re.compile(r"<(\d+)/>|<(\d+)>(.*?)</\2>", re.S)


def _tag_block(el):
    """Remplace les éléments en ligne par des balises numérotées :
    <1>gras</1>, <2/> pour une image ou une note.
    Retourne (texte, jetons) ou None si le bloc est trop complexe."""
    parts, tokens = [el.text or ""], {}
    for n, child in enumerate(el, start=1):
        sub = "".join(child.itertext()) if isinstance(child.tag, str) else ""
        if (not isinstance(child.tag, str) or _local(child.tag) in VOID
                or not needs_translation(sub)):
            tokens[n] = ("void", child)          # gardé tel quel
            parts.append(f"<{n}/>")
        elif len(child) == 0:
            tokens[n] = ("pair", child)
            parts.append(f"<{n}>{child.text or ''}</{n}>")
        else:
            return None                          # mise en forme imbriquée
        parts.append(child.tail or "")
    text = "".join(parts)
    plain = TAG_RE.sub("", text)
    if "<" in plain or ">" in plain:
        return None
    return " ".join(text.split()), tokens


def _rebuild_from_tags(el, result, tokens):
    """Remet la traduction dans le bloc. Faux si le modèle a abîmé
    les balises (on retombe alors sur la traduction morceau par morceau)."""
    matches = list(TAG_RE.finditer(result))
    seen = [int(m.group(1) or m.group(2)) for m in matches]
    if sorted(seen) != sorted(tokens):
        return False
    for m in matches:
        n = int(m.group(1) or m.group(2))
        if (tokens[n][0] == "void") != (m.group(1) is not None):
            return False
    rest = TAG_RE.sub("", result)
    if re.search(r"</?\d+/?>", rest):
        return False

    for child in list(el):
        el.remove(child)
    el.text = result[: matches[0].start()] if matches else result
    for i, m in enumerate(matches):
        kind, child = tokens[int(m.group(1) or m.group(2))]
        if kind == "pair":
            child.text = m.group(3)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(result)
        child.tail = result[m.end():end]
        el.append(child)
    return True


def _translate_nodes(el, translator):
    """Dernier recours : chaque morceau de texte traduit séparément."""
    for node in el.iter(etree.Element):
        if node.text:
            node.text = _translate_keep_spaces(translator, node.text)
        if node is not el and node.tail:
            node.tail = _translate_keep_spaces(translator, node.tail)


def _translate_leaf(el, translator):
    """Traduit un bloc (paragraphe, titre…). Retourne 1 si traduit."""
    text = "".join(el.itertext())
    if not needs_translation(text):
        return 0
    if {_local(d.tag) for d in el.iterdescendants()} & SKIP - {"svg", "math"}:
        return 0
    children = list(el)

    # 1. Texte simple, ou tout en italique / gras : phrase entière
    only_wrapper = (len(children) == 1 and _local(children[0].tag) in WRAPPERS
                    and len(children[0]) == 0
                    and not (el.text or "").strip()
                    and not (children[0].tail or "").strip())
    if not children or only_wrapper:
        translated = translator.translate(" ".join(text.split()))
        target = children[0] if only_wrapper else el
        target.text = translated
        return 1

    # 2. Gras, liens, notes, images dans la phrase : balises numérotées
    tagged = _tag_block(el)
    if tagged and hasattr(translator, "translate_tagged"):
        source, tokens = tagged
        result = translator.translate_tagged(source)
        if _rebuild_from_tags(el, result, tokens):
            return 1

    # 3. Sinon : morceau par morceau
    _translate_nodes(el, translator)
    return 1


def _translate_xhtml(data, translator, lang_code):
    parser = etree.XMLParser(resolve_entities=False, recover=True)
    root = etree.fromstring(data, parser)
    count = 0
    for el in list(root.iter()):
        if _local(el.tag) in SKIP:
            continue
        if _is_leaf_block(el) and _local(el.tag) not in ("html", "head", "body"):
            count += _translate_leaf(el, translator)
    if lang_code:
        root.set("lang", lang_code)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", lang_code)
    return etree.tostring(root, xml_declaration=True, encoding="utf-8"), count


def _translate_ncx(data, translator):
    root = etree.fromstring(data, etree.XMLParser(recover=True))
    count = 0
    for el in root.iter():
        if _local(el.tag) == "text" and needs_translation(el.text):
            el.text = translator.translate(el.text.strip())
            count += 1
    return etree.tostring(root, xml_declaration=True, encoding="utf-8"), count


def epub_documents(z):
    """Fichiers XHTML dans l'ordre de lecture + OPF + NCX."""
    container = etree.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//{*}rootfile").get("full-path")
    opf = etree.fromstring(z.read(opf_path))
    base = posixpath.dirname(opf_path)
    items = {i.get("id"): i for i in opf.iter("{*}item")}
    spine = [items[r.get("idref")] for r in opf.iter("{*}itemref")
             if r.get("idref") in items]
    docs = [posixpath.normpath(posixpath.join(base, i.get("href")))
            for i in spine]
    # pages de navigation (nav.xhtml) hors du fil de lecture
    for i in items.values():
        path = posixpath.normpath(posixpath.join(base, i.get("href")))
        if "nav" in (i.get("properties") or "").split() and path not in docs:
            docs.append(path)
    ncx = [posixpath.normpath(posixpath.join(base, i.get("href")))
           for i in items.values()
           if i.get("media-type") == "application/x-dtbncx+xml"]
    names = set(z.namelist())
    return ([d for d in docs if d in names], opf_path,
            [n for n in ncx if n in names])


def translate_epub(src, dst, translator, lang_code=None,
                   translate_images=False, ocr_lang=None,
                   on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        docs, opf_path, ncx = epub_documents(z)
        stats = _stats(len(docs))
        replaced = {}
        for i, name in enumerate(docs, start=1):
            if should_stop and should_stop():
                stats["interrompu"] = True
                return stats
            data, n = _translate_xhtml(z.read(name), translator, lang_code)
            replaced[name] = data
            stats["blocs"] += n
            if on_progress:
                on_progress(i, len(docs), f"{posixpath.basename(name)} : "
                                          f"{n} paragraphe(s)")
        for name in ncx:
            replaced[name], n = _translate_ncx(z.read(name), translator)
            stats["blocs"] += n
        # titre du livre et langue dans le fichier OPF
        opf = etree.fromstring(z.read(opf_path))
        for el in opf.iter("{*}title"):
            if needs_translation(el.text):
                el.text = translator.translate(el.text.strip())
        if lang_code:
            for el in opf.iter("{*}language"):
                el.text = lang_code
        replaced[opf_path] = etree.tostring(opf, xml_declaration=True,
                                            encoding="utf-8")
        if translate_images and ocr_lang:
            if on_progress:
                on_progress(len(docs), len(docs), "texte dans les images…")
            _translate_archive_images(z, replaced, translator, ocr_lang,
                                      lang_code, stats, should_stop)
            if stats["interrompu"]:
                return stats
        _write_zip(z, dst, replaced)
    return stats


# ============================================================
# DOCX
# ============================================================

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_PARTS = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")


import copy

WT, WR, WRPR = f"{{{W}}}t", f"{{{W}}}r", f"{{{W}}}rPr"
WHYPER, WPPR = f"{{{W}}}hyperlink", f"{{{W}}}pPr"
INLINE_VOID = {f"{{{W}}}{n}" for n in ("br", "tab", "cr", "noBreakHyphen",
                                        "softHyphen")}
# Paragraphes trop particuliers pour être recomposés : champs (table des
# matières, numéros de page), suivi des modifications, contrôles de contenu
COMPLEX = {f"{{{W}}}{n}" for n in ("fldChar", "instrText", "fldSimple", "ins",
                                    "del", "sdt", "smartTag", "customXml")}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _own(p, tag):
    """Éléments `tag` de ce paragraphe (pas d'une zone de texte imbriquée)."""
    return [e for e in p.iter(tag)
            if next(e.iterancestors(f"{{{W}}}p")) is p]


def _is_code_paragraph(p):
    style = p.find(f"{WPPR}/{{{W}}}pStyle")
    name = (style.get(f"{{{W}}}val") if style is not None else "") or ""
    return any(k in name.lower() for k in ("code", "source", "verbatim", "html"))


def _atoms(p):
    """Découpe le paragraphe en éléments dans l'ordre :
    ("text", rPr, lien, texte) ou ("void", élément à remettre tel quel)."""
    atoms = []
    for child in p:
        if child.tag == WPPR:
            continue
        runs, link = [child], None
        if child.tag == WHYPER:
            runs, link = list(child), child
        for r in runs:
            if r.tag != WR:
                atoms.append(("void", r))
                continue
            texts = [c for c in r if c.tag == WT]
            if not texts:
                atoms.append(("void", r if link is None else _wrap(r, link)))
                continue
            rpr = r.find(WRPR)
            for c in r:
                if c.tag == WT:
                    atoms.append(("text", rpr, link, c.text or ""))
                elif c.tag in INLINE_VOID:
                    atoms.append(("void", _new_run(rpr, [copy.deepcopy(c)])))
                elif c.tag != WRPR:
                    atoms.append(("void", _new_run(rpr, [copy.deepcopy(c)])))
    return atoms


def _new_run(rpr, children):
    r = etree.Element(WR)
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    for c in children:
        r.append(c)
    return r


def _text_run(rpr, text):
    t = etree.Element(WT)
    t.text = text
    t.set(XML_SPACE, "preserve")
    return _new_run(rpr, [t])


def _wrap(run, link):
    """Remet un run dans une copie (vide) de son lien hypertexte."""
    h = etree.Element(link.tag, link.attrib)
    h.append(copy.deepcopy(run))
    return h


def _fmt_key(rpr, link):
    return (etree.tostring(rpr) if rpr is not None else b"", id(link))


def _rebuild_paragraph(p, pieces):
    """Remplace le contenu du paragraphe (sauf pPr) par `pieces`."""
    for child in list(p):
        if child.tag != WPPR:
            p.remove(child)
    for el in pieces:
        p.append(el)


def _translate_paragraph(p, translator):
    """Retourne 1 si le paragraphe a été traduit."""
    texts = _own(p, WT)
    full = "".join(t.text or "" for t in texts)
    if not needs_translation(full) or _is_code_paragraph(p):
        return 0

    simple_ok = not any(e.tag in COMPLEX for e in p.iter())
    if simple_ok:
        atoms = _atoms(p)
        # regrouper les morceaux de texte consécutifs de même mise en forme
        groups = []
        for a in atoms:
            if (a[0] == "text" and groups and groups[-1][0] == "text"
                    and _fmt_key(groups[-1][1], groups[-1][2]) == _fmt_key(a[1], a[2])):
                groups[-1] = ("text", a[1], a[2], groups[-1][3] + a[3])
            else:
                groups.append(a)
        # mise en forme majoritaire = texte « normal », sans balise
        weight = {}
        for g in groups:
            if g[0] == "text":
                k = _fmt_key(g[1], g[2])
                weight[k] = weight.get(k, 0) + len(g[3])
        base_key = max(weight, key=weight.get)
        base = next(g for g in groups if g[0] == "text"
                    and _fmt_key(g[1], g[2]) == base_key)

        parts, tokens, n = [], {}, 0
        for g in groups:
            if g[0] == "text" and _fmt_key(g[1], g[2]) == base_key:
                parts.append(g[3])
            elif g[0] == "text" and not needs_translation(g[3]):
                n += 1
                tokens[n] = ("void", g)
                parts.append(f"<{n}/>")
            elif g[0] == "text":
                n += 1
                tokens[n] = ("pair", g)
                parts.append(f"<{n}>{g[3]}</{n}>")
            else:
                n += 1
                tokens[n] = ("void", g)
                parts.append(f"<{n}/>")
        source = " ".join("".join(parts).split())
        plain = TAG_RE.sub("", source)

        if not tokens:
            result, matches = translator.translate(source), []
        elif "<" in plain or ">" in plain or not hasattr(translator, "translate_tagged"):
            result = None
        else:
            result = translator.translate_tagged(source)
            matches = list(TAG_RE.finditer(result))
            seen = sorted(int(m.group(1) or m.group(2)) for m in matches)
            kinds_ok = all((tokens[int(m.group(1) or m.group(2))][0] == "void")
                           == (m.group(1) is not None) for m in matches
                           if int(m.group(1) or m.group(2)) in tokens)
            if (seen != sorted(tokens) or not kinds_ok
                    or re.search(r"</?\d+/?>", TAG_RE.sub("", result))):
                result = None

        if result is not None:
            pieces, pos = [], 0

            def add_text(text, g):
                if not text:
                    return
                run = _text_run(g[1], text)
                pieces.append(_wrap(run, g[2]) if g[2] is not None else run)

            for m in matches:
                add_text(result[pos:m.start()], base)
                kind, g = tokens[int(m.group(1) or m.group(2))]
                if kind == "pair":
                    add_text(m.group(3), g)
                elif g[0] == "text":
                    add_text(g[3], g)
                else:
                    pieces.append(g[1])
                pos = m.end()
            add_text(result[pos:], base)
            _rebuild_paragraph(p, pieces)
            return 1

    # Repli : tout le texte dans le premier morceau, le reste vidé
    # (images, champs et sauts restent à leur place)
    texts[0].text = translator.translate(" ".join(full.split()))
    texts[0].set(XML_SPACE, "preserve")
    for t in texts[1:]:
        t.text = ""
    return 1


def _translate_docx_part(data, translator, progress=None):
    root = etree.fromstring(data)
    paragraphs = list(root.iter(f"{{{W}}}p"))
    count = 0
    for k, p in enumerate(paragraphs, start=1):
        count += _translate_paragraph(p, translator)
        if progress:
            progress(k, len(paragraphs))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True), count


def docx_paragraph_count(path):
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
        return sum(1 for _ in root.iter(f"{{{W}}}p"))


def translate_docx(src, dst, translator, translate_images=False,
                   ocr_lang=None, target_code=None,
                   on_progress=None, should_stop=None):
    with zipfile.ZipFile(src) as z:
        parts = [n for n in z.namelist() if DOCX_PARTS.match(n)]
        parts.sort(key=lambda n: n != "word/document.xml")   # corps d'abord
        total = docx_paragraph_count(src)
        stats = _stats(total)
        replaced = {}

        class Stop(Exception):
            pass

        def progress(k, n):
            if should_stop and should_stop():
                raise Stop
            if on_progress:
                on_progress(min(k, total), total, "paragraphes")

        try:
            for name in parts:
                prog = progress if name == "word/document.xml" else None
                replaced[name], n = _translate_docx_part(z.read(name),
                                                         translator, prog)
                stats["blocs"] += n
        except Stop:
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


# ============================================================
# TXT / Markdown
# ============================================================

MD_PREFIX = re.compile(
    r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*|\[\^[^\]]+\]:\s*)+)")
MD_INLINE = re.compile(
    r"(?P<img>!\[[^\]]*\]\([^)]*\))"            # image : gardée telle quelle
    r"|(?P<code>`[^`]+`)"                        # code en ligne
    r"|(?P<fn>\[\^[^\]]+\])"                     # appel de note
    r"|\[(?P<link>[^\]]+)\](?P<url>\([^)]*\))"   # lien : texte traduit
    r"|\*\*(?P<b>[^*]+)\*\*"                     # gras
    r"|(?<![*\w])\*(?P<i>[^*\s][^*]*)\*(?![*\w])"  # italique *…*
    r"|(?<![_\w])_(?P<u>[^_\s][^_]*)_(?![_\w])"  # italique _…_
)
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _md_inline(text, translator):
    """Traduit une ligne Markdown en protégant liens, gras, images…"""
    if not needs_translation(MD_INLINE.sub(" ", text)):
        return text
    tokens, out, n = {}, [], 0

    def repl(m):
        nonlocal n
        n += 1
        if m.group("img") or m.group("code") or m.group("fn"):
            tokens[n] = ("void", m.group(0))
            return f"<{n}/>"
        if m.group("link") is not None:
            tokens[n] = ("pair", "[{}]" + m.group("url"))
            return f"<{n}>{m.group('link')}</{n}>"
        for g, tpl in (("b", "**{}**"), ("i", "*{}*"), ("u", "_{}_")):
            if m.group(g) is not None:
                tokens[n] = ("pair", tpl)
                return f"<{n}>{m.group(g)}</{n}>"
        return m.group(0)

    lead = text[: len(text) - len(text.lstrip())]
    tagged = MD_INLINE.sub(repl, " ".join(text.split()))
    if not tokens:
        return lead + translator.translate(tagged)
    result = translator.translate_tagged(tagged)
    matches = list(TAG_RE.finditer(result))
    ok = (sorted(int(m.group(1) or m.group(2)) for m in matches) == sorted(tokens)
          and not re.search(r"</?\d+/?>", TAG_RE.sub("", result)))
    if not ok:                     # balises abîmées : texte brut traduit
        return lead + translator.translate(" ".join(text.split()))

    def back(m):
        kind, value = tokens[int(m.group(1) or m.group(2))]
        return value if kind == "void" else value.format(m.group(3))
    return lead + TAG_RE.sub(back, result)


def _md_table(para, translator):
    rows = []
    for line in para.splitlines():
        if TABLE_SEP.match(line):
            rows.append(line)
            continue
        cells = line.split("|")
        rows.append("|".join(
            c if not needs_translation(c) else
            " " + _md_inline(c.strip(), translator) + " " for c in cells))
    return "\n".join(rows)


def _front_matter(text, translator):
    """En-tête YAML (--- … ---) : gardé, seul le titre est traduit."""
    m = re.match(r"---\n.*?\n(---|\.\.\.)\n", text, re.S)
    if not m:
        return "", text
    head = re.sub(r"^(title:\s*)(.+)$",
                  lambda t: t.group(1) + translator.translate(t.group(2).strip()),
                  m.group(0), flags=re.M)
    return head, text[m.end():]


def _paragraphs(text):
    return re.split(r"\n\s*\n", text)


def translate_text_file(src, dst, translator, markdown=False,
                        on_progress=None, should_stop=None):
    raw = open(src, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")

    head = ""
    if markdown:
        head, text = _front_matter(text, translator)
    parts = _paragraphs(text)
    stats = _stats(len(parts))
    if not markdown:
        # TXT : un paragraphe peut faire tout le fichier (aucune ligne vide),
        # le compteur avance donc à chaque morceau envoyé au modèle
        from translator import split_long_text
        total = sum(len(split_long_text(" ".join(p.split()))) if needs_translation(p) else 1
                    for p in parts)
        done = 0
    out, in_code = [], False
    for i, para in enumerate(parts, start=1):
        if should_stop and should_stop():
            stats["interrompu"] = True
            return stats
        lines = para.splitlines()
        if markdown and (in_code or para.lstrip().startswith("```")):
            if para.count("```") % 2 == 1:      # bloc de code : inchangé
                in_code = not in_code
            out.append(para)
        elif not needs_translation(para):
            out.append(para)
        elif markdown and lines and all(l.lstrip().startswith("|") for l in lines if l.strip()):
            out.append(_md_table(para, translator))
            stats["blocs"] += 1
        elif markdown and any(MD_PREFIX.match(l) or l.endswith(("\\", "  "))
                              for l in lines):
            # titres, listes, citations, notes, vers : ligne par ligne
            new = []
            for line in lines:
                m = MD_PREFIX.match(line)
                prefix = m.group(1) if m else ""
                body = line[len(prefix):]
                end = "\\" if body.endswith("\\") else ("  " if body.endswith("  ") else "")
                body = body[: len(body) - len(end)] if end else body
                new.append(prefix + _md_inline(body, translator) + end)
            out.append("\n".join(new))
            stats["blocs"] += 1
        elif markdown:
            out.append(_md_inline(para, translator))
            stats["blocs"] += 1
        else:
            # TXT : lignes coupées à la main → on recolle le paragraphe
            lead = para[: len(para) - len(para.lstrip("\n"))]
            done_parts = []
            for chunk in split_long_text(" ".join(para.split())):
                done_parts.append(translator.translate(chunk))
                done += 1
                if on_progress:
                    on_progress(done, total, "")
            out.append(lead + " ".join(done_parts))
            stats["blocs"] += 1
            continue
        if not markdown:
            done += 1
            if on_progress:
                on_progress(done, total, "")
        elif on_progress:
            on_progress(i, len(parts), "paragraphes")

    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write((head + "\n\n".join(out)).replace("\n", newline))
    return stats
