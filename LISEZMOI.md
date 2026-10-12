# Traducteur de livres

Traduit en local, avec Ollama, tous les livres et images d'un dossier
(PDF, EPUB, DOCX, TXT, MD, JPG, PNG, WEBP, BMP, TIFF, GIF), en gardant
les images et la mise en page.

## Installation en un clic (Windows)

Double-clic sur `BookTranslator_Setup.exe` : choisir la langue, puis le
modèle IA (léger 4b ou haute qualité 12b, présélectionné selon la carte
graphique). L'installateur télécharge et installe tout seul Python
(portable, dans le dossier du programme), les bibliothèques, Tesseract OCR
avec 12 langues, Ollama et le modèle. Une icône « Book Translator » est
créée sur le bureau et dans le menu Démarrer. Relancer l'installateur
répare ou met à jour sans tout retélécharger.

Reconstruire l'installateur : `installer/build.sh` (il faut `makensis`).

## Installation manuelle

1. Python 3.10 ou plus récent.
2. Ollama avec le modèle : `ollama pull translategemma:12b`
3. Tesseract OCR (pour les PDF scannés) :
   https://github.com/UB-Mannheim/tesseract/wiki — cocher les langues de
   tes livres dans « Additional language data ».
4. Dans ce dossier : `pip install -r requirements.txt`

## Utilisation

Double-clic sur `lancer_dashboard.bat` (ou `python server.py`) : le
dashboard s'ouvre sur http://127.0.0.1:8765.

Chaque traduction est créée à côté de son original, avec le code de la
langue à la fin : `livre.pdf` → `livre_fr.pdf`, `images/schema.png` →
`images/schema_fr.png`. Les originaux ne sont jamais modifiés.

Ligne de commande : `python cli.py livre.pdf --cible French` (langue détectée
automatiquement ; `--source German` pour la forcer)
(`--images` : traduire aussi le texte dans les images ; `--test` : sans
Ollama, pour vérifier la mise en page).

## Mode d'emploi

Le bouton « ? » en haut de la page ouvre le mode d'emploi (5 étapes et
conseils, dans la langue de l'interface). Il s'ouvre tout seul au premier
lancement, jusqu'à ce qu'on coche « Ne plus afficher au démarrage ».

## Langue de l'interface

Menu 🌐 en haut à droite : 12 langues (한국어 par défaut, English, 中文,
Español, Français, العربية, Português, Русский, 日本語, Deutsch, Italiano,
Nederlands). Le choix est mémorisé. Les textes sont dans `static/i18n.js` :
pour ajouter une langue, copier le bloc `en`, le traduire et l'ajouter à
`LANGUES_UI`.

## Traduire pendant le scan

Avec la case « Traduire au fur et à mesure du scan », chaque livre part
dans la file dès qu'il est analysé (réglages de l'étape 2) : la traduction
commence sans attendre la fin du scan et continue jusqu'au dernier livre.
Les livres déjà traduits, déjà dans la langue de sortie ou déjà dans la
file ne sont pas envoyés une deuxième fois.

## Langue des livres

Par défaut, « Langue du livre » est sur **Détection automatique** : chaque
livre est traduit depuis sa propre langue, détectée au scan (extrait de
texte, ou OCR d'une page pour les scans et les images). La langue détectée
s'affiche dans la liste et peut être corrigée livre par livre. Choisir une
langue dans le menu (mode manuel) l'applique à tous les livres.

## Comment chaque format est traité

| Format | Méthode |
|---|---|
| PDF avec texte | le texte est effacé bloc par bloc et réécrit au même endroit |
| PDF scanné / illisible | OCR Tesseract, paragraphe recouvert de la couleur du papier |
| EPUB | seul le texte du XHTML change ; images, CSS, table des matières intacts |
| DOCX | paragraphe recomposé avec sa mise en forme (gras, italique, liens, notes) |
| TXT / MD | paragraphe par paragraphe ; code, tableaux et en-tête Markdown protégés |
| Fichiers image | OCR, texte effacé (inpainting OpenCV) et réécrit traduit, même couleur |
| Images DANS les livres | idem, si la case « Traduire aussi le texte dans les images » est cochée |

`cache_traductions.sqlite` garde chaque traduction : après une coupure,
relancer reprend sans retraduire. `cache_scan.sqlite` garde l'analyse de
chaque fichier (type, langue) : un fichier inchangé n'est pas réanalysé au
scan suivant. Les supprimer efface ces mémoires (sans risque).

## Fichiers

- `server.py` : dashboard (FastAPI) · `static/` : page web
- `jobs.py` : file d'attente · `scanner.py` : scan du dossier
- `engines.py` : choix du moteur selon le format
- `pdf_engine.py`, `ocr_engine.py`, `doc_engines.py` : les moteurs
- `image_text.py` : texte dans les images
- `translator.py` : client Ollama · `cache.py` : cache SQLite
