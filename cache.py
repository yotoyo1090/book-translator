"""Cache SQLite des traductions : permet la reprise après un plantage
et évite de retraduire un texte identique (titres courants, pieds de page)."""

import hashlib
import sqlite3
import threading


class TranslationCache:

    def __init__(self, path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS traductions ("
            " cle TEXT PRIMARY KEY,"
            " source TEXT NOT NULL,"
            " traduction TEXT NOT NULL)"
        )
        self._db.commit()

    @staticmethod
    def make_key(text, source_lang, target_lang, model):
        raw = "\x1f".join([model, source_lang, target_lang, text])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key):
        with self._lock:
            row = self._db.execute(
                "SELECT traduction FROM traductions WHERE cle = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def put(self, key, source, translation):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO traductions VALUES (?, ?, ?)",
                (key, source, translation),
            )
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()
