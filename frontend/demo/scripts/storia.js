"use strict";
/* Penguin Inc: la storyline sopra il motore del gioco (app.js).
 *
 * L'ufficio (scena in pixel art) è la schermata principale: il protagonista cammina, parla con Jody (il
 * collega senior), offre caffè, e quando si siede alla scrivania si apre il «monitor», cioè l'interfaccia
 * del gioco (app.js) sul tavolo, cioè sulla rete vera costruita con Mininet.
 *
 * Testi, giornate e aiuti sono in frontend/demo/gioco/storia.yaml (arrivano in TESTI.storia). Qui c'è solo
 * il motore: scena, movimenti, dialoghi, avanzamento delle giornate e buoni caffè.
 *
 * Il progresso della storia (giornata, dialoghi visti, buoni spesi) è nel browser (localStorage); quello di
 * gioco (missioni, livelli) resta sul server. Una giornata è finita quando sul server risultano
 * completate le sue missioni dell'Accademia, o vinti i suoi livelli del Pronto Soccorso.
 *
 * Usa le funzioni globali di app.js: $, $$, esc, t, tt, api, act, job, applyState, chiedi, toast, S.
 */

const Storia = (() => {
  const IMG = "/assets/penguin/";
  const W = 384, H = 288;            // dimensioni della scena in pixel originali
  const CHIAVE = "penguinInc.storia.v1";

  /* ── geometria dell'ufficio (coordinate dell'immagine 384×288; i punti sono i piedi) ── */
  const ENTRATA = { x: 123, y: 272 };
  const DOVE = {
    computer: { percorso: [[123, 100], [166, 100]], guarda: "dx" },
    collega:  { percorso: [[123, 252], [282, 252]], guarda: "dx" },
    caffe:    { percorso: [[123, 86], [80, 86]], guarda: "su" },
    capo:     { percorso: [[123, 70], [314, 70]], guarda: "su" },
  };
  const ZONE = {                     // aree cliccabili [x, y, larghezza, altezza]
    computer: [176, 108, 72, 58],
    collega:  [284, 192, 46, 54],
    caffe:    [52, 18, 46, 56],
    capo:     [288, 4, 54, 50],
  };
  /* Dove si può camminare (i piedi): il pavimento meno gli oggetti, rettangoli [x1, y1, x2, y2] misurati su
   * ufficio.png. I percorsi si calcolano su una griglia di CELLA unità e girano il meno possibile. */
  const PAVIMENTO = [[26, 62, 358, 260], [104, 62, 142, 280]];      // la stanza e il corridoio dell'entrata
  const OSTACOLI = [
    [48, 0, 100, 78],      // macchinetta del caffè
    [20, 0, 50, 70],       // pianta
    [18, 164, 84, 254],    // scrivania a sinistra con la sedia
    [168, 104, 338, 246],  // le quattro scrivanie, con le sedie
    [280, 0, 344, 60],     // porta del Capo (si entra dal punto davanti)
  ];
  const CELLA = 4;
  const SEDIA_JR = { x: 182, y: 97 };   // angolo in alto a sinistra dello sprite seduto (32×40)
  const SEDIA_SR = { x: 291, y: 201 };
  const RIGA = { giu: 0, sx: 1, dx: 2, su: 3 };

  let ST = null;            // stato della storia salvato nel browser
  let STORIA = null;        // storia.yaml
  let vista = "ufficio";    // titolo | ufficio | monitor
  let occupato = false;     // una scena (dialogo, camminata) è in corso
  let aiutoInCorso = false;
  let saltaOra = false;     // collaudo: Esc ha chiesto di saltare il dialogo in corso
  const CHEAT = leggiCheat(); // collaudo: {salta, veloce, aperto}, per questa scheda del browser
  function leggiCheat() {
    try { return { salta: false, veloce: false, aperto: false, ...JSON.parse(sessionStorage.getItem("penguinInc.collaudo") || "{}") }; }
    catch { return { salta: false, veloce: false, aperto: false }; }
  }
  const jr = { el: null, x: ENTRATA.x, y: ENTRATA.y, dove: null, seduto: false };
  const sr = { el: null, x: 150, y: 246, seduto: true };

  /* ───────────── stato salvato ───────────── */
  // caffe: i caffè presi alla macchinetta e non ancora dati a Jody (ognuno costa un buono)
  // guida: i pop-up di Jody sul monitor già visti ({"g1-lan:passo:3": true})
  const nuovo = () => ({ giorno: 1, intro: {}, fine: {}, aiuti: {}, spesi: 0, caffe: 0, avviati: {}, vinti: {}, inSospeso: null, finale: false, guida: {}, briefing: {} });
  function carica() {
    try { ST = { ...nuovo(), ...JSON.parse(localStorage.getItem(CHIAVE) || "{}") }; } catch { ST = nuovo(); }
  }
  function salva() { try { localStorage.setItem(CHIAVE, JSON.stringify(ST)); } catch { /* niente */ } }

  /* ───────────── giornate ───────────── */
  const giorni = () => STORIA.giorni || [];
  const giorno = (n = ST.giorno) => giorni().find((g) => g.numero === n);
  const ultimo = () => Math.max(0, ...giorni().map((g) => g.numero));
  const s = (k, v) => t(`storia.interfaccia.${k}`, v);
  const ss = (k, v) => tt(`storia.interfaccia.${k}`, v);

  function completato(g = giorno()) {
    if (!g || !S) return false;
    const m = g.monitor || {};
    if (m.modalita === "rescue") return (m.livelli || []).every((n) => ST.vinti[n]);
    return (m.missioni || []).every((id) => (S.academy.missions.find((x) => x.id === id) || {}).done);
  }
  function buoni() {
    const guadagnati = giorni().filter((g) => ST.fine[g.numero]).reduce((a, g) => a + (g.buoni || 0), 0);
    return Math.max(0, guadagnati - ST.spesi);
  }

  /* ───────────── avvio ───────────── */
  function avvia() {
    STORIA = TESTI.storia;
    if (!STORIA || !giorni().length) {  // senza storia.yaml: il client classico
      $("#app").classList.remove("hidden");
      return false;
    }
    carica();
    document.body.classList.add("storia");
    costruisciScena();
    $("#btnUfficio").addEventListener("click", tornaInUfficio);
    $("#btnUfficio span").textContent = ss("torna_in_ufficio");
    $("#btnNuova").textContent = ss("nuova_partita");
    $("#btnNuova").addEventListener("click", nuovaPartita);
    if (COLLAUDO) collaudo();
    preparaMusica();
    // dopo un cheat che ricarica la pagina si torna dritti in ufficio, senza ripassare dal titolo
    let salta = false;
    try { salta = sessionStorage.getItem("penguinInc.saltaTitolo") === "1"; sessionStorage.removeItem("penguinInc.saltaTitolo"); } catch { /* niente */ }
    if (salta && ST.nome) { mostraUfficio(); entraInUfficio(); }
    else mostraTitolo();
    return true;
  }

  /* ───────────── schermata iniziale: titolo e nome del protagonista ───────────── */
  const ti = (k, v) => tt(`storia.schermata_iniziale.${k}`, v);
  const NOME_MAX = 16;
  const pulisciNome = (v) => String(v || "").replace(/\s+/g, " ").trim().slice(0, NOME_MAX);
  const inCorso = () => !!(ST.nome || Object.keys(ST.intro).length);

  function mostraTitolo() {
    vista = "titolo";
    $("#app").classList.add("hidden");
    $("#ufficio").classList.add("hidden");
    $("#cartello").classList.add("hidden");
    $("#titoloLogo").textContent = STORIA.titolo || "";
    $("#titoloSotto").textContent = STORIA.sottotitolo || "";
    $("#titoloDomanda").textContent = ti("domanda_nome");
    $("#titoloNota").textContent = ti("nota");
    $("#titoloErr").textContent = "";
    const input = $("#nomeGiocatore");
    input.placeholder = ti("esempio");
    input.value = ST.nome || "";
    const targhetta = () => { $("#titoloTarghetta").textContent = pulisciNome(input.value) || "?"; };
    input.oninput = () => { targhetta(); $("#titoloErr").textContent = ""; };
    targhetta();
    const g = giorno();
    const azioni = inCorso()
      ? [["continua", g ? ti("continua", { giorno: g.numero }) : ti("continua_finale"), "primary"], ["nuova", ti("nuova"), ""]]
      : [["inizia", ti("inizia"), "primary"]];
    $("#titoloAzioni").innerHTML = azioni.map(([k, testo, cls], i) =>
      `<button class="btn ${cls}" type="${i ? "button" : "submit"}" data-titolo="${k}">${esc(testo)}</button>`).join("");
    $$("#titoloAzioni button[type=button]").forEach((b) => b.addEventListener("click", () => sceltaTitolo(b.dataset.titolo)));
    $("#titoloForm").onsubmit = (e) => { e.preventDefault(); sceltaTitolo(azioni[0][0]); };
    if (STORIA.crediti && !$("#titoloCrediti")) {   // «Crediti» sotto la nota: i titoli di coda senza finire il gioco
      const b = document.createElement("button");
      b.type = "button"; b.id = "titoloCrediti"; b.className = "titolo-crediti";
      b.textContent = STORIA.crediti.pulsante || "Crediti";
      b.addEventListener("click", crediti);
      $("#titoloForm").appendChild(b);
    }
    $("#titolo").classList.remove("hidden");
    setTimeout(() => input.focus(), 50);
  }

  async function sceltaTitolo(scelta) {
    const nome = pulisciNome($("#nomeGiocatore").value);
    if (!nome) { $("#titoloErr").textContent = ti("nome_mancante"); $("#nomeGiocatore").focus(); return; }
    if (scelta === "nuova") {
      if (!(await chiedi(ti("conferma_nuova")))) return;
      ST = nuovo();
      if (S && S.mode !== "academy") await api("/api/mode", { mode: "academy" });
      job("/api/academy/reset", {});
    }
    ST.nome = nome; salva();
    musica();
    $("#titolo").classList.add("hidden");
    mostraUfficio();
    occupato = false;
    entraInUfficio();
  }

  function costruisciScena() {
    const sc = $("#scena");
    sc.innerHTML = `<img class="sfondo" id="sfondo" src="${IMG}scene/ufficio.png" alt="">
      ${Object.entries(ZONE).map(([k, [x, y, w, h]]) => `<button class="zona" data-zona="${k}"
        style="left:${pc(x, W)};top:${pc(y, H)};width:${pc(w, W)};height:${pc(h, H)}" aria-label="${esc(ss(`oggetti.${k}`))}"></button>`).join("")}
      <div class="sprite" id="srSprite"></div><div class="sprite" id="jrSprite"></div>
      <div class="segnale hidden" id="segnale">!</div>
      <div class="war-room-scena hidden" id="warRoom">
        <img class="war-room-sfondo" src="${IMG}scene/war_room.png" alt="">
        <div class="sprite war-room-capo" id="wrCapo"></div><div class="sprite" id="wrJr"></div>
        <div class="war-room-nome pix">WAR ROOM</div>
      </div>
      <div class="etichetta hidden" id="etichetta"></div>`;
    jr.el = $("#jrSprite"); sr.el = $("#srSprite");
    $$(".zona", sc).forEach((z) => {
      z.addEventListener("click", (e) => { e.stopPropagation(); z.blur(); usa(z.dataset.zona); });
      z.addEventListener("pointerenter", () => etichetta(z.dataset.zona));
      z.addEventListener("pointerleave", () => etichetta(null));
    });
    sc.addEventListener("click", (e) => {
      if (occupato || e.target.closest(".zona")) return;
      const r = sc.getBoundingClientRect();
      passeggia((e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H);
    });
    piazzaJr(); piazzaSr();
    setInterval(sbatti, 2600);
  }
  const pc = (v, tot) => `${(v / tot * 100).toFixed(3)}%`;

  function etichetta(k) {
    const el = $("#etichetta");
    if (!k || occupato) { el.classList.add("hidden"); return; }
    const [x, y, w] = ZONE[k];
    el.textContent = ss(`oggetti.${k}`);
    el.style.left = pc(x + w / 2, W); el.style.top = pc(Math.max(y - 4, 10), H);
    el.classList.remove("hidden");
  }

  /* ───────────── sprite ───────────── */
  function foglio(el, file, cols, rows, col, row, w, h) {
    el.style.backgroundImage = `url(${IMG}sprite/${file})`;
    el.style.backgroundSize = `${cols * 100}% ${rows * 100}%`;
    el.style.backgroundPosition = `${cols > 1 ? col * 100 / (cols - 1) : 0}% ${rows > 1 ? row * 100 / (rows - 1) : 0}%`;
    el.style.width = pc(w, W); el.style.height = pc(h, H);
  }
  function inPiedi(a, file, dir = "giu", passo = 0) {
    foglio(a.el, file, 4, 4, passo, RIGA[dir], 32, 32);
    a.el.style.left = pc(a.x - 16, W); a.el.style.top = pc(a.y - 31, H);
    a.el.style.zIndex = 10 + Math.round(a.y);
  }
  function piazzaJr(dir = "su") {
    if (jr.seduto) {
      foglio(jr.el, "jr_seduto.png", 5, 1, 0, 0, 32, 40);
      jr.el.style.left = pc(SEDIA_JR.x, W); jr.el.style.top = pc(SEDIA_JR.y, H); jr.el.style.zIndex = 10 + SEDIA_JR.y + 30;
    } else inPiedi(jr, "jr_cammina.png", dir);
  }
  function piazzaSr(dir = "giu") {
    if (sr.seduto) {
      foglio(sr.el, "sr_seduto.png", 5, 1, 4, 0, 32, 40);     // di spalle, al suo computer
      sr.el.style.left = pc(SEDIA_SR.x, W); sr.el.style.top = pc(SEDIA_SR.y, H); sr.el.style.zIndex = 10 + SEDIA_SR.y + 30;
    } else inPiedi(sr, "sr_cammina.png", dir);
  }
  function sbatti() {   // il protagonista seduto ogni tanto sbatte le palpebre
    if (!jr.seduto) return;
    foglio(jr.el, "jr_seduto.png", 5, 1, 1, 0, 32, 40);
    setTimeout(() => jr.seduto && foglio(jr.el, "jr_seduto.png", 5, 1, 0, 0, 32, 40), 160);
  }

  /* Cammina lungo i punti dati, un segmento alla volta, con l'animazione del passo. */
  function cammina(a, file, punti) {
    return new Promise((fine) => {
      const coda = punti.map(([x, y]) => ({ x, y }));
      // pagina non visibile (scheda in secondo piano): il browser ferma le animazioni, quindi si arriva subito
      if (document.hidden && coda.length) {
        const p = coda[coda.length - 1];
        a.x = p.x; a.y = p.y; inPiedi(a, file, "giu", 0); fine("giu"); return;
      }
      let passo = 0, ultimoPasso = 0, prima = null, dir = "giu";
      // se il browser non anima la pagina (scheda o pannello in secondo piano), si arriva comunque a destinazione
      const guardia = setTimeout(() => {
        if (prima !== null) return;
        const p = coda[coda.length - 1];
        coda.length = 0;
        if (p) { a.x = p.x; a.y = p.y; }
        inPiedi(a, file, "giu", 0); fine("giu");
      }, 400);
      const vel = 130 * (COLLAUDO && CHEAT.veloce ? 4 : 1);  // pixel originali al secondo (×4 in collaudo)
      function frame(ts) {
        if (prima === null) { prima = ts; clearTimeout(guardia); }
        const dt = Math.min(0.05, (ts - prima) / 1000); prima = ts;
        const p = coda[0];
        if (!p) { inPiedi(a, file, dir, 0); fine(dir); return; }
        const dx = p.x - a.x, dy = p.y - a.y, d = Math.hypot(dx, dy);
        if (d < 0.5) { a.x = p.x; a.y = p.y; coda.shift(); requestAnimationFrame(frame); return; }
        dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "dx" : "sx") : (dy > 0 ? "giu" : "su");
        const k = Math.min(1, vel * dt / d);
        a.x += dx * k; a.y += dy * k;
        if (ts - ultimoPasso > 140) { passo = (passo + 1) % 4; ultimoPasso = ts; }
        inPiedi(a, file, dir, passo);
        requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  async function alzati() {
    if (!jr.seduto) return;
    jr.seduto = false; jr.x = 166; jr.y = 100; piazzaJr("sx");
    await pausa(120);
  }
  async function vaiA(dove) {
    await alzati();
    if (jr.dove === dove) return;
    const p = DOVE[dove];
    const arrivo = p.percorso[p.percorso.length - 1];
    await cammina(jr, "jr_cammina.png", percorsoLibero(jr, { x: arrivo[0], y: arrivo[1] }));
    jr.dove = dove;
    piazzaJr(p.guarda);
  }
  async function passeggia(x, y) {
    occupato = true;
    await alzati();
    await cammina(jr, "jr_cammina.png", percorsoLibero(jr, { x, y }));
    jr.dove = null;
    occupato = false;
  }

  /* ── percorsi che non passano sopra gli oggetti ── */
  const GC = Math.ceil(W / CELLA), GR = Math.ceil(H / CELLA);
  const libera = (() => {
    const g = new Uint8Array(GC * GR);
    for (let r = 0; r < GR; r++) for (let c = 0; c < GC; c++) {
      const x = c * CELLA + CELLA / 2, y = r * CELLA + CELLA / 2;
      const dentro = (q) => x >= q[0] && x <= q[2] && y >= q[1] && y <= q[3];
      g[r * GC + c] = PAVIMENTO.some(dentro) && !OSTACOLI.some(dentro) ? 1 : 0;
    }
    return g;
  })();
  const cella = (p) => [Math.min(GC - 1, Math.max(0, Math.floor(p.x / CELLA))), Math.min(GR - 1, Math.max(0, Math.floor(p.y / CELLA)))];
  function piuVicinaLibera(c, r) {   // la cella calpestabile più vicina (per un clic sopra un oggetto)
    if (libera[r * GC + c]) return [c, r];
    let meglio = null, d = Infinity;
    for (let rr = 0; rr < GR; rr++) for (let cc = 0; cc < GC; cc++) {
      if (!libera[rr * GC + cc]) continue;
      const dd = (cc - c) ** 2 + (rr - r) ** 2;
      if (dd < d) { d = dd; meglio = [cc, rr]; }
    }
    return meglio || [c, r];
  }
  /* Dijkstra su (cella, direzione): 1 per passo, SVOLTA per ogni curva, così il pinguino cammina dritto e gira
   * negli angoli. Restituisce i punti dove cambia direzione, poi la destinazione. */
  function percorsoLibero(da, a) {
    const SVOLTA = 12, DIR = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    const [c0, r0] = piuVicinaLibera(...cella(da));
    const [c1, r1] = piuVicinaLibera(...cella(a));
    const meta = { x: c1 === cella(a)[0] && r1 === cella(a)[1] ? a.x : c1 * CELLA + CELLA / 2,
                   y: c1 === cella(a)[0] && r1 === cella(a)[1] ? a.y : r1 * CELLA + CELLA / 2 };
    const N = GC * GR * 4, costo = new Float64Array(N).fill(Infinity), prima = new Int32Array(N).fill(-1);
    const coda = [];   // [costo, stato] in un heap minimo
    const metti = (k, st) => {
      coda.push([k, st]); let i = coda.length - 1;
      while (i) { const j = (i - 1) >> 1; if (coda[j][0] <= coda[i][0]) break; [coda[i], coda[j]] = [coda[j], coda[i]]; i = j; }
    };
    const togli = () => {
      const top = coda[0], last = coda.pop();
      if (coda.length) {
        coda[0] = last; let i = 0;
        for (;;) {
          const l = 2 * i + 1, r = l + 1; let m = i;
          if (l < coda.length && coda[l][0] < coda[m][0]) m = l;
          if (r < coda.length && coda[r][0] < coda[m][0]) m = r;
          if (m === i) break; [coda[i], coda[m]] = [coda[m], coda[i]]; i = m;
        }
      }
      return top;
    };
    for (let d = 0; d < 4; d++) { const st = (r0 * GC + c0) * 4 + d; costo[st] = 0; metti(0, st); }
    let fine = -1;
    while (coda.length) {
      const [k, st] = togli();
      if (k > costo[st]) continue;
      const pos = st >> 2, d = st & 3, c = pos % GC, r = (pos / GC) | 0;
      if (c === c1 && r === r1) { fine = st; break; }
      for (let nd = 0; nd < 4; nd++) {
        const cc = c + DIR[nd][0], rr = r + DIR[nd][1];
        if (cc < 0 || rr < 0 || cc >= GC || rr >= GR || !libera[rr * GC + cc]) continue;
        const ns = (rr * GC + cc) * 4 + nd, nk = k + 1 + (nd === d ? 0 : SVOLTA);
        if (nk < costo[ns]) { costo[ns] = nk; prima[ns] = st; metti(nk, ns); }
      }
    }
    if (fine < 0) return [[meta.x, meta.y]];   // nessuna strada (non dovrebbe succedere): si va dritti
    const celle = [];
    for (let st = fine; st >= 0; st = prima[st]) celle.unshift(st);
    const punti = [];
    for (let i = 1; i < celle.length - 1; i++) {   // gli angoli: dove cambia la direzione
      if ((celle[i] & 3) !== (celle[i + 1] & 3)) {
        const pos = celle[i] >> 2;
        punti.push([(pos % GC) * CELLA + CELLA / 2, ((pos / GC) | 0) * CELLA + CELLA / 2]);
      }
    }
    // primo tratto: dal punto esatto di partenza al centro della sua cella, poi gli angoli e l'arrivo esatto
    return [[c0 * CELLA + CELLA / 2, r0 * CELLA + CELLA / 2], ...punti, [meta.x, meta.y]];
  }
  const pausa = (ms) => new Promise((r) => setTimeout(r, ms));

  /* ───────────── dialoghi ───────────── */
  /* Una battuta del YAML è {"sr.caffe": "testo"}. */
  function battuta(b) {
    const [k, testo] = Object.entries(b)[0] || ["jr.idle", ""];
    const [chi, faccia] = k.split(".");
    return { chi, faccia, testo: String(testo ?? "") };
  }
  function riempi(testo, valori) {
    return testo.replace(/\{(\w+)\}/g, (m, k) => (k in valori ? esc(valori[k]) : m));
  }

  let avanzaDialogo = null;
  function parla(battute, valori = {}) {
    saltaOra = false;
    if (COLLAUDO && CHEAT.salta) battute = [];   // collaudo: dialoghi saltati
    return (async () => {
      for (const b of battute || []) {
        if (saltaOra) break;           // collaudo: Esc salta il resto del dialogo
        await una(battuta(b), valori);
      }
      $("#dialogo").classList.add("hidden");
    })();
  }
  function una({ chi, faccia, testo }, valori, scelte) {
    return new Promise((fine) => {
      const p = (STORIA.personaggi || {})[chi] || { nome: chi, ritratti: {} };
      const ritratto = (p.ritratti || {})[faccia] || Object.values(p.ritratti || {})[0];
      const box = $("#dialogo");
      box.className = `dialogo chi-${chi}`;
      $(".ritratto", box).src = ritratto ? IMG + "ritratti/" + ritratto : "";
      $(".ritratto", box).classList.toggle("hidden", !ritratto);
      valori = { nome: ST.nome || "", ...valori };
      $(".chi", box).innerHTML = `${riempi(esc(p.nome), valori)}${p.ruolo ? ` <small>${esc(p.ruolo)}</small>` : ""}`;
      const el = $(".testo", box);
      el.innerHTML = riempi(testo, valori);
      $(".scelte", box).innerHTML = "";
      $(".avanti", box).textContent = scelte ? "" : ss("avanti");
      const scrivi = macchinaDaScrivere(el);
      const chiudi = (v) => { avanzaDialogo = null; fine(v); };
      if (scelte) {
        const mostra = () => {
          $(".scelte", box).innerHTML = scelte.map((s_, i) => `<button class="btn ${i ? "" : "primary"}" data-i="${i}">${esc(s_)}</button>`).join("");
          $$(".scelte button", box).forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); chiudi(+b.dataset.i === 0); }));
          $(".scelte button", box).focus();
        };
        scrivi.finito.then(mostra);
        avanzaDialogo = () => scrivi.completa();
      } else {
        avanzaDialogo = () => { if (!scrivi.completa()) chiudi(); };
      }
    });
  }
  /* Scrive il testo (anche con <b>) una lettera alla volta. completa() → true se l'ha appena completato. */
  function macchinaDaScrivere(el) {
    const nodi = [];
    const giro = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (giro.nextNode()) { nodi.push([giro.currentNode, giro.currentNode.textContent]); giro.currentNode.textContent = ""; }
    let i = 0, j = 0, fatto = false, timer = null, risolvi;
    const finito = new Promise((r) => { risolvi = r; });
    const fine = () => { fatto = true; clearInterval(timer); nodi.forEach(([n, tx]) => { n.textContent = tx; }); risolvi(); };
    timer = setInterval(() => {
      if (i >= nodi.length) { fine(); return; }
      const [n, tx] = nodi[i];
      j += 2; n.textContent = tx.slice(0, j);
      if (j >= tx.length) { i++; j = 0; }
    }, 22);
    return { finito, completa: () => { if (fatto) return false; fine(); return true; } };
  }
  $("#dialogo").addEventListener("click", () => avanzaDialogo && avanzaDialogo());
  document.addEventListener("keydown", (e) => {
    if (!avanzaDialogo || $("#dialogo").classList.contains("hidden") || $("#ufficio").classList.contains("hidden")) return;
    if (e.target.closest && e.target.closest(".scelte")) return;   // Invio su «Sì»/«No» è un clic
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); avanzaDialogo(); }
  });
  async function domanda(chi_faccia, testo, valori) {
    const b = battuta({ [chi_faccia]: testo });
    const si = await una(b, valori, [ss("risposta_si"), ss("risposta_no")]);
    $("#dialogo").classList.add("hidden");
    return si;
  }

  /* ───────────── cartelli (inizio giornata, fine demo) ───────────── */
  function cartello(html, ms = 2600) {
    return new Promise((fine) => {
      const c = $("#cartello");
      c.innerHTML = html;
      c.classList.remove("hidden");
      const chiudi = () => { c.classList.add("hidden"); c.removeEventListener("click", chiudi); clearTimeout(tm); fine(); };
      const tm = ms ? setTimeout(chiudi, ms) : null;
      c.addEventListener("click", chiudi);
    });
  }

  /* ───────────── ufficio ───────────── */
  function mostraUfficio() {
    vista = "ufficio";
    $("#app").classList.add("hidden");
    $("#ufficio").classList.remove("hidden");
    aggiornaHud();
  }

  function aggiornaHud() {
    if (!STORIA) return;
    const g = giorno();
    $("#hudGiorno").textContent = g ? ss("giorno", { numero: g.numero }) : ss("fine_demo_titolo");
    $("#hudTitolo").textContent = g ? (inAllarme(g) ? `${ss("defcon")} · ` : "") + g.titolo : "";
    sfondo(g);
    musica();
    $("#hudObiettivo").innerHTML = g ? `<b>${s("obiettivo")}:</b> ${esc(g.obiettivo || "")}` : "";
    $("#hudBuoni").textContent = buoni();
    $("#hudBuoni").parentElement.title = ss("buoni");
    $("#hudCaffe").textContent = ST.caffe || 0;
    $("#hudCaffe").parentElement.title = ss("caffe_in_mano");
    aggiornaMonitor(g);
    // il «!» sopra la prossima cosa da fare
    const seg = $("#segnale");
    const dove = !g ? null : completato(g) ? "collega" : convocazioneInSospeso(g) ? "capo" : "computer";
    if (!dove || occupato) { seg.classList.add("hidden"); return; }
    const [x, y, w] = ZONE[dove];
    seg.style.left = pc(x + w / 2, W); seg.style.top = pc(y - 2, H);
    seg.classList.remove("hidden");
  }

  /* La barra del titolo del monitor: giornata, cliente e buoni caffè (al posto dei punti). */
  function aggiornaMonitor(g = giorno()) {
    $("#appGiorno").textContent = g ? `${ss("giorno", { numero: g.numero })} · ${g.cliente || ""}` : "";
    $("#appBuoni b").textContent = buoni();
    $("#appBuoni").title = ss("buoni");
  }

  /* «allarme: true» (giorno 4, DEFCON): luci rosse in ufficio per tutta la giornata, finché non è finita. */
  const inAllarme = (g = giorno()) => !!(g && g.allarme && !ST.fine[g.numero]);
  function sfondo(g = giorno()) {
    const src = IMG + (inAllarme(g) ? "scene/ufficio_allarme.png" : "scene/ufficio.png");
    const img = $("#sfondo");
    if (img && !img.src.endsWith(src)) img.src = src;
    $("#scena").classList.toggle("allarme", inAllarme(g));
  }

  /* ───────────── war room (giorno 4) ─────────────
   * Con «war_room: true» nella giornata, si parla con il Capo nella war room: il protagonista entra dalla porta
   * in basso e si ferma in fondo al tavolo, il Capo è seduto a capotavola. Lì si parla soltanto; finito il
   * dialogo si torna nell'ufficio, dove si gioca. war_room.png (256×320) è disegnata alta quanto la scena:
   * scala 0,9 e 76,8 unità di margine a sinistra. */
  // il protagonista entra dalla porta e risale il corridoio a destra del tavolo (sotto, il dialogo copre la scena)
  const WR = { capo: { x: 176, y: 50 }, porta: { x: 192, y: 300 },
    percorso: [{ x: 192, y: 252, dir: "su" }, { x: 266, y: 252, dir: "dx" }, { x: 266, y: 128, dir: "su" }], guarda: "sx" };
  async function inWarRoom(battute, valori = {}) {
    const box = $("#warRoom"), j = $("#wrJr"), c = $("#wrCapo");
    c.style.backgroundImage = `url(${IMG}sprite/boss_seduto.png)`;
    c.style.backgroundSize = "100% 100%";
    c.style.left = pc(WR.capo.x, W); c.style.top = pc(WR.capo.y, H); c.style.width = pc(32, W); c.style.height = pc(32, H);
    foglio(j, "jr_cammina.png", 4, 4, 0, RIGA.su, 32, 32);
    const metti = (p) => { j.style.left = pc(p.x - 16, W); j.style.top = pc(p.y - 31, H); };
    j.style.transition = "none"; metti(WR.porta);
    box.classList.remove("hidden");
    await pausa(450);                                     // dissolvenza della stanza
    let passo = 0, dir = "su", da = WR.porta;
    const anima = setInterval(() => foglio(j, "jr_cammina.png", 4, 4, (passo = (passo + 1) % 4), RIGA[dir], 32, 32), 140);
    for (const p of WR.percorso) {
      const ms = Math.round(Math.hypot(p.x - da.x, p.y - da.y) * 9);   // ~110 unità al secondo
      dir = p.dir; j.style.transition = `left ${ms}ms linear, top ${ms}ms linear`; metti(p);
      await pausa(ms + 30); da = p;
    }
    clearInterval(anima); foglio(j, "jr_cammina.png", 4, 4, 0, RIGA[WR.guarda], 32, 32);
    try { await parla(battute, valori); } finally {
      box.classList.add("chiudi");
      await pausa(350);
      box.classList.add("hidden"); box.classList.remove("chiudi");
    }
  }
  /* Parlare con il Capo: nella war room se la giornata la prevede, altrimenti davanti alla sua porta. */
  const colCapo = (g, battute, valori) => (g && g.war_room ? inWarRoom(battute, valori) : parla(battute, valori));

  /* «briefing» della giornata: {id challenge: battute}. Quando una challenge diventa quella in corso il Capo
   * convoca il giocatore nel suo ufficio e spiega cosa sta succedendo (una volta per partita, ST.briefing). */
  function briefing(g = giorno()) {
    const cur = S && S.mode === "academy" && S.academy && S.academy.current;
    const b = g && g.briefing && cur && g.briefing[cur.id];
    return b && !completato(g) ? { id: cur.id, battute: b } : null;
  }
  function convocazioneInSospeso(g = giorno()) {
    const b = briefing(g);
    return b && !(ST.briefing || {})[b.id] ? b : null;
  }
  async function convocazione(g, b) {
    await parla(g.convocazione);
    await vaiA("capo");
    await colCapo(g, b.battute);
    ST.briefing = { ...(ST.briefing || {}), [b.id]: true }; salva();
    aggiornaHud();
  }

  /* Il cuore della storia: cosa succede quando il protagonista è in ufficio. */
  async function entraInUfficio() {
    if (occupato) return;
    occupato = true; etichetta(null); aggiornaHud();
    try {
      for (;;) {
        const g = giorno();
        if (!g) { await finale(); break; }
        if (!ST.intro[g.numero]) { await inizioGiornata(g); continue; }
        if (S && completato(g) && !ST.fine[g.numero]) { await fineGiornata(g); continue; }
        const b = convocazioneInSospeso(g);
        if (b) { await convocazione(g, b); continue; }
        break;
      }
    } finally { occupato = false; aggiornaHud(); }
  }

  async function inizioGiornata(g) {
    sfondo(g);
    jr.seduto = false; jr.dove = null; jr.x = ENTRATA.x; jr.y = ENTRATA.y; piazzaJr("su");
    sr.seduto = false; sr.x = 168; sr.y = 236; piazzaSr("sx");
    aggiornaHud();
    await cartello(`<div class="pix grande">${esc(ss("giorno", { numero: g.numero }))}</div>
      <div class="pix">${esc(g.titolo)}</div><div class="cliente">${esc(g.cliente || "")}</div>`);
    await cammina(jr, "jr_cammina.png", [[ENTRATA.x, 248]]);
    piazzaJr("dx");
    // «allarme: true»: la giornata si apre con l'allarme e la war room; le luci rosse restano (DEFCON)
    if (g.allarme) { allarmeIniziato = true; musica(); }
    await parla(g.inizio, { buoni: buoni() });
    if (g.allarme) {
      if (g.war_room && g.war_room_inizio) {
        await vaiA("capo");
        // il briefing della prima challenge si sente già qui, nella stessa visita alla war room
        const b = convocazioneInSospeso(g);
        await inWarRoom([...g.war_room_inizio, ...(b ? b.battute : [])], { buoni: buoni() });
        if (b) { ST.briefing = { ...(ST.briefing || {}), [b.id]: true }; salva(); }
      } else {
        await cartello(`<img class="war-room" src="${IMG}scene/war_room.png" alt="">
          <div class="pix">${esc(ss("defcon"))}</div><div class="pix">${esc(g.titolo)}</div>`, 4000);
      }
      if (g.dopo_allarme) await parla(g.dopo_allarme, { buoni: buoni() });
    }
    ST.intro[g.numero] = true; salva();
    await cammina(sr, "sr_cammina.png", [[sr.x, 254], [307, 254], [307, 236]]);
    sr.seduto = true; piazzaSr();
  }

  async function fineGiornata(g) {
    await vaiA("collega");
    await parla(g.fine, { buoni: buoni() + (g.buoni || 0) });
    ST.fine[g.numero] = true; ST.giorno = g.numero + 1; salva();
    aggiornaHud(); sfondo(g);
    toast(`🎟️ +${g.buoni || 0} · ${ss("buoni")}: ${buoni()}`, "ok", 4000);
  }

  async function finale() {
    if (!ST.finale && STORIA.finale) {
      $("#sfondo").src = IMG + "scene/ufficio.png";
      $("#scena").classList.remove("allarme");
      const P = STORIA.promozione;
      if (P) await vaiA("capo");          // la promozione si riceve nell'ufficio del Capo
      await parla(STORIA.finale);
      if (P) {
        festa();
        await cartello(`<div class="pix grande">🏅 ${esc(P.titolo || "")}</div>
          <div class="promozione-targhetta"><div class="pix">${esc(ST.nome || "")}</div>
          <div class="cliente">${esc(P.ruolo || "")}</div></div>
          <p>${riempi(esc(P.testo || ""), { nome: esc(ST.nome || "") })}</p>
          <button class="btn primary">${esc(P.ok || "OK")}</button>`, 0);
        if (P.dopo) await parla(P.dopo);
      }
      ST.finale = true; salva();
    }
    await crediti();
    $("#sfondo").src = IMG + "scene/ufficio.png";
    $("#scena").classList.remove("allarme");
    await cartello(`<img class="war-room" src="${IMG}scene/war_room.png" alt="">
      <div class="pix grande">${esc(ss("fine_demo_titolo"))}</div><p>${esc(ss("fine_demo_testo"))}</p>
      <button class="btn primary" onclick="Storia.nuovaPartita()">${esc(ss("nuova_partita"))}</button>`, 0);
  }

  /* Titoli di coda (storia.yaml, «crediti»): nel finale e dal pulsante «Crediti» della schermata iniziale.
   * Hanno un riquadro proprio attaccato a <body>, perché il cartello dell'ufficio si nasconde con l'ufficio.
   * Si chiudono solo con il pulsante o cliccando fuori, così i link si possono aprire. */
  function crediti() {
    const C = STORIA.crediti;
    if (!C) return Promise.resolve();
    const riga = (r) => {   // una stringa, oppure {testo, link} (attenzione: "…".link esiste, è un metodo di String)
      if (!r || typeof r !== "object") return esc(String(r ?? ""));
      const testo = esc(r.testo || "");
      return typeof r.link === "string" ? `<a href="${esc(r.link)}" target="_blank" rel="noopener">${testo}</a>` : testo;
    };
    return new Promise((fine) => {
      const el = document.createElement("div");
      el.className = "cartello crediti";
      el.innerHTML = `<div class="pix grande">${esc(C.titolo || "")}</div>
        <div class="crediti-rullo">${(C.sezioni || []).map((sez) => `<section>
          <div class="pix">${esc(sez.titolo || "")}</div>${(sez.righe || []).map((r) => `<p>${riga(r)}</p>`).join("")}
        </section>`).join("")}</div>
        <button class="btn primary" type="button">${esc(C.ok || "OK")}</button>`;
      document.body.appendChild(el);
      const chiudi = () => { el.remove(); fine(); };
      el.querySelector("button").addEventListener("click", chiudi);
      el.addEventListener("click", (e) => { if (e.target === el) chiudi(); });
    });
  }

  /* Coriandoli sopra i cartelli (quelli del monitor stanno sotto). */
  function festa() {
    if (typeof confetti !== "function") return;
    for (const ms of [0, 900, 1800]) setTimeout(() => {
      confetti();
      const box = document.body.lastElementChild;
      if (box && box.style.position === "fixed") box.style.zIndex = "950";
    }, ms);
  }

  /* ───────────── musica (storia.yaml, «musica») ─────────────
   * «gameplay» per quasi tutto il gioco; «attacco» da quando attaccano le foche (l'inizio della giornata con
   * «allarme») fino al finale. Parte al primo gesto del giocatore (i browser non la fanno partire da sola);
   * il tasto 🔊/🔇 la spegne, e la scelta resta nel browser. */
  const audio = new Audio();
  audio.loop = true;
  let tracciaOra = "", allarmeIniziato = false, dissolvenza = null;
  const muto = () => { try { return localStorage.getItem("penguinInc.muto") === "1"; } catch { return false; } };
  function tracciaVoluta() {
    const M = STORIA && STORIA.musica;
    if (!M || !ST) return "";
    const g = giorno();
    const primoAllarme = Math.min(...giorni().filter((x) => x.allarme).map((x) => x.numero));
    const attacco = !g ? isFinite(primoAllarme) : g.numero > primoAllarme
      || (g.numero === primoAllarme && (ST.intro[g.numero] || allarmeIniziato));
    return (attacco ? M.attacco : M.gameplay) || "";
  }
  function volumeVerso(fine, poi) {
    clearInterval(dissolvenza);
    const passo = (fine - audio.volume) / 10;
    let n = 0;
    dissolvenza = setInterval(() => {
      audio.volume = Math.min(1, Math.max(0, audio.volume + passo));
      if (++n >= 10) { clearInterval(dissolvenza); audio.volume = fine; if (poi) poi(); }
    }, 60);
  }
  function musica() {
    const vuole = tracciaVoluta();
    const vol = Number((STORIA.musica || {}).volume ?? 0.4);
    if (!vuole || muto()) { if (!audio.paused) volumeVerso(0, () => audio.pause()); return; }
    if (vuole === tracciaOra && !audio.paused) return;
    const parti = () => {
      if (vuole !== tracciaOra) { audio.src = "/assets/" + vuole; tracciaOra = vuole; }
      audio.volume = 0;
      audio.play().then(() => volumeVerso(vol)).catch(() => { /* niente gesto ancora: riprova al prossimo */ });
    };
    if (!audio.paused && tracciaOra) volumeVerso(0, parti); else parti();
  }
  function preparaMusica() {
    if (!STORIA.musica) return;
    const b = document.createElement("button");
    b.id = "tastoMusica"; b.className = "tasto-musica"; b.type = "button";
    const disegna = () => { b.textContent = muto() ? "🔇" : "🔊"; b.title = ss(muto() ? "musica_accendi" : "musica_spegni"); };
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      try { localStorage.setItem("penguinInc.muto", muto() ? "0" : "1"); } catch { /* niente */ }
      disegna(); musica();
    });
    disegna();
    document.body.appendChild(b);
    // il primo gesto sulla pagina sblocca l'audio (anche quando si salta la schermata iniziale)
    const primo = () => { musica(); if (!audio.paused) { removeEventListener("pointerdown", primo); removeEventListener("keydown", primo); } };
    addEventListener("pointerdown", primo);
    addEventListener("keydown", primo);
  }

  /* Il giocatore clicca qualcosa nell'ufficio. */
  async function usa(cosa) {
    if (occupato) return;
    const g = giorno();
    // giornata finita (o storia finita): qualunque cosa si clicchi, prima si fa rapporto a Jody
    if (!g || (cosa === "collega" && completato(g) && !ST.fine[g.numero])) return entraInUfficio();
    occupato = true; etichetta(null); aggiornaHud();
    try {
      const b = cosa === "computer" && convocazioneInSospeso(g);
      if (b) await convocazione(g, b);
      await vaiA(cosa);
      if (cosa === "computer") {
        if (completato(g)) { await parla(STORIA.oggetti.computer_finito); return; }
        jr.seduto = true; piazzaJr();
        await apriMonitor();
      } else if (cosa === "caffe") {
        // la macchinetta: un buono = un caffè, da portare a Jody in cambio di un consiglio
        const M = STORIA.macchinetta || {};
        const valori = { buoni: buoni(), caffe: ST.caffe || 0 };
        await parla(M.saluto, valori);
        if (buoni() > 0) {
          if (await domanda("jr.idle", M.domanda, valori)) {
            ST.spesi += 1; ST.caffe = (ST.caffe || 0) + 1; salva(); aggiornaHud();
            await parla(M.preso, { buoni: buoni(), caffe: ST.caffe });
          }
        } else {
          await parla(M.senza_buoni, valori);
        }
      } else if (cosa === "capo") {
        // nei primi giorni la porta è chiusa: si parla con il Capo solo se la giornata lo prevede («capo»)
        const b = briefing(g);
        if (b) await colCapo(g, b.battute);
        else await parla(g.capo || STORIA.oggetti.capo_chiuso);
      } else if (cosa === "collega") {
        await parlaConJody(g);
      }
    } finally { occupato = false; aggiornaHud(); }
  }

  async function parlaConJody(g) {
    // la challenge in corso ha i consigli del collega senior (accademia.yaml): uno alla volta, gratis nei giorni
    // con «consigli_gratis», altrimenti in cambio di un caffè preso alla macchinetta
    const c = typeof consiglioSuccessivo === "function" ? consiglioSuccessivo() : null;
    const K = STORIA.consigli;
    if (c && K) {
      if (c.finiti) { await parla(K.finiti); return; }
      const valori = { numero: c.numero, totale: c.totale, testo: c.testo, buoni: buoni(), caffe: ST.caffe || 0 };
      if (!g.consigli_gratis) {
        if (!ST.caffe) { await parla(buoni() > 0 ? K.serve_caffe : K.serve_caffe_senza_buoni, valori); return; }
        if (!(await domanda("jr.preoccupato", K.offri, valori))) { await parla(K.rifiuta, valori); return; }
        ST.caffe -= 1; salva(); aggiornaHud();
        await parla(K.grazie, valori);
      }
      segnaConsiglio();
      await parla([K.battuta], valori);
      return;
    }
    const a = g.aiuto;
    if (!a) { await parla(g.collega); return; }
    if (ST.aiuti[g.numero]) { await parla(a.gia_dato || g.collega); return; }
    const costo = a.costo || 0;
    const valori = { costo, buoni: buoni() };
    if (costo === 0) {
      ST.aiuti[g.numero] = true; salva();
      await parla(a.accetta, valori);
      await eseguiAiuto(g);
      return;
    }
    await parla(g.collega, valori);
    await parla(a.proposta, valori);
    if (buoni() < costo) { await parla(a.senza_buoni, valori); return; }
    const si = await domanda("jr.preoccupato", a.domanda || "", valori);
    if (!si) { await parla(a.rifiuta, valori); return; }
    ST.spesi += costo; ST.aiuti[g.numero] = true; salva();
    aggiornaHud();
    toast(`🎟️ −${costo} · ${ss("buoni")}: ${buoni()}`, "info", 3500);
    await parla(a.accetta, valori);
    await eseguiAiuto(g);
  }

  /* ───────────── l'aiuto del collega, sul tavolo vero ───────────── */
  async function eseguiAiuto(g) {
    const a = g.aiuto || {};
    if (a.azione === "meta_topologia") await conAiuto(metaTopologia);
    else if (a.azione === "comandi") {
      ST.inSospeso = { giorno: g.numero, livello: a.livello }; salva();
      await provaComandiInSospeso();
    }
  }

  async function conAiuto(fn) {
    if (aiutoInCorso) return;
    aiutoInCorso = true;
    toast(ss("aiuto_al_lavoro"), "info", 4000);
    try { await fn(); toast(ss("aiuto_fatto"), "ok", 5000); }
    catch (e) { toast(ss("aiuto_fallito", { errore: e.message || e }), "bad", 9000); }
    finally { aiutoInCorso = false; }
  }

  async function chiama(path, body) {
    const d = await api(path, body);
    if (!d) throw new Error(path);
    if (d.topology) applyState(d);
    return d;
  }

  /* Giorno 2: Jody costruisce metà della rete della Pescheria (server e router), sulla rete del giorno 1. */
  async function metaTopologia() {
    const T = () => S.topology;
    const trova = (n) => T().devices.find((d) => d.name === n);
    const pcs = T().devices.filter((d) => d.kind === "pc" && hostIp(d));
    if (!pcs.length) throw new Error("sul tavolo non ci sono PC con un IP");
    const { ip, mask } = hostIp(pcs[0]);
    const rete = ip.split(".").slice(0, 3).join(".");
    const vicino = (nome) => {
      const l = T().links.find((x) => x.a === nome || x.b === nome);
      return l ? (l.a === nome ? { dev: l.b, porta: l.pb, mia: l.pa } : { dev: l.a, porta: l.pa, mia: l.pb }) : null;
    };
    const v = vicino(pcs[0].name);
    const sw = v && trova(v.dev);
    if (!sw || sw.kind !== "switch") throw new Error("i PC non sono collegati a uno switch");
    const collegato = (a, b) => T().links.some((l) => (l.a === a && l.b === b) || (l.a === b && l.b === a));

    let srv = T().devices.find((d) => d.kind === "server");
    let nomeSrv = srv && srv.name;
    if (!srv) nomeSrv = (await chiama("/api/device/add", { kind: "server", x: sw.x + 190, y: sw.y + 10 })).selected;
    if (!collegato(nomeSrv, sw.name)) await chiama("/api/link", { a: nomeSrv, b: sw.name });
    await chiama("/api/host/ip", { name: nomeSrv, mode: "static", ip: `${rete}.100`, mask, gateway: `${rete}.1` });

    let r = T().devices.find((d) => d.kind === "router");
    let nomeR = r && r.name;
    if (!r) nomeR = (await chiama("/api/device/add", { kind: "router", x: sw.x, y: sw.y - 170 })).selected;
    if (!collegato(nomeR, sw.name)) await chiama("/api/link", { a: nomeR, b: sw.name });
    const l = T().links.find((x) => (x.a === nomeR && x.b === sw.name) || (x.b === nomeR && x.a === sw.name));
    const porta = l.a === nomeR ? l.pa : l.pb;
    await chiama("/api/router/iface", { name: nomeR, port: porta, ip: `${rete}.1`, mask, on: true });

    for (const p of T().devices.filter((d) => d.kind === "pc" && hostIp(d) && hostIp(d).ip.startsWith(`${rete}.`))) {
      const h = hostIp(p);
      await chiama("/api/host/ip", { name: p.name, mode: "static", ip: h.ip, mask: h.mask, gateway: `${rete}.1` });
    }
  }

  /* Giorno 3: i comandi del YAML valgono per un livello preciso; se non è ancora aperto aspettano. */
  async function provaComandiInSospeso() {
    const p = ST.inSospeso;
    if (!p || aiutoInCorso || !S || (S.job && !S.job.done)) return;
    const g = giorno(p.giorno);
    const c = S.rescue.current;
    if (!g || ST.giorno !== p.giorno) { ST.inSospeso = null; salva(); return; }
    if (!c || c.n !== p.livello || !ST.avviati[p.livello]) return;
    if (c.status === "won" || ST.vinti[p.livello]) { ST.inSospeso = null; salva(); return; }
    if (c.status !== "playing") return;
    ST.inSospeso = null; salva();
    await conAiuto(async () => { for (const k of g.aiuto.comandi || []) await chiama(k.api, k.dati); });
  }

  /* ───────────── monitor (l'interfaccia del gioco, app.js) ───────────── */
  async function apriMonitor() {
    const av = $("#avvioMonitor");
    av.classList.remove("hidden");
    await pausa(650);
    vista = "monitor";
    $("#ufficio").classList.add("hidden");
    $("#app").classList.remove("hidden");
    av.classList.add("hidden");
    const g = giorno();
    const voluta = g && g.monitor && g.monitor.modalita;
    if (voluta && S.mode !== voluta) setMode(voluta);
    gameSig = ""; renderGame(); fit();
    preparaTavolo();
    guida();
  }

  /* La challenge in corso parte da un tavolo preciso (vuoto o una topologia di gioco/reti/) e non è ancora stato preparato:
   * lo prepara il server, prima che il giocatore cominci. Così, per esempio, il giorno 1 parte davvero dal
   * tavolo vuoto anche se sul tavolo era rimasta un'altra rete. */
  let preparata = null;   // la missione di cui si è già chiesto di preparare il tavolo (niente richieste doppie)
  function preparaTavolo() {
    const cur = S && S.academy && S.academy.current;
    if (vista !== "monitor" || !cur || !cur.needs_setup || (S.job && !S.job.done) || preparata === cur.id) return;
    preparata = cur.id;
    job("/api/academy/network", { start: true });
  }

  async function tornaInUfficio() {
    mostraUfficio();
    jr.seduto = true; jr.dove = "computer"; piazzaJr();
    await pausa(250);
    entraInUfficio();
  }

  /* ───────────── guida: i pop-up di Jody sul monitor (storia.yaml, «guida» nella giornata) ─────────────
   * Ognuno si vede una volta per partita (ST.guida). Si mostrano solo al monitor, a tavolo pronto, uno alla
   * volta: prima quelli di «inizio» della challenge, poi quello del passo da fare ora. */
  const PUNTA = {
    pannello: "#gamePanel", tavolo: ".canvas-wrap", dispositivi: "#addTools", cavo: '[data-tool="cable"]',
    ispettore: "#inspector", legenda: ".legend", verifica: '[data-guida="verifica"]', manuale: '[data-guida="manuale"]',
    ufficio: "#btnUfficio",
  };
  let guidaAperta = false;
  function prossimoPopup() {
    const g = giorno();
    const cur = S && S.mode === "academy" && S.academy && S.academy.current;
    const gd = g && g.guida && cur && g.guida[cur.id];
    if (!gd || completato(g)) return null;
    ST.guida = ST.guida || {};
    const inizio = gd.inizio || [];
    for (let i = 0; i < inizio.length; i++) {
      const k = `${cur.id}:inizio:${i}`;
      if (!ST.guida[k]) return { k, p: inizio[i] };
    }
    const n = cur.steps.findIndex((x) => !x.ok) + 1;   // il passo da fare ora, da 1 (0: tutti fatti)
    const p = n && (gd.passi || {})[n];
    const k = `${cur.id}:passo:${n}`;
    return p && !ST.guida[k] ? { k, p } : null;
  }
  function guida() {
    if (guidaAperta || vista !== "monitor" || !STORIA || !ST || !S) return;
    if ((S.job && !S.job.done) || (S.academy.current && S.academy.current.needs_setup)) return;
    const prossimo = prossimoPopup();
    if (!prossimo) return;
    guidaAperta = true;
    // dopo il render del pannello, così l'elemento da indicare esiste già
    setTimeout(() => mostraPopup(prossimo.p).then(() => {
      ST.guida[prossimo.k] = true; salva();
      guidaAperta = false;
      guida();
    }), 300);
  }
  function mostraPopup(p) {
    return new Promise((fine) => {
      if (vista !== "monitor") { guidaAperta = false; return; }   // il giocatore è già tornato in ufficio
      const sr_ = (STORIA.personaggi || {}).sr || { nome: "Jody", ritratti: {} };
      const ritratto = (sr_.ritratti || {}).tastiera || Object.values(sr_.ritratti || {})[0];
      const box = document.createElement("div");
      box.className = "guida-pop";
      box.setAttribute("role", "dialog");
      box.innerHTML = `${ritratto ? `<img class="guida-ritratto" src="${IMG}ritratti/${ritratto}" alt="">` : ""}
        <div class="guida-testo"><div class="guida-chi">${esc(sr_.nome)}</div>
          ${p.titolo ? `<b class="guida-titolo">${riempi(esc(p.titolo), { nome: ST.nome || "" })}</b>` : ""}
          <p>${riempi(String(p.testo || ""), { nome: ST.nome || "" })}</p>
          <div class="row end"><button type="button" class="btn primary small">${esc(ss("guida_ok"))}</button></div></div>`;
      const bersaglio = p.punta && PUNTA[p.punta] ? $(PUNTA[p.punta]) : null;
      if (bersaglio) bersaglio.classList.add("guida-evidenzia");
      const chiudi = () => {
        box.remove();
        $$(".guida-evidenzia").forEach((el) => el.classList.remove("guida-evidenzia"));
        fine();
      };
      $("button", box).addEventListener("click", chiudi);
      $("#app").appendChild(box);
      $("button", box).focus({ preventScroll: true });
    });
  }

  /* Hook da app.js: a ogni stato nuovo dal server. */
  function aggiorna() {
    if (!STORIA || !ST) return;
    const g = giorno();
    const c = S.rescue && S.rescue.current;
    if (g && g.monitor && g.monitor.modalita === "rescue" && c && c.status === "won"
        && (g.monitor.livelli || []).includes(c.n) && ST.avviati[c.n] && !ST.vinti[c.n]) {
      ST.vinti[c.n] = true; salva();
    }
    provaComandiInSospeso();
    preparaTavolo();
    if (vista === "ufficio") aggiornaHud();
    else { aggiornaMonitor(g); guida(); }
  }

  /* Hook da app.js: quello che il pannello di gioco deve mostrare in modalità storia.
   * Ritorna null (pannello normale), {prima} (riquadro sopra il pannello normale) o {html} (al posto suo). */
  function pannello() {
    if (!STORIA || !ST || !S) return null;
    const g = giorno();
    if (!g) return { html: `<section class="card"><h2>${esc(ss("fine_demo_titolo"))}</h2><p>${esc(ss("fine_demo_testo"))}</p></section>` };
    const torna = `<button class="btn primary" onclick="Storia.tornaInUfficio()">🏢 ${esc(ss("torna_in_ufficio"))}</button>`;
    if (completato(g)) {
      return { html: `<section class="card storia-fine"><p>${s("giornata_finita")}</p><div class="row">${torna}</div></section>` };
    }
    const m = g.monitor || {};
    const intest = `<div class="storia-cliente">${th("storia.interfaccia.missioni_del_giorno", { cliente: g.cliente || "" })}</div>`;
    if (m.modalita !== "rescue") {
      const conv = convocazioneInSospeso(g) ? `<section class="card storia-convocazione"><p>${s("convocato_monitor")}</p>
        <div class="row">${torna}</div></section>` : "";
      return { prima: conv + intest };
    }
    const busy = S.job && !S.job.done;
    const prossimo = (m.livelli || []).find((n) => !ST.vinti[n]);
    const c = S.rescue.current;
    const inGioco = c && c.n === prossimo && ST.avviati[c.n] && ["playing", "won"].includes(c.status);
    if (inGioco) return { prima: intest };
    const lv = (S.rescue.levels || []).find((x) => x.n === prossimo) || { title: "" };
    const pos = (m.livelli || []).indexOf(prossimo) + 1;
    const err = c && c.status === "error" && ST.avviati[c.n] ? `<div class="banner bad">${esc(c.message)}</div>` : "";
    const ticket = `<section class="card storia-ticket"><h3>${th("storia.interfaccia.ticket_in_arrivo", { cliente: g.cliente || "" })}</h3>
      <p>${th("storia.interfaccia.ticket_testo", { numero: pos, totale: m.livelli.length, titolo: lv.title })}</p>${err}
      <div class="row"><button class="btn primary" ${busy ? "disabled" : ""} onclick="Storia.apriTicket(${prossimo})">📨 ${esc(ss("apri_ticket"))}</button></div></section>`;
    // il livello appena vinto resta visibile sotto (con la lezione), il ticket nuovo sopra
    const vinto = c && c.status === "won" && ST.vinti[c.n] && (m.livelli || []).includes(c.n);
    return vinto ? { prima: intest + ticket } : { html: intest + ticket };
  }

  async function apriTicket(n) {
    const lv = (S.rescue.levels || []).find((x) => x.n === n) || { title: "" };
    if (!(await chiedi(ss("conferma_apri_ticket", { titolo: lv.title })))) return;
    ST.avviati[n] = true; delete ST.vinti[n]; salva();
    if (S.mode !== "rescue") await api("/api/mode", { mode: "rescue" });
    showLevels = false;
    job("/api/rescue/start", { level: n });
  }

  /* Missioni dell'Accademia della giornata (per la striscia in alto nel pannello). */
  function missioni() {
    const g = STORIA && ST && giorno();
    return g && g.monitor && g.monitor.modalita !== "rescue" ? g.monitor.missioni || null : null;
  }
  const firma = () => (ST ? JSON.stringify([ST.giorno, ST.vinti, ST.avviati, completato()]) : "");

  async function nuovaPartita() {
    if (!(await chiedi(ss("conferma_nuova_partita")))) return;
    $("#cartello").classList.add("hidden");
    const nome = ST.nome;
    ST = nuovo(); salva();
    if (S && S.mode !== "academy") await api("/api/mode", { mode: "academy" });
    job("/api/academy/reset", {});
    occupato = false;
    mostraTitolo();
    $("#nomeGiocatore").value = nome || "";   // il nome di prima resta proposto, ma si può cambiare
    $("#nomeGiocatore").dispatchEvent(new Event("input"));
  }

  /* ───────────── collaudo: cheat per il debug (solo con PNA_COLLAUDO=1 sul server) ─────────────
   * Strumento per chi sviluppa, non per chi gioca: i testi sono qui e non in storia.yaml.
   * Il pannello 🐞 (o F9) permette di saltare a una giornata, completarla, dare buoni caffè, saltare i
   * dialoghi (anche solo quello in corso, con Esc) e camminare più veloce. Le missioni dell'Accademia si
   * segnano con POST /api/collaudo/accademia, che esiste solo in collaudo; il tavolo non viene toccato. */
  const missioniPrimaDel = (n) => giorni()
    .filter((g) => g.numero < n && (g.monitor || {}).modalita !== "rescue")
    .flatMap((g) => g.monitor.missioni || []);

  function ricarica() {   // ricarica dopo un cheat, saltando la schermata iniziale
    try { sessionStorage.setItem("penguinInc.saltaTitolo", "1"); } catch { /* niente */ }
    location.reload();
  }

  function salvaCheat() { try { sessionStorage.setItem("penguinInc.collaudo", JSON.stringify(CHEAT)); } catch { /* niente */ } }

  async function vaiAlGiorno(n) {
    const spesi = ST.spesi, nome = ST.nome || "Collaudo";
    ST = nuovo(); ST.spesi = spesi; ST.nome = nome; ST.giorno = n;
    for (const g of giorni().filter((x) => x.numero < n)) {
      ST.intro[g.numero] = true; ST.fine[g.numero] = true; ST.aiuti[g.numero] = false;
      for (const l of (g.monitor || {}).livelli || []) { ST.avviati[l] = true; ST.vinti[l] = true; }
    }
    if (n > ultimo()) ST.finale = false;
    salva();
    if (await api("/api/collaudo/accademia", { done: missioniPrimaDel(n) })) ricarica();
  }

  async function completaGiornata() {
    const g = giorno();
    if (!g) return toast("Nessuna giornata in corso: la storia è finita.", "hint");
    const m = g.monitor || {};
    if (m.modalita === "rescue") {
      for (const l of m.livelli || []) { ST.avviati[l] = true; ST.vinti[l] = true; }
      salva();
      ricarica();
    } else if (await api("/api/collaudo/accademia", { done: [...missioniPrimaDel(g.numero), ...(m.missioni || [])] })) {
      ricarica();
    }
  }

  /* Segna come fatta la challenge in corso (solo quella): la storia va avanti da sola alla prossima. */
  async function saltaChallenge() {
    const cur = S && S.academy && S.academy.current;
    if (!cur) return toast("Nessuna challenge in corso.", "hint");
    const fatte = (S.academy.missions || []).filter((m) => m.done).map((m) => m.id);
    if (await api("/api/collaudo/accademia", { done: [...fatte, cur.id] })) toast(`🐞 Saltata «${cur.title}»`, "info", 2500);
  }

  function buoniCheat(delta) {
    if (delta < 0 && buoni() === 0) return;
    ST.spesi -= delta; salva(); aggiornaHud();
    toast(`🐞 🎟️ ${buoni()}`, "info", 1500);
  }

  function collaudo() {
    const box = document.createElement("div");
    box.id = "collaudo";
    box.className = "collaudo";
    const numeri = giorni().map((g) => g.numero);
    box.innerHTML = `<button class="collaudo-tasto" title="Collaudo (F9)">🐞</button>
      <div class="collaudo-pannello">
        <h3>🐞 Collaudo</h3>
        <p class="muted">PNA_COLLAUDO=1 · solo per il debug</p>
        <div class="collaudo-riga"><span>Vai al giorno</span>
          ${numeri.map((n) => `<button class="btn small" data-giorno="${n}">${n}</button>`).join("")}
          <button class="btn small" data-giorno="${ultimo() + 1}">Finale</button></div>
        <div class="collaudo-riga"><span>Buoni caffè</span>
          <button class="btn small" data-buoni="-1">−1</button><button class="btn small" data-buoni="1">+1</button></div>
        <div class="collaudo-riga"><button class="btn small primary" data-azione="salta">⏭ Salta la challenge</button>
          <button class="btn small primary" data-azione="completa">✔ Completa la giornata</button></div>
        <label class="switch-toggle"><input type="checkbox" data-cheat="salta" ${CHEAT.salta ? "checked" : ""}> Salta i dialoghi</label>
        <label class="switch-toggle"><input type="checkbox" data-cheat="veloce" ${CHEAT.veloce ? "checked" : ""}> Camminata veloce (×4)</label>
        <div class="collaudo-riga"><button class="btn small danger" data-azione="azzera">↺ Azzera la storia (il tavolo resta)</button></div>
        <p class="muted">F9 apre e chiude · Esc salta il dialogo in corso.<br>
          «Vai al giorno», «Salta» e «Completa» segnano le missioni senza costruire la rete
          (la challenge dopo parte dalla rete che c'è, o dalla sua topologia se ne ha una).</p>
      </div>`;
    document.body.appendChild(box);
    const apri = (si) => { CHEAT.aperto = si; salvaCheat(); box.classList.toggle("aperto", si); };
    apri(CHEAT.aperto);
    $(".collaudo-tasto", box).addEventListener("click", () => apri(!CHEAT.aperto));
    document.addEventListener("keydown", (e) => {
      if (e.key === "F9") { e.preventDefault(); apri(!CHEAT.aperto); }
      if (e.key === "Escape" && avanzaDialogo && !$("#dialogo").classList.contains("hidden")) {
        saltaOra = true; avanzaDialogo(); if (avanzaDialogo) avanzaDialogo();
      }
    });
    box.addEventListener("click", async (e) => {
      const b = e.target.closest("button");
      if (!b || b.classList.contains("collaudo-tasto")) return;
      if (b.dataset.giorno) await vaiAlGiorno(+b.dataset.giorno);
      else if (b.dataset.buoni) buoniCheat(+b.dataset.buoni);
      else if (b.dataset.azione === "completa") await completaGiornata();
      else if (b.dataset.azione === "salta") await saltaChallenge();
      else if (b.dataset.azione === "azzera") {
        try { localStorage.removeItem(CHIAVE); } catch { /* niente */ }
        ricarica();
      }
    });
    $$("input[data-cheat]", box).forEach((c) => c.addEventListener("change", () => { CHEAT[c.dataset.cheat] = c.checked; salvaCheat(); }));
  }

  return { avvia, aggiorna, pannello, missioni, firma, apriTicket, tornaInUfficio, nuovaPartita, attiva: () => !!(STORIA && ST) };
})();
