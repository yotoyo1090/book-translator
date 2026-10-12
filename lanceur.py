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


def main():
    if port_ouvert(8765):            # déjà lancé
        webbrowser.open(URL)
        return
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
