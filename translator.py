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


class TranslationStopped(BaseException):
    """Annuler / Pause / Quitter demandé : on s'arrête tout de suite.
    BaseException : les « except Exception » des moteurs (images
    illisibles…) ne doivent pas l'avaler."""


class PassageRefused(RuntimeError):
    """Ollama fonctionne mais échoue sur CE texte (erreur 500) :
    on garde le passage dans sa langue d'origine et on continue."""


# au-delà, ce n'est plus un passage difficile : Ollama a un vrai problème
MAX_REFUSED_IN_A_ROW = 5

# Le modèle ne se charge pas (carte graphique pleine, pilote lent…) :
# on attend avant de réessayer, au lieu de mettre la file en pause.
LOAD_WAITS = [60, 60, 60]         # secondes
LOAD_ERRORS = ("llm server", "failed to load", "unable to load", "loading model",
               "out of memory", "unable to allocate", "cuda", "requires more",
               "runner")
KEEP_ALIVE = "30m"                # modèle gardé en mémoire entre deux textes


def _is_load_error(err):
    """Erreur 500 due au chargement du modèle (et non au texte envoyé)."""
    if not isinstance(err, requests.HTTPError) or err.response is None \
            or err.response.status_code != 500:
        return False
    try:
        message = (err.response.json().get("error") or "").lower()
    except ValueError:
        message = (err.response.text or "").lower()
    return any(k in message for k in LOAD_ERRORS)


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
        self.should_stop = None   # fonction : vrai si l'utilisateur arrête
        self.refused = 0          # passages laissés en langue d'origine
        self._refused_streak = 0

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
        try:
            result = self._call_ollama(text, prompt=self._tagged_prompt(text))
        except PassageRefused:
            self.refused += 1
            return text                      # balises intactes : remis tel quel
        if self.cache:
            self.cache.put(key, text, result)
        return result

    def _check_stop(self):
        if self.should_stop and self.should_stop():
            raise TranslationStopped()

    def _sleep(self, seconds):
        """Attente qui s'interrompt dès qu'on demande l'arrêt."""
        end = time.time() + seconds
        while time.time() < end:
            self._check_stop()
            time.sleep(min(0.5, max(0, end - time.time())))

    def _call_ollama(self, text, prompt=None):
        self._check_stop()
        last_error, load_waits, attempt = None, list(LOAD_WAITS), 0
        while True:
            attempt += 1
            try:
                r = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user",
                                      "content": prompt or self._prompt(text)}],
                        "stream": False,
                        "keep_alive": KEEP_ALIVE,
                        "options": {"temperature": 0},
                    },
                    timeout=600,
                )
                r.raise_for_status()
                self._refused_streak = 0
                return r.json()["message"]["content"].strip()
            except requests.RequestException as e:
                last_error = e
            if _is_load_error(last_error):
                if not load_waits:
                    break
                self._sleep(load_waits.pop(0))    # laisser le modèle se charger
                continue
            if attempt >= self.retries:
                break
            self._sleep(2 * attempt)
        if _is_load_error(last_error) and not self._probe():
            raise OllamaError(
                f"Ollama ne répond pas ({self.base_url}) : le modèle n'a pas pu "
                f"se charger (mémoire de la carte graphique pleine ?)")
        if (isinstance(last_error, requests.HTTPError)
                and last_error.response.status_code == 500
                and self._model_available()):
            self._refused_streak += 1
            if self._refused_streak < MAX_REFUSED_IN_A_ROW:
                raise PassageRefused(text[:80])
        if self._refused_streak >= MAX_REFUSED_IN_A_ROW:
            detail = (f"{self._refused_streak} passages refusés de suite "
                      f"(erreur 500) — redémarre Ollama")
        elif isinstance(last_error, requests.ConnectionError):
            detail = "connexion refusée — Ollama est-il lancé ?"
        elif isinstance(last_error, requests.HTTPError):
            detail = (f"erreur HTTP {last_error.response.status_code} — "
                      f"le modèle « {self.model} » est-il installé ?")
        else:
            detail = type(last_error).__name__
        raise OllamaError(f"Ollama ne répond pas ({self.base_url}) : {detail}")

    def _probe(self):
        """Le modèle répond-il à un tout petit texte ? Si oui, c'est le
        passage envoyé qui le fait planter, pas le modèle."""
        try:
            r = requests.post(f"{self.base_url}/api/chat", timeout=300, json={
                "model": self.model, "stream": False, "keep_alive": KEEP_ALIVE,
                "messages": [{"role": "user", "content": "Translate to French: Good morning."}],
                "options": {"temperature": 0, "num_predict": 8}})
            r.raise_for_status()
            return True
        except requests.RequestException:
            return False

    def _model_available(self):
        """Ollama répond et le modèle est installé."""
        try:
            return self.model in list_models(self.base_url)
        except requests.RequestException:
            return False

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
            try:
                result = self._call_ollama(chunk)
            except PassageRefused:
                self.refused += 1
                parts.append(chunk)          # gardé tel quel, pas mis en cache
                continue
            if self.cache:
                self.cache.put(key, chunk, result)
            parts.append(result)
        return " ".join(parts)
