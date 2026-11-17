"use strict";
/* Penguin Inc., client demo: mostra il tavolo (la rete vera, costruita con Mininet) e lo comanda attraverso l'API del server.
 *
 * I testi e le impostazioni non sono qui: arrivano da GET /api/ui, che li legge dal pacchetto di gioco
 * (frontend/demo/gioco/interfaccia.yaml e impostazioni.yaml). Un testo si usa con t("zona.chiave", {valori}).
 */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ───────────── testi e impostazioni dal pacchetto di gioco ───────────── */
let TESTI = {};             // interfaccia.yaml
let CFG = null;             // {dispositivi, interfaccia, punteggio} da impostazioni.yaml
let COLLAUDO = false;       // server avviato con PNA_COLLAUDO=1: cheat per il debug (scripts/storia.js)

/* Il testo «zona.chiave» con i segnaposto {nome} sostituiti. Il testo del YAML può contenere HTML (lo scrive
 * il team); i valori NO: chi lo usa dentro innerHTML passa valori già ripuliti con esc(). Se la chiave manca
 * mostra la chiave stessa, così chi modifica il YAML vede subito cosa manca. */
function t(chiave, valori = {}) {
  const v = chiave.split(".").reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), TESTI);
  if (typeof v !== "string") return chiave;
  return v.replace(/\{(\w+)\}/g, (m, k) => (k in valori ? valori[k] : m));
}
/* Come t(), per i punti dove il risultato va in innerHTML: i valori vengono ripuliti qui. */
function th(chiave, valori = {}) {
  const puliti = {};
  for (const [k, v] of Object.entries(valori)) puliti[k] = esc(v);
  return t(chiave, puliti);
}
/* Per textContent, title e le domande di conferma: niente HTML, quindi i tag del testo vengono tolti. */
const tt = (chiave, valori) => t(chiave, valori).replace(/<br\s*\/?>/g, "\n").replace(/<[^>]+>/g, "");

const dispositivo = (kind) => (CFG && CFG.dispositivi[kind]) || { nome: kind, icona: "❔", articolo: kind };
const opz = (k) => CFG.interfaccia[k];

function applicaTestiStatici() {
  $$("[data-t]").forEach((el) => { el.innerHTML = t(el.dataset.t); });
  $$("[data-t-title]").forEach((el) => { el.title = tt(el.dataset.tTitle); });
  $$("[data-t-placeholder]").forEach((el) => { el.placeholder = tt(el.dataset.tPlaceholder); });
  document.title = (TESTI.generale && TESTI.generale.titolo_scheda) || tt("generale.nome");
  // pulsanti «Aggiungi»: uno per ogni dispositivo di impostazioni.yaml
  const box = $("#addTools");
  $$(".tool[data-add]", box).forEach((b) => b.remove());
  for (const [kind, d] of Object.entries(CFG.dispositivi)) {
    const b = document.createElement("button");
    b.className = "tool"; b.dataset.add = kind; b.title = d.nome;
    b.innerHTML = `${esc(d.icona)} <span>${esc(d.nome)}</span>`;
    b.addEventListener("click", () => setTool("select", kind));
    box.appendChild(b);
  }
}

function mostraErroreContenuti(errore) {
  const b = $("#contentError");
  b.classList.toggle("hidden", !errore);
  if (errore) b.textContent = tt("generale.errore_contenuti", { errore });
}

/* ───────────── stato del client ───────────── */
let S = null;               // ultimo Stato ricevuto dal server
let sel = null;             // dispositivo selezionato
let selPort = null;         // porta selezionata nel pannello
let tool = "select";        // select | cable | erase
let addKind = null;         // tipo di dispositivo da piazzare
let pickA = null;           // primo dispositivo di un cavo
let drag = null;            // {name, dx, dy, moved, x, y}
let view = null;            // viewBox dell'SVG {x, y, w, h}
let dirty = false;          // il pannello ha campi modificati e non applicati
let inspSig = "", gameSig = "";
let showAllPorts = false;
let showLevels = false;
const consoleOut = {};      // uscita della console per dispositivo
const auditOut = {};        // ultimo controllo di sicurezza per dispositivo
const usersOut = {};        // utenti locali letti per dispositivo (senza password)
const servOut = {};         // servizi esposti letti per server
const scanOut = {};         // ultima scansione delle porte, per dispositivo di partenza
const sniffOut = {};        // ultima cattura dello sniffer, per dispositivo
const limOut = {};          // limiti di velocità letti per router
let fwEd = null;            // regole del firewall in modifica: {fw, rules}
let fwPort = null;          // porta del firewall selezionata
/* Come si mostra un dispositivo: il suo tipo, oppure il ruolo che gli dà il gioco (un server «cloud»). */
const vista = (d) => (S && S.topology.roles && S.topology.roles[d.name]) || d.kind;
let aclEd = null;           // ACL in modifica (vedi aclNew/aclEdit): {router, acl, type, port, dir, rules, orig}
const reported = new Set(); // lavori di cui si è già mostrato il risultato
let pollTimer = null;
let ultimiNomi = [];        // i dispositivi dell'ultima lettura: per ricentrare la vista quando la rete cambia tutta

/* ───────────── API ───────────── */
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opt); } catch { toast(tt("generale.server_irraggiungibile"), "bad"); return null; }
  if (r.status === 401) { showLogin(); return null; }
  let data = null;
  try { data = await r.json(); } catch { /* vuoto */ }
  if (!r.ok || !data || data.ok === false) {
    toast((data && data.error) || tt("generale.errore", { codice: r.status }), r.status === 409 ? "hint" : "bad");
    return null;
  }
  return data;
}

async function act(path, body, okMsg) {
  // Se la rete è occupata il server mette il comando in coda: lo diciamo, così l'attesa non sembra un blocco.
  if (S && (S.busy || (S.job && !S.job.done))) toast(tt("generale.in_coda"), "info", 4000);
  setBusy(true);
  const d = await api(path, body);
  setBusy(false);
  if (!d) return null;
  if (d.topology) applyState(d);
  if (d.toast) toast(d.toast, "hint", 9000);
  else if (okMsg) toast(okMsg, "ok");
  return d;
}

async function job(path, body) {
  const d = await api(path, body);
  if (d && d.job) { S.job = d.job; renderJob(); schedulePoll(400); }
}

function setBusy(b) { $("#netStatus").classList.toggle("busy", b); }

/* ───────────── avvio, login e aggiornamento ───────────── */
async function caricaInterfaccia() {
  let d = null;
  try { d = await (await fetch("/api/ui")).json(); } catch { /* sotto */ }
  // Unico testo scritto qui: serve proprio quando i testi del YAML non si possono caricare.
  if (!d || !d.ok) { $("#loading").textContent = "Server non raggiungibile"; return false; }
  TESTI = d.testi || {};
  CFG = d.impostazioni;
  COLLAUDO = !!d.collaudo;
  applicaTestiStatici();
  $("#loading").textContent = tt("generale.caricamento");
  mostraErroreContenuti(d.errore_contenuti);
  return true;
}

function showLogin() {
  $("#loading").classList.add("hidden");
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#code").focus();
  clearTimeout(pollTimer);
}

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginErr").textContent = "";
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: $("#code").value }) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { $("#loginErr").textContent = d.error || tt("generale.errore", { codice: r.status }); return; }
  start();
});

async function start() {
  const d = await api("/api/state");
  if (!d) return;
  $("#loading").classList.add("hidden");
  $("#login").classList.add("hidden");
  applyState(d);
  // Con la storyline (scripts/storia.js + gioco/storia.yaml) si parte dall'ufficio di Penguin Inc;
  // senza, dal tavolo come prima.
  if (!(typeof Storia !== "undefined" && Storia.avvia())) $("#app").classList.remove("hidden");
  fit();
  schedulePoll();
}

function schedulePoll(ms) {
  clearTimeout(pollTimer);
  const running = S && S.job && !S.job.done;
  pollTimer = setTimeout(poll, ms ?? (running ? opz("aggiornamento_durante_lavoro_ms") : opz("aggiornamento_ms")));
}

async function poll() {
  if (!drag) {
    const d = await api("/api/state");
    if (d) applyState(d);
    else if (!$("#login").classList.contains("hidden")) return;
  }
  schedulePoll();
}

/* ───────────── Stato → interfaccia ───────────── */
function applyState(d) {
  const firstLoad = !S;
  if (firstLoad && d.job && d.job.done) reported.add(d.job.id);  // non ripetere un vecchio risultato al ricaricamento
  S = d;
  if (sel && !dev(sel)) { sel = null; selPort = null; }
  mostraErroreContenuti(d.errore_contenuti);
  const st = $("#netStatus");
  st.classList.toggle("ok", d.rete.ok);
  st.title = tt("generale.stato_rete", { stato: d.rete.status });
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.mode === d.mode));
  if (storiaAttiva()) Storia.aggiorna();
  renderGame();
  if (!drag) renderCanvas();
  renderInspector();
  renderLog();
  renderJob();
  // la rete è cambiata tutta insieme (una topologia caricata, un livello, il tavolo svuotato): si ricentra la vista
  const nomi = d.topology.devices.map((x) => x.name);
  const cambiati = nomi.filter((n) => !ultimiNomi.includes(n)).length + ultimiNomi.filter((n) => !nomi.includes(n)).length;
  if (firstLoad || (cambiati > 1 && !drag)) fit();
  ultimiNomi = nomi;
}

const dev = (name) => S && S.topology.devices.find((x) => x.name === name);
const hostIp = (d) => (d.ports || []).find((p) => p.ip && p.ip !== "0.0.0.0");
const shortPort = (n) => n.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa").replace("Serial", "Se").replace(/^Ethernet/, "Eth");
const maskBits = (m) => (m || "").split(".").reduce((a, o) => a + ((+o >>> 0).toString(2).match(/1/g) || []).length, 0);

/* ───────────── pannello di gioco ───────────── */
const storiaAttiva = () => typeof Storia !== "undefined" && Storia.attiva();

function renderGame() {
  const g = S.mode === "academy" ? S.academy : S.rescue;
  const sig = JSON.stringify([S.mode, g, showLevels, S.job && !S.job.done, storiaAttiva() && Storia.firma()]);
  if (sig === gameSig) return;
  gameSig = sig;
  // In modalità storia la giornata può aggiungere un riquadro sopra il pannello (prima) o sostituirlo (html).
  const st = storiaAttiva() ? Storia.pannello() : null;
  $("#gamePanel").innerHTML = st && st.html ? st.html
    : ((st && st.prima) || "") + (S.mode === "academy" ? academyHtml(S.academy) : rescueHtml(S.rescue));
}

/* Il «Manuale» della challenge in corso: la teoria in pillole, in una finestra a parte. */
function apriManuale() {
  const m = S && S.academy && S.academy.current;
  if (!m || $(".dialog.manuale-finestra")) return;
  const fondo = document.createElement("div");
  fondo.className = "dialog-back";
  fondo.innerHTML = `<div class="dialog card manuale-finestra" role="dialog" aria-modal="true">
    <h3>${t("accademia.manuale_titolo")}</h3><p class="muted">${esc(m.title)}</p>
    <dl>${m.theory.map(([c, d]) => `<dt>${esc(c)}</dt><dd>${esc(d)}</dd>`).join("")}</dl>
    <div class="row end"><button type="button" class="btn primary">${t("accademia.chiudi")}</button></div></div>`;
  const chiudi = () => { fondo.remove(); document.removeEventListener("keydown", esci); };
  const esci = (e) => { if (e.key === "Escape") chiudi(); };
  fondo.querySelector("button").addEventListener("click", chiudi);
  fondo.addEventListener("pointerdown", (e) => { if (e.target === fondo) chiudi(); });
  document.addEventListener("keydown", esci);
  document.body.appendChild(fondo);
  fondo.querySelector("button").focus();
}

function academyHtml(a) {
  const busy = S.job && !S.job.done;
  // con la storia: solo le missioni della giornata, e niente «Ricomincia» (si ricomincia dall'ufficio)
  const delGiorno = storiaAttiva() ? Storia.missioni() : null;
  if (delGiorno && a.current && delGiorno.includes(a.current.id)) {
    a = { ...a, missions: a.missions.filter((m) => delGiorno.includes(m.id)) };
  }
  const strip = `<div class="mission-strip">${a.missions.map((m, i) =>
    `<span class="${m.done ? "done" : a.current && a.current.id === m.id ? "cur" : ""}" title="${esc(m.title)}">${m.done ? "✓" : i + 1}</span>`).join("")}</div>`;
  if (a.finished) {
    return `<section class="card">${strip}<h2>${t("accademia.finita_titolo")}</h2>
      <p class="story">${t("accademia.finita_testo")}</p>
      <div class="row"><button class="btn primary" onclick="setMode('rescue')">${t("accademia.vai_al_soccorso")}</button>
      <button class="btn" onclick="academyReset()">${t("accademia.ricomincia_da_zero")}</button></div></section>`;
  }
  const m = a.current;
  const idx = a.missions.findIndex((x) => x.id === m.id);
  const firstOpen = m.steps.findIndex((s) => !s.ok);
  // La guida passo passo (con gli aiuti e «cosa manca») c'è solo nel tutorial. Fuori dal tutorial gli obiettivi
  // si scoprono man mano: quelli già raggiunti e quello da fare adesso (quando restano solo le prove sul traffico,
  // tutte quelle che «Verifica» farà), più i passi interattivi (domanda, viaggio del ping) quando si aprono.
  const guida = m.tutorial;
  const soloTraffico = firstOpen !== -1 && m.steps.slice(firstOpen).every((s) => s.ping || s.kind === "domanda" || s.kind === "osserva");
  const steps = m.steps.map((s, i) => {
    const now = i === firstOpen;
    // i passi interattivi (domanda, viaggio del ping) si giocano appena sono «aperti», anche dopo un passo di traffico
    const gioca = !s.ok && s.open && (s.kind === "domanda" || s.kind === "osserva");
    let extra = "";
    if (gioca && s.kind === "domanda") {
      extra = `<div class="domanda"><b>${esc(s.question.text)}</b>${s.question.choices.map((c, j) =>
        `<button class="btn small" ${busy ? "disabled" : ""} onclick="academyAnswer(${i}, ${j})">${esc(c)}</button>`).join("")}</div>`;
    } else if (gioca && s.kind === "osserva") {
      extra = `<div class="row"><button class="btn small" ${busy ? "disabled" : ""} onclick="academyObserve(${i})">${t("accademia.osserva")}</button></div>`;
    }
    if (!guida && !gioca && !s.ok && !now && !(soloTraffico && s.ping)) return "";
    const adesso = now || gioca || (!guida && soloTraffico && s.ping);
    return `<li class="${s.ok ? "ok" : adesso ? "now" : ""}"><span class="ic">${s.ok ? "✅" : s.ping ? "📡" : adesso ? "👉" : "⚪"}</span>
      <span class="t">${esc(s.text)}</span>
      ${(now || gioca) && guida && s.hint ? `<span class="h">${esc(s.hint)}</span>` : ""}
      ${now && guida && s.msg && !s.ping && !gioca ? `<span class="m">${esc(s.msg)}</span>` : ""}${extra}</li>`;
  }).join("");
  const ready = firstOpen === -1 || m.steps[firstOpen].ping;
  const fatti = m.steps.filter((s) => s.ok).length;
  const progressi = guida ? "" : `<div class="progressi" title="${esc(tt("accademia.progressi", { fatti, totale: m.steps.length }))}">
    <span>${th("accademia.progressi", { fatti, totale: m.steps.length })}</span>
    <i style="--quanto:${Math.round((100 * fatti) / Math.max(1, m.steps.length))}%"></i></div>`;
  // i consigli del collega senior si chiedono a Jody, in ufficio: qui solo il promemoria di quelli già avuti
  const ricevuti = consigliRicevuti(m.id), tips = m.tips || [];
  const consigli = tips.length ? `<div class="consigli">
    ${tips.slice(0, ricevuti).map((c, k) => `<div class="consiglio">💡 <b>${k + 1}.</b> ${esc(c)}</div>`).join("")}
    <span class="muted">${ricevuti < tips.length ? th("accademia.consigli_da_jody", { numero: ricevuti, totale: tips.length })
      : t("accademia.consigli_finiti")}</span></div>` : "";
  return `<section class="card">${strip}
    <div class="missione-testa">
      <div><h4>${th("accademia.missione", { numero: idx + 1, totale: a.missions.length })}</h4>
      <h2>${esc(m.title)}</h2></div>
      ${m.theory.length ? `<button class="btn small manuale" data-guida="manuale" onclick="apriManuale()" title="${esc(tt("accademia.manuale_aiuto"))}">${t("accademia.manuale")}</button>` : ""}
    </div>
    <p class="story">${esc(m.story)}</p>
    ${consigli}
    ${progressi}
    ${steps.trim() ? `<ol class="steps">${steps}</ol>` : ""}
    <div class="row">
      <button class="btn primary" data-guida="verifica" ${busy ? "disabled" : ""} onclick="academyCheck()">${ready ? t("accademia.verifica_ping") : t("accademia.verifica")}</button>
      ${m.has_network ? `<button class="btn small" ${busy ? "disabled" : ""} onclick="academyRestart()" title="${esc(tt("accademia.ricomincia_challenge_aiuto"))}">${t("accademia.ricomincia_challenge")}</button>` : ""}
      ${delGiorno ? "" : `<button class="btn small" ${busy ? "disabled" : ""} onclick="academyReset()" title="${esc(tt("accademia.ricomincia_aiuto"))}">${t("accademia.ricomincia")}</button>`}
    </div></section>`;
}

function rescueHtml(r) {
  const busy = S.job && !S.job.done;
  const c = r.current;
  const playing = c && ["playing", "won"].includes(c.status) && !showLevels;
  if (!playing) {
    const err = c && c.status === "error" ? `<div class="banner bad">${esc(c.message)}</div>` : "";
    return `<section class="card"><h2>${t("soccorso.titolo")}</h2>
      <p class="story">${t("soccorso.intro")}</p>
      ${err}
      <div class="levels">${r.levels.map((l) => `<button class="level" ${busy ? "disabled" : ""} onclick="rescueStart(${l.n})">
        <span class="n">${l.n}</span><span><b>${esc(l.title)}</b><br><span class="best">${th("soccorso.guasti_del_livello", { numero: l.faults })}</span></span><span>▶</span></button>`).join("")}</div>
      ${c && showLevels ? `<p><button class="btn small" onclick="showLevels=false;gameSig='';renderGame()">${th("soccorso.torna_al_livello", { numero: c.n })}</button></p>` : ""}
      <p class="muted" style="font-size:12px;margin-top:10px">${t("soccorso.avviso_sostituzione")}</p></section>`;
  }
  const faults = c.faults.map((f, i) => `<li class="${f.solved ? "solved" : ""}">
      <b>${f.solved ? "✅" : f.surrendered ? "🏳️" : "❓"} ${th("soccorso.guasto", { numero: i + 1 })}</b><br>
      ${f.title ? esc(f.title) : `<span class="muted">${th("soccorso.sintomo", { sintomo: f.symptom })}</span>`}
      ${f.hints.map((h, k) => `<div class="hint">💡 ${k + 1}. ${esc(h)}</div>`).join("")}
      ${f.solution ? `<div class="sol">${th("soccorso.soluzione", { soluzione: f.solution })}</div>` : ""}</li>`).join("");
  const won = c.status === "won";
  return `<section class="card">
    <h4>${th("soccorso.livello", { numero: c.n })}</h4><h2>${esc(c.title)}</h2>
    <div class="ticket">🎫 ${esc(c.ticket)}</div>
    <ul class="faults">${faults}</ul>
    ${c.message ? `<div class="banner ${won ? "ok" : "info"}">${esc(c.message)}</div>` : ""}
    ${won && c.lesson ? `<p class="story">📘 ${esc(c.lesson)}</p>` : ""}
    <div class="row">
      <button class="btn primary" ${busy ? "disabled" : ""} onclick="rescueCheck()">${t("soccorso.verifica")}</button>
      ${won ? "" : `<button class="btn small" ${busy ? "disabled" : ""} onclick="rescueHint()">${t("soccorso.indizio")}</button>
      <button class="btn small danger" ${busy ? "disabled" : ""} onclick="rescueSurrender()">${t("soccorso.arrendo")}</button>`}
      <button class="btn small" onclick="showLevels=true;gameSig='';renderGame()">${t("soccorso.livelli")}</button>
    </div></section>`;
}

function setMode(m) { api("/api/mode", { mode: m }).then((d) => d && applyState(d)); }
function academyCheck() { job("/api/academy/check", {}); }
async function academyRestart() {
  const m = S.academy.current;
  if (m && await chiedi(tt("accademia.conferma_ricomincia_challenge", { titolo: m.title }))) {
    try { localStorage.removeItem(`pna.consigli.${m.id}`); } catch { /* niente */ }
    job("/api/academy/network", {});
  }
}
async function academyAnswer(step, choice) {
  const d = await act("/api/academy/answer", { step, choice });
  if (d && d.answer) toast(d.answer.message, d.answer.correct ? "ok" : "hint", d.answer.correct ? 9000 : 5000);
  if (d && d.answer && d.answer.correct && opz("coriandoli")) confetti();
}
async function academyObserve(step) {
  toast(tt("accademia.osserva_in_corso"), "info", 30000);
  const d = await act("/api/academy/observe", { step });
  document.querySelectorAll("#toasts .toast.info").forEach((x) => x.remove());
  if (d && d.trace) mostraTraccia(d.trace, d.explanation);
}

/* I consigli del collega senior per la challenge in corso: quanti ne ha già chiesti il giocatore (nel browser). */
function consigliRicevuti(mid) {
  try { return +(localStorage.getItem(`pna.consigli.${mid}`) || 0); } catch { return 0; }
}
/* Il prossimo consiglio della challenge in corso (senza darlo), null se la challenge non ne ha, {finiti} se li ha
 * già avuti tutti. Lo usa l'ufficio (storia.js): i consigli si chiedono a Jody. */
function consiglioSuccessivo() {
  const m = S && S.academy && S.academy.current;
  if (!m || !(m.tips || []).length) return null;
  const n = consigliRicevuti(m.id);
  if (n >= m.tips.length) return { finiti: true, totale: m.tips.length };
  return { numero: n + 1, totale: m.tips.length, testo: m.tips[n] };
}
/* Jody ha dato il consiglio: il prossimo sarà quello dopo (e nel pannello compare tra quelli avuti). */
function segnaConsiglio() {
  const m = S && S.academy && S.academy.current;
  if (!m) return;
  try { localStorage.setItem(`pna.consigli.${m.id}`, consigliRicevuti(m.id) + 1); } catch { /* niente */ }
  gameSig = ""; renderGame();
}

/* La finestra con il viaggio del ping (la traccia calcolata dal server, confermata dal ping vero). */
function mostraTraccia(tr, spiegazione) {
  const stato = { sent: tt("accademia.traccia_inviato"), accepted: tt("accademia.traccia_accettato"), dropped: tt("accademia.traccia_scartato") };
  const fondo = document.createElement("div");
  fondo.className = "dialog-back";
  const righe = tr.hops.map((h) => `<tr class="${esc(h.status)}"><td>${h.leg === "ritorno" ? "↩" : "→"}</td><td><b>${esc(h.device)}</b></td>
    <td class="mono">${esc(h.in ? shortPort(h.in) : "—")}</td><td class="mono">${esc(h.out ? shortPort(h.out) : "—")}</td>
    <td>${esc(stato[h.status] || h.status)}</td></tr>`).join("");
  const esito = tr.delivered ? t("accademia.traccia_arrivato")
    : th("accademia.traccia_non_arrivato", { dove: (tr.blocked_by && tr.blocked_by.device) || "?" });
  fondo.innerHTML = `<div class="dialog card traccia" role="dialog" aria-modal="true">
    <h3>${th("accademia.traccia_titolo", { da: tr.from, a: tr.to })}</h3>
    <p class="banner ${tr.delivered ? "ok" : "bad"}">${esito}</p>
    <table class="ports"><tr><th></th><th>${t("accademia.traccia_colonna_dispositivo")}</th><th>${t("accademia.traccia_colonna_entra")}</th>
      <th>${t("accademia.traccia_colonna_esce")}</th><th>${t("accademia.traccia_colonna_stato")}</th></tr>${righe}</table>
    ${spiegazione && tr.delivered ? `<p class="story">${esc(spiegazione)}</p>` : ""}
    <div class="row end"><button type="button" class="btn primary">${t("accademia.chiudi")}</button></div></div>`;
  const chiudi = () => fondo.remove();
  fondo.querySelector("button").addEventListener("click", chiudi);
  fondo.addEventListener("pointerdown", (e) => { if (e.target === fondo) chiudi(); });
  document.body.appendChild(fondo);
  fondo.querySelector("button").focus();
}
async function academyReset() { if (await chiedi(tt("accademia.conferma_ricomincia"))) job("/api/academy/reset", {}); }
async function rescueStart(n) {
  if (await chiedi(tt("soccorso.conferma_avvio", { numero: n }))) { showLevels = false; job("/api/rescue/start", { level: n }); }
}
function rescueCheck() { job("/api/rescue/check", {}); }
async function rescueHint() { if (await chiedi(tt("soccorso.conferma_indizio"))) act("/api/rescue/hint", {}); }
async function rescueSurrender() { if (await chiedi(tt("soccorso.conferma_arrendo"))) act("/api/rescue/surrender", {}); }
$$(".tab").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

/* ───────────── lavori lunghi e diario ───────────── */
function renderJob() {
  const j = S && S.job;
  const box = $("#job");
  if (!j || (j.done && reported.has(j.id))) { box.classList.add("hidden"); return; }
  if (j.done) {
    reported.add(j.id);
    box.classList.add("hidden");
    const msg = (j.result && j.result.message) || (j.ok ? tt("generale.fatto") : tt("generale.operazione_fallita"));
    toast(msg, j.result && j.result.win ? "ok" : j.ok ? "ok" : "bad", 10000);
    // risultati da mostrare nel pannello del dispositivo (scansione, sniffer)
    if (j.result && j.result.scan) { scanOut[j.result.scan.from] = j.result.scan; inspSig = ""; renderInspector(); }
    if (j.result && j.result.capture) { sniffOut[j.result.capture.device] = j.result.capture; inspSig = ""; renderInspector(); }
    if (j.result && j.result.win && opz("coriandoli")) confetti();
    gameSig = "";
    return;
  }
  box.classList.remove("hidden");
  $("#jobTitle").textContent = j.title;
  $("#jobTime").textContent = `${j.elapsed}s`;
  $("#jobSteps").innerHTML = j.steps.map((s) => `<li>${esc(s)}</li>`).join("");
}

function renderLog() {
  $("#log").innerHTML = S.log.map((l) => `<li class="${esc(l.kind)}"><span>${esc(l.msg)}</span></li>`).join("")
    || `<li class="muted">${t("diario.vuoto")}</li>`;
}

/* Domande al giocatore in una finestra della pagina. Niente confirm()/prompt() del browser: in alcuni
 * ambienti (per esempio il pannello browser dell'app Claude) sono disattivati e rispondono subito «Annulla».
 * chiedi() → Promise<bool>; chiediTesto() → Promise<string|null>. Invio conferma, Esc annulla. */
function finestra(msg, campo) {
  return new Promise((risolvi) => {
    const fondo = document.createElement("div");
    fondo.className = "dialog-back";
    fondo.innerHTML = `<form class="dialog card" role="dialog" aria-modal="true">
      <p class="dialog-msg"></p>${campo !== undefined ? `<input class="dialog-input mono">` : ""}
      <div class="row end"><button type="button" class="btn" data-no>${t("generale.annulla")}</button>
      <button type="submit" class="btn primary">${t("generale.conferma")}</button></div></form>`;
    $(".dialog-msg", fondo).textContent = msg;
    const input = $(".dialog-input", fondo);
    if (input) input.value = campo;
    const chiudi = (valore) => { fondo.remove(); document.removeEventListener("keydown", esc_); risolvi(valore); };
    const esc_ = (e) => { if (e.key === "Escape") { e.stopPropagation(); chiudi(input ? null : false); } };
    $("form", fondo).addEventListener("submit", (e) => { e.preventDefault(); chiudi(input ? input.value.trim() : true); });
    $("[data-no]", fondo).addEventListener("click", () => chiudi(input ? null : false));
    fondo.addEventListener("pointerdown", (e) => { if (e.target === fondo) chiudi(input ? null : false); });
    document.addEventListener("keydown", esc_, true);
    document.body.appendChild(fondo);
    (input || $("button[type=submit]", fondo)).focus();
    if (input) input.select();
  });
}
const chiedi = (msg) => finestra(msg);
const chiediTesto = (msg, valore = "") => finestra(msg, valore);

function toast(msg, kind = "info", ms) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").prepend(el);
  setTimeout(() => el.remove(), ms ?? (CFG ? opz("durata_avvisi_ms") : 5000));
  while ($("#toasts").children.length > 4) $("#toasts").lastChild.remove();
}

function confetti() {
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:40;overflow:hidden";
  const colors = ["#2563eb", "#16a34a", "#d97706", "#7c3aed", "#dc2626"];
  for (let i = 0; i < 70; i++) {
    const p = document.createElement("i");
    const x = Math.random() * 100, d = 1.5 + Math.random() * 1.5;
    p.style.cssText = `position:absolute;left:${x}vw;top:-10px;width:8px;height:12px;background:${colors[i % 5]};
      transform:rotate(${Math.random() * 360}deg);transition:transform ${d}s ease-in, top ${d}s ease-in;`;
    box.appendChild(p);
    requestAnimationFrame(() => requestAnimationFrame(() => { p.style.top = "105vh"; p.style.transform = `rotate(${Math.random() * 900}deg)`; }));
  }
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 3200);
}

/* ───────────── tavolo ───────────── */
const svg = $("#canvas");
const center = (d) => ({ x: d.x + 25, y: d.y + 20 });

function fit() {
  const ds = S ? S.topology.devices : [];
  if (!ds.length) { view = { x: 0, y: 0, w: 900, h: 600 }; }
  else {
    const xs = ds.map((d) => center(d).x), ys = ds.map((d) => center(d).y);
    const x0 = Math.min(...xs) - 90, x1 = Math.max(...xs) + 90, y0 = Math.min(...ys) - 70, y1 = Math.max(...ys) + 90;
    const w = Math.max(x1 - x0, 600), h = Math.max(y1 - y0, 420);
    view = { x: (x0 + x1) / 2 - w / 2, y: (y0 + y1) / 2 - h / 2, w, h };
  }
  renderCanvas();
}
$("#fitBtn").addEventListener("click", fit);

function portState(d, pn) {
  const p = d && (d.ports || []).find((x) => x.n === pn);
  if (!p) return "wait";
  if (!p.on) return "off";
  return p.up ? "up" : "wait";
}

function iconSvg(kind) {
  switch (kind) {
    case "router": return `<circle r="20" fill="var(--router)"/>
      <path d="M-11 -4h22m-5-5 5 5-5 5M11 4h-22m5-5-5 5 5 5" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`;
    case "switch": return `<rect x="-27" y="-15" width="54" height="30" rx="7" fill="var(--switch)"/>
      <path d="M-15 -5h28m-5-4 5 4-5 4M15 5h-28m5-4-5 4 5 4" stroke="#fff" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`;
    case "pc": return `<rect x="-21" y="-18" width="42" height="29" rx="4" fill="var(--pc)"/>
      <rect x="-17" y="-14" width="34" height="21" rx="2" fill="#cfe1ff"/><rect x="-8" y="12" width="16" height="4" rx="1" fill="var(--pc)"/>
      <rect x="-13" y="16" width="26" height="3" rx="1.5" fill="var(--pc)"/>`;
    case "server": return `<rect x="-16" y="-21" width="32" height="42" rx="5" fill="var(--server)"/>
      <path d="M-10 -11h20M-10 -3h20M-10 5h20" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="7" cy="14" r="2.5" fill="#9ef0b0"/>`;
    case "firewall": return `<rect x="-24" y="-18" width="48" height="36" rx="5" fill="var(--firewall)"/>
      <path d="M-24 -6h48M-24 6h48M-12 -18v12M8 -18v12M-2 -6v12M16 -6v12M-12 6v12M8 6v12" stroke="#fff" stroke-width="2" opacity=".85"/>`;
    case "cloud": return `<path d="M-20 12a11 11 0 0 1 1-22 14 14 0 0 1 26-4 10 10 0 0 1 13 13 8 8 0 0 1-2 13z" fill="var(--cloud)"/>
      <path d="M-8 3h16M-8 -3h10" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>`;
    default: return `<rect x="-18" y="-18" width="36" height="36" rx="6" fill="var(--muted)"/>`;
  }
}

function subLabel(d) {
  if (d.kind === "pc" || d.kind === "server") {
    const p = hostIp(d);
    if (!p) return d.dhcp ? tt("tavolo.pc_dhcp_senza_ip") : tt("tavolo.pc_senza_ip");
    return `${p.ip}/${maskBits(p.mask)}`;
  }
  if (d.kind === "router") {
    const n = d.ports.filter((p) => p.ip && p.ip !== "0.0.0.0").length;
    return n === 0 ? tt("tavolo.router_senza_ip") : n === 1 ? tt("tavolo.router_una_porta") : tt("tavolo.router_porte", { numero: n });
  }
  if (d.kind === "firewall") {
    const z = (d.fw && d.fw.zones) || [];
    return z.length ? z.map((x) => x.nameif).join(" · ") : tt("firewall.senza_zone");
  }
  return d.model;
}

function renderCanvas() {
  if (!S) return;
  if (!view) fit();
  svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.w} ${view.h}`);
  svg.classList.toggle("erase", tool === "erase");
  const ds = S.topology.devices;
  $("#empty").classList.toggle("hidden", ds.length > 0);
  let h = `<defs><pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="var(--grid)" stroke-width="1"/></pattern></defs>
    <rect class="bg" x="${view.x - 2000}" y="${view.y - 2000}" width="${view.w + 4000}" height="${view.h + 4000}"/>`;
  for (const l of S.topology.links) {
    const a = dev(l.a), b = dev(l.b);
    if (!a || !b) continue;
    const A = center(a), B = center(b);
    const len = Math.hypot(B.x - A.x, B.y - A.y) || 1, ux = (B.x - A.x) / len, uy = (B.y - A.y) / len;
    const r1 = Math.min(30, len / 3), r2 = Math.min(52, len / 2.3);
    const cross = l.cable === "8101" ? " cross" : "";
    h += `<g class="link${cross}" data-a="${esc(l.a)}" data-pa="${esc(l.pa)}" data-b="${esc(l.b)}" data-pb="${esc(l.pb)}">
      <line class="hit" x1="${A.x}" y1="${A.y}" x2="${B.x}" y2="${B.y}"/>
      <line class="wire" x1="${A.x}" y1="${A.y}" x2="${B.x}" y2="${B.y}"/>
      <circle class="${portState(a, l.pa)}" cx="${A.x + ux * r1}" cy="${A.y + uy * r1}" r="5"/>
      <circle class="${portState(b, l.pb)}" cx="${B.x - ux * r1}" cy="${B.y - uy * r1}" r="5"/>
      ${l.a === sel || l.b === sel ? `
      <text class="pl" x="${A.x + ux * r2}" y="${A.y + uy * r2 + 4}" text-anchor="middle">${esc(shortPort(l.pa))}</text>
      <text class="pl" x="${B.x - ux * r2}" y="${B.y - uy * r2 + 4}" text-anchor="middle">${esc(shortPort(l.pb))}</text>` : ""}</g>`;
  }
  for (const d of ds) {
    const c = center(d);
    const cls = ["dev", d.name === sel ? "sel" : "", d.name === pickA ? "pick" : "", d.power === false ? "off" : ""].join(" ");
    h += `<g class="${cls}" data-name="${esc(d.name)}" transform="translate(${c.x} ${c.y})">
      <rect class="halo" x="-36" y="-30" width="72" height="88" rx="12"/>
      ${iconSvg(vista(d))}
      <text class="name" y="40">${esc(d.name)}</text>
      <text class="sub" y="54">${esc(subLabel(d))}</text></g>`;
  }
  svg.innerHTML = h;
}

function svgPoint(evt) {
  const punto = svg.createSVGPoint();
  punto.x = evt.clientX; punto.y = evt.clientY;
  return punto.matrixTransform(svg.getScreenCTM().inverse());
}

function setTool(nuovo, kind = null) {
  tool = nuovo; addKind = kind; pickA = null;
  $$(".tool[data-tool]").forEach((b) => b.classList.toggle("active", b.dataset.tool === nuovo && !kind));
  $$(".tool[data-add]").forEach((b) => b.classList.toggle("active", b.dataset.add === kind));
  const msg = kind ? tt("tavolo.metti", { articolo: dispositivo(kind).articolo })
    : nuovo === "cable" ? tt("tavolo.cavo_primo")
    : nuovo === "erase" ? tt("tavolo.togli") : "";
  hint(msg);
  renderCanvas();
}
function hint(msg) { const b = $("#hintBar"); b.textContent = msg; b.classList.toggle("show", !!msg); }

$$(".tool[data-tool]").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setTool("select"); });

svg.addEventListener("pointerdown", async (e) => {
  if (!S) return;
  const g = e.target.closest(".dev");
  const lk = e.target.closest(".link");
  const p = svgPoint(e);
  if (addKind && !g) {
    await act("/api/device/add", { kind: addKind, x: Math.round(p.x - 25), y: Math.round(p.y - 20) });
    return;
  }
  if (g) {
    const name = g.dataset.name;
    if (tool === "cable") {
      if (!pickA) { pickA = name; hint(tt("tavolo.cavo_secondo", { nome: name })); renderCanvas(); }
      else if (pickA !== name) {
        const a = pickA; pickA = null; hint(tt("tavolo.cavo_ricomincia"));
        await act("/api/link", { a, b: name }, tt("tavolo.cavo_fatto", { a, b: name }));
      }
      return;
    }
    if (tool === "erase") {
      if (await chiedi(tt("tavolo.conferma_togli_dispositivo", { nome: name }))) await act("/api/device/delete", { name });
      return;
    }
    select(name);
    const d = dev(name);
    drag = { name, dx: p.x - d.x, dy: p.y - d.y, moved: false, x: d.x, y: d.y };
    svg.setPointerCapture(e.pointerId);
    return;
  }
  if (lk) {
    const { a, pa, b, pb } = lk.dataset;
    if (tool === "erase") {
      if (await chiedi(tt("tavolo.conferma_stacca_cavo", { a, pa: shortPort(pa), b, pb: shortPort(pb) }))) await act("/api/unlink", { name: a, port: pa });
    } else toast(tt("tavolo.info_cavo", { a, pa, b, pb }), "info", 3500);
    return;
  }
  if (pickA) { pickA = null; renderCanvas(); }
  if (tool === "select") select(null);
});

svg.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const p = svgPoint(e);
  const d = dev(drag.name);
  const nx = Math.round(p.x - drag.dx), ny = Math.round(p.y - drag.dy);
  if (Math.abs(nx - drag.x) + Math.abs(ny - drag.y) > 3) drag.moved = true;
  d.x = nx; d.y = ny;
  renderCanvas();
});

svg.addEventListener("pointerup", async () => {
  if (!drag) return;
  const dr = drag; drag = null;
  if (dr.moved) {
    const d = dev(dr.name);
    await act("/api/device/move", { name: dr.name, x: d.x, y: d.y });
  }
});

/* ───────────── pannello del dispositivo ───────────── */
function select(name) {
  if (name !== sel) { selPort = null; fwPort = null; dirty = false; inspSig = ""; }
  sel = name;
  renderCanvas();
  renderInspector();
}

function renderInspector() {
  const box = $("#inspector");
  const d = sel && dev(sel);
  const sig = JSON.stringify([d, selPort, showAllPorts, consoleOut[sel], auditOut[sel], usersOut[sel], aclEd, fwEd, fwPort, servOut[sel], scanOut[sel], sniffOut[sel], limOut[sel], S.firewall, S.dns, S.dhcp_excluded, S.topology.links.filter((l) => d && (l.a === d.name || l.b === d.name))]);
  if (sig === inspSig) return;
  if (dirty && box.contains(document.activeElement)) return;  // non cancellare quello che il giocatore sta scrivendo
  inspSig = sig;
  if (!d) {
    box.innerHTML = `<section class="card empty-insp"><div class="big">👆</div><p>${t("pannello.vuoto")}</p>
      <p class="muted">${t("pannello.vuoto_aiuto")}</p></section>`;
    return;
  }
  const info = dispositivo(vista(d));
  const head = `<section class="card"><div class="insp-head"><div class="badge ${vista(d)}">${esc(info.icona)}</div>
    <div class="t"><b>${esc(d.name)}</b><span class="muted">${esc(info.nome)} · ${esc(d.model)}</span></div></div>
    <div class="row" style="margin-top:10px"><button class="btn small" onclick="renameDev()">${t("pannello.rinomina")}</button>
    <button class="btn small danger" onclick="deleteDev()">${t("pannello.togli")}</button></div></section>`;
  let body = "";
  // sul server il DNS sta subito sotto la configurazione IP: è lì che lo si cerca
  if (d.kind === "pc" || d.kind === "server") body = hostPanel(d, d.kind === "server" ? dnsPanel(d) : "") + (d.kind === "server" ? servicesPanel(d) : "") + scanPanel(d);
  else if (d.kind === "router") body = routerPanel(d) + rateLimitPanel(d);
  else if (d.kind === "switch") body = switchPanel(d);
  else if (d.kind === "firewall") body = firewallPanel(d);
  body += sniffPanel(d);
  // Le sezioni a scomparsa aperte restano aperte quando il pannello si ridisegna (la rete cambia spesso).
  const aperte = new Set($$("details[data-sez]", box).filter((x) => x.open).map((x) => x.dataset.sez));
  const scorri = box.scrollTop;   // ridisegnando non si torna in cima: quello che hai appena aggiunto resta sotto gli occhi
  box.innerHTML = head + body;
  $$("details[data-sez]", box).forEach((x) => { if (aperte.has(x.dataset.sez)) x.open = true; });
  box.scrollTop = scorri;
  $$("input,select,textarea", box).forEach((el) => el.addEventListener("input", () => { dirty = true; }));
}

function peerOf(d, pn) {
  const l = S.topology.links.find((x) => (x.a === d.name && x.pa === pn) || (x.b === d.name && x.pb === pn));
  if (!l) return null;
  return l.a === d.name ? { dev: l.b, port: l.pb } : { dev: l.a, port: l.pa };
}

const help = (k) => `<span class="help" title="${esc(tt(`aiuti.${k}`))}">?</span>`;
const accesa = (on) => (on ? t("pannello.accesa") : t("pannello.spenta"));

function hostPanel(d, dopoConfigurazione = "") {
  const p = (d.ports || [])[0] || {};
  const ipP = hostIp(d) || {};
  const peer = p.n ? peerOf(d, p.n) : null;
  const dhcp = !!d.dhcp;
  const out = consoleOut[d.name];
  return `<section class="card"><h3>${t("pc.configurazione")}</h3>
    <div class="seg"><button class="${dhcp ? "" : "on"}" onclick="hostMode('static')">${t("pc.statico")}</button><button class="${dhcp ? "on" : ""}" onclick="hostMode('dhcp')">${t("pc.dhcp")} ${help("dhcp")}</button></div>
    ${dhcp ? `<p>${t("pc.indirizzo_ricevuto")}: <b class="mono">${ipP.ip ? `${esc(ipP.ip)} / ${esc(ipP.mask)}` : t("pc.nessuno")}</b><br>${t("pc.gateway")}: <b class="mono">${esc(d.gw || "—")}</b>
      <br>DNS: <b class="mono">${esc(d.dns && d.dns !== "0.0.0.0" ? d.dns : "—")}</b></p>
      <button class="btn" onclick="hostMode('dhcp')">${t("pc.rinnova")}</button>` : `
    <form onsubmit="hostIpSubmit(event)"><div class="form-grid">
      <label class="full">${t("pc.ip")} ${help("ip")}<input name="ip" value="${esc(ipP.ip || "")}" inputmode="decimal"></label>
      <label>${t("pc.maschera")} ${help("maschera")}<input name="mask" value="${esc(ipP.mask || "255.255.255.0")}" inputmode="decimal"></label>
      <label>${t("pc.gateway")} ${help("gateway")}<input name="gateway" value="${esc(d.gw && d.gw !== "0.0.0.0" ? d.gw : "")}" inputmode="decimal"></label>
      <label class="full">${t("pc.dns")}<input name="dns" inputmode="decimal" value="${esc(d.dns && d.dns !== "0.0.0.0" ? d.dns : "")}" title="${esc(tt("pc.dns_aiuto"))}"></label>
    </div><div class="row end"><button class="btn primary">${t("pannello.applica")}</button></div></form>`}
    <p class="muted" style="margin:10px 0 0">${peer ? th("pc.cavo_collegato", { dispositivo: peer.dev, porta: shortPort(peer.port) }) : t("pc.cavo_scollegato")}</p>
  </section>
  ${dopoConfigurazione}
  <section class="card"><h3>${t("pc.prompt")}</h3>
    <form onsubmit="hostCmd(event)" class="row"><input name="cmd" value="ping " list="hostCmds" style="flex:1" class="mono">
    <button class="btn primary">${t("pannello.esegui")}</button></form>
    <datalist id="hostCmds">${opz("comandi_pc").map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
    <p class="muted" style="font-size:12px;margin:6px 0 0">${t("pc.prompt_aiuto")}</p>
    ${out ? `<pre class="console">${esc(out)}</pre>` : ""}</section>`;
}

function routerPanel(d) {
  const rows = d.ports.map((p) => {
    const peer = peerOf(d, p.n);
    const st = !p.on ? "off" : p.up && peer ? "up" : "wait";
    return `<tr class="pick ${selPort === p.n ? "cur" : ""}" onclick="pickPort('${esc(p.n)}')">
      <td><b>${esc(shortPort(p.n))}</b></td><td class="mono">${p.ip && p.ip !== "0.0.0.0" ? `${esc(p.ip)}/${maskBits(p.mask)}` : "—"}</td>
      <td><i class="dot ${st}"></i> ${accesa(p.on)}</td><td>${peer ? esc(peer.dev) : ""}</td></tr>`;
  }).join("");
  const p = selPort && d.ports.find((x) => x.n === selPort);
  const edit = p ? `<form onsubmit="routerIface(event)" class="card flat" style="margin-top:10px"><h4>${esc(p.n)}</h4><div class="form-grid">
      <label>${t("pc.ip")} ${help("ip")}<input name="ip" value="${esc(p.ip && p.ip !== "0.0.0.0" ? p.ip : "")}"></label>
      <label>${t("pc.maschera")} ${help("maschera")}<input name="mask" value="${esc(p.mask && p.mask !== "0.0.0.0" ? p.mask : "255.255.255.0")}"></label>
      <label class="full switch-toggle"><input type="checkbox" name="on" ${p.on ? "checked" : ""}> ${t("router.porta_accesa")} ${help("porta_accesa")}</label>
    </div><div class="row end"><button class="btn primary">${t("pannello.applica")}</button></div></form>`
    : `<p class="muted" style="font-size:12.5px;margin:8px 0 0">${t("router.scegli_porta")}</p>`;
  const routes = (d.routes || []).filter((r) => r.proto !== "L").map((r) => `<li><b>${esc(r.proto)}</b> ${esc(r.net)}/${maskBits(r.mask)}
      ${r.via && r.via !== "0.0.0.0" ? `via ${esc(r.via)}` : `→ ${esc(shortPort(r.port))}`}
      ${r.proto === "S" ? `<button class="btn small" style="padding:0 6px" title="${esc(tt("router.togli_rotta"))}" onclick="routeDel('${esc(r.net)}','${esc(r.mask)}','${esc(r.via)}')">✕</button>` : ""}</li>`).join("");
  const pools = (d.pools || []).map((pl) => `<li>${esc(pl.name)}: ${esc(pl.net)}/${maskBits(pl.mask)} gw ${esc(pl.gw)}${pl.dns && pl.dns !== "0.0.0.0" ? ` · DNS ${esc(pl.dns)}` : ""}
      <button class="btn small" style="padding:0 6px" title="${esc(tt("router.modifica_pool"))}" onclick="dhcpEdit('${esc(pl.name)}')">✏️</button>
      <button class="btn small" style="padding:0 6px" title="${esc(tt("router.togli_pool"))}" onclick="dhcpDel('${esc(pl.name)}')">✕</button></li>`).join("");
  const esclusi = ((S.dhcp_excluded || {})[d.name] || []).map(([x, y]) => `<li>${t("router.esclusi")} <span class="mono">${esc(x)}${y !== x ? `–${esc(y)}` : ""}</span>
      <button class="btn small" style="padding:0 6px" title="${esc(tt("router.togli_esclusi"))}" onclick="dhcpExclDel('${esc(x)}','${esc(y)}')">✕</button></li>`).join("");
  return `<section class="card"><h3>${t("router.porte")}</h3><table class="ports"><tr><th>${t("router.colonna_porta")}</th><th>${t("router.colonna_ip")}</th><th>${t("router.colonna_stato")}</th><th>${t("router.colonna_verso")}</th></tr>${rows}</table>${edit}</section>
    <section class="card"><details data-sez="routing"><summary><b>${t("router.routing")}</b> <span class="muted">${t("router.routing_sotto")}</span></summary>
      <ul class="routes">${routes || `<li>${t("router.routing_vuota")}</li>`}</ul>
      <p class="muted" style="font-size:12px">${t("router.legenda_routing")}</p>
      <form onsubmit="routeAdd(event)"><h4>${t("router.nuova_rotta")} ${help("rotta")}</h4><div class="form-grid">
        <label>${t("router.rete_destinazione")}<input name="network"></label>
        <label>${t("pc.maschera")}<input name="mask" value="255.255.255.0"></label>
        <label class="full">${t("router.via")}<input name="via"></label>
      </div><div class="row end"><button class="btn primary">${t("router.aggiungi_rotta")}</button></div></form></details></section>
    <section class="card"><details data-sez="dhcp"><summary><b>${t("router.dhcp")}</b> <span class="muted">${pools ? th("router.dhcp_pool", { numero: d.pools.length }) : t("router.dhcp_spento")}</span></summary>
      <ul class="routes">${pools}${esclusi}</ul>
      <form onsubmit="dhcpPool(event)" id="dhcpForm"><div class="form-grid">
        <label>${t("router.nome_pool")}<input name="pool" value="LAN${(d.pools || []).length + 1}"></label>
        <label>${t("router.rete")}<input name="network"></label>
        <label>${t("pc.maschera")}<input name="mask" value="255.255.255.0"></label>
        <label>${t("router.gateway_pc")}<input name="gateway"></label>
        <label class="full">${t("router.dns")}<input name="dns"></label>
        <label>${t("router.escludi_da")}<input name="exclude_from"></label>
        <label>${t("router.escludi_a")}<input name="exclude_to"></label>
      </div><div class="row end"><button class="btn primary">${t("router.crea_pool")}</button></div></form></details></section>
    ${securityPanel(d)}
    ${consolePanel(d, opz("console_router"))}`;
}

function switchPanel(d) {
  const ports = d.ports.filter((p) => showAllPorts || p.link || !p.on || selPort === p.n);
  const rows = ports.map((p) => {
    const peer = peerOf(d, p.n);
    const st = !p.on ? "off" : p.up ? "up" : "wait";
    return `<tr class="pick ${selPort === p.n ? "cur" : ""}" onclick="pickPort('${esc(p.n)}')">
      <td><b>${esc(shortPort(p.n))}</b></td><td>${peer ? esc(peer.dev) : "—"}</td>
      <td>${p.vlan === "trunk" ? "trunk" : `VLAN ${esc(p.vlan ?? 1)}`}</td><td><i class="dot ${st}"></i> ${accesa(p.on)}</td></tr>`;
  }).join("");
  const p = selPort && d.ports.find((x) => x.n === selPort);
  const edit = p ? `<div class="card flat" style="margin-top:10px"><h4>${esc(p.n)}</h4>
      <div class="row"><button class="btn ${p.on ? "danger" : "primary"}" onclick="setPortState(${!p.on})">${p.on ? t("switch.spegni") : t("switch.accendi")}</button></div>
      ${p.vlan === "trunk" ? `<p class="muted" style="font-size:12.5px">${t("switch.trunk")}</p>` :
      `<form onsubmit="setVlan(event)" class="row" style="margin-top:10px"><label style="flex:1;margin:0">${t("switch.vlan")} ${help("vlan")}<input name="vlan" value="${esc(p.vlan ?? 1)}" inputmode="numeric"></label>
      <button class="btn" style="align-self:flex-end">${t("switch.imposta_vlan")}</button></form>`}</div>`
    : `<p class="muted" style="font-size:12.5px;margin:8px 0 0">${t("switch.scegli_porta")}</p>`;
  return `<section class="card"><h3>${t("switch.porte")}</h3>
    <label class="switch-toggle" style="margin-bottom:6px"><input type="checkbox" ${showAllPorts ? "checked" : ""} onchange="showAllPorts=this.checked;inspSig='';renderInspector()"> ${t("switch.mostra_libere")}</label>
    <table class="ports"><tr><th>${t("switch.colonna_porta")}</th><th>${t("switch.colonna_verso")}</th><th>${t("switch.colonna_vlan")}</th><th>${t("switch.colonna_stato")}</th></tr>${rows || `<tr><td colspan="4" class="muted">${t("switch.nessun_cavo")}</td></tr>`}</table>${edit}</section>
    ${securityPanel(d)}
    ${consolePanel(d, opz("console_switch"))}`;
}

function consolePanel(d, quick) {
  const out = consoleOut[d.name];
  return `<section class="card"><details data-sez="console" ${out ? "open" : ""}><summary><b>${t("console.titolo")}</b> <span class="muted">${t("console.sotto")}</span></summary>
    <div class="row" style="margin:8px 0">${quick.map((q, i) => `<button class="btn small mono" onclick="runQuick('${d.kind}', ${i})">${esc(q)}</button>`).join("")}</div>
    <form onsubmit="cliForm(event)"><textarea name="cmds" rows="3" class="mono" placeholder="${esc(tt("console.esempio"))}"></textarea>
    <div class="row end" style="margin-top:6px"><button class="btn primary">${t("pannello.esegui")}</button></div></form>
    ${out ? `<pre class="console">${esc(out)}</pre>` : ""}</details></section>`;
}

/* azioni del pannello */
const formData = (e) => { e.preventDefault(); dirty = false; return Object.fromEntries(new FormData(e.target)); };
function pickPort(n) { selPort = selPort === n ? null : n; dirty = false; inspSig = ""; renderInspector(); }
async function renameDev() {
  const n = await chiediTesto(tt("pannello.nuovo_nome"), sel);
  if (n && n !== sel) { const d = await act("/api/device/rename", { name: sel, new: n }); if (d) { sel = d.selected; inspSig = ""; renderInspector(); } }
}
async function deleteDev() { if (await chiedi(tt("pannello.conferma_togli", { nome: sel }))) await act("/api/device/delete", { name: sel }); }
async function hostMode(m) {
  if (m === "dhcp") { toast(tt("pc.chiedo_dhcp", { nome: sel }), "info", 3000); await act("/api/host/ip", { name: sel, mode: "dhcp" }); }
  else { const d = dev(sel); d.dhcp = false; inspSig = ""; renderInspector(); }
}
async function hostIpSubmit(e) { const f = formData(e); await act("/api/host/ip", { name: sel, mode: "static", ...f }, tt("pc.configurato", { nome: sel })); }
async function hostCmd(e) {
  const f = formData(e); const name = sel;
  consoleOut[name] = `${name}> ${f.cmd}\n${tt("pannello.in_esecuzione")}`; inspSig = ""; renderInspector();
  const d = await act("/api/host/cmd", { name, command: f.cmd });
  consoleOut[name] = d ? `${name}> ${f.cmd}\n${d.console}` : `${name}> ${f.cmd}\n${tt("pannello.errore_console")}`; inspSig = ""; renderInspector();
}
async function routerIface(e) {
  const f = formData(e);
  await act("/api/router/iface", { name: sel, port: selPort, ip: f.ip, mask: f.mask, on: !!f.on, clear_ip: !f.ip },
    tt("router.porta_configurata", { nome: sel, porta: shortPort(selPort) }));
}
async function setPortState(on) {
  await act("/api/port/state", { name: sel, port: selPort, on }, tt("switch.porta_cambiata", { porta: shortPort(selPort), stato: tt(on ? "pannello.accesa" : "pannello.spenta") }));
}
async function setVlan(e) { const f = formData(e); await act("/api/switch/vlan", { name: sel, port: selPort, vlan: f.vlan }, tt("switch.vlan_impostata", { porta: shortPort(selPort), vlan: f.vlan })); }
async function routeAdd(e) { const f = formData(e); await act("/api/router/route", { name: sel, ...f }, tt("router.rotta_aggiunta")); }
async function routeDel(network, mask, via) {
  if (await chiedi(tt("router.conferma_togli_rotta", { rete: `${network}/${maskBits(mask)}`, via }))) {
    await act("/api/router/route", { name: sel, network, mask, via, remove: true }, tt("router.rotta_tolta"));
  }
}
async function dhcpPool(e) { const f = formData(e); await act("/api/router/dhcp", { name: sel, ...f }, tt("router.pool_creato")); }
/* Modificare un pool: il modulo si riempie con i suoi valori; «Crea pool» con lo stesso nome lo sostituisce. */
function dhcpEdit(nome) {
  const pl = ((dev(sel) || {}).pools || []).find((x) => x.name === nome);
  const f = $("#dhcpForm");
  if (!pl || !f) return;
  f.closest("details").open = true;
  f.pool.value = pl.name; f.network.value = pl.net; f.mask.value = pl.mask; f.gateway.value = pl.gw;
  f.dns.value = pl.dns && pl.dns !== "0.0.0.0" ? pl.dns : "";
  f.network.focus();
}
async function dhcpDel(nome) {
  if (await chiedi(tt("router.conferma_togli_pool", { pool: nome }))) await act("/api/router/dhcp/remove", { name: sel, pool: nome }, tt("router.pool_tolto"));
}
async function dhcpExclDel(da, a) { await act("/api/router/dhcp/exclude", { name: sel, from: da, to: a, remove: true }, tt("router.esclusi_tolti")); }
function cliForm(e) { const f = formData(e); runCli(f.cmds.split("\n")); }
/* I comandi pronti della console vengono da impostazioni.yaml: si passano per indice, non come testo nell'HTML. */
function runQuick(kind, i) { runCli([opz(kind === "router" ? "console_router" : "console_switch")[i]]); }
async function runCli(cmds) {
  const name = sel;
  consoleOut[name] = `${cmds.join("\n")}\n${tt("pannello.in_esecuzione")}`; inspSig = ""; renderInspector();
  const d = await act("/api/cli", { name, commands: cmds });
  consoleOut[name] = d ? d.console : tt("pannello.errore_console"); inspSig = ""; renderInspector();
}

/* ───────────── sicurezza: ACL (router), password, credenziali, controllo ─────────────
 * Usa gli endpoint /api/router/acl, /api/router/acl/remove, /api/device/harden, /api/device/login e /api/audit.
 * L'ACL in modifica vive in aclEd (non nel DOM), così il pannello si può ridisegnare senza perderla. */
const PORTE_NOTE = { www: 80, http: 80, ftp: 21, ssh: 22, telnet: 23, smtp: 25, domain: 53, pop3: 110, https: 443 };
const GRAVITA = { high: "alta", medium: "media", low: "bassa" };

/* Una riga dell'ACL («deny tcp 192.168.1.0 0.0.0.255 host 192.168.0.3 eq www») → regola dell'API, o null. */
function aclParseRule(line, extended) {
  const tk = line.replace(/\(.*?\)/g, " ").trim().split(/\s+/);
  if (/^\d+$/.test(tk[0])) tk.shift();                       // numero di sequenza
  const action = tk.shift();
  if (action !== "permit" && action !== "deny") return null;
  const addr = () => {
    const a = tk.shift();
    if (a === "any") return "any";
    if (a === "host") return tk.shift();
    if (tk[0] && /^\d+\.\d+\.\d+\.\d+$/.test(tk[0]) && /^0\./.test(tk[0])) {  // indirizzo + wildcard
      const w = tk.shift();
      const bits = w.split(".").reduce((n, o) => n + Number(o).toString(2).replace(/0/g, "").length, 0);
      return `${a}/${32 - bits}`;
    }
    return a;
  };
  if (!extended) return { action, source: addr() };
  const protocol = tk.shift();
  const r = { action, protocol, source: addr(), destination: addr(), port: "" };
  if (tk[0] === "eq") r.port = String(PORTE_NOTE[tk[1]] || tk[1]);
  return r.source && r.destination ? r : null;
}
function aclBinding(d, id) {
  for (const p of d.ports) for (const dir of ["in", "out"]) if (p[`acl_${dir}`] === id) return { port: p.n, dir };
  return { port: "", dir: "in" };
}
const aclRefresh = () => { dirty = false; inspSig = ""; renderInspector(); };
const nuovaRegola = (action = "deny") => ({ action, protocol: "ip", source: "", destination: "", port: "" });
function aclNew() {
  const d = dev(sel);
  const usati = new Set((d.acls || []).map((a) => a.id));
  let n = 10; while (usati.has(String(n))) n++;
  aclEd = { router: sel, acl: String(n), type: "standard", port: "", dir: "in", rules: [nuovaRegola("deny"), { ...nuovaRegola("permit"), source: "any" }], orig: null };
  aclRefresh();
}
function aclEdit(id) {
  const d = dev(sel); const a = (d.acls || []).find((x) => x.id === id);
  if (!a) return;
  const rules = a.rules.map((l) => aclParseRule(l, a.extended));
  if (rules.some((r) => !r)) { toast(tt("sicurezza.acl_non_leggibile"), "bad"); return; }
  const b = aclBinding(d, id);
  aclEd = { router: sel, acl: id, type: a.extended ? "extended" : "standard", port: b.port, dir: b.dir, rules, orig: { id, ...b } };
  aclRefresh();
}
function aclSet(i, k, v) {
  if (!aclEd) return;
  if (i < 0) aclEd[k] = v; else aclEd.rules[i][k] = v;
  if (k === "type" && /^\d+$/.test(aclEd.acl)) {
    // I numeri dicono il tipo: standard 1-99, estese 100-199. Si passa al primo numero libero giusto.
    const n = Number(aclEd.acl), ext = v === "extended";
    if (ext ? n < 100 || n > 199 : n < 1 || n > 99) {
      const usati = new Set((dev(aclEd.router).acls || []).map((x) => x.id));
      let m = ext ? 100 + (n % 100) : n % 100 || 1;
      while (usati.has(String(m)) && m < (ext ? 199 : 99)) m++;
      aclEd.acl = String(m);
    }
  }
  if (k === "type" || k === "protocol") aclRefresh();               // cambiano le colonne del modulo
}
function aclRow(op, i) {
  if (!aclEd) return;
  if (op === "add") aclEd.rules.splice(aclEd.rules.length ? aclEd.rules.length - 1 : 0, 0, nuovaRegola());
  else if (op === "del") aclEd.rules.splice(i, 1);
  else if (op === "up" && i > 0) [aclEd.rules[i - 1], aclEd.rules[i]] = [aclEd.rules[i], aclEd.rules[i - 1]];
  aclRefresh();
}
function aclCancel() { aclEd = null; aclRefresh(); }
async function aclSave(e) {
  e.preventDefault();
  const a = aclEd; if (!a) return;
  const ext = a.type === "extended";
  const rules = a.rules.map((r) => ext
    ? { action: r.action, protocol: r.protocol, source: r.source || "any", destination: r.destination || "any", ...(r.port && ["tcp", "udp"].includes(r.protocol) ? { port: Number(r.port) } : {}) }
    : { action: r.action, source: r.source || "any" });
  // «port» sempre presente: il server stacca l'ACL dalle porte di prima e la mette solo qui ("" = da nessuna parte).
  const body = { name: a.router, acl: a.acl.trim(), type: a.type, rules, port: a.port, ...(a.port ? { direction: a.dir } : {}) };
  const d = await act("/api/router/acl", body, tt("sicurezza.acl_salvata", { acl: body.acl }));
  if (!d) return;
  // Cambiato nome: la nuova c'è, ora si toglie la vecchia (in quest'ordine, così un errore non perde niente).
  if (a.orig && a.orig.id !== body.acl) await act("/api/router/acl/remove", { name: a.router, acl: a.orig.id });
  aclEd = null; aclRefresh();
}
async function aclRemove(id) {
  if (await chiedi(tt("sicurezza.conferma_togli_acl", { acl: id, nome: sel }))) {
    if (aclEd && aclEd.orig && aclEd.orig.id === id) aclEd = null;
    await act("/api/router/acl/remove", { name: sel, acl: id }, tt("sicurezza.acl_tolta", { acl: id }));
  }
}
async function hardenSubmit(e) {
  const f = formData(e);
  const body = { name: sel };
  if (f.enable_secret) body.enable_secret = f.enable_secret.trim();
  if (f.cifra) body.password_encryption = true;
  if (Object.keys(body).length === 1) { toast(tt("sicurezza.niente_da_fare"), "hint"); return; }
  await act("/api/device/harden", body, tt("sicurezza.protetto", { nome: sel }));
}
async function loginSubmit(e) {
  const f = formData(e);
  const body = { name: sel };
  for (const k of ["username", "secret", "enable_secret"]) if (f[k]) body[k] = f[k].trim();
  await act("/api/device/login", body);
}
async function auditRun() {
  const name = sel;
  const d = await act("/api/audit", { name });
  if (d && d.audit) { auditOut[name] = d.audit; inspSig = ""; renderInspector(); await usersLoad(); }
}
async function usersLoad() {
  const name = sel;
  const d = await act("/api/device/users", { name });
  if (d && d.users) { usersOut[name] = d.users; inspSig = ""; renderInspector(); }
}
async function userRemove(i) {
  const name = sel; const u = (usersOut[name] || [])[i];
  if (!u || !(await chiedi(tt("sicurezza.conferma_togli_utente", { utente: u.username, nome: name })))) return;
  const d = await act("/api/device/users/remove", { name, username: u.username }, tt("sicurezza.utente_tolto", { utente: u.username }));
  if (d && d.users) { usersOut[name] = d.users; inspSig = ""; renderInspector(); }
}

function aclEditor(d) {
  const a = aclEd;
  const ext = a.type === "extended";
  const opt = (v, cur, label) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
  const rows = a.rules.map((r, i) => `<tr>
      <td><select onchange="aclSet(${i},'action',this.value)">${opt("permit", r.action, tt("sicurezza.permetti"))}${opt("deny", r.action, tt("sicurezza.nega"))}</select></td>
      ${ext ? `<td><select onchange="aclSet(${i},'protocol',this.value)">${["ip", "icmp", "tcp", "udp"].map((x) => opt(x, r.protocol, tt(`sicurezza.protocollo_${x}`))).join("")}</select></td>` : ""}
      <td><input class="mono" value="${esc(r.source)}" placeholder="any" oninput="aclSet(${i},'source',this.value)"></td>
      ${ext ? `<td><input class="mono" value="${esc(r.destination)}" placeholder="any" oninput="aclSet(${i},'destination',this.value)"></td>
      <td>${["tcp", "udp"].includes(r.protocol) ? `<input class="mono" style="width:64px" value="${esc(r.port)}" placeholder="80" inputmode="numeric" oninput="aclSet(${i},'port',this.value)">` : ""}</td>` : ""}
      <td style="white-space:nowrap">${i > 0 ? `<button type="button" class="btn small" title="${esc(tt("sicurezza.su"))}" onclick="aclRow('up',${i})">↑</button>` : ""}
        <button type="button" class="btn small danger" title="${esc(tt("sicurezza.togli_regola"))}" onclick="aclRow('del',${i})">✕</button></td></tr>`).join("");
  return `<form onsubmit="aclSave(event)" class="card flat" style="margin-top:10px">
    <h4>${a.orig ? th("sicurezza.modifica_acl", { acl: a.orig.id }) : t("sicurezza.nuova_acl_titolo")}</h4>
    <div class="form-grid">
      <label>${t("sicurezza.nome_acl")} ${help("acl_nome")}<input class="mono" value="${esc(a.acl)}" oninput="aclSet(-1,'acl',this.value)"></label>
      <label>${t("sicurezza.tipo")} ${help("acl_tipo")}<select onchange="aclSet(-1,'type',this.value)">${opt("standard", a.type, tt("sicurezza.standard"))}${opt("extended", a.type, tt("sicurezza.estesa"))}</select></label>
      <label>${t("sicurezza.porta")} ${help("acl_porta")}<select onchange="aclSet(-1,'port',this.value)">${opt("", a.port, tt("sicurezza.nessuna_porta"))}${d.ports.map((p) => opt(p.n, a.port, `${shortPort(p.n)}${peerOf(d, p.n) ? ` → ${peerOf(d, p.n).dev}` : ""}`)).join("")}</select></label>
      <label>${t("sicurezza.verso")} ${help("acl_verso")}<select onchange="aclSet(-1,'dir',this.value)">${opt("in", a.dir, tt("sicurezza.in"))}${opt("out", a.dir, tt("sicurezza.out"))}</select></label>
    </div>
    <table class="ports acl-rules"><tr><th>${t("sicurezza.azione")}</th>${ext ? `<th>${t("sicurezza.protocollo")}</th>` : ""}<th>${t("sicurezza.da")} ${help("acl_indirizzo")}</th>${ext ? `<th>${t("sicurezza.a")}</th><th>${t("sicurezza.porta_servizio")}</th>` : ""}<th></th></tr>${rows}</table>
    <p class="muted" style="font-size:12px;margin:6px 0">${t("sicurezza.deny_implicito")}</p>
    <div class="row"><button type="button" class="btn small" onclick="aclRow('add')">${t("sicurezza.aggiungi_regola")}</button>
      <span style="flex:1"></span><button type="button" class="btn small" onclick="aclCancel()">${t("generale.annulla")}</button>
      <button class="btn primary">${t("sicurezza.salva_acl")}</button></div></form>`;
}

function securityPanel(d) {
  const router = d.kind === "router";
  let acl = "";
  if (router) {
    const items = (d.acls || []).map((a) => {
      const b = aclBinding(d, a.id);
      return `<li class="acl-item"><div class="row"><b class="mono">${esc(a.id)}</b>
        <span class="muted">${a.extended ? t("sicurezza.estesa") : t("sicurezza.standard")} · ${b.port ? th("sicurezza.applicata", { porta: shortPort(b.port), verso: tt(`sicurezza.${b.dir}`) }) : t("sicurezza.non_applicata")}</span>
        <span style="flex:1"></span><button class="btn small" onclick="aclEdit('${esc(a.id)}')">${t("sicurezza.modifica")}</button>
        <button class="btn small danger" onclick="aclRemove('${esc(a.id)}')">${t("pannello.togli")}</button></div>
        <pre class="console">${esc(a.rules.join("\n"))}</pre></li>`;
    }).join("");
    acl = `<h4>${t("sicurezza.acl")} ${help("acl")}</h4>
      <ul class="routes acl-list">${items || `<li>${t("sicurezza.nessuna_acl")}</li>`}</ul>
      ${aclEd && aclEd.router === d.name ? aclEditor(d) : `<div class="row" style="margin-top:8px"><button class="btn small" onclick="aclNew()">${t("sicurezza.nuova_acl")}</button></div>`}`;
  }
  const au = auditOut[d.name];
  const audit = au ? `<ul class="audit">${au.findings.length ? au.findings.map((f) => `<li class="sev-${esc(f.severity)}"><b>${esc(tt(`sicurezza.gravita_${GRAVITA[f.severity] || "bassa"}`))}</b> ${esc(f.message)}</li>`).join("") : `<li class="sev-ok">${t("sicurezza.nessuna_falla")}</li>`}</ul>` : "";
  const us = usersOut[d.name];
  const utenti = us ? `<ul class="audit">${us.length ? us.map((u, i) => `<li class="sev-${u.storage === "cifrata" ? "ok" : u.storage === "chiaro" ? "high" : "medium"}">
      <div class="row"><b class="mono">${esc(u.username)}</b> <span>${th("sicurezza.privilegio", { livello: u.privilege })} · ${t(`sicurezza.password_${u.storage}`)}</span>
      <span style="flex:1"></span><button class="btn small danger" onclick="userRemove(${i})">${t("pannello.togli")}</button></div></li>`).join("")
    : `<li>${t("sicurezza.nessun_utente")}</li>`}</ul>` : "";
  const aperto = (aclEd && aclEd.router === d.name) || au || us ? "open" : "";
  return `<section class="card"><details data-sez="sicurezza" ${aperto}><summary><b>${t("sicurezza.titolo")}</b> <span class="muted">${router ? t("sicurezza.sotto_router") : t("sicurezza.sotto_switch")}</span></summary>
    ${acl}
    <h4>${t("sicurezza.password")} ${help("enable_secret")}</h4>
    <form onsubmit="hardenSubmit(event)"><div class="form-grid">
      <label class="full">${t("sicurezza.enable_secret")}<input name="enable_secret" class="mono" autocomplete="off" placeholder="${esc(tt("sicurezza.enable_secret_esempio"))}"></label>
      <label class="full switch-toggle"><input type="checkbox" name="cifra"> ${t("sicurezza.cifra")} ${help("cifra")}</label>
    </div><div class="row end"><button class="btn primary">${t("sicurezza.proteggi")}</button></div></form>
    <details data-sez="login" style="margin-top:8px"><summary class="muted">${t("sicurezza.login_titolo")}</summary>
      <p class="muted" style="font-size:12px">${t("sicurezza.login_aiuto")}</p>
      <form onsubmit="loginSubmit(event)"><div class="form-grid">
        <label>${t("sicurezza.utente")}<input name="username" class="mono" autocomplete="off"></label>
        <label>${t("sicurezza.password_utente")}<input name="secret" class="mono" autocomplete="off"></label>
        <label class="full">${t("sicurezza.enable_secret")}<input name="enable_secret" class="mono" autocomplete="off"></label>
      </div><div class="row end"><button class="btn">${t("sicurezza.accedi")}</button></div></form></details>
    <h4>${t("sicurezza.utenti")} ${help("utenti")}</h4>
    <div class="row"><button class="btn small" onclick="usersLoad()">${us ? t("sicurezza.aggiorna_utenti") : t("sicurezza.mostra_utenti")}</button></div>
    ${utenti}
    <h4>${t("sicurezza.controllo")} ${help("audit")}</h4>
    <div class="row"><button class="btn" onclick="auditRun()">${t("sicurezza.controlla")}</button></div>
    ${audit}</details></section>`;
}

/* ───────────── firewall: zone e regole ─────────────
 * Zone = porte con un nome e un livello di sicurezza; regole = eccezioni («da outside verso dmz, tcp 80: permetti»).
 * Il server le compila in access-list del firewall con le risposte (backend/firewall.py). */
const ZONE_PROPOSTE = { inside: 100, dmz: 50, outside: 0 };
function fwPick(n) { fwPort = fwPort === n ? null : n; dirty = false; inspSig = ""; renderInspector(); }
async function fwZoneSubmit(e) {
  const f = formData(e);
  await act("/api/firewall/zone", { name: sel, port: fwPort, zone: f.zone, level: Number(f.level), ip: f.ip, mask: f.mask },
    tt("firewall.zona_salvata", { zona: f.zone }));
}
async function fwZoneClear() {
  if (await chiedi(tt("firewall.conferma_togli_zona", { porta: shortPort(fwPort) }))) await act("/api/firewall/zone", { name: sel, port: fwPort, clear: true });
}
function fwZoneLevel(v) { const l = ZONE_PROPOSTE[v.trim().toLowerCase()]; const el = $("#fwLevel"); if (el && l !== undefined) el.value = l; }
const nuovaRegolaFw = (zone) => ({ from: zone[zone.length - 1] || "", to: zone[0] || "", action: "permit", protocol: "tcp", port: "80", dest: "" });
function fwEdit() {
  const d = dev(sel);
  fwEd = { fw: sel, rules: JSON.parse(JSON.stringify((S.firewall || {})[sel] || [])).map((r) => ({ ...r, port: r.port ?? "" })) };
  if (!fwEd.rules.length) fwEd.rules.push(nuovaRegolaFw(((d.fw && d.fw.zones) || []).map((z) => z.nameif)));
  aclRefresh();
}
function fwSet(i, k, v) { if (!fwEd) return; fwEd.rules[i][k] = v; if (k === "protocol") aclRefresh(); }
function fwRow(op, i) {
  if (!fwEd) return;
  const zone = ((dev(fwEd.fw).fw || {}).zones || []).map((z) => z.nameif);
  if (op === "add") fwEd.rules.push(nuovaRegolaFw(zone));
  else if (op === "del") fwEd.rules.splice(i, 1);
  else if (op === "up" && i > 0) [fwEd.rules[i - 1], fwEd.rules[i]] = [fwEd.rules[i], fwEd.rules[i - 1]];
  aclRefresh();
}
async function fwSave(e) {
  e.preventDefault();
  if (!fwEd) return;
  const rules = fwEd.rules.map((r) => ({ from: r.from, to: r.to, action: r.action, protocol: r.protocol,
    ...(r.port && ["tcp", "udp"].includes(r.protocol) ? { port: Number(r.port) } : {}), dest: r.dest || "", source: r.source || "" }));
  const d = await act("/api/firewall/rules", { name: fwEd.fw, rules }, tt("firewall.regole_salvate"));
  if (d) { fwEd = null; aclRefresh(); }
}
function fwCancel() { fwEd = null; aclRefresh(); }

function firewallPanel(d) {
  const zone = (d.fw && d.fw.zones) || [];
  const zonaDi = (pn) => zone.find((z) => z.port === pn);
  const rows = d.ports.filter((p) => !p.n.startsWith("Management")).map((p) => {
    const z = zonaDi(p.n), peer = peerOf(d, p.n);
    const st = !p.on ? "off" : p.up && peer ? "up" : "wait";
    return `<tr class="pick ${fwPort === p.n ? "cur" : ""}" onclick="fwPick('${esc(p.n)}')">
      <td><b>${esc(shortPort(p.n))}</b></td><td>${z ? `<b>${esc(z.nameif)}</b> <span class="muted">${esc(z.level)}</span>` : "—"}</td>
      <td class="mono">${p.ip && p.ip !== "0.0.0.0" ? `${esc(p.ip)}/${maskBits(p.mask)}` : "—"}</td><td><i class="dot ${st}"></i> ${peer ? esc(peer.dev) : ""}</td></tr>`;
  }).join("");
  const p = fwPort && d.ports.find((x) => x.n === fwPort);
  const z = p && zonaDi(p.n);
  const edit = p ? `<form onsubmit="fwZoneSubmit(event)" class="card flat" style="margin-top:10px"><h4>${esc(p.n)}</h4><div class="form-grid">
      <label>${t("firewall.zona")} ${help("fw_zona")}<input name="zone" list="fwZone" value="${esc(z ? z.nameif : "")}" placeholder="inside" oninput="fwZoneLevel(this.value)"></label>
      <datalist id="fwZone"><option value="inside"><option value="dmz"><option value="outside"></datalist>
      <label>${t("firewall.livello")} ${help("fw_livello")}<input id="fwLevel" name="level" inputmode="numeric" value="${esc(z && z.level !== null ? z.level : 50)}"></label>
      <label>${t("pc.ip")}<input name="ip" value="${esc(p.ip && p.ip !== "0.0.0.0" ? p.ip : "")}"></label>
      <label>${t("pc.maschera")}<input name="mask" value="${esc(p.mask && p.mask !== "0.0.0.0" ? p.mask : "255.255.255.0")}"></label>
    </div><div class="row end">${z ? `<button type="button" class="btn danger" onclick="fwZoneClear()">${t("firewall.togli_zona")}</button>` : ""}
      <button class="btn primary">${t("pannello.applica")}</button></div></form>`
    : `<p class="muted" style="font-size:12.5px;margin:8px 0 0">${t("firewall.scegli_porta")}</p>`;
  const regole = (S.firewall || {})[d.name] || [];
  const nomiZone = zone.map((x) => x.nameif);
  const opt = (v, cur, label) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label ?? v)}</option>`;
  let ed = "";
  if (fwEd && fwEd.fw === d.name) {
    // una regola = un riquadro con i campi su due colonne: nella scheda stretta una tabella a 7 colonne non ci sta
    const righe = fwEd.rules.map((r, i) => `<div class="fw-regola">
      <div class="fw-regola-testa"><b>${th("firewall.regola_n", { numero: i + 1 })}</b>
        <span>${i > 0 ? `<button type="button" class="btn small" title="${esc(tt("firewall.sposta_su"))}" onclick="fwRow('up',${i})">↑</button>` : ""}<button type="button" class="btn small danger" title="${esc(tt("firewall.togli_regola"))}" onclick="fwRow('del',${i})">✕</button></span></div>
      <div class="form-grid">
        <label>${t("firewall.da")}<select onchange="fwSet(${i},'from',this.value)">${nomiZone.map((x) => opt(x, r.from)).join("")}</select></label>
        <label>${t("firewall.verso")}<select onchange="fwSet(${i},'to',this.value)">${nomiZone.map((x) => opt(x, r.to)).join("")}</select></label>
        <label>${t("sicurezza.azione")}<select onchange="fwSet(${i},'action',this.value)">${opt("permit", r.action, tt("sicurezza.permetti"))}${opt("deny", r.action, tt("sicurezza.nega"))}</select></label>
        <label>${t("sicurezza.protocollo")}<select onchange="fwSet(${i},'protocol',this.value)">${["ip", "icmp", "tcp", "udp"].map((x) => opt(x, r.protocol, tt(`sicurezza.protocollo_${x}`))).join("")}</select></label>
        ${["tcp", "udp"].includes(r.protocol) ? `<label class="full">${t("sicurezza.porta_servizio")}<input class="mono" inputmode="numeric" value="${esc(r.port)}" oninput="fwSet(${i},'port',this.value)"></label>` : ""}
        <label class="full">${t("firewall.sorgente")} <span class="muted">${t("firewall.vuoto_tutta_la_zona")}</span><input class="mono" value="${esc(r.source || "")}" oninput="fwSet(${i},'source',this.value)"></label>
        <label class="full">${t("firewall.destinazione")} ${help("fw_destinazione")} <span class="muted">${t("firewall.vuoto_tutta_la_zona")}</span><input class="mono" value="${esc(r.dest || "")}" oninput="fwSet(${i},'dest',this.value)"></label>
      </div></div>`).join("");
    ed = `<form onsubmit="fwSave(event)" class="card flat" style="margin-top:10px">
      ${righe}
      <div class="row" style="margin-top:6px"><button type="button" class="btn small" onclick="fwRow('add')">${t("sicurezza.aggiungi_regola")}</button>
        <span style="flex:1"></span><button type="button" class="btn small" onclick="fwCancel()">${t("generale.annulla")}</button>
        <button class="btn primary">${t("firewall.salva_regole")}</button></div></form>`;
  } else {
    ed = `<ul class="routes">${regole.length ? regole.map((r) => `<li>${esc(r.from)} → ${esc(r.to)}: <b>${esc(tt(r.action === "permit" ? "sicurezza.permetti" : "sicurezza.nega"))}</b> ${esc(r.protocol)}${r.port ? ` ${esc(r.port)}` : ""}${r.source ? ` ${esc(tt("firewall.da_indirizzo", { ip: r.source }))}` : ""}${r.dest ? ` → ${esc(r.dest)}` : ""}</li>`).join("") : `<li>${t("firewall.nessuna_regola")}</li>`}</ul>
      <div class="row" style="margin-top:8px"><button class="btn small" onclick="fwEdit()" ${zone.length < 2 ? "disabled" : ""}>${t("firewall.modifica_regole")}</button></div>`;
  }
  const acl = Object.entries((d.fw && d.fw.acls) || {}).map(([n, r]) => `${n}\n  ${r.join("\n  ")}`).join("\n");
  return `<section class="card"><h3>${t("firewall.zone")} ${help("fw_zone")}</h3>
    <table class="ports"><tr><th>${t("router.colonna_porta")}</th><th>${t("firewall.zona")}</th><th>${t("router.colonna_ip")}</th><th>${t("router.colonna_verso")}</th></tr>${rows}</table>${edit}</section>
    <section class="card"><h3>${t("firewall.regole")} ${help("fw_regole")}</h3>
      <p class="muted" style="font-size:12px;margin:0 0 6px">${t("firewall.base")}</p>${ed}
      ${acl ? `<details data-sez="fw_acl" style="margin-top:8px"><summary class="muted">${t("firewall.acl_scritte")}</summary><pre class="console">${esc(acl)}</pre></details>` : ""}</section>
    <section class="card"><details data-sez="fw_rotte"><summary><b>${t("firewall.rotte")}</b> <span class="muted">${t("firewall.rotte_sotto")}</span></summary>
      <ul class="routes">${((d.fw && d.fw.routes) || []).map((r) => `<li><b>${esc(r.zone)}</b> ${esc(r.net)}/${maskBits(r.mask)} via ${esc(r.via)}
        <button class="btn small" style="padding:0 6px" onclick="fwRouteDel('${esc(r.net)}','${esc(r.mask)}','${esc(r.via)}')">✕</button></li>`).join("") || `<li>${t("firewall.nessuna_rotta")}</li>`}</ul>
      <form onsubmit="fwRouteAdd(event)"><h4>${t("router.nuova_rotta")}</h4><div class="form-grid">
        <label>${t("router.rete_destinazione")}<input name="network"></label>
        <label>${t("pc.maschera")}<input name="mask" value="255.255.255.0"></label>
        <label class="full">${t("router.via")}<input name="via"></label>
      </div><div class="row end"><button class="btn primary">${t("router.aggiungi_rotta")}</button></div></form></details></section>`;
}
async function fwRouteAdd(e) { const f = formData(e); await act("/api/firewall/route", { name: sel, ...f }, tt("router.rotta_aggiunta")); }
async function fwRouteDel(network, mask, via) { await act("/api/firewall/route", { name: sel, network, mask, via, remove: true }); }

/* ───────────── servizi esposti di un server ───────────── */
const SERVIZI_UI = [["http", 80], ["https", 443], ["ftp", 21], ["email", "25 · 110"]];
async function servLoad() { const name = sel; const d = await act("/api/server/services", { name }); if (d && d.services) { servOut[name] = d.services; inspSig = ""; renderInspector(); } }
async function servSet(service, on) {
  const name = sel; const d = await act("/api/server/service", { name, service, on }, tt("servizi.cambiato", { servizio: service.toUpperCase(), stato: tt(on ? "servizi.acceso" : "servizi.spento") }));
  if (d && d.services) { servOut[name] = d.services; inspSig = ""; renderInspector(); }
}
function servicesPanel(d) {
  const s = servOut[d.name];
  const on = (k) => (k === "email" ? s.smtp || s.pop3 : s[k]);
  const lista = s ? `<table class="ports">${SERVIZI_UI.map(([k, porta]) => `<tr><td><b>${esc(tt(`servizi.${k}`))}</b></td><td class="mono">${esc(porta)}</td>
      <td>${on(k) ? `<span class="tag-on">${t("servizi.aperta")}</span>` : `<span class="muted">${t("servizi.chiusa")}</span>`}</td>
      <td><button class="btn small ${on(k) ? "danger" : ""}" onclick="servSet('${k}', ${!on(k)})">${on(k) ? t("servizi.spegni") : t("servizi.accendi")}</button></td></tr>`).join("")}</table>
      <p class="muted" style="font-size:12px;margin:6px 0 0">${t("servizi.nota")}</p>` : "";
  return `<section class="card"><details data-sez="servizi" ${s ? "open" : ""}><summary><b>${t("servizi.titolo")}</b> <span class="muted">${t("servizi.sotto")}</span></summary>
    <div class="row" style="margin:8px 0"><button class="btn small" onclick="servLoad()">${s ? t("servizi.aggiorna") : t("servizi.mostra")}</button></div>${lista}</details></section>`;
}

/* ───────────── il DNS di un server: servizio e record «nome → IP» ───────────── */
function dnsPanel(d) {
  const x = (S.dns || {})[d.name] || { on: false, records: {} };
  const rec = Object.entries(x.records || {});
  return `<section class="card"><details data-sez="dns" ${x.on || rec.length ? "open" : ""}><summary><b>${t("dns.titolo")}</b>
      <span class="muted">${x.on ? t("dns.acceso") : t("dns.spento")}</span></summary>
    <div class="row" style="margin:8px 0"><button class="btn small ${x.on ? "danger" : "primary"}" onclick="dnsOn(${!x.on})">${x.on ? t("dns.spegni") : t("dns.accendi")}</button></div>
    <ul class="routes">${rec.length ? rec.map(([n, ip]) => `<li><b class="mono">${esc(n)}</b> → <span class="mono">${esc(ip)}</span>
      <button class="btn small" style="padding:0 6px" onclick="dnsDel('${esc(n)}')">✕</button></li>`).join("") : `<li>${t("dns.nessun_record")}</li>`}</ul>
    <form onsubmit="dnsAdd(event)"><h4>${t("dns.nuovo_record")}</h4><div class="form-grid">
      <label class="full">${t("dns.nome")}<input name="nome" autocapitalize="off" spellcheck="false"></label>
      <label class="full">${t("pc.ip")}<input name="ip" inputmode="decimal"></label>
    </div><div class="row end"><button class="btn primary">${t("dns.aggiungi")}</button></div></form></details></section>`;
}
async function dnsOn(on) { await act("/api/server/dns", { name: sel, on }, tt(on ? "dns.acceso" : "dns.spento")); inspSig = ""; renderInspector(); }
async function dnsAdd(e) {
  const f = formData(e);
  await act("/api/server/dns", { name: sel, add: [{ name: f.nome.trim(), ip: f.ip.trim() }] }, tt("dns.record_aggiunto", { nome: f.nome.trim() }));
  inspSig = ""; renderInspector();
}
async function dnsDel(nome) { await act("/api/server/dns", { name: sel, remove: [nome] }); inspSig = ""; renderInspector(); }

/* ───────────── scanner di porte (come nmap) ───────────── */
function scanSubmit(e) { const f = formData(e); job("/api/scan", { from: sel, to: f.to.trim() }); }
function scanPanel(d) {
  const r = scanOut[d.name];
  const res = r ? `<p style="margin:8px 0 4px">${th("scansione.risultato", { ip: r.ip })}</p><table class="ports">${r.ports.map((p) => `<tr class="scan-${esc(p.state)}"><td class="mono"><b>${esc(p.port)}</b></td><td>${esc(p.service)}</td><td>${esc(tt(`scansione.${p.state}`))}</td></tr>`).join("")}</table>` : "";
  return `<section class="card"><details data-sez="scan" ${r ? "open" : ""}><summary><b>${t("scansione.titolo")}</b> <span class="muted">${t("scansione.sotto")}</span></summary>
    <form onsubmit="scanSubmit(event)" class="row" style="margin-top:8px"><input name="to" class="mono" style="flex:1" placeholder="${esc(tt("scansione.bersaglio"))}">
    <button class="btn primary">${t("scansione.avvia")}</button></form>
    <p class="muted" style="font-size:12px;margin:6px 0 0">${t("scansione.aiuto")}</p>${res}</details></section>`;
}

/* ───────────── sniffer (come tcpdump / Wireshark) ───────────── */
function sniffRun() { job("/api/sniff", { name: sel }); }
function sniffPanel(d) {
  const c = sniffOut[d.name];
  const righe = c ? c.frames.map((f) => `<tr class="${f.status === "dropped" ? "sniff-drop" : ""}">
      <td class="mono">${esc(f.n)}</td><td>${esc(f.src_dev || "")} <span class="mono muted">${esc(f.src)}</span></td>
      <td>${esc(f.dst_dev || "")} <span class="mono muted">${esc(f.dst)}</span></td>
      <td><b>${esc(f.app || f.proto)}</b>${f.port ? ` <span class="mono">:${esc(f.port)}</span>` : ""}</td><td>${esc(tt(`sniffer.stato_${f.status}`) )}</td></tr>`).join("") : "";
  return `<section class="card"><details data-sez="sniffer" ${c ? "open" : ""}><summary><b>${t("sniffer.titolo")}</b> <span class="muted">${t("sniffer.sotto")}</span></summary>
    <div class="row" style="margin:8px 0"><button class="btn" onclick="sniffRun()">${t("sniffer.cattura")}</button></div>
    <p class="muted" style="font-size:12px;margin:0">${t("sniffer.aiuto")}</p>
    ${c ? `<div class="sniff-box"><table class="ports sniff"><tr><th>#</th><th>${t("sniffer.da")}</th><th>${t("sniffer.a")}</th><th>${t("sniffer.protocollo")}</th><th>${t("sniffer.esito")}</th></tr>${righe || `<tr><td colspan="5" class="muted">${t("sniffer.vuoto")}</td></tr>`}</table></div>` : ""}</details></section>`;
}

/* ───────────── limiti di velocità (rate limiting) di un router ───────────── */
async function limLoad() { const name = sel; const d = await act("/api/router/ratelimit", { name }); if (d && d.limits) { limOut[name] = d.limits; inspSig = ""; renderInspector(); } }
async function limSubmit(e) {
  const f = formData(e); const name = sel;
  const d = await act("/api/router/ratelimit", { name, port: f.port, kbps: Number(f.kbps || 0) }, tt("limiti.salvato"));
  if (d && d.limits) { limOut[name] = d.limits; inspSig = ""; renderInspector(); }
}
function rateLimitPanel(d) {
  const l = limOut[d.name];
  const lista = l ? `<ul class="routes">${Object.keys(l).length ? Object.entries(l).map(([p, k]) => `<li>${esc(shortPort(p))}: ${esc(k)} kbps</li>`).join("") : `<li>${t("limiti.nessuno")}</li>`}</ul>` : "";
  return `<section class="card"><details data-sez="limiti" ${l ? "open" : ""}><summary><b>${t("limiti.titolo")}</b> <span class="muted">${t("limiti.sotto")}</span></summary>
    <div class="row" style="margin:8px 0"><button class="btn small" onclick="limLoad()">${l ? t("limiti.aggiorna") : t("limiti.mostra")}</button></div>${lista}
    <form onsubmit="limSubmit(event)"><div class="form-grid">
      <label>${t("limiti.porta")}<select name="port">${d.ports.map((p) => `<option value="${esc(p.n)}">${esc(shortPort(p.n))}${peerOf(d, p.n) ? ` → ${esc(peerOf(d, p.n).dev)}` : ""}</option>`).join("")}</select></label>
      <label>${t("limiti.kbps")} ${help("limite")}<input name="kbps" inputmode="numeric" placeholder="512"></label>
    </div><div class="row end"><button class="btn primary">${t("limiti.applica")}</button></div></form>
    <p class="muted" style="font-size:12px;margin:6px 0 0">${t("limiti.nota")}</p></details></section>`;
}

/* avvio: prima testi e impostazioni, poi il gioco */
caricaInterfaccia().then((ok) => ok && start());
