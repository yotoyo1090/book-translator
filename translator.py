"""Client Ollama : traduit un texte d'une langue vers une autre,
en passant par le cache SQLite."""

import re
import time

import requests

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "translategemma:12b"

# Taille max d'un morceau envoyé au modèle (caractères)
CHUNK_SIZE = 3000


class OllamaError(RuntimeError):
    pass


def list_models(base_url=OLLAMA_URL):
    """Modèles installés dans Ollama (pour le futur dashboard)."""
    r = requests.get(f"{base_url}/api/tags", timeout=10)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def split_long_text(text, max_chars=CHUNK_SIZE):
    """Découpe un texte trop long aux fins de phrases."""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if current and len(current) + len(s) + 1 > max_chars:
            chunks.append(current)
            current = s
        else:
            current = f"{current} {s}".strip()
    if current:
        chunks.append(current)
    # Une « phrase » plus longue que max_chars : coupe brute
    out = []
    for c in chunks:
        while len(c) > max_chars:
            out.append(c[:max_chars])
            c = c[max_chars:]
        out.append(c)
    return out


class Translator:

    def __init__(self, source_lang, target_lang, model=DEFAULT_MODEL,
                 cache=None, base_url=OLLAMA_URL, retries=3):
        self.source_lang = source_lang      # ex. "English"
        self.target_lang = target_lang      # ex. "French"
        self.model = model
        self.cache = cache
        self.base_url = base_url
        self.retries = retries

    def _prompt(self, text):
        return (
            f"You are a professional {self.source_lang} to {self.target_lang} "
            f"translator. Your goal is to accurately convey the meaning and "
            f"nuances of the original {self.source_lang} text while adhering "
            f"to {self.target_lang} grammar, vocabulary, and cultural "
            f"sensitivities.\n"
            f"Translate every sentence. Do not summarize, omit, add or "
            f"explain anything. Keep names, numbers and references.\n"
            f"Produce only the {self.target_lang} translation, without any "
            f"additional explanations or commentary.\n\n"
            f"Please translate the following {self.source_lang} text into "
            f"{self.target_lang}:\n\n{text}"
        )

    def _tagged_prompt(self, text):
        return (
            self._prompt(text).replace(
                "Produce only",
                "The text contains numbered tags such as <1>...</1> and <2/>. "
                "Keep every tag exactly as written, around the translated "
                "words they belong to. Do not add or remove tags.\n"
                "Produce only")
        )

    def translate_tagged(self, text):
        """Comme translate(), pour un texte contenant des balises <n>…</n>
        (gras, italique, liens) que le modèle doit conserver."""
        text = text.strip()
        key = None
        if self.cache:
            key = self.cache.make_key("TAGGED\x1f" + text, self.source_lang,
                                      self.target_lang, self.model)
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        result = self._call_ollama(text, prompt=self._tagged_prompt(text))
        if self.cache:
            self.cache.put(key, text, result)
        return result

    def _call_ollama(self, text, prompt=None):
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                r = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user",
                                      "content": prompt or self._prompt(text)}],
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                    timeout=600,
                )
                r.raise_for_status()
                return r.json()["message"]["content"].strip()
            except requests.RequestException as e:
                last_error = e
                time.sleep(2 * attempt)
        if isinstance(last_error, requests.ConnectionError):
            detail = "connexion refusée — Ollama est-il lancé ?"
        elif isinstance(last_error, requests.HTTPError):
            detail = (f"erreur HTTP {last_error.response.status_code} — "
                      f"le modèle « {self.model} » est-il installé ?")
        else:
            detail = type(last_error).__name__
        raise OllamaError(f"Ollama ne répond pas ({self.base_url}) : {detail}")

    def translate(self, text):
        text = text.strip()
        if not text:
            return text
        parts = []
        for chunk in split_long_text(text):
            key = None
            if self.cache:
                key = self.cache.make_key(chunk, self.source_lang,
                                          self.target_lang, self.model)
                hit = self.cache.get(key)
                if hit is not None:
                    parts.append(hit)
                    continue
            result = self._call_ollama(chunk)
            if self.cache:
                self.cache.put(key, chunk, result)
            parts.append(result)
        return " ".join(parts)
