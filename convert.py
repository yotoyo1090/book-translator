"""Anciens formats binaires : 한글 HWP, Word DOC, Excel XLS, PowerPoint PPT.

Aucune bibliothèque libre ne sait les réécrire proprement. On s'appuie
donc sur le programme installé sur le PC :
  - HWP           → le programme 한글 (HWPFrame.HwpObject), HWP ⇄ HWPX ;
  - DOC/XLS/PPT   → Microsoft Office (Word, Excel, PowerPoint), sinon
                    LibreOffice (soffice) : DOC ⇄ DOCX, XLS ⇄ XLSX, PPT ⇄ PPTX.
Le fichier est converti vers le format récent, traduit, puis reconverti
dans son format d'origine.
"""

import os
import shutil
import subprocess
import sys
import tempfile

LEGACY = {".hwp": ".hwpx", ".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}


class ConverterMissing(RuntimeError):
    pass


# ------------------------------------------------------------
# Disponibilité des programmes
# ------------------------------------------------------------

def _com_available(progid):
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid))
        return True
    except OSError:
        return False


def soffice_path():
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.exists(p):
            return p
    return shutil.which("soffice") or shutil.which("libreoffice")


OFFICE_PROGID = {".doc": "Word.Application", ".xls": "Excel.Application",
                 ".ppt": "PowerPoint.Application"}


def converter_for(ext):
    """Nom du programme qui convertira ce format, ou None."""
    ext = ext.lower()
    if ext == ".hwp":
        return "hangul" if _com_available("HWPFrame.HwpObject") else None
    if ext in OFFICE_PROGID:
        if _com_available(OFFICE_PROGID[ext]):
            return "office"
        if soffice_path():
            return "libreoffice"
    return None


# ------------------------------------------------------------
# Conversions
# ------------------------------------------------------------

def _com(progid):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()           # appelé depuis le fil de la file d'attente
    return win32com.client.DispatchEx(progid)


def _hangul(src, dst, fmt):
    """한글 : ouvre src et l'enregistre au format fmt ("HWP" ou "HWPX")."""
    hwp = _com("HWPFrame.HwpObject")
    try:
        try:   # module de sécurité : évite la question « autoriser l'accès ? »
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:
            pass
        try:
            hwp.XHwpWindows.Item(0).Visible = False
        except Exception:
            pass
        src_fmt = "HWPX" if src.lower().endswith(".hwpx") else "HWP"
        if not hwp.Open(src, src_fmt, "forceopen:true"):
            raise RuntimeError("한글 : ouverture impossible")
        if not hwp.SaveAs(dst, fmt, ""):
            raise RuntimeError("한글 : enregistrement impossible")
    finally:
        try:
            hwp.Clear(1)
            hwp.Quit()
        except Exception:
            pass


# codes FileFormat des programmes Office
WORD_FMT = {".docx": 16, ".doc": 0}
EXCEL_FMT = {".xlsx": 51, ".xls": 56}
PPT_FMT = {".pptx": 24, ".ppt": 1}


def _office(src, dst):
    ext_in = os.path.splitext(src)[1].lower()
    ext_out = os.path.splitext(dst)[1].lower()
    kind = {".doc": "word", ".docx": "word", ".xls": "excel", ".xlsx": "excel",
            ".ppt": "ppt", ".pptx": "ppt"}[ext_in]
    if kind == "word":
        app = _com("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        doc = app.Documents.Open(src, ConfirmConversions=False, ReadOnly=True,
                                 AddToRecentFiles=False)
        try:
            doc.SaveAs2(dst, FileFormat=WORD_FMT[ext_out])
        finally:
            doc.Close(False)
            app.Quit()
    elif kind == "excel":
        app = _com("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        wb = app.Workbooks.Open(src, UpdateLinks=0, ReadOnly=True)
        try:
            wb.SaveAs(dst, FileFormat=EXCEL_FMT[ext_out])
        finally:
            wb.Close(False)
            app.Quit()
    else:
        app = _com("PowerPoint.Application")
        pres = app.Presentations.Open(src, ReadOnly=True, WithWindow=False)
        try:
            pres.SaveAs(dst, PPT_FMT[ext_out])
        finally:
            pres.Close()
            app.Quit()


def _libreoffice(src, dst):
    out_dir = os.path.dirname(dst)
    ext = os.path.splitext(dst)[1].lstrip(".")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # profil séparé : marche même si LibreOffice est déjà ouvert
    profile = "file:///" + os.path.join(tempfile.gettempdir(), "bt_lo_profile").replace("\\", "/").lstrip("/")
    subprocess.run([soffice_path(), f"-env:UserInstallation={profile}",
                    "--headless", "--convert-to", ext, "--outdir", out_dir, src],
                   check=True, timeout=600, creationflags=flags,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    made = os.path.join(out_dir, os.path.splitext(os.path.basename(src))[0] + "." + ext)
    if not os.path.exists(made):
        raise RuntimeError("LibreOffice : conversion impossible")
    if made != dst:
        shutil.move(made, dst)


def _convert(src, dst, tool):
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if tool == "hangul":
        _hangul(src, dst, "HWPX" if dst.lower().endswith(".hwpx") else "HWP")
    elif tool == "office":
        _office(src, dst)
    else:
        _libreoffice(src, dst)


def translate_legacy(src, dst, translate_modern, on_progress=None):
    """translate_modern(src_moderne, dst_moderne) -> stats."""
    ext = os.path.splitext(src)[1].lower()
    tool = converter_for(ext)
    if tool is None:
        need = "한글" if ext == ".hwp" else "Microsoft Office ou LibreOffice"
        raise ConverterMissing(f"Format {ext} : il faut {need} sur ce PC")
    modern = LEGACY[ext]
    with tempfile.TemporaryDirectory(prefix="bt_") as tmp:
        a = os.path.join(tmp, "source" + modern)
        b = os.path.join(tmp, "traduit" + modern)
        c = os.path.join(tmp, "traduit" + ext)
        if on_progress:
            on_progress(0, 1, f"conversion {ext} → {modern}…")
        _convert(src, a, tool)
        stats = translate_modern(a, b)
        if stats.get("interrompu"):
            return stats
        if on_progress:
            on_progress(1, 1, f"conversion {modern} → {ext}…")
        _convert(b, c, tool)
        shutil.copyfile(c, dst)
    return stats
