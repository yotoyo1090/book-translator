"""Empreinte des fichiers installés : change dès qu'un fichier du programme
est mis à jour. Le lanceur la compare à celle du programme qui tourne
pour savoir s'il faut le redémarrer."""

import hashlib
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def code_version():
    h = hashlib.sha1()
    for folder in (HERE, os.path.join(HERE, "static")):
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.endswith((".py", ".js", ".html", ".css")):
                st = os.stat(path)
                h.update(f"{name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:12]
