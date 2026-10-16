"""Lanceur de l'application installée (raccourci « Book Translator »).

Démarre Ollama s'il ne tourne pas, puis le dashboard, sans fenêtre de
console. Si le dashboard tourne déjà (deuxième clic), ouvre simplement
le navigateur dessus.
"""

import os
import socket
import subprocess
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

URL = "http://127.0.0.1:8765"


def port_ouvert(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ollama_exe():
    for path in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
                 r"C:\Program Files\Ollama\ollama.exe"):
        if os.path.exists(path):
            return path
    return "ollama"


def demarrer_ollama():
    if port_ouvert(11434):
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([ollama_exe(), "serve"], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return                       # le dashboard affichera « Ollama hors ligne »
    for _ in range(30):
        if port_ouvert(11434):
            return
        time.sleep(0.5)


def version_en_cours():
    """Empreinte du programme qui tourne (None : ancienne version)."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(URL + "/api/version", timeout=5) as r:
            return json.load(r).get("version")
    except Exception:
        return None


def arreter_ancien():
    """Arrête proprement le programme qui tourne (bouton « 종료 »), et en
    dernier recours le processus lui-même."""
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(URL + "/api/quitter",
                                                      method="POST"), timeout=5)
    except Exception:
        pass
    for _ in range(90):                       # arrêt propre : jusqu'à 90 s
        if not port_ouvert(8765):
            return
        time.sleep(1)
    if sys.platform == "win32":               # ancienne version sans « 종료 »
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                  f"Where-Object {{ $_.CommandLine -like '*{HERE}*' -and $_.ProcessId -ne {os.getpid()} }} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       creationflags=flags, timeout=30)
        for _ in range(20):
            if not port_ouvert(8765):
                return
            time.sleep(0.5)


def main():
    from version import code_version
    if port_ouvert(8765):
        if version_en_cours() == code_version():   # même version : rouvrir la page
            webbrowser.open(URL)
            return
        arreter_ancien()                           # version mise à jour : redémarrer
    # pythonw n'a pas de console : les messages vont dans un fichier
    log = open(os.path.join(HERE, "journal.txt"), "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    demarrer_ollama()
    import server                    # après Ollama : la liste des modèles est prête
    import threading
    import uvicorn
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    uvicorn.run(server.app, host=server.HOST, port=server.PORT, log_level="warning")


if __name__ == "__main__":
    main()
