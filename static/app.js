const $ = (id) => document.getElementById(id);
let livres = [];
let listeLangues = [];
let codesLangues = {};
let ocrInfo = null;        // dernière réponse de /api/ocr
let modelesInfo = null;    // dernière réponse de /api/modeles
let ocrLangues = null;
let dernierEtat = null;    // dernière réponse de /api/status
let dossierScanne = "";
let scanEnCours = false;

// ---------- utilitaires ----------
function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { return null; }
}

async function api(url, options = {}) {
  const r = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || t("erreur_http", { n: r.status }));
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function duree(sec) {
  if (!sec || sec < 0) return "";
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m ? t("duree_min", { m, s }) : t("duree_s", { s });
}

// nom d'une langue du serveur (« English ») dans la langue de l'interface
function nomDe(nom) {
  return codesLangues[nom] ? nomLangue(codesLangues[nom]) : nom;
}

// ---------- langue de l'interface ----------
$("langue-ui").innerHTML = LANGUES_UI.map(([c, n]) =>
  `<option value="${c}">${esc(n)}</option>`).join("");
$("langue-ui").value = langueUI;
$("langue-ui").addEventListener("change", () => {
  langueUI = $("langue-ui").value;
  store("ui", langueUI);
  toutAfficher();
});

// réécrit tous les textes de la page dans la langue choisie
function toutAfficher() {
  appliquerI18n();
  remplirLangues();
  afficherPastilles();
  if (livres.length) afficherLivres(); else majBouton();
  if (dernierEtat) afficherJobs(dernierEtat);
  afficherMsg();
}

// ---------- langues et modèles ----------
function remplirLangues() {
  if (!listeLangues.length) return;
  const src = $("source").value || store("source") || "auto";
  const cib = $("cible").value || store("cible") || "French";
  const options = listeLangues.map((l) =>
    `<option value="${l.nom}">${esc(nomLangue(l.code))}</option>`).join("");
  $("cible").innerHTML = options;
  $("source").innerHTML = `<option value="auto">${esc(t("detection_auto"))}</option>` + options;
  $("source").value = src;
  $("cible").value = cib;
}

function afficherPastilles() {
  if (ocrInfo) {
    const o = ocrInfo;
    $("ocr").textContent = o.ok ? t("ocr_pret", { n: o.langues.length }) : t("ocr_indispo");
    $("ocr").className = o.ok ? "pill ok" : "pill err";
    $("ocr").title = o.ok ? o.langues.map(nomDe).join(", ") : tradServeur(o.erreur);
  }
  if (modelesInfo) {
    const m = modelesInfo, pill = $("ollama");
    pill.textContent = m.ok ? t("ollama_ok", { n: m.modeles.length }) : t("ollama_hors");
    pill.className = m.ok ? "pill ok" : "pill err";
    pill.title = m.ok ? "" : tradServeur(m.erreur);
  }
  verifierOcr();
}

async function chargerOptions() {
  const langues = await api("/api/langues");
  langues.forEach((l) => { codesLangues[l.nom] = l.code; });
  listeLangues = langues;
  remplirLangues();

  ocrInfo = await api("/api/ocr");
  ocrLangues = ocrInfo.langues;
  modelesInfo = await api("/api/modeles");
  afficherPastilles();

  const m = modelesInfo;
  const liste = m.modeles.length ? m.modeles : [m.defaut];
  $("modele").innerHTML = liste.map((x) => `<option>${esc(x)}</option>`).join("");
  const pref = store("modele") || m.defaut;
  if (liste.includes(pref)) $("modele").value = pref;
}

const AVEC_OCR = ["scanné", "texte illisible", "mixte", "image", "endommagé"];

// langues qui auront besoin de l'OCR mais ne sont pas installées dans Tesseract
function verifierOcr() {
  if (!ocrLangues) return;
  const besoins = new Set();
  if ($("source").value !== "auto") {
    besoins.add($("source").value);
  } else {
    livres.filter((l) => AVEC_OCR.includes(l.type)).forEach((l) => {
      const lg = langueDe(l);
      if (lg) besoins.add(lg);
    });
  }
  const manquantes = [...besoins].filter((lg) => !ocrLangues.includes(lg));
  $("ocr-msg").textContent = manquantes.length
    ? t("ocr_manquantes", { liste: manquantes.map(nomDe).join(", ") })
    : "";
}
$("source").addEventListener("change", () => { if (livres.length) afficherLivres(); verifierOcr(); });
// changer de langue cible change ce qui est « déjà traduit »
$("cible").addEventListener("change", () => { if (livres.length) afficherLivres(); });

for (const id of ["source", "cible", "modele"]) {
  $(id).addEventListener("change", () => store(id, $(id).value));
}
$("images").checked = store("images") === "1";
$("auto-traduire").checked = store("auto") === "1";
$("auto-traduire").addEventListener("change", () => store("auto", $("auto-traduire").checked ? "1" : "0"));
$("images").addEventListener("change", () => store("images", $("images").checked ? "1" : "0"));

// ---------- explorateur de dossiers ----------
let expChemin = "";

async function explorer(path) {
  const d = await api(`/api/browse?path=${encodeURIComponent(path)}`);
  expChemin = d.path;
  $("exp-chemin").textContent = d.path || t("ordinateur");
  const items = [];
  if (d.parent !== null && d.parent !== undefined) {
    items.push(`<li class="up" data-path="${esc(d.parent)}">${esc(t("dossier_parent"))}</li>`);
  }
  for (const dir of d.dirs) {
    const nom = dir.split(/[\\/]/).filter(Boolean).pop() || dir;
    items.push(`<li data-path="${esc(dir)}" dir="ltr">📁 ${esc(nom)}</li>`);
  }
  if (!d.dirs.length) items.push(`<li class="up">${esc(t("aucun_sous_dossier"))}</li>`);
  $("exp-liste").innerHTML = items.join("");
  $("exp-choisir").disabled = !d.path;
}

$("exp-liste").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-path]");
  if (li) explorer(li.dataset.path).catch((err) => alertMsg(tradServeur(err.message)));
});
$("btn-parcourir").addEventListener("click", async () => {
  await explorer($("dossier").value.trim()).catch(() => explorer(""));
  $("explorateur").showModal();
});
$("exp-annuler").addEventListener("click", () => $("explorateur").close());
$("exp-choisir").addEventListener("click", () => {
  $("dossier").value = expChemin;
  $("explorateur").close();
  scanner();
});

// ---------- message sous le dossier ----------
// Le message est gardé sous forme de fonction : il se réécrit tout seul
// quand on change la langue de l'interface.
let msgCourant = () => "";
function alertMsg(msg) {
  msgCourant = typeof msg === "function" ? msg : () => msg;
  afficherMsg();
}
function afficherMsg() { $("scan-msg").textContent = msgCourant(); }
const erreurMsg = (e) => alertMsg(() => tradServeur(e.message));

// ---------- liste des livres ----------
const TYPES = {
  "texte": "type_texte",
  "scanné": "type_scanne",
  "texte illisible": "type_illisible",
  "mixte": "type_mixte",
  "chapitres": "type_chapitres",
  "paragraphes": "type_paragraphes",
  "texte brut": "type_texte",
  "image": "type_image",
  "GIF animé": "type_gif",
  "endommagé": "type_endommage",
  "vide": "type_vide",
  "feuilles": "type_feuilles",
  "diapositives": "type_diapos",
  "page web": "type_web",
  "sous-titres": "type_srt",
  "texte enrichi": "type_rtf",
  "programme requis": "type_programme",
};
// programme qui convertit les anciens formats (HWP, DOC, XLS, PPT)
const OUTILS = { hangul: "한글", office: "MS Office", libreoffice: "LibreOffice" };
const LIBELLE_FORMAT = { HWPX: "type_hwpx", ODF: "type_odf" };

function badge(l) {
  if (l.type && l.type.startsWith("erreur")) {
    return `<span class="badge err" title="${esc(l.type)}">${esc(t("type_erreur"))}</span>`;
  }
  if (l.type === "programme requis") return `<span class="badge off">${esc(t("type_programme"))}</span>`;
  if (!l.supporte) return `<span class="badge off">${esc(t("bientot"))}</span>`;
  const cls = AVEC_OCR.includes(l.type) || l.type === "conversion" ? "scan" : "texte";
  const label = l.type === "conversion" ? t("type_conversion", { outil: OUTILS[l.outil] || l.outil })
    : LIBELLE_FORMAT[l.format] ? t(LIBELLE_FORMAT[l.format])
    : TYPES[l.type] ? t(TYPES[l.type]) : l.type;
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

// langue utilisée pour traduire ce livre
function langueDe(l) {
  return $("source").value === "auto" ? (l.choix || l.langue) : $("source").value;
}

// livre déjà écrit dans la langue de sortie
function dansLaCible(l) {
  return langueDe(l) === $("cible").value;
}

function celluleLangue(l, i) {
  if (!l.supporte) return "–";
  if ($("source").value !== "auto") {
    return `<span class="muted">${esc(nomDe($("source").value))}</span>`;
  }
  const actuelle = l.choix || l.langue || "";
  const opts = listeLangues.map((x) =>
    `<option value="${x.nom}" ${x.nom === actuelle ? "selected" : ""}>${esc(nomLangue(x.code))}</option>`).join("");
  return `<select class="langue-livre" data-i="${i}" title="${esc(t(l.langue ? "langue_detectee" : "langue_non_detectee"))}">
    <option value="" ${actuelle ? "" : "selected"}>${esc(t("a_choisir"))}</option>${opts}</select>`;
}

function aCocher(l) {
  return l.supporte && !dejaTraduit(l) && !dansLaCible(l) && !dansLaFile(l)
    && !!langueDe(l);
}

function dejaTraduit(l) {
  return (l.traductions || []).includes(codesLangues[$("cible").value]);
}

// statut d'un livre : [classe, texte, infobulle]
function statut(l) {
  if (l.type === "programme requis") {
    return ["w", t("st_programme", { prog: l.format === "HWP" ? "한글" : "Office / LibreOffice" }), ""];
  }
  if (!l.supporte) return ["m", t("st_non_pris"), ""];
  if (dansLaFile(l)) return ["m", "⏳ " + t("st_dans_file"), ""];
  if (dejaTraduit(l)) return ["d", "✓ " + t("st_deja_traduit"), t("st_deja_traduit_titre")];
  if (dansLaCible(l)) return ["m", "– " + t("st_deja_en", { langue: nomDe($("cible").value) }), ""];
  if (!langueDe(l)) return ["w", "? " + t("st_langue_inconnue"), ""];
  return ["a", "● " + t("st_a_traduire"), ""];
}

const morceaux = (l) => l.relpath.split(/[\\/]/);
const nomFichier = (l) => morceaux(l).pop();

// livres regroupés par dossier : [[dossier, [indices]], …], racine d'abord
function groupes() {
  const g = new Map();
  livres.forEach((l, i) => {
    const k = morceaux(l).slice(0, -1).join(" / ");
    if (!g.has(k)) g.set(k, []);
    g.get(k).push(i);
  });
  return [...g.keys()]
    .sort((a, b) => (a === "" ? -1 : b === "" ? 1 : a.localeCompare(b)))
    .map((k) => [k, g.get(k).sort((a, b) =>
      nomFichier(livres[a]).localeCompare(nomFichier(livres[b])))]);
}

function ligneLivre(i, g) {
  const l = livres[i];
  const [cls, txt, titre] = statut(l);
  return `
    <tr class="${cls === "a" ? "" : "off"}">
      <td><input type="checkbox" data-i="${i}" data-g="${g}" ${l.supporte ? "" : "disabled"}
           ${aCocher(l) ? "checked" : ""}></td>
      <td class="livre" title="${esc(l.relpath)}"><span class="fic" dir="auto">${esc(nomFichier(l))}</span></td>
      <td class="c"><span class="fmt">${esc(nomFichier(l).split(".").pop().toUpperCase())}</span></td>
      <td>${celluleLangue(l, i)}</td>
      <td class="num">${l.pages ?? "–"}</td>
      <td>${badge(l)}</td>
      <td><span class="st ${cls}" title="${esc(titre)}">${esc(txt)}</span></td>
    </tr>`;
}

function afficherLivres() {
  const body = $("livres");
  if (!livres.length) {
    body.innerHTML = `<tr><td colspan="7" class="muted">${esc(t("aucun_fichier"))}</td></tr>`;
  } else {
    const gs = groupes();
    const racine = dossierScanne.split(/[\\/]/).filter(Boolean).pop() || dossierScanne;
    body.innerHTML = gs.map(([dossier, indices], g) => {
      // un seul dossier : pas besoin de ligne de titre
      const tete = gs.length < 2 ? "" : `
        <tr class="dossier">
          <td><input type="checkbox" class="chk-dossier" data-dossier="${g}"></td>
          <td colspan="6"><span dir="auto">📁 ${esc(dossier || racine)}</span>
            <span class="n">· ${esc(t("n_fichiers", { n: indices.length }))}</span></td>
        </tr>`;
      return tete + indices.map((i) => ligneLivre(i, g)).join("");
    }).join("");
  }
  const n = (f) => livres.filter(f).length;
  const chip = (k, v) => (v ? `<span class="chip">${esc(t(k, { n: v }))}</span>` : "");
  $("resume").innerHTML = [
    `<span class="chip">${esc(t("n_fichiers", { n: livres.length }))}</span>`,
    `<span class="chip">${esc(t("n_pris", { n: n((l) => l.supporte) }))}</span>`,
    chip("n_ocr", n((l) => l.type === "scanné" || l.type === "texte illisible")),
    chip("n_non_pris", n((l) => !l.supporte)),
    chip("n_deja", n(dejaTraduit)),
    chip("n_inconnue", n((l) => l.supporte && !langueDe(l))),
  ].join("");
  verifierOcr();
  majCases();
}

function selection() {
  return [...document.querySelectorAll("#livres input[data-i]:checked")]
    .map((c) => livres[+c.dataset.i].path);
}

// cases des dossiers et « tout » : cochées si tous leurs livres le sont
function majCases() {
  const etat = (cases) => {
    const actives = [...cases].filter((c) => !c.disabled);
    const n = actives.filter((c) => c.checked).length;
    return { checked: !!actives.length && n === actives.length, indeterminate: n > 0 && n < actives.length };
  };
  document.querySelectorAll("#livres .chk-dossier").forEach((c) => {
    Object.assign(c, etat(document.querySelectorAll(`#livres input[data-g="${c.dataset.dossier}"]`)));
  });
  Object.assign($("tout"), etat(document.querySelectorAll("#livres input[data-i]")));
  majBouton();
}

function majBouton() {
  const n = selection().length;
  $("btn-traduire").disabled = n === 0 || scanEnCours;
  $("btn-traduire").textContent = n ? t("btn_traduire_n", { n }) : t("btn_traduire");
}

$("livres").addEventListener("change", (e) => {
  const sel = e.target.closest("select.langue-livre");
  if (sel) {                       // langue corrigée à la main pour ce livre
    const l = livres[+sel.dataset.i];
    l.choix = sel.value || null;
    afficherLivres();
    return;
  }
  const d = e.target.closest(".chk-dossier");
  if (d) {                         // case du dossier : tous ses livres
    document.querySelectorAll(`#livres input[data-g="${d.dataset.dossier}"]:not(:disabled)`)
      .forEach((c) => (c.checked = d.checked));
  }
  majCases();
});
$("tout").addEventListener("change", (e) => {
  document.querySelectorAll("#livres input[type=checkbox]:not(:disabled)")
    .forEach((c) => (c.checked = e.target.checked));
  majCases();
});

// ---------- scan ----------

async function scanner() {
  const dossier = $("dossier").value.trim();
  if (!dossier) return alertMsg(() => t("indique_dossier"));
  $("btn-scanner").disabled = true;
  scanEnCours = true;
  majBouton();
  try {
    await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({
        dossier,
        recursif: $("recursif").checked,
        auto_traduire: $("auto-traduire").checked,
        source: $("source").value,
        cible: $("cible").value,
        modele: $("modele").value,
        images: $("images").checked,
      }),
    });
    store("dossier", dossier);
    dossierScanne = dossier;
    alertMsg(() => t("scan_en_cours"));
    let vus = -1;
    for (;;) {                     // suivi de la progression
      await new Promise((r) => setTimeout(r, 500));
      const d = await api("/api/scan");
      if (d.livres.length !== vus) {
        vus = d.livres.length;
        livres = d.livres;
        afficherLivres();
      }
      if (d.etat === "en cours") {
        alertMsg(() => (d.total ? t("scan_progression", { fait: d.fait, total: d.total }) : t("scan_en_cours")) +
          (d.envoyes ? t("scan_envoyes", { n: d.envoyes }) : ""));
        rafraichir();
        continue;
      }
      if (d.etat === "erreur") throw new Error(d.erreur || "Erreur pendant le scan");
      livres = d.livres;
      scanEnCours = false;
      afficherLivres();
      alertMsg(() => t("scan_fini", { n: livres.length }) +
        (d.envoyes ? t("scan_fini_envoyes", { n: d.envoyes }) : ""));
      break;
    }
  } catch (e) {
    erreurMsg(e);
  } finally {
    scanEnCours = false;
    $("btn-scanner").disabled = false;
    majBouton();
  }
}
$("btn-scanner").addEventListener("click", scanner);
$("dossier").addEventListener("keydown", (e) => { if (e.key === "Enter") scanner(); });

// ---------- lancer ----------
$("btn-traduire").addEventListener("click", async () => {
  try {
    const d = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        chemins: selection(),
        source: $("source").value,
        langues: Object.fromEntries(livres.filter((l) => l.choix).map((l) => [l.path, l.choix])),
        cible: $("cible").value,
        modele: $("modele").value,
        images: $("images").checked,
      }),
    });
    alertMsg(() => {
      const ignores = [
        d.langue_inconnue ? t("ign_inconnue", { n: d.langue_inconnue }) : "",
        d.deja_dans_la_langue ? t("ign_langue", { n: d.deja_dans_la_langue }) : "",
        d.deja_en_file ? t("ign_file", { n: d.deja_en_file }) : "",
      ].filter(Boolean).join(", ");
      return t("ajoutes", { n: d.ajoutes }) + (ignores ? t("ignores", { liste: ignores }) : "");
    });
    rafraichir();
  } catch (e) {
    erreurMsg(e);
  }
});

// ---------- file d'attente ----------
let enPause = false;

// livres dans la file (en attente / en cours / en pause), par langue de sortie
let enFile = new Set();

// À chaque rafraîchissement de la file :
// - un livre traduit passe en « déjà traduit » (même s'il a été traduit
//   pendant le scan, avant d'apparaître dans la liste) ;
// - un livre déjà dans la file est signalé et décoché.
function synchroniserListe(jobs) {
  let change = false;
  for (const j of jobs) {
    if (j.etat !== "terminé") continue;
    const l = livres.find((x) => x.path === j.path);
    const code = codesLangues[j.cible];
    if (l && code && !(l.traductions || []).includes(code)) {
      l.traductions = [...(l.traductions || []), code];
      change = true;
    }
  }
  const actifs = new Set(jobs
    .filter((j) => ["en attente", "en cours", "en pause"].includes(j.etat))
    .map((j) => `${j.path}|${j.cible}`));
  if ([...actifs].sort().join("\n") !== [...enFile].sort().join("\n")) {
    enFile = actifs;
    change = true;
  }
  if (change && livres.length) afficherLivres();
}

function dansLaFile(l) {
  return enFile.has(`${l.path}|${$("cible").value}`);
}

const ETATS = {
  "en attente": "etat_en_attente", "en cours": "etat_en_cours", "en pause": "etat_en_pause",
  "terminé": "etat_termine", "erreur": "etat_erreur", "annulé": "etat_annule",
};

function afficherJobs(state) {
  dernierEtat = state;
  synchroniserListe(state.jobs);
  enPause = state.pause;
  $("btn-pause").textContent = t(enPause ? "btn_reprendre" : "btn_pause");
  const box = $("jobs");
  if (!state.jobs.length) {
    box.innerHTML = `<p class="muted">${esc(t("file_vide"))}</p>`;
    return;
  }
  const now = Date.now() / 1000;
  box.innerHTML = state.jobs.map((j) => {
    const pct = j.etat === "terminé" ? 100 : (j.pages ? Math.round((j.page / j.pages) * 100) : 0);
    let temps = "";
    if (j.etat === "en cours" && j.debut && j.page > 0) {
      const ecoule = now - j.debut;
      const reste = (ecoule / j.page) * (j.pages - j.page);
      temps = ` · ${t("reste", { d: duree(reste) })}`;
    } else if (j.etat === "terminé" && j.fin) {
      temps = ` · ${duree(j.fin - j.debut)}`;
    }
    const actions = [];
    if (["en attente", "en cours", "en pause"].includes(j.etat)) {
      actions.push(`<button class="small secondary" data-annuler="${j.id}">${esc(t("btn_annuler"))}</button>`);
    }
    if (["erreur", "annulé"].includes(j.etat)) {
      actions.push(`<button class="small secondary" data-reessayer="${j.id}">${esc(t("btn_reessayer"))}</button>`);
    }
    if (j.sortie) {
      actions.push(`<button class="small secondary" data-ouvrir="${esc(j.sortie)}" title="${esc(j.sortie)}">${esc(t("btn_ouvrir"))}</button>`);
    }
    return `
      <div class="job ${esc(j.etat)}">
        <div class="job-top">
          <span class="job-name" dir="auto">${esc(j.relpath)}</span>
          <span class="row">${actions.join("")}</span>
        </div>
        <div class="bar"><div style="width:${pct}%"></div></div>
        <div class="job-meta">
          <span class="etat-${esc(j.etat)}">${esc(ETATS[j.etat] ? t(ETATS[j.etat]) : j.etat)}</span>
          · ${esc(t("page_n", { i: j.page, n: j.pages || "?" }))} · ${esc(nomDe(j.source))} → ${esc(nomDe(j.cible))}${esc(temps)}
          ${j.info ? `<br>${esc(tradServeur(j.info))}` : ""}
          ${j.sortie ? `<br><code dir="ltr">${esc(j.sortie)}</code>` : ""}
        </div>
      </div>`;
  }).join("");
}

async function rafraichir() {
  try { afficherJobs(await api("/api/status")); } catch (e) { /* serveur arrêté */ }
}

$("jobs").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-annuler]");
  const o = e.target.closest("[data-ouvrir]");
  const r = e.target.closest("[data-reessayer]");
  if (a) await api(`/api/annuler/${a.dataset.annuler}`, { method: "POST" });
  if (r) await api(`/api/reessayer/${r.dataset.reessayer}`, { method: "POST" });
  if (o) await api(`/api/ouvrir?chemin=${encodeURIComponent(o.dataset.ouvrir)}`, { method: "POST" }).catch(() => {});
  rafraichir();
});
$("btn-pause").addEventListener("click", async () => {
  await api(enPause ? "/api/reprendre" : "/api/pause", { method: "POST" });
  rafraichir();
});
$("btn-nettoyer").addEventListener("click", async () => {
  await api("/api/nettoyer", { method: "POST" });
  rafraichir();
});

// ---------- mode d'emploi ----------
// s'ouvre tout seul au démarrage, sauf si « Ne plus afficher » est coché
$("btn-aide").addEventListener("click", () => $("aide").showModal());
$("aide-fermer").addEventListener("click", () => $("aide").close());
$("aide").addEventListener("click", (e) => { if (e.target === $("aide")) $("aide").close(); });
$("aide-masquer").checked = store("aide_masquee") === "1";
$("aide-masquer").addEventListener("change", () =>
  store("aide_masquee", $("aide-masquer").checked ? "1" : "0"));

// ---------- démarrage ----------
appliquerI18n();
if (store("aide_masquee") !== "1") $("aide").showModal();
$("dossier").value = store("dossier") || "";
chargerOptions().catch(erreurMsg);
rafraichir();
setInterval(rafraichir, 1000);
