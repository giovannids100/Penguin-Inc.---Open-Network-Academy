# /// script
# requires-python = ">=3.11"
# dependencies = ["starlette>=0.37", "uvicorn>=0.30", "pyyaml>=6"]
# ///
"""Penguin Inc. — Open Network Academy: server HTTP del gioco. Input e output sono una rete vera.

Browser ──HTTP──▶ questo server ──▶ rete/motore.py ──▶ modello della rete ──▶ Mininet (namespace Linux)

Avvio:     docker compose up              (dalla cartella principale: http://127.0.0.1:8787)
           python3 server.py              (senza Docker: rete vera se c'è Mininet, altrimenti simulata)
Variabili: PNA_HOST (indirizzo di ascolto, default 127.0.0.1; 0.0.0.0 per la rete locale, serve PNA_CODE),
           PNA_CODE (codice di accesso; se manca il gioco è libero ma si usa solo da questo computer),
           PNA_PORT o PORT (default 8787),
           PNA_FRONTEND (cartella del client servito, default ../frontend/demo),
           PNA_GIOCO (pacchetto di gioco YAML, default ../frontend/demo/gioco: vedi contenuti.py),
           PNA_RETE=simulata (niente Mininet: le prove si calcolano sul modello), PNA_COLLAUDO=1 (debug)

Il server è generico: i contenuti del gioco (missioni, livelli, messaggi, punteggi, dispositivi) arrivano dal
pacchetto di gioco YAML, e il client è una cartella statica qualsiasi.
Documentazione: docs/API.md (contratto HTTP per i client) e docs/ARCHITETTURA.md (come estenderlo).
Il file è diviso in sezioni, nell'ordine: configurazione, stato della partita, lavori lunghi, aiutanti di
validazione, Accademia, Pronto Soccorso, livello HTTP (login, stato, wrapper), endpoint, tabella delle route.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import campagna as K
import contenuti as C
import firewall as FW
import missions as M
import rescue as R
from rete.kernel import entra_nel_namespace
from rete.motore import ErroreRete, Motore
from rete.topo import Topo, short_port

# Su Windows la console e i file aperti senza encoding usano cp1252, che non ha caratteri come → e ↔:
# l'output va sempre in UTF-8 (anche rediretto in un file di log) e i file si aprono con encoding="utf-8".
for _flusso in (sys.stdout, sys.stderr):
    if hasattr(_flusso, "reconfigure"):
        _flusso.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
FRONTEND = Path(os.environ.get("PNA_FRONTEND") or HERE.parent / "frontend" / "demo")
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)
STATE_FILE = DATA / "state.json"
PORT = int(os.environ.get("PNA_PORT") or os.environ.get("PORT") or 8787)
# Codice di accesso facoltativo. Senza codice non c'è login, ma il server risponde solo a richieste
# indirizzate a questo computer (vedi solo_locale): né da Internet né da siti esterni aperti nel browser.
ACCESS_CODE = os.environ.get("PNA_CODE", "").strip()
LOGIN_REQUIRED = bool(ACCESS_CODE)
# Modalità collaudo (PNA_COLLAUDO=1), solo per chi sviluppa, mai in una demo o in una partita vera:
#  - il client mostra i cheat per il debug e si accende POST /api/collaudo/accademia;
#  - all'avvio di un livello il server controlla che la rete di partenza funzioni e che ogni guasto si veda
#    davvero (costa minuti di ping: in gioco i livelli sono già collaudati e si salta).
COLLAUDO = os.environ.get("PNA_COLLAUDO", "").strip() == "1"
SESSION_TOKEN = secrets.token_urlsafe(32)
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
CODA_S = 180  # quanto un'azione aspetta in coda che la rete si liberi prima di rispondere 409

# Pacchetto di gioco (contenuti.py): missioni, livelli, messaggi, punteggi e dispositivi, letti dai YAML e
# ricaricati da soli quando cambiano. Un errore al primo avvio ferma il server con un messaggio chiaro.
try:
    GIOCO = C.Contenuti()
except C.ErroreContenuti as _e:
    raise SystemExit(f"\n  ERRORE nel pacchetto di gioco ({C.CARTELLA}):\n  {_e}\n")


def G() -> C.Pacchetto:
    """Il pacchetto di gioco aggiornato (ricaricato se un YAML è cambiato)."""
    return GIOCO.get()


def punteggio(k: str) -> int:
    """Una voce di «punteggio» di impostazioni.yaml: guasto_riparato, costo_indizio, bonus_senza_indizi."""
    return G().impostazioni["punteggio"][k]

# Validazione degli input che finiscono nella rete.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
POOL_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
CLI_LINE_RE = re.compile(r"^[\x20-\x7E]{0,160}$")
TUTTI = ("router", "switch", "firewall", "pc", "server")  # i dispositivi che il gioco sa gestire
TIPO_DI = {"cloud": "server"}   # tipi del gioco che nella rete sono un altro dispositivo (il cloud è un server)
HOST_CMD_RE = re.compile(r"^(ping|ipconfig|tracert|arp|nslookup)( [A-Za-z0-9./ -]{0,60})?$", re.I)


# ───────────────────────────── stato della partita (data/state.json) ─────────────────────────────
def default_state() -> dict:
    """Partita nuova. Lo schema è descritto in docs/ARCHITETTURA.md, «Lo stato della partita»."""
    return {"score": 0, "mode": "academy", "academy": {"done": [], "passi": {}, "pronte": []},
            "rescue": {"level": None, "status": "idle", "faults": {}, "best": {}, "message": ""},
            # campagna: giorni completati, saldo della valuta (None = ancora da dare quella iniziale),
            # aiuti comprati per giorno ({"2": ["mezza-rete"]}) e ultimo giorno chiuso (per il dialogo «dopo»)
            "campaign": {"done": [], "wallet": None, "helps": {}, "last": None},
            "credentials": {},  # dispositivo → credenziali della console (mai restituite dall'API)
            "firewall": {},     # firewall → regole del giocatore (compilate nelle access-list del firewall)
            "roles": {},        # dispositivo → ruolo mostrato dal client (es. "cloud": un server presentato come cloud)
            "dns": {},          # server DNS → {nome: IP}: i record creati dal giocatore (li mostra il client)
            "dhcp_esclusi": {},  # router → [[da, a], …]: indirizzi esclusi dai pool (li mostra il client)
            "log": []}


def load_state() -> dict:
    """Carica la partita salvata, completando i campi mancanti con quelli di una partita nuova."""
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        base = default_state()
        base.update(s)
        for k, v in default_state()["campaign"].items():  # partite salvate prima della campagna
            base["campaign"].setdefault(k, v)
        base["academy"].setdefault("passi", {})               # passi interattivi completati, per missione
        base["academy"].setdefault("pronte", [])              # missioni il cui tavolo di partenza è già pronto
        return base
    except Exception:
        return default_state()


STATE = load_state()


def save() -> None:
    """Salva la partita su disco. Va chiamata dopo ogni modifica a STATE (log() lo fa già)."""
    # Scrittura su un file temporaneo e poi sostituzione: un errore a metà non tronca la partita salvata.
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def log(msg: str, kind: str = "info", points: int = 0) -> None:
    """Aggiunge una riga al diario, somma i punti e salva.

    kind: info, build (tavolo), config, ok, fail, win, hint. Il diario tiene le ultime 80 righe.
    """
    STATE["score"] += points
    STATE["log"].insert(0, {"t": time.time(), "msg": msg, "kind": kind, "points": points})
    del STATE["log"][80:]
    save()


motore = Motore(DATA)  # il motore di rete (rete/motore.py), condiviso da tutte le richieste


# ───────────────────────────── lavori lunghi (job) ─────────────────────────────
class Job:
    """Un'operazione lunga che gira in background (preparare un livello, verificare).

    Il client la segue leggendo GET /api/state, campo «job» (vedi view()).
    """

    def __init__(self, title: str) -> None:
        self.id = secrets.token_hex(4)
        self.title = title
        self.steps: list[str] = []
        self.done = False
        self.ok = False
        self.result: dict = {}
        self.started = time.time()

    def step(self, msg: str) -> None:
        """Racconta il passo in corso: il client lo mostra mentre aspetta."""
        self.steps.append(msg)

    def view(self) -> dict:
        """La forma pubblica del lavoro, documentata in docs/API.md, «Lavoro (job)»."""
        return {"id": self.id, "title": self.title, "steps": self.steps[-8:], "done": self.done, "ok": self.ok,
                "result": self.result, "elapsed": round(time.time() - self.started)}


JOB: Job | None = None  # l'ultimo lavoro, in corso o finito


def start_job(title: str, coro_fn) -> JSONResponse:
    """Lancia coro_fn(job) in background con il lock della rete e risponde subito.

    coro_fn restituisce il risultato {"ok", "message", "win"?}; un ValueError o un ErroreRete diventano
    un risultato con ok=False e il messaggio. Se c'è già un altro lavoro in corso risponde 409; se la rete è solo
    occupato da un'azione breve il lavoro parte e aspetta il suo turno sul lock.
    """
    global JOB
    if JOB and not JOB.done:
        return err(f"La rete è occupata: {JOB.title if JOB and not JOB.done else 'un’altra operazione'}", 409)
    job = Job(title)
    JOB = job

    async def runner():
        async with motore.lock:
            try:
                job.result = await coro_fn(job) or {}
                job.ok = job.result.get("ok", True)
            except ErroreRete as e:
                job.result, job.ok = {"ok": False, "message": f"Rete: {e}"}, False
            except ValueError as e:
                job.result, job.ok = {"ok": False, "message": str(e)}, False
            except Exception as e:  # noqa: BLE001
                job.result, job.ok = {"ok": False, "message": f"Errore inatteso: {e}"}, False
            finally:
                try:
                    await motore.refresh()
                except Exception:
                    pass
                job.done = True
    asyncio.create_task(runner())
    return JSONResponse({"ok": True, "job": job.view()})


# ───────────────────────────── aiutanti di validazione ─────────────────────────────
# Ogni errore dell'utente è un ValueError con un messaggio in italiano per il giocatore: i wrapper HTTP lo
# trasformano in una risposta 400.

def err(msg: str, code: int = 400) -> JSONResponse:
    """Risposta di errore nel formato comune: {"ok": false, "error": msg}."""
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def ip_of(v, what: str) -> str:
    """Un indirizzo IPv4 valido, normalizzato. what = come chiamarlo nel messaggio d'errore."""
    try:
        return str(ipaddress.IPv4Address(str(v).strip()))
    except ValueError:
        raise ValueError(f"{what} non valido: «{v}». Scrivilo come quattro numeri tra 0 e 255, es. 192.168.10.1")


def mask_of(v) -> str:
    """Una maschera valida in forma puntata. Accetta anche «24» e «/24»."""
    v = str(v).strip()
    if v.startswith("/"):
        v = v[1:]
    if v.isdigit():
        if not 0 < int(v) <= 32:
            raise ValueError("maschera non valida")
        return str(ipaddress.IPv4Network(f"0.0.0.0/{v}").netmask)
    m = ip_of(v, "Maschera")
    try:
        ipaddress.IPv4Network(f"0.0.0.0/{m}")
    except ValueError:
        raise ValueError(f"Maschera non valida: «{m}». Esempi validi: 255.255.255.0, 255.255.255.252")
    return m


def need(kind: str | tuple, name: str) -> dict:
    """Il dispositivo «name» della fotografia attuale, che deve essere di uno dei tipi indicati."""
    d = motore.topo.dev(name)
    kinds = (kind,) if isinstance(kind, str) else kind
    if not d:
        raise ValueError(f"il dispositivo «{name}» non esiste")
    if d["kind"] not in kinds:
        raise ValueError(f"operazione non valida su {name}")
    return d


def need_port(d: dict, port: str) -> dict:
    """La porta «port» (nome completo) del dispositivo d."""
    p = Topo.port(d, port)
    if not p:
        raise ValueError(f"{d['name']} non ha la porta «{port}»")
    return p


def need_bool(b: dict, key: str) -> bool:
    """Il campo booleano obbligatorio «key» del body (true/false JSON, non stringhe)."""
    v = b.get(key)
    if not isinstance(v, bool):
        raise ValueError(f"il campo «{key}» deve essere true o false")
    return v


async def console(device: str, commands: list[str], timeout: float = 30) -> tuple[bool, str]:
    """Scrive comandi nella console di un router o switch, facendo prima il login se serve.

    Le credenziali arrivano da STATE["credentials"] (salvate da /api/device/harden o /api/device/login).
    Se la console è protetta e le credenziali mancano o sono sbagliate: ValueError con le istruzioni.
    """
    creds = STATE["credentials"].get(device)
    if creds:
        try:
            await motore.unlock(device, creds)
        except ValueError as e:
            raise ValueError(f"{e}. Aggiorna le credenziali con /api/device/login.")
    if motore.bloccata(device):
        raise ValueError(f"La console di {device} è protetta da password: comunica utente e password "
                         f"con /api/device/login (o dal pannello del dispositivo) e riprova.")
    return await motore.cli(device, commands)


async def configura(device: str, lines: list[str], what: str) -> str:
    """Esegue righe di configurazione sulla console (con login se serve) e torna in modalità privilegiata.

    Aggiunge enable / configure terminal / end. Se la console rifiuta una riga lancia ValueError con la riga
    incriminata. what = cosa si stava facendo.
    """
    good, out = await console(device, ["enable", "configure terminal", *lines, "end"])
    if not good:
        bad = [l for l in out.splitlines() if l.lstrip().startswith(("%", "ERROR"))]
        raise ValueError(f"{what}: la console ha rifiutato il comando. " + " ".join(bad[:3])[:400])
    return out


# ───────────────────────────── Accademia ─────────────────────────────
def missione_corrente(g: C.Pacchetto) -> M.Mission | None:
    """La missione da giocare: la prima non fatta del giorno di campagna in corso, altrimenti la prima non fatta."""
    done = STATE["academy"]["done"]
    giorno = g.campagna.corrente(STATE["campaign"]["done"])
    if giorno:
        per_id = {m.id: m for m in g.missioni}
        m = next((per_id[i] for i in giorno.missioni if i not in done and i in per_id), None)
        if m:
            return m
    return next((m for m in g.missioni if m.id not in done), None)


def academy_view(t: Topo) -> dict:
    """Il campo «academy» dello Stato: missioni e passi della missione in corso, ricalcolati su t."""
    done = STATE["academy"]["done"]
    missioni = G().missioni
    current = missione_corrente(G())
    out = []
    for m in missioni:
        out.append({"id": m.id, "title": m.title, "points": m.points, "done": m.id in done})
    cur = None
    if current:
        cur = {"id": current.id, "title": current.title, "points": current.points, "story": current.story,
               "theory": current.theory, "steps": M.evaluate(current, t, passi_fatti(current)),
               "topology": current.topologia, "plan": current.piano, "tips": current.consigli,
               "has_network": bool(current.rete), "tutorial": current.tutorial,
               # il tavolo di partenza (una topologia o il tavolo vuoto) non è ancora stato preparato: il client
               # chiede POST /api/academy/network prima che il giocatore cominci
               "needs_setup": bool(current.rete) and current.id not in STATE["academy"]["pronte"]}
    return {"missions": out, "current": cur, "finished": current is None}


def passi_fatti(m: M.Mission) -> set[int]:
    """Gli indici dei passi interattivi (domanda, osserva) già completati in questa missione."""
    return set(STATE["academy"]["passi"].get(m.id, []))


def segna_passo(m: M.Mission, i: int) -> None:
    fatti = STATE["academy"]["passi"].setdefault(m.id, [])
    if i not in fatti:
        fatti.append(i)
    save()


async def carica_rete_missione(job: Job | None, m: M.Mission) -> None:
    """Prepara il tavolo per la missione m: carica la sua topologia (rete di partenza, gioco/reti/*.yaml),
    oppure svuota il tavolo (rete «vuota», o nessuna rete). Sostituisce la rete che c'è!
    Azzera i passi interattivi e segna la missione come pronta."""
    g = G()
    STATE["roles"], STATE["firewall"] = {}, {}
    STATE["academy"]["passi"].pop(m.id, None)
    if m.rete and m.rete != "vuota":
        if job:
            job.step(g.accademia("carico_rete", titolo=m.title))
        await motore.carica_topologia(GIOCO.cartella / "reti" / m.rete)
    else:
        if job:
            job.step(g.accademia("svuoto_tavolo"))
        await motore.clear()
    if m.id not in STATE["academy"]["pronte"]:
        STATE["academy"]["pronte"].append(m.id)
    save()


async def job_academy_check(job: Job) -> dict:
    """Lavoro: verifica la missione in corso (passi sulla fotografia, poi il ping vero). Assegna i punti."""
    motore.links_dirty = True
    await motore.settle()
    t = await motore.refresh()
    done = STATE["academy"]["done"]
    g = G()
    m = missione_corrente(g)
    if not m:
        return {"ok": True, "message": g.accademia("tutte_completate"), **campaign_advance()}
    job.step(g.accademia("controllo_in_corso", titolo=m.title))
    fatti = passi_fatti(m)
    # prima i controlli sulla fotografia e i passi interattivi (istantanei), poi il traffico vero
    for i, s in enumerate(m.steps):
        if s.kind == "controllo":
            good, msg = s.check(t)
        elif s.interattivo:
            good = i in fatti
            msg = g.accademia("rispondi" if s.kind == "domanda" else "osserva_prima")
        else:
            continue
        if not good:
            log(g.accademia("passo_mancante_diario", titolo=m.title, numero=i + 1), "fail")
            if not m.tutorial and s.kind == "controllo":
                # fuori dal tutorial non si dice cosa sistemare: il giocatore ci arriva da solo (o con i consigli)
                return {"ok": False, "message": g.accademia("condizione_mancante", passo=s.text)}
            return {"ok": False, "message": g.accademia("passo_mancante", numero=i + 1, passo=s.text, motivo=msg)}
    for i, s in enumerate(m.steps):
        if s.kind == "verifica":
            if not m.tutorial:      # fuori dal tutorial si vede solo la condizione, non i singoli ping
                job.step(g.accademia("verifico_condizione", passo=s.text))
            for c in s.verifiche:
                good, why = await run_check(job if m.tutorial else None, t, c)
                if not good:
                    log(g.accademia("ping_fallito_diario", titolo=m.title), "fail")
                    if not m.tutorial:   # fuori dal tutorial non si dice quale dispositivo non risponde
                        return {"ok": False, "message": g.accademia("condizione_mancante", passo=s.text)}
                    return {"ok": False, "message": g.accademia("verifica_fallita", numero=i + 1, passo=s.text,
                                                                 motivo=why)}
        elif s.kind == "ping":
            plan = s.ping(t)
            if not plan:
                return {"ok": False, "message": g.accademia("niente_da_pingare", numero=i + 1)}
            notes = []
            for src, dst, label in plan[:2]:
                job.step(g.accademia("ping_in_corso", etichetta=label, ip=dst))
                good, _ = await motore.ping(src, dst, STATE["credentials"].get(src))
                if good:
                    break
                notes.append(g.accademia("ping_fallito", etichetta=label, ip=dst))
            else:
                log(g.accademia("ping_fallito_diario", titolo=m.title), "fail")
                return {"ok": False, "message": " ".join(notes) + " " + g.accademia("ping_fallito_consiglio")}
    done.append(m.id)
    log(g.accademia("completata_diario", titolo=m.title), "win", m.points)
    esito = {"ok": True, "message": g.accademia("completata", titolo=m.title, punti=m.points), "win": True,
             **campaign_advance()}
    # la missione dopo ha una sua rete di partenza (la topologia cambia): la si carica subito
    dopo = missione_corrente(g)
    if dopo and dopo.rete:
        await carica_rete_missione(job, dopo)
        esito["message"] += " " + g.accademia("rete_pronta", titolo=dopo.title)
    return esito


async def job_academy_reset(job: Job) -> dict:
    """Lavoro: svuota il tavolo e riporta l'Accademia alla missione 1 (il punteggio resta)."""
    g = G()
    job.step(g.accademia("svuoto_tavolo"))
    await motore.clear()
    STATE["roles"], STATE["firewall"] = {}, {}
    STATE["academy"]["done"] = []
    STATE["academy"]["passi"] = {}
    STATE["academy"]["pronte"] = []
    STATE["mode"] = "academy"
    prima = missione_corrente(g)
    if prima and prima.rete and prima.rete != "vuota":   # la prima missione può partire da una rete già pronta
        await carica_rete_missione(job, prima)
    elif prima and prima.rete == "vuota":                 # il tavolo è appena stato svuotato: è già pronta
        STATE["academy"]["pronte"].append(prima.id)
    log(g.accademia("ricominciata_diario"), "info")
    return {"ok": True, "message": g.accademia("ricominciata")}


# ───────────────────────────── Pronto Soccorso ─────────────────────────────
def rescue_view() -> dict:
    """Il campo «rescue» dello Stato. Titolo e soluzione di un guasto escono solo quando è chiuso."""
    rs = STATE["rescue"]
    g = G()
    levels = [{"n": lv.n, "title": lv.title, "faults": len(lv.faults), "best": rs["best"].get(str(lv.n))}
              for lv in g.livelli]
    cur = None
    lv = g.livelli_per_numero.get(rs["level"]) if rs["level"] else None
    if lv:
        faults = []
        for f in lv.faults:
            st = rs["faults"].get(f.id, {})
            item = {"id": f.id, "solved": st.get("solved", False), "hints": f.hints[:st.get("hints", 0)],
                    "surrendered": st.get("surrendered", False), "points": st.get("points", 0)}
            if item["solved"] or item["surrendered"]:
                item["title"], item["solution"] = f.title, f.solution
            else:
                item["symptom"] = f.symptom
            faults.append(item)
        cur = {"n": lv.n, "title": lv.title, "ticket": lv.ticket, "status": rs["status"], "faults": faults,
               "message": rs.get("message", ""), "lesson": lv.lesson if rs["status"] == "won" else ""}
    return {"levels": levels, "current": cur, "hint_cost": punteggio("costo_indizio"),
            "fault_points": punteggio("guasto_riparato")}


async def run_check(job: Job | None, t: Topo, c: R.Check) -> tuple[bool, str]:
    """Esegue una verifica di un livello sulla rete vera: (passata, perché no).

    Riusa la stessa logica degli endpoint: motore.ping, do_trace (/api/trace), do_browse (/api/host/browse),
    do_audit (/api/audit) e lo spegnimento (/api/device/power).
    """
    if job:
        job.step("Verifico: " + c.label())
    if c.kind == "ping":
        ip = R.resolve(t, c.dst)
        if not ip:
            return False, f"{c.dst.lstrip('@')} non ha un indirizzo IP"
        good, _ = await motore.ping(c.src, ip, STATE["credentials"].get(c.src))
        return good, "" if good else f"{c.src} → {ip} senza risposta"
    if c.kind == "blocked":
        tr = await do_trace(c.src, c.dst)
        if tr["delivered"]:
            return False, f"{c.src} raggiunge ancora {c.dst}"
        if c.at and (tr["blocked_by"] or {}).get("device") != c.at:
            where = (tr["blocked_by"] or {}).get("device") or "nessun dispositivo"
            return False, f"il traffico da {c.src} a {c.dst} si ferma su {where}, non su {c.at}"
        return True, ""
    if c.kind == "through":
        tr = await do_trace(c.src, c.dst)
        if not tr["delivered"]:
            where = (tr["blocked_by"] or {}).get("device")
            return False, f"{c.src} non raggiunge {c.dst}" + (f" (il ping si ferma su {where})" if where else "")
        if not any(h.get("device") == c.at for h in tr["hops"]):
            return False, f"il traffico da {c.src} a {c.dst} arriva, ma non passa da {c.at}"
        return True, ""
    if c.kind == "limited":
        tr = await do_trace(c.src, c.dst)
        if not tr["delivered"]:
            where = (tr["blocked_by"] or {}).get("device")
            return False, f"{c.src} non raggiunge {c.dst}" + (f" (il ping si ferma su {where})" if where else "")
        visti = set()
        for h in tr["hops"]:
            d = t.dev(h.get("device") or "")
            if h.get("leg") != "andata" or not d or d["kind"] != "router" or d["name"] in visti:
                continue
            visti.add(d["name"])
            limiti = await leggi_limiti(d["name"])
            if limiti.get(h.get("out") or "") or 0:
                return True, ""
        return False, f"sulla strada da {c.src} a {c.dst} nessun router ha un limite di velocità in uscita"
    if c.kind in ("web", "web_blocked"):
        good, _ = await do_browse(c.src, c.url)
        if c.kind == "web":
            return good, "" if good else f"{c.src} non riesce ad aprire {c.url}"
        return not good, "" if not good else f"{c.src} apre ancora {c.url}"
    if c.kind == "audit_clean":
        audit = await do_audit(c.device)
        if c.codes:
            bad = [f for f in audit["findings"] if f.get("code") in c.codes]
        else:
            bad = [f for f in audit["findings"] if R.SEVERITY.get(f["severity"], 0) >= R.SEVERITY[c.worst]]
        return not bad, "" if not bad else f"{c.device}: " + "; ".join(f["message"] for f in bad)
    if c.kind == "port":
        ip = R.resolve(t, c.dst)
        if not ip:
            return False, f"{c.dst.lstrip('@')} non ha un indirizzo IP"
        stato = await sonda_porta(c.src, ip, c.port)
        if c.passa is True:    # il traffico arriva al server (anche se lì non ascolta nessuno): non è filtrato
            return stato != "filtrata", "" if stato != "filtrata" else f"da {c.src} la porta {c.port} di {ip} è bloccata"
        if c.passa is False:   # il traffico non arriva: lo scarta un firewall o un'ACL
            return stato == "filtrata", "" if stato == "filtrata" else f"da {c.src} la porta {c.port} di {ip} passa ancora"
        if c.open:
            return stato == "aperta", "" if stato == "aperta" else f"{c.src} non riesce ad aprire {ip}:{c.port} ({stato})"
        return stato != "aperta", "" if stato != "aperta" else f"da {c.src} la porta {c.port} di {ip} è ancora aperta"
    if c.kind == "resolve":
        risolto, server = await nslookup(c.src, c.url)
        if not risolto:
            return False, f"da {c.src} il nome {c.url} non si risolve"
        atteso = c.dst
        if atteso == "resolver":
            atteso = server
        elif atteso == "server":   # un server qualsiasi sul tavolo
            ok_ = any(t.iface(s) and str(t.iface(s).ip) == risolto for s in t.of("server"))
            return ok_, "" if ok_ else f"{c.url} porta a {risolto}, che non è un server"
        elif atteso:
            atteso = R.resolve(t, atteso)
        return risolto == atteso, "" if risolto == atteso else f"{c.url} porta a {risolto}, non a {atteso}"
    if c.kind == "one":
        passate = []
        for inner in c.inner:
            good, _ = await run_check(None, t, inner)
            if good:
                passate.append(inner.src)
        if len(passate) == 1:
            return True, ""
        return False, ("nessuno: " if not passate else f"più di uno ({', '.join(passate)}): ") + c.label()
    if c.kind == "plan":
        checks = M.PIANI_VERIFICA[c.device](t, STATE["dns"])
        if not checks:
            return False, "sul tavolo non trovo ancora i dispositivi da provare"
        for inner in checks:
            good, why = await run_check(job, t, inner)
            if not good:
                return False, why
        return True, ""
    if c.kind == "survives":
        await motore.power(c.device, False)
        try:
            t2 = await motore.refresh()
            for inner in c.inner:
                good, why = await run_check(job, t2, inner)
                if not good:
                    return False, f"con {c.device} spento: {why}"
            return True, ""
        finally:
            await motore.power(c.device, True)
            await motore.settle()
    raise ValueError(f"verifica sconosciuta: {c.kind}")


async def run_checks(job: Job | None, t: Topo, checks: list[R.Check]) -> tuple[bool, str, R.Check | None]:
    """Esegue le verifiche in ordine; si ferma alla prima che fallisce: (tutte passate, perché no, quale)."""
    for c in checks:
        good, why = await run_check(job, t, c)
        if not good:
            return False, why, c
    return True, "", None


async def wait_hosts(job: Job, timeout: float = 60) -> Topo:
    """Aspetta (al massimo timeout secondi) che tutti gli host abbiano un indirizzo dal DHCP."""
    end = time.time() + timeout
    while True:
        t = await motore.refresh()
        missing = [h["name"] for h in t.hosts() if not t.iface(h)]
        if not missing or time.time() > end:
            return t
        job.step("In attesa degli indirizzi DHCP: " + ", ".join(missing))
        motore.links_dirty = True
        await motore.settle()
        await asyncio.sleep(1)


AT_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]{0,23})")


def risolvi_at(t: Topo, testo: str) -> str:
    """Sostituisce ogni «@PC3» con l'IP che PC3 ha adesso (serve per gli IP dati dal DHCP). ValueError se manca."""
    def sub(m):
        ip = R.resolve(t, "@" + m.group(1))
        if not ip:
            raise ValueError(f"{m.group(1)} non ha un indirizzo IP")
        return ip
    return AT_RE.sub(sub, testo)


async def esegui_azioni(ops: list, t: Topo) -> None:
    """Esegue operazioni sulla rete descritte nel pacchetto di gioco (guasti da iniettare, aiuti dei colleghi).

    ("cli", dispositivo, righe)           righe della console così come sono (con enable/configure terminal/end);
                                           «@PC3» diventa l'IP attuale di PC3
    ("hostip", argomenti)                  {"device", "mode"?: "dhcp", "ip", "mask", "gateway", "dns"?};
                                           i valori «@PC2» diventano l'IP attuale di PC2
    ("add", tipo, nome, x, y)              aggiunge un dispositivo (modello da impostazioni.yaml), se non c'è già
    ("link", a, b)                         collega due dispositivi sulla prima porta libera adatta, se non lo sono già
    ("portip", router, verso, ip, maschera)  IP e no shutdown sulla porta del router collegata a «verso»
    ("fwzone", firewall, verso, zona, livello, ip, maschera)  la porta del firewall verso «verso» diventa una zona
    ("fwrules", firewall, regole)          regole del firewall (come /api/firewall/rules), salvate e applicate
    Si ferma alla prima che fallisce con ValueError. Non prende il lock: lo deve avere chi chiama.
    """
    for op in ops:
        if op[0] == "cli":
            good, out = await motore.cli(op[1], [risolvi_at(t, r) for r in op[2]])
            if not good:
                raise ValueError(f"{op[1]}: {out[-300:]}")
        elif op[0] == "hostip":
            args = {k: (R.resolve(t, v) if isinstance(v, str) and v.startswith("@") else v) for k, v in op[1].items()}
            if args.get("mode") == "dhcp":
                await motore.host_dhcp(args["device"])
            else:
                await motore.host_ip(args["device"], args["ip"], mask_of(args.get("mask", "255.255.255.0")),
                                  args.get("gateway") or "", args.get("dns"))
        elif op[0] == "add":
            _, kind, name, x, y = op
            if kind == "cloud":
                STATE["roles"][name] = "cloud"
            if not t.dev(name):
                await motore.add_device(TIPO_DI.get(kind, kind), name, x, y)
        elif op[0] == "link":
            a, c = t.dev(op[1]), t.dev(op[2])
            if not a or not c:
                raise ValueError(f"manca {op[1] if not a else op[2]} sul tavolo")
            if not any(peer == c["name"] for _, peer, _ in t.neighbors(a["name"])):
                pa, pb = t.free_port(a, c["kind"]), t.free_port(c, a["kind"])
                if not pa or not pb:
                    raise ValueError(f"nessuna porta libera su {a['name'] if not pa else c['name']}")
                await motore.link(a["name"], pa, c["name"], pb)
        elif op[0] == "portip":
            _, router, verso, ip, mask = op
            porta = next((p for p, peer, _ in t.neighbors(router) if peer == verso), None)
            if not porta:
                raise ValueError(f"{router} non è collegato a {verso}")
            await configura(router, [f"interface {porta}", f"ip address {ip} {mask}", "no shutdown"], f"{router} {porta}")
        elif op[0] == "fwzone":
            _, fw, verso, zona, livello, ip, mask = op
            porta = next((p for p, peer, _ in t.neighbors(fw) if peer == verso), None)
            if not porta:
                raise ValueError(f"{fw} non è collegato a {verso}")
            await configura_firewall(fw, [(f"interface {porta}", "global"), (f"nameif {zona}", ""), (f"security-level {livello}", ""),
                           (f"ip address {ip} {mask}", ""), ("no shutdown", ""), ("exit", "")], f"{fw} {porta}")
        elif op[0] == "fwrules":
            _, fw, regole = op
            t = await motore.refresh()
            d = t.dev(fw)
            if not d:
                raise ValueError(f"manca {fw} sul tavolo")
            zone = zone_firewall(d)
            STATE["firewall"][fw] = [FW.valida_regola(r, zone, i) for i, r in enumerate(regole, 1)]
            await applica_regole(d)
        else:
            raise ValueError(f"azione sconosciuta: {op[0]}")
        if op[0] in ("add", "link", "fwzone"):
            await motore.settle()
            t = await motore.refresh()


async def job_rescue_start(job: Job, n: int) -> dict:
    """Lavoro: prepara il livello n. Svuota, costruisce, rompe.

    Con PNA_COLLAUDO=1 (sviluppo) verifica anche che la rete di partenza funzioni e che ogni guasto si veda.
    """
    g = G()
    lv = g.livelli_per_numero[n]
    rs = STATE["rescue"]
    rs.update({"level": n, "status": "setup", "faults": {}, "message": ""})
    STATE["mode"] = "rescue"
    save()
    job.step(g.soccorso("svuoto_tavolo"))
    await motore.clear()
    STATE["roles"], STATE["firewall"] = {}, {}
    job.step(g.soccorso("costruisco", numero=n, titolo=lv.title))
    if lv.build:   # la topologia di partenza (gioco/reti/livelloN.yaml)
        await motore.carica_topologia(GIOCO.cartella / "reti" / lv.build)
    if lv.setup:  # reti costruite con le azioni (firewall, cloud) o ritocchi alla topologia
        await esegui_azioni(lv.setup, await motore.refresh())
    motore.links_dirty = True
    await motore.settle()
    t = await wait_hosts(job)  # gli IP dal DHCP servono per i guasti che usano «@PC2»
    if COLLAUDO:
        job.step(g.soccorso("controllo_partenza"))
        for f in lv.faults:
            if not f.inject:   # la falla c'è già nella rete di partenza («rompi: []»): lì il guasto si vede da subito
                continue
            # Solo il traffico legittimo (ping, web): le verifiche di sicurezza all'inizio di solito falliscono.
            good, why, _ = await run_checks(job, t, [c for c in f.all_checks() if c.functional])
            if not good:
                raise ValueError(f"La rete di partenza non funziona ({why}): riprova a lanciare il livello.")
    job.step(g.soccorso("rompo"))
    for f in lv.faults:
        try:
            await esegui_azioni(f.inject, t)
        except ValueError as e:
            raise ValueError(f"Non sono riuscito a preparare il guasto {f.id}: {e}")
    if COLLAUDO:
        await asyncio.sleep(2)
        t = await motore.refresh()
        job.step(g.soccorso("controllo_guasti"))
        for f in lv.faults:
            # Almeno una verifica deve fallire, altrimenti il guasto non si vede. Fino a 3 tentativi a 5 s:
            # dopo un cambio di routing (es. OSPF) le rotte spariscono con qualche secondo di ritardo.
            broken = False
            for attempt in range(3):
                good, _, _ = await run_checks(job, t, f.all_checks())
                if not good:
                    broken = True
                    break
                await asyncio.sleep(5)
            if not broken:
                raise ValueError(f"Il guasto {f.id} non si vede: tutte le sue verifiche passano già. "
                                 f"Riprova a lanciare il livello o controlla la sua definizione.")
    rs.update({"status": "playing", "faults": {f.id: {"solved": False, "hints": 0, "surrendered": False, "points": 0}
                                               for f in lv.faults}, "started": time.time(),
               "message": g.soccorso("istruzioni")})
    log(g.soccorso("pronto_diario", numero=n, titolo=lv.title), "info")
    return {"ok": True, "message": g.soccorso("pronto", numero=n)}


async def job_rescue_check(job: Job) -> dict:
    """Lavoro: per ogni guasto aperto rifà le sue verifiche; se passano (e il ticket è rispettato) è riparato.

    Quando sono riparati tutti esegue le verifiche finali, chiude il livello e assegna bonus e record.
    """
    rs = STATE["rescue"]
    g = G()
    lv = g.livelli_per_numero.get(rs["level"])
    if not lv or rs["status"] not in ("playing", "won"):
        raise ValueError(g.soccorso("nessun_livello"))
    job.step(g.soccorso("avanzo_tempo"))
    motore.links_dirty = True
    await motore.settle()
    t = await motore.refresh()
    newly, still = [], []
    for f in lv.faults:
        # setdefault: se nel frattempo il YAML ha aggiunto un guasto al livello, parte da zero
        st = rs["faults"].setdefault(f.id, {"solved": False, "hints": 0, "surrendered": False, "points": 0})
        if st["solved"]:
            continue
        good, why, failed = await run_checks(job, t, f.all_checks())
        is_security = any(not c.functional for c in f.all_checks())
        if not good and failed is not None and failed.functional and is_security:
            # Falla di sicurezza: le verifiche di sicurezza (scritte per prime) passano, ma si è rotto traffico
            # che deve passare. Va detto chiaramente, altrimenti il giocatore rileggerebbe il sintomo originale.
            still.append(g.soccorso("troppo_bloccato", motivo=why))
            continue
        if good and f.require:
            # Il sintomo è sparito: solo ora si controlla che non sia una scorciatoia che viola il ticket.
            ok_req, why = f.require(t)
            if not ok_req:
                still.append(g.soccorso("scorciatoia", motivo=why))
                continue
        if good:
            st["solved"] = True
            st["points"] = 0 if st["surrendered"] else max(
                0, punteggio("guasto_riparato") - punteggio("costo_indizio") * st["hints"])
            newly.append(f)
            log(g.soccorso("guasto_riparato_diario", titolo=f.title), "win", st["points"])
        else:
            still.append(f.symptom)
    if still:
        rs["message"] = g.soccorso("ancora_da_sistemare", elenco=" · ".join(still))
        save()
        riparati = "" if not newly else (g.soccorso("riparati_uno") if len(newly) == 1
                                         else g.soccorso("riparati_piu", numero=len(newly)))
        msg = riparati + rs["message"]
        return {"ok": bool(newly), "message": msg}
    job.step(g.soccorso("controllo_finale"))
    good, why, _ = await run_checks(job, t, lv.all_final_checks())
    if not good:
        rs["message"] = g.soccorso("finale_fallito", motivo=why)
        save()
        return {"ok": False, "message": rs["message"]}
    if rs["status"] != "won":
        rs["status"] = "won"
        hints = sum(s["hints"] for s in rs["faults"].values())
        surrendered = any(s["surrendered"] for s in rs["faults"].values())
        bonus = punteggio("bonus_senza_indizi") if hints == 0 and not surrendered else 0
        total = sum(s["points"] for s in rs["faults"].values()) + bonus
        best = rs["best"].get(str(lv.n)) or 0
        rs["best"][str(lv.n)] = max(best, total)
        rs["message"] = g.soccorso("completato", punti=total) + (g.soccorso("bonus", bonus=bonus) if bonus else "")
        log(g.soccorso("completato_diario", numero=lv.n, titolo=lv.title), "win", bonus)
    return {"ok": True, "message": rs["message"], "win": True, **campaign_advance()}


def rescue_open_fault() -> tuple[R.Level, R.Fault, dict]:
    """Il primo guasto aperto del livello in corso: (livello, guasto, suo stato). ValueError se non c'è."""
    rs = STATE["rescue"]
    g = G()
    lv = g.livelli_per_numero.get(rs["level"])
    if not lv or rs["status"] != "playing":
        raise ValueError(g.soccorso("nessun_livello"))
    for f in lv.faults:
        st = rs["faults"].setdefault(f.id, {"solved": False, "hints": 0, "surrendered": False, "points": 0})
        if not st["solved"] and not st["surrendered"]:
            return lv, f, st
    raise ValueError(g.soccorso("nessun_guasto_aperto"))


# ───────────────────────────── Campagna (campagna.py, campagna.yaml) ─────────────────────────────
def wallet() -> int:
    """Il saldo della valuta di gioco (buoni caffè). Alla prima lettura vale «buoni_iniziali»."""
    cs = STATE["campaign"]
    if cs["wallet"] is None:
        cs["wallet"] = G().campagna.buoni_iniziali
    return cs["wallet"]


def livelli_vinti() -> set[int]:
    return {int(k) for k in STATE["rescue"]["best"]}


def battute(c: K.Campagna, righe: list[K.Battuta]) -> list[dict]:
    """Dialoghi per il client: con nome, ruolo e icona di chi parla."""
    out = []
    for b in righe:
        p = c.personaggi.get(b.chi) or K.Personaggio(b.chi, b.chi)
        out.append({"who": b.chi, "name": p.nome, "role": p.ruolo, "icon": p.icona, "text": b.testo})
    return out


def premio(g: C.Pacchetto, giorno: K.Giorno) -> str:
    """« +2 buoni caffè», oppure niente se il giorno non dà ricompensa."""
    return g.storia("premio", ricompensa=g.campagna.valuta_per(giorno.buoni)) if giorno.buoni else ""


def campaign_advance() -> dict:
    """Chiude i giorni le cui attività sono tutte fatte: ricompensa, diario, dialogo «dopo».

    Si chiama dopo ogni missione o livello vinto (e prima delle verifiche, per chi aveva già giocato).
    Risposta da aggiungere a quella dell'endpoint: {"day_done": {n, title, reward, outro}} se si è chiuso un giorno.
    """
    g = G()
    c, cs = g.campagna, STATE["campaign"]
    chiusi = []
    while True:
        giorno = c.corrente(cs["done"])
        if not giorno or not giorno.fatto(STATE["academy"]["done"], livelli_vinti()):
            break
        wallet()
        cs["done"].append(giorno.n)
        cs["wallet"] += giorno.buoni
        cs["last"] = giorno.n
        chiusi.append(giorno)
        log(g.storia("giorno_completato_diario", icona=c.valuta["icona"], numero=giorno.n, titolo=giorno.titolo,
                     premio=premio(g, giorno)), "win")
        if not c.corrente(cs["done"]):
            log(g.storia("campagna_finita_diario"), "win")
    if not chiusi:
        return {}
    giorno = chiusi[-1]
    return {"day_done": {"n": giorno.n, "title": giorno.titolo, "reward": giorno.buoni,
                         "message": g.storia("giorno_completato", numero=giorno.n, titolo=giorno.titolo,
                                             premio=premio(g, giorno)),
                         "outro": battute(c, giorno.dopo)}}


def campaign_view() -> dict:
    """Il campo «campaign» dello Stato. Le risposte dei colleghi escono solo per gli aiuti già comprati."""
    g = G()
    c, cs = g.campagna, STATE["campaign"]
    saldo = wallet() if c.giorni else 0
    corrente = c.corrente(cs["done"])
    missioni = {m.id: m for m in g.missioni}
    livelli = g.livelli_per_numero
    vinti = livelli_vinti()
    rs = STATE["rescue"]
    cur = None
    if corrente:
        comprati = cs["helps"].get(str(corrente.n), [])
        attivita = [{"kind": "mission", "id": i, "title": missioni[i].title if i in missioni else i,
                     "done": i in STATE["academy"]["done"]} for i in corrente.missioni]
        attivita += [{"kind": "level", "n": n, "title": livelli[n].title if n in livelli else str(n),
                      "done": n in vinti} for n in corrente.livelli]
        aiuti = []
        for a in corrente.aiuti:
            p = c.personaggi[a.collega]
            usato = a.id in comprati
            disponibile = a.livello is None or (rs["level"] == a.livello and rs["status"] == "playing")
            aiuti.append({"id": a.id, "colleague": {"id": p.id, "name": p.nome, "role": p.ruolo, "icon": p.icona},
                          "cost": a.costo, "request": a.richiesta, "used": usato,
                          "answer": a.risposta if usato else "", "acts": bool(a.azioni), "level": a.livello,
                          "available": disponibile, "affordable": saldo >= a.costo})
        cur = {"n": corrente.n, "title": corrente.titolo, "reward": corrente.buoni,
               "intro": battute(c, corrente.prima), "activities": attivita, "helps": aiuti}
    ultimo = next((x for x in c.giorni if x.n == cs["last"]), None)
    return {
        "enabled": bool(c.giorni),
        "currency": c.valuta,
        "wallet": saldo,
        "days": [{"n": x.n, "title": x.titolo, "done": x.n in cs["done"], "current": corrente is x} for x in c.giorni],
        "current": cur,
        "last": {"n": ultimo.n, "title": ultimo.titolo, "outro": battute(c, ultimo.dopo)} if ultimo else None,
        "finished": bool(c.giorni) and corrente is None,
        "ending": battute(c, c.finale) if c.giorni and corrente is None else [],
        "characters": {p.id: {"name": p.nome, "role": p.ruolo, "icon": p.icona} for p in c.personaggi.values()},
    }


# ───────────────────────────── livello HTTP: login, stato, wrapper ─────────────────────────────
FAILS: dict[str, list[float]] = {}  # indirizzo → istanti dei codici sbagliati (limite ai tentativi)


def client_ip(req: Request) -> str:
    """L'indirizzo del client. Il proxy (Tailscale Funnel) aggiunge quello vero in fondo a X-Forwarded-For;
    i valori prima li scrive il client e non sono affidabili."""
    return req.headers.get("x-forwarded-for", "").split(",")[-1].strip() or (req.client.host if req.client else "?")


def authed(req: Request) -> bool:
    """Il cookie di sessione è valido; senza codice di accesso non serve (ci pensa solo_locale)."""
    if not LOGIN_REQUIRED:
        return True
    return secrets.compare_digest(req.cookies.get("pna", ""), SESSION_TOKEN)


def request_host(req: Request) -> str:
    """Il nome a cui è indirizzata la richiesta (header Host), senza porta: «127.0.0.1», «localhost», «::1»…"""
    host = req.headers.get("host", "")
    if host.startswith("["):
        return host[1:host.find("]")]
    return host.split(":")[0]


async def login(req: Request):
    """POST /api/login — Body: {"code": str}. Imposta il cookie «pna». 403 codice sbagliato, 429 troppi tentativi.

    Senza codice di accesso risponde sempre {"ok": true}: non c'è niente da verificare.
    """
    if not LOGIN_REQUIRED:
        return JSONResponse({"ok": True})
    ip = client_ip(req)
    recent = [t for t in FAILS.get(ip, []) if time.time() - t < 300]
    everyone = sum(1 for v in FAILS.values() for t in v if time.time() - t < 300)
    if len(recent) >= 8 or everyone >= 40:
        return err("Troppi tentativi: riprova tra qualche minuto.", 429)
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not secrets.compare_digest(str(body.get("code", "")).strip(), ACCESS_CODE):
        FAILS[ip] = recent + [time.time()]
        await asyncio.sleep(1.0)
        return err("Codice errato", 403)
    r = JSONResponse({"ok": True})
    https = req.headers.get("x-forwarded-proto") == "https" or req.url.scheme == "https"
    r.set_cookie("pna", SESSION_TOKEN, httponly=True, samesite="strict", secure=https, max_age=86400 * 7)
    return r


def public_state() -> dict:
    """L'oggetto Stato restituito da quasi tutte le risposte (docs/API.md, «L'oggetto Stato»)."""
    t = motore.topo
    return {
        "ok": True,
        "rete": {"ok": motore.ok, "status": motore.status},
        "topology": {**t.to_json(), "roles": STATE["roles"]},
        "firewall": STATE["firewall"],  # regole del giocatore per ogni firewall (le ACL compilate sono in topology)
        # solo per i dispositivi che ci sono ancora (un server o un router tolto non lascia tracce nel pannello)
        "dns": {k: v for k, v in STATE["dns"].items() if t.dev(k)},       # server DNS → {"on", "records"}
        "dhcp_excluded": {k: v for k, v in STATE.setdefault("dhcp_esclusi", {}).items() if t.dev(k)},  # router → [[da, a]]
        "snap_age": round(time.time() - motore.snap_at, 1),
        "busy": motore.lock.locked(),
        "job": JOB.view() if JOB else None,
        "score": STATE["score"],
        "mode": STATE["mode"],
        "academy": academy_view(t),
        "rescue": rescue_view(),
        "campaign": campaign_view(),
        "log": STATE["log"][:30],
        "errore_contenuti": GIOCO.errore,  # errore nei YAML del pacchetto di gioco ("" = tutto bene)
    }


async def state(req: Request):
    """GET /api/state — lo Stato. Non aspetta mai il lock: se la rete è libera rinnova una fotografia vecchia di 3 s."""
    if not authed(req):
        return err("non autenticato", 401)
    if time.time() - motore.snap_at > 3 and not motore.lock.locked():
        try:
            async with motore.lock:
                await motore.refresh()
        except Exception as e:  # noqa: BLE001
            motore.ok, motore.status = False, f"{e}"[:200]
    return JSONResponse(public_state())


async def health(req: Request):
    """GET /api/health — senza login. Il server è acceso e la rete è pronta?

    Risponde solo con lo stato del collegamento (niente rete, niente partita): serve al lanciatore
    «Avvia Penguin Inc.command» per sapere quando il server è pronto, e a un monitoraggio esterno.
    """
    G()  # ricarica il pacchetto di gioco se è cambiato, così un errore nei YAML si vede subito qui
    return JSONResponse({"ok": True, "app": "penguin-inc", "login": LOGIN_REQUIRED,
                         "rete": {"ok": motore.ok, "status": motore.status},
                         "contenuti": {"ok": not GIOCO.errore, "errore": GIOCO.errore}})


async def ui(req: Request):
    """GET /api/ui — senza login. Testi e impostazioni dell'interfaccia dal pacchetto di gioco.

    Risposta: {"ok", "testi": interfaccia.yaml così com'è, "impostazioni": {"dispositivi", "interfaccia",
    "punteggio"}, "errore_contenuti"}. Nessun dato della partita e nessuna soluzione: le soluzioni dei livelli
    restano sul server finché il giocatore non le sblocca.
    """
    g = G()
    imp = g.impostazioni
    return JSONResponse({"ok": True, "testi": g.interfaccia,
                         "impostazioni": {"dispositivi": imp["dispositivi"], "interfaccia": imp["interfaccia"],
                                          "punteggio": imp["punteggio"]},
                         "collaudo": COLLAUDO,
                         "errore_contenuti": GIOCO.errore})


def action(fn):
    """Wrapper degli endpoint che modificano la rete.

    Controlla login e body JSON, prende il lock, esegue fn(body) e rilegge la rete.
    Se la rete è occupata la richiesta si mette in coda (asyncio.Lock serve chi aspetta in ordine di
    arrivo) per al massimo CODA_S secondi; solo dopo risponde 409.
    Risposta: Stato + i campi del dict restituito da fn. ValueError → 400, ErroreRete → 502.
    """
    async def handler(req: Request):
        if not authed(req):
            return err("non autenticato", 401)
        if "application/json" not in req.headers.get("content-type", ""):
            return err("richiesta non valida")
        try:
            body = await req.json()
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return err("richiesta non valida")
        try:
            await asyncio.wait_for(motore.lock.acquire(), CODA_S)
        except asyncio.TimeoutError:
            return err(f"La rete è occupata da troppo tempo{': ' + JOB.title if JOB and not JOB.done else ''}. "
                       f"Riprova tra poco.", 409)
        try:
            if time.time() - motore.snap_at > 1.5:
                await motore.refresh()
            extra = await fn(body) or {}
            await motore.refresh()
        except ValueError as e:
            return err(str(e))
        except ErroreRete as e:
            return err(f"Rete: {e}"[:600], 502)
        finally:
            motore.lock.release()
        return JSONResponse({**public_state(), **extra})
    return handler


def job_action(fn):
    """Wrapper degli endpoint che avviano un lavoro lungo: login e body JSON, poi fn(body) chiama start_job()."""
    async def handler(req: Request):
        if not authed(req):
            return err("non autenticato", 401)
        if "application/json" not in req.headers.get("content-type", ""):
            return err("richiesta non valida")
        try:
            body = await req.json()
        except Exception:
            body = {}
        try:
            return fn(body if isinstance(body, dict) else {})
        except ValueError as e:
            return err(str(e))
    return handler


def game_action(fn):
    """Wrapper degli endpoint di gioco che non toccano la rete (modalità, indizi, resa).

    Stesse regole di action() per login e body; nessun lock. Risposta: Stato + il dict restituito da fn.
    """
    async def handler(req: Request):
        if not authed(req):
            return err("non autenticato", 401)
        if "application/json" not in req.headers.get("content-type", ""):
            return err("richiesta non valida")
        try:
            body = await req.json()
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return err("richiesta non valida")
        try:
            extra = fn(body) or {}
        except ValueError as e:
            return err(str(e))
        return JSONResponse({**public_state(), **extra})
    return handler


# ───────────────────────────── endpoint: dispositivi e cavi ─────────────────────────────
# Ogni a_… riceve il body già validato come dict. Contratto completo di ciascuno in docs/API.md.
async def a_add(b):
    """POST /api/device/add — Body: {"kind", "x"?, "y"?}. Crea il dispositivo con il primo nome libero.

    Risposta: Stato + {"selected": nome}.
    """
    kind = b.get("kind")
    dispositivi = G().impostazioni["dispositivi"]
    if kind not in dispositivi:
        raise ValueError("tipo di dispositivo non ammesso")
    x, y = max(20, min(int(b.get("x", 300)), 2000)), max(20, min(int(b.get("y", 300)), 1400))
    name = motore.topo.next_name(dispositivi[kind]["prefisso"])
    await motore.add_device(TIPO_DI.get(kind, kind), name, x, y)
    if kind == "cloud":  # un server presentato come servizio cloud (fittizio): stesso dispositivo, altro ruolo
        STATE["roles"][name] = "cloud"
    log(f"Aggiunto {name}", "build")
    return {"selected": name}


async def a_move(b):
    """POST /api/device/move — Body: {"name", "x", "y"}."""
    d = need(TUTTI, b.get("name", ""))
    x, y = max(20, min(int(b["x"]), 2000)), max(20, min(int(b["y"]), 1400))
    await motore.move(d["name"], x, y)


async def a_delete(b):
    """POST /api/device/delete — Body: {"name"}. Toglie il dispositivo e i suoi cavi."""
    d = need(TUTTI, b.get("name", ""))
    await motore.delete(d["name"])
    for k in ("credentials", "firewall", "roles"):
        STATE[k].pop(d["name"], None)
    log(f"Eliminato {d['name']}", "build")


async def a_rename(b):
    """POST /api/device/rename — Body: {"name", "new"}. Risposta: Stato + {"selected": nuovo nome}."""
    d = need(TUTTI, b.get("name", ""))
    new = str(b.get("new", "")).strip()
    if not NAME_RE.match(new):
        raise ValueError("nome non valido: solo lettere, numeri, - e _ (max 24)")
    if motore.topo.dev(new):
        raise ValueError(f"esiste già un dispositivo chiamato {new}")
    await motore.rename(d["name"], new)
    # tutto ciò che il gioco ricorda per nome segue il dispositivo (record DNS, esclusi DHCP, regole…)
    for k in ("credentials", "firewall", "roles", "dns", "dhcp_esclusi"):
        if d["name"] in STATE.setdefault(k, {}):
            STATE[k][new] = STATE[k].pop(d["name"])
    log(f"{d['name']} rinominato in {new}", "build")
    return {"selected": new}


async def a_link(b):
    """POST /api/link — Body: {"a", "b", "pa"?, "pb"?}. Collega due dispositivi.

    Senza pa/pb sceglie la prima porta libera adatta (Topo.free_port).
    """
    a, c = motore.topo.dev(b.get("a", "")), motore.topo.dev(b.get("b", ""))
    if not a or not c or a is c:
        raise ValueError("scegli due dispositivi diversi")
    if a["kind"] in ("pc", "server") and c["kind"] in ("pc", "server"):
        raise ValueError("Due PC/server non si collegano direttamente in questo gioco: passa da uno switch.")
    pa = b.get("pa") or motore.topo.free_port(a, c["kind"])
    pb = b.get("pb") or motore.topo.free_port(c, a["kind"])
    if not pa or not pb:
        raise ValueError(f"Nessuna porta libera su {a['name'] if not pa else c['name']}.")
    for d, p in ((a, pa), (c, pb)):
        port = need_port(d, p)
        if port["link"]:
            raise ValueError(f"La porta {p} di {d['name']} è già occupata")
    await motore.link(a["name"], pa, c["name"], pb)
    log(f"Cavo {a['name']} {short_port(pa)} ↔ {c['name']} {short_port(pb)}", "build")


async def a_unlink(b):
    """POST /api/unlink — Body: {"name", "port"}. Stacca il cavo da una porta (una qualsiasi delle due estremità)."""
    d = need(TUTTI, b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    if not p["link"]:
        raise ValueError("su quella porta non c'è un cavo")
    await motore.unlink(d["name"], p["n"])
    log(f"Scollegato {d['name']} {short_port(p['n'])}", "build")


# ───────────────────────────── endpoint: configurazione ─────────────────────────────
async def a_host_ip(b):
    """POST /api/host/ip — Body: {"name", "mode"?: "static"|"dhcp", "ip", "mask"?, "gateway"?, "dns"?}.

    In DHCP il PC chiede subito un indirizzo. Risposta: Stato; in DHCP + {"toast": esito}.
    """
    d = need(("pc", "server"), b.get("name", ""))
    if b.get("mode") == "dhcp":
        got = await motore.host_dhcp(d["name"])
        log(f"{d['name']}: DHCP" + (f" → {got}" if got else " (nessuna risposta)"), "config")
        return {"toast": f"{d['name']} ha ricevuto {got}" if got else
                f"{d['name']} non ha ricevuto un indirizzo: c'è un server DHCP in questa rete?"}
    args = {"device": d["name"], "mode": "static", "ip": ip_of(b.get("ip"), "IP"), "mask": mask_of(b.get("mask", "255.255.255.0"))}
    gw = str(b.get("gateway", "")).strip()
    args["gateway"] = ip_of(gw, "Gateway") if gw else "0.0.0.0"
    dns = str(b.get("dns", "")).strip()
    if dns:   # vuoto = il DNS resta quello che c'è
        args["dns"] = ip_of(dns, "DNS")
    await motore.host_ip(d["name"], args["ip"], args["mask"], args["gateway"], args.get("dns"))
    log(f"{d['name']}: IP {args['ip']} / {args['mask']}" + (f", gateway {args['gateway']}" if gw else "")
        + (f", DNS {args['dns']}" if dns else ""), "config")


async def a_iface(b):
    """POST /api/router/iface — Body: {"name", "port", "ip"?, "mask"?, "clear_ip"?, "on"?}.

    Console: interface · ip address (o no ip address) · no shutdown / shutdown.
    """
    d = need("router", b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    lines = [f"interface {p['n']}"]
    ip = str(b.get("ip", "")).strip()
    if ip:
        lines.append(f"ip address {ip_of(ip, 'IP')} {mask_of(b.get('mask', '255.255.255.0'))}")
    elif b.get("clear_ip"):
        lines.append("no ip address")
    on = need_bool(b, "on") if "on" in b else True
    lines.append("no shutdown" if on else "shutdown")
    await configura(d["name"], lines, f"{d['name']} {p['n']}")
    motore.links_dirty = True
    log(f"{d['name']} {short_port(p['n'])}: " + (f"IP {ip}, " if ip else "") + ("accesa" if on else "spenta"), "config")


async def a_port_state(b):
    """POST /api/port/state — Body: {"name", "port", "on": bool}. Router o switch. Console: interface · no shutdown / shutdown."""
    d = need(("router", "switch"), b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    on = need_bool(b, "on")
    await configura(d["name"], [f"interface {p['n']}", "no shutdown" if on else "shutdown"], f"{d['name']} {p['n']}")
    motore.links_dirty = True
    log(f"{d['name']} {short_port(p['n'])} {'accesa' if on else 'spenta'}", "config")


async def a_vlan(b):
    """POST /api/switch/vlan — Body: {"name", "port", "vlan": 1-1005}. Console: vlan · interface · switchport mode access · switchport access vlan."""
    d = need("switch", b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    try:
        v = int(b.get("vlan"))
        assert 1 <= v <= 1005
    except Exception:
        raise ValueError("VLAN non valida (1-1005)")
    await configura(d["name"], [f"vlan {v}", "exit", f"interface {p['n']}", "switchport mode access",
                          f"switchport access vlan {v}"], f"{d['name']} {p['n']}")
    motore.links_dirty = True
    log(f"{d['name']} {short_port(p['n'])} → VLAN {v}", "config")


async def a_dhcp_pool(b):
    """POST /api/router/dhcp — Body: {"name", "pool"?, "network", "mask"?, "gateway", "dns"?, "exclude_from"?,
    "exclude_to"?}.

    Console: ip dhcp excluded-address <gateway> [· ip dhcp excluded-address <da> <a>] · ip dhcp pool · network ·
    default-router · dns-server. Gli esclusi servono a non dare via gli indirizzi fissi dei server.
    """
    d = need("router", b.get("name", ""))
    name = str(b.get("pool", "")).strip() or "LAN"
    if not POOL_RE.match(name):
        raise ValueError("nome del pool non valido (lettere, numeri, - _)")
    net, mask = ip_of(b.get("network"), "Rete"), mask_of(b.get("mask", "255.255.255.0"))
    gw = ip_of(b.get("gateway"), "Gateway")
    if ipaddress.IPv4Address(gw) not in ipaddress.IPv4Network(f"{net}/{mask}", strict=False):
        raise ValueError(f"Il gateway {gw} non è dentro la rete {net}/{mask}.")
    esclusi = [[gw, gw]]
    da, a = str(b.get("exclude_from", "")).strip(), str(b.get("exclude_to", "")).strip()
    if da:
        esclusi.append(intervallo_escluso(da, a))
    lines = [f"ip dhcp excluded-address {x} {y}" for x, y in esclusi]
    lines += [f"ip dhcp pool {name}", f"network {net} {mask}", f"default-router {gw}"]
    if str(b.get("dns", "")).strip():
        lines.append(f"dns-server {ip_of(b['dns'], 'DNS')}")
    await configura(d["name"], lines, f"pool DHCP su {d['name']}")
    mie = STATE.setdefault("dhcp_esclusi", {}).setdefault(d["name"], [])
    for e in esclusi:
        if e not in mie:
            mie.append(e)
    save()
    dns = str(b.get("dns", "")).strip()
    log(f"{d['name']}: pool DHCP {name} {net}/{mask} gw {gw}" + (f", DNS {dns}" if dns else "")
        + (f", esclusi {esclusi[1][0]}–{esclusi[1][1]}" if da else ""), "config")
    # i PC già in DHCP prendono le novità del pool (gateway, DNS) solo rinnovando l'indirizzo
    clienti = [h["name"] for h in motore.topo.hosts() if h.get("dhcp")]
    if clienti:
        return {"toast": f"Pool salvato. I PC già in DHCP ({', '.join(clienti[:4])}) ricevono le novità solo con "
                         f"«Rinnova indirizzo»."}


def intervallo_escluso(da, a) -> list[str]:
    """[da, a] validati: due IP, il secondo non più basso del primo (a vuoto = uno solo)."""
    da = ip_of(da, "Primo indirizzo escluso")
    a = ip_of(a, "Ultimo indirizzo escluso") if str(a or "").strip() else da
    if ipaddress.IPv4Address(a) < ipaddress.IPv4Address(da):
        raise ValueError("Gli indirizzi esclusi vanno dal più basso al più alto.")
    return [da, a]


async def a_dhcp_remove(b):
    """POST /api/router/dhcp/remove — Body: {"name", "pool"}. Toglie un pool DHCP e gli indirizzi esclusi che
    stanno nella sua rete. Console: no ip dhcp pool · no ip dhcp excluded-address."""
    d = need("router", b.get("name", ""))
    nome = str(b.get("pool", "")).strip()
    pool = next((x for x in d.get("pools", []) if x.get("name") == nome), None)
    if not pool:
        raise ValueError(f"Su {d['name']} non c'è il pool «{nome}».")
    lines = [f"no ip dhcp pool {nome}"]
    mie = STATE.setdefault("dhcp_esclusi", {}).get(d["name"], [])
    try:
        rete = ipaddress.IPv4Network(f"{pool['net']}/{pool['mask']}", strict=False)
    except ValueError:
        rete = None
    via = [e for e in mie if rete and ipaddress.IPv4Address(e[0]) in rete]
    lines += [f"no ip dhcp excluded-address {x} {y}" for x, y in via]
    await configura(d["name"], lines, f"pool DHCP su {d['name']}")
    for e in via:
        mie.remove(e)
    save()
    log(f"{d['name']}: tolto il pool DHCP {nome}", "config")


async def a_dhcp_exclude(b):
    """POST /api/router/dhcp/exclude — Body: {"name", "from", "to"?, "remove"?}. Aggiunge o toglie un intervallo di
    indirizzi che il DHCP del router non deve dare via. Console: [no] ip dhcp excluded-address <da> <a>."""
    d = need("router", b.get("name", ""))
    e = intervallo_escluso(b.get("from"), b.get("to"))
    neg = "no " if b.get("remove") else ""
    await configura(d["name"], [f"{neg}ip dhcp excluded-address {e[0]} {e[1]}"], f"indirizzi esclusi su {d['name']}")
    mie = STATE.setdefault("dhcp_esclusi", {}).setdefault(d["name"], [])
    if neg and e in mie:
        mie.remove(e)
    elif not neg and e not in mie:
        mie.append(e)
    save()
    log(f"{d['name']}: {'tolti' if neg else 'esclusi'} dal DHCP {e[0]}–{e[1]}", "config")


async def a_route(b):
    """POST /api/router/route — Body: {"name", "network", "mask", "via", "remove"?}. Console: [no] ip route <network> <mask> <via>."""
    d = need("router", b.get("name", ""))
    net, mask, via = ip_of(b.get("network"), "Rete"), mask_of(b.get("mask")), ip_of(b.get("via"), "Next hop")
    neg = "no " if b.get("remove") else ""
    if not neg:
        controlla_rotta(d, net, mask, via)
    await configura(d["name"], [f"{neg}ip route {net} {mask} {via}"], f"rotta su {d['name']}")
    if not neg:
        # Una rotta accettata dalla console entra in tabella solo se il next hop è raggiungibile (porta accesa e
        # collegata): si controlla nella tabella e, se manca, si dice perché.
        motore.links_dirty = True
        t = await motore.refresh()
        r = t.dev(d["name"]) or {}
        if not any(x.get("proto") == "S" and x.get("net") == net and x.get("mask") == mask for x in r.get("routes", [])):
            raise ValueError(f"{d['name']} non ha messo in tabella la rotta per {net}/{mask} via {via}: controlla che "
                             f"la porta verso {via} sia accesa e collegata.")
    log(f"{d['name']}: {'tolta' if neg else 'aggiunta'} rotta {net}/{mask} via {via}", "config")


def controlla_rotta(d: dict, net: str, mask: str, via: str) -> None:
    """Gli errori più comuni con le rotte statiche, spiegati prima di mandarle alla console (che li
    rifiuterebbe senza spiegare): un indirizzo al posto della rete, il next hop che è il router stesso o che non è un vicino."""
    rete = ipaddress.IPv4Network(f"{net}/{mask}", strict=False)
    if str(rete.network_address) != net:
        raise ValueError(f"«{net}» è un indirizzo, non una rete: con la maschera {mask} la rete da scrivere è "
                         f"{rete.network_address}.")
    v = ipaddress.IPv4Address(via)
    porte = [(p, Topo.port_iface(p)) for p in d.get("ports", [])]
    if any(i and i.ip == v for _, i in porte):
        raise ValueError(f"{via} è un indirizzo di {d['name']} stesso: il next hop è l'indirizzo dell'ALTRO router, "
                         f"quello all'altro capo del cavo.")
    if not any(i and v in i.network for _, i in porte):
        raise ValueError(f"{via} non è in nessuna rete collegata a {d['name']}: il next hop dev'essere un router "
                         f"vicino, raggiungibile da una porta di {d['name']} (per esempio sul cavo tra i due router).")
    if any(i and i.network == rete for _, i in porte):
        raise ValueError(f"La rete {net}/{rete.prefixlen} è già collegata a una porta di {d['name']}: non serve una rotta.")


# ───────────────────────────── endpoint: console e prove ─────────────────────────────
async def a_cli(b):
    """POST /api/cli — Body: {"name", "commands": [righe] | testo}. Righe libere della console su router e switch.

    Max 15 righe ASCII stampabili da 160 caratteri. Un comando rifiutato dalla console non è un errore
    HTTP: l'esito è nel testo. Risposta: Stato + {"console": testo}.
    """
    d = need(("router", "switch"), b.get("name", ""))
    cmds = b.get("commands")
    if isinstance(cmds, str):
        cmds = cmds.splitlines()
    if not isinstance(cmds, list) or not 0 < len(cmds) <= 15 or not all(isinstance(c, str) and CLI_LINE_RE.match(c) for c in cmds):
        raise ValueError("comandi non validi (max 15 righe di testo semplice)")
    cmds = [c for c in cmds if c.strip()]
    good, out = await console(d["name"], cmds)
    motore.links_dirty = True
    log(f"{d['name']} console: {cmds[0]}" + (f" (+{len(cmds) - 1})" if len(cmds) > 1 else ""), "config")
    return {"console": out}


async def a_host_cmd(b):
    """POST /api/host/cmd — Body: {"name", "command"}. Prompt dei comandi di PC e server.

    Solo ping, ipconfig, tracert, arp, nslookup (HOST_CMD_RE). Risposta: Stato + {"console": testo}.
    """
    d = need(("pc", "server"), b.get("name", ""))
    cmd = str(b.get("command", "")).strip()
    if not HOST_CMD_RE.match(cmd):
        raise ValueError("Comandi ammessi: ping, ipconfig, tracert, arp, nslookup")
    out = await motore.host_cmd(d["name"], cmd)
    log(f"{d['name']}> {cmd}", "config")
    return {"console": out}


async def a_ping(b):
    """POST /api/ping — Body: {"from", "ip"}. Ping vero da PC, server o router (4 pacchetti, Motore.ping).

    Risposta: Stato + {"ping": {"ok", "out"}}.
    """
    d = need(("pc", "server", "router"), b.get("from", ""))
    ip = ip_of(b.get("ip", ""), "IP")
    good, out = await motore.ping(d["name"], ip)
    log(f"Ping {d['name']} → {ip}: {'risponde ✅' if good else 'nessuna risposta ❌'}", "ok" if good else "fail")
    return {"ping": {"ok": good, "out": out}}


# ───────────────────────────── endpoint: sicurezza ─────────────────────────────
# Mattoni per sfide di cybersicurezza: filtrare il traffico (ACL), provare cosa passa e perché viene
# bloccato (web, traccia), irrobustire i dispositivi (hardening, port-security, audit) e metterne
# alla prova la resistenza (spegnimento, HSRP). Contratto completo in docs/API.md, «Sicurezza».

ACL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,29}$|^\d{1,4}$")
SECRET_RE = re.compile(r"^[\x21-\x7E]{4,32}$")          # niente spazi: è una password della console
USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,19}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9-]{1,30}(\.[A-Za-z0-9-]{1,30}){1,3}$")
BANNER_RE = re.compile(r"^[\x20-\x22\x24-\x7E]{1,120}$")  # testo semplice senza «#», che la console usa come delimitatore
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]{1,60}(:\d{1,5})?(/[A-Za-z0-9._~/-]{0,80})?$")
ACL_PROTOCOLS = ("ip", "icmp", "tcp", "udp")
VIOLATIONS = ("shutdown", "restrict", "protect")

# Codici dell'audit (rete/console.py, audit()), spiegati al giocatore.
AUDIT_IT = {
    "NO_ENABLE_SECRET": "Manca la password di enable (enable secret): chiunque arrivi alla console diventa amministratore.",
    "ENABLE_SECRET_REVERSIBLE": "La password di enable è salvata in modo reversibile: si può ricavare dalla configurazione.",
    "ENABLE_SECRET_WEAK_ALGO": "La password di enable usa un algoritmo debole.",
    "ENABLE_PASSWORD_PRESENT": "C'è ancora una vecchia «enable password», salvata in chiaro o quasi.",
    "USER_CREDENTIAL_REVERSIBLE": "L'utente locale «{utente}» ha la password salvata in modo reversibile: chi legge la configurazione la ricava.",
    "USER_CREDENTIAL_WEAK_ALGO": "L'utente locale «{utente}» ha la password salvata con un algoritmo debole.",
    "NO_LOCAL_USERS": "Non ci sono utenti locali: non si può usare il login con utente e password (né SSH).",
    "NO_SERVICE_PASSWORD_ENCRYPTION": "«service password-encryption» è spento: le password restano leggibili nella configurazione.",
    "NO_BANNER_MOTD": "Manca il banner di avviso per chi si collega.",
    "CONFIG_REGISTER_BYPASS": "Il config-register è 0x2142: al prossimo riavvio la configurazione salvata viene ignorata.",
}


def acl_address(v, what: str) -> str:
    """Indirizzo di una regola ACL in forma leggibile → forma della console.

    "any" → any · "192.168.1.10" → host 192.168.1.10 · "192.168.1.0/24" → 192.168.1.0 0.0.0.255
    """
    v = str(v or "").strip().lower()
    if v in ("", "any"):
        return "any"
    if "/" in v:
        try:
            n = ipaddress.IPv4Network(v, strict=False)
        except ValueError:
            raise ValueError(f"{what} non valido: «{v}». Esempi: any, 192.168.1.10, 192.168.1.0/24")
        return f"{n.network_address} {n.hostmask}"
    return f"host {ip_of(v, what)}"


def acl_line(e: dict) -> str:
    """Una regola validata da acl_rules → riga della console («permit tcp any host 10.0.0.5 eq 80»)."""
    if "protocol" not in e:
        return f"{e['action']} {e['source']}"
    porta = f" eq {e['dest_port']}" if e.get("dest_port") else ""
    return f"{e['action']} {e['protocol']} {e['source']} {e['destination']}{porta}"


def acl_rules(rules, extended: bool) -> list[dict]:
    """Regole ACL dell'API → regole validate una per una (acl_line le scrive per la console)."""
    if not isinstance(rules, list) or not 0 < len(rules) <= 30:
        raise ValueError("servono da 1 a 30 regole")
    out = []
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict) or r.get("action") not in ("permit", "deny"):
            raise ValueError(f"regola {i}: «action» deve essere permit o deny")
        e = {"action": r["action"], "source": acl_address(r.get("source"), f"regola {i}: indirizzo di origine")}
        if extended:
            proto = str(r.get("protocol", "ip")).lower()
            if proto not in ACL_PROTOCOLS:
                raise ValueError(f"regola {i}: protocollo ammesso: {', '.join(ACL_PROTOCOLS)}")
            e["protocol"] = proto
            e["destination"] = acl_address(r.get("destination"), f"regola {i}: indirizzo di destinazione")
            if r.get("port") not in (None, ""):
                if proto not in ("tcp", "udp"):
                    raise ValueError(f"regola {i}: la porta si indica solo con tcp o udp")
                try:
                    port = int(r["port"])
                    assert 1 <= port <= 65535
                except Exception:
                    raise ValueError(f"regola {i}: porta non valida (1-65535)")
                e["dest_port_op"], e["dest_port"] = "eq", port
        elif any(r.get(k) not in (None, "", "any", "ip") for k in ("destination", "port", "protocol")):
            raise ValueError(f"regola {i}: una ACL standard guarda solo l'origine")
        out.append(e)
    return out


def acl_delete_lines(acl: dict) -> list[str]:
    """Righe della console che cancellano un'ACL esistente (nominata o numerata) letta dallo snapshot."""
    if acl["id"].isdigit():
        return [f"no access-list {acl['id']}"]
    return [f"no ip access-list {'extended' if acl.get('extended') else 'standard'} {acl['id']}"]


async def a_acl(b):
    """POST /api/router/acl — crea o SOSTITUISCE un'ACL e, se indicato, la applica a una porta.

    Body: {"name", "acl", "type": "standard"|"extended", "rules": [...], "port"?, "direction"?: "in"|"out"}.
    Regola: {"action": "permit"|"deny", "protocol"?, "source"?, "destination"?, "port"?}, indirizzi in
    forma leggibile (vedi acl_address). Se l'ACL esiste già viene cancellata prima: la console altrimenti
    accoderebbe le regole nuove a quelle vecchie.

    Porte: con "port" l'ACL viene staccata dalle altre porte a cui era applicata e messa solo lì (spostarla
    è un'unica richiesta); con "port": "" viene staccata da tutte; senza "port" restano quelle di prima.
    """
    d = need("router", b.get("name", ""))
    acl_id = str(b.get("acl", "")).strip()
    if not ACL_NAME_RE.match(acl_id):
        raise ValueError("nome dell'ACL non valido: una parola (lettere, numeri, - _) o un numero")
    kind = b.get("type")
    if kind not in ("standard", "extended"):
        raise ValueError("«type» deve essere standard o extended")
    if acl_id.isdigit():
        n = int(acl_id)
        if kind == "standard" and not (1 <= n <= 99 or 1300 <= n <= 1999):
            raise ValueError(f"Le ACL standard con un numero vanno da 1 a 99: {acl_id} non va bene (oppure usa un nome).")
        if kind == "extended" and not (100 <= n <= 199 or 2000 <= n <= 2699):
            raise ValueError(f"Le ACL estese con un numero vanno da 100 a 199: {acl_id} non va bene (oppure usa un nome).")
    entries = acl_rules(b.get("rules"), kind == "extended")
    binding = None
    if b.get("port"):
        p = need_port(d, b["port"])
        direction = b.get("direction", "in")
        if direction not in ("in", "out"):
            raise ValueError("«direction» deve essere in o out")
        binding = (p["n"], direction)
    lines = []
    if "port" in b:  # la porta indicata (o nessuna) sostituisce quelle di prima
        for p in d["ports"]:
            for direction in ("in", "out"):
                if p.get(f"acl_{direction}") == acl_id:
                    lines += [f"interface {p['n']}", f"no ip access-group {acl_id} {direction}", "exit"]
    old = next((a for a in d.get("acls", []) if a["id"] == acl_id), None)
    if old:
        lines += acl_delete_lines(old)
    if acl_id.isdigit():
        lines += [f"access-list {acl_id} {acl_line(e)}" for e in entries]
    else:
        lines += [f"ip access-list {kind} {acl_id}", *[acl_line(e) for e in entries], "exit"]
    if binding:
        lines += [f"interface {binding[0]}", f"ip access-group {acl_id} {binding[1]}", "exit"]
    await configura(d["name"], lines, f"ACL {acl_id}")
    where = f" su {short_port(binding[0])} {binding[1]}" if binding else ""
    log(f"{d['name']}: ACL {acl_id} ({len(entries)} regole){where}", "config")


async def a_acl_remove(b):
    """POST /api/router/acl/remove — Body: {"name", "acl"}. Stacca l'ACL da tutte le porte e la cancella.

    Console: interface · no ip access-group <acl> in|out (per ogni porta) · no ip access-list / no access-list.
    """
    d = need("router", b.get("name", ""))
    acl_id = str(b.get("acl", "")).strip()
    old = next((a for a in d.get("acls", []) if a["id"] == acl_id), None)
    if not old:
        raise ValueError(f"{d['name']} non ha un'ACL «{acl_id}»")
    lines = []
    for p in d["ports"]:
        for direction in ("in", "out"):
            if p.get(f"acl_{direction}") == acl_id:
                lines += [f"interface {p['n']}", f"no ip access-group {acl_id} {direction}", "exit"]
    await configura(d["name"], lines + acl_delete_lines(old), f"rimozione dell'ACL {acl_id}")
    log(f"{d['name']}: ACL {acl_id} rimossa", "config")


async def a_browse(b):
    """POST /api/host/browse — Body: {"name", "url"}. Apre un indirizzo web dal browser di un PC o server.

    Serve a provare un servizio, non solo la raggiungibilità: un'ACL può lasciar passare il ping e bloccare
    il web. Risposta: Stato + {"web": {"ok", "text"}}.
    """
    d = need(("pc", "server"), b.get("name", ""))
    url = str(b.get("url", "")).strip()
    if not URL_RE.match(url):
        raise ValueError("indirizzo non valido: es. http://192.168.0.3 o http://www.esempio.it/pagina")
    ok, text = await do_browse(d["name"], url)
    log(f"{d['name']} apre {url}: {'pagina ricevuta ✅' if ok else 'nessuna risposta ❌'}", "ok" if ok else "fail")
    return {"web": {"ok": ok, "text": text}}


async def do_browse(host: str, url: str) -> tuple[bool, str]:
    """Apre url dal browser di host: (pagina ricevuta, testo della pagina o spiegazione). Usata anche dai livelli."""
    return await motore.browse(host, url)


async def a_trace(b):
    """POST /api/trace — Body: {"from", "to"} (nomi di dispositivi). Segue un ping passo per passo.

    Dice dove e perché un pacchetto viene scartato: per le ACL riporta la riga che l'ha bloccato
    (rete/percorso.py); nella rete vera un ping conferma il verdetto. Risposta: Stato + {"trace": {...}}.
    """
    a = need(("pc", "server", "router"), b.get("from", ""))
    c = need(("pc", "server", "router"), b.get("to", ""))
    if a is c:
        raise ValueError("scegli due dispositivi diversi")
    t = await do_trace(a["name"], c["name"])
    log(f"Traccia {a['name']} → {c['name']}: " + ("arriva ✅" if t["delivered"] else
        f"bloccato su {t['blocked_by']['device']} ❌" if t["blocked_by"] else "non arriva ❌"),
        "ok" if t["delivered"] else "fail")
    return {"trace": t}


async def do_trace(src: str, dst: str) -> dict:
    """Traccia di un ping da src a dst (nomi di dispositivi). Usata anche dai livelli."""
    return await motore.trace(src, dst)


async def a_port_security(b):
    """POST /api/switch/port-security — Body: {"name", "port", "max_mac"?, "violation"?, "sticky"?}.

    Limita quanti dispositivi possono usare una porta dello switch.
    Console: switchport mode access · switchport port-security · maximum · violation · mac-address sticky.
    """
    d = need("switch", b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    try:
        mx = int(b.get("max_mac", 1))
        assert 1 <= mx <= 64
    except Exception:
        raise ValueError("«max_mac» deve essere un numero da 1 a 64")
    violation = b.get("violation", "shutdown")
    if violation not in VIOLATIONS:
        raise ValueError("«violation» deve essere shutdown, restrict o protect")
    sticky = need_bool(b, "sticky") if "sticky" in b else True
    await configura(d["name"], [f"interface {p['n']}", "switchport mode access", "switchport port-security",
                          f"switchport port-security maximum {mx}", f"switchport port-security violation {violation}",
                          f"{'' if sticky else 'no '}switchport port-security mac-address sticky"], "port-security")
    log(f"{d['name']} {short_port(p['n'])}: port-security (max {mx}, {violation})", "config")


async def a_harden(b):
    """POST /api/device/harden — irrobustisce un router o uno switch.

    Body (tutto facoltativo, almeno un campo): {"name", "enable_secret", "users": [{"username", "secret",
    "privilege"?}], "ssh": {"domain", "modulus"?}, "banner", "password_encryption": bool}.
    Diventa righe di console: enable secret, username … secret, line con 0 / line vty 0 4 con login local,
    banner motd, service password-encryption; con «ssh» dominio, chiavi RSA, SSH v2 e vty solo SSH.

    Con degli utenti la console stessa chiede utente e password: il server salva le credenziali
    (STATE["credentials"]) e da lì in poi fa il login da solo. Le password restano dentro la rete del gioco,
    non finiscono nel diario e non escono mai dall'API.
    """
    d = need(("router", "switch"), b.get("name", ""))
    args: dict = {"device": d["name"]}
    if b.get("enable_secret"):
        if not SECRET_RE.match(str(b["enable_secret"])):
            raise ValueError("password di enable non valida: da 4 a 32 caratteri, senza spazi")
        args["enable_secret"] = b["enable_secret"]
    if b.get("users"):
        if not isinstance(b["users"], list) or len(b["users"]) > 5:
            raise ValueError("«users» deve essere una lista di al massimo 5 utenti")
        users = []
        for u in b["users"]:
            if not isinstance(u, dict) or not USER_RE.match(str(u.get("username", ""))) \
                    or not SECRET_RE.match(str(u.get("secret", ""))):
                raise ValueError("utente non valido: username (lettere, numeri, - _) e secret di 4-32 caratteri")
            priv = int(u.get("privilege", 1)) if str(u.get("privilege", 1)).isdigit() else -1
            if not 0 <= priv <= 15:
                raise ValueError("«privilege» deve essere da 0 a 15")
            users.append({"username": u["username"], "secret": u["secret"], "privilege": priv})
        args["users"] = users
    if b.get("ssh"):
        ssh = b["ssh"] if isinstance(b["ssh"], dict) else {}
        if not DOMAIN_RE.match(str(ssh.get("domain", ""))):
            raise ValueError("per SSH serve un dominio, es. lab.local")
        if not args.get("users"):
            raise ValueError("SSH richiede almeno un utente locale in «users»")
        modulus = ssh.get("modulus", 1024)
        if modulus not in (512, 768, 1024, 2048):
            raise ValueError("«modulus» ammessi: 512, 768, 1024, 2048")
        # Le chiavi RSA prendono il nome da hostname.dominio: l'hostname diventa il nome del dispositivo.
        ssh_lines = [f"hostname {d['name']}", f"ip domain-name {ssh['domain']}",
                     f"crypto key generate rsa general-keys modulus {modulus}",
                     "ip ssh version 2", "line vty 0 4", "transport input ssh", "login local", "exit"]
    if b.get("banner"):
        if not BANNER_RE.match(str(b["banner"])):
            raise ValueError("banner non valido: testo semplice fino a 120 caratteri, senza «#»")
        args["banner_motd"] = b["banner"]
    if "password_encryption" in b:
        args["service_password_encryption"] = need_bool(b, "password_encryption")
    if len(args) == 1 and not b.get("ssh"):
        raise ValueError("indica almeno una cosa da configurare")
    if len(args) > 1:
        righe = []
        if args.get("enable_secret"):
            righe.append(f"enable secret {args['enable_secret']}")
        for u in args.get("users", []):
            righe.append(f"username {u['username']} privilege {u['privilege']} secret {u['secret']}")
        if args.get("banner_motd"):
            righe.append(f"banner motd #{args['banner_motd']}#")
        if "service_password_encryption" in args:
            righe.append(("" if args["service_password_encryption"] else "no ") + "service password-encryption")
        await configura(d["name"], righe, f"hardening di {d['name']}")
    # Credenziali per i prossimi comandi da console: l'utente più privilegiato e la password di enable.
    creds = dict(STATE["credentials"].get(d["name"], {}))
    if args.get("users"):
        top = max(args["users"], key=lambda u: u["privilege"])
        creds.update(username=top["username"], secret=top["secret"])
    if args.get("enable_secret"):
        creds["enable_secret"] = args["enable_secret"]
    if creds:
        STATE["credentials"][d["name"]] = creds
        save()
    if args.get("users"):   # da qui in poi la console chiede utente e password (il gioco entra con creds)
        await configura(d["name"], ["line con 0", "login local", "exit", "line vty 0 4", "login local", "exit"],
                  f"login su {d['name']}")
        motore.console.chiudi(d["name"])
    if b.get("ssh"):
        await configura(d["name"], ssh_lines, f"SSH su {d['name']}")
    done = [k for k in ("enable_secret", "users", "banner_motd", "service_password_encryption") if k in args]
    done += ["ssh"] if b.get("ssh") else []
    log(f"{d['name']}: hardening ({', '.join(done)})", "config")


async def a_login(b):
    """POST /api/device/login — Body: {"name", "username"?, "secret"?, "enable_secret"?}.

    Comunica al server le credenziali di un dispositivo protetto (per esempio configurato a mano o già
    protetto nella rete di partenza di una sfida). Le prova subito facendo il login sulla console: se sono
    sbagliate risponde 400 e non le salva. Risposta: Stato + {"toast": esito}.
    """
    d = need(("router", "switch"), b.get("name", ""))
    creds = {}
    for k in ("username", "secret", "enable_secret"):
        v = str(b.get(k, "") or "")
        if v:
            if not (USER_RE if k == "username" else SECRET_RE).match(v):
                raise ValueError(f"«{k}» non valido")
            creds[k] = v
    if not creds:
        raise ValueError("indica almeno utente e password o la password di enable")
    motore.console.chiudi(d["name"])
    await motore.unlock(d["name"], creds)
    STATE["credentials"][d["name"]] = creds
    save()
    log(f"{d['name']}: login riuscito", "ok")
    return {"toast": f"Login su {d['name']} riuscito: d'ora in poi il gioco usa queste credenziali."}


async def a_audit(b):
    """POST /api/audit — Body: {"name"?}. Controlla la sicurezza della configurazione di router e switch.

    Senza «name» controlla tutti (le password non escono mai dal dispositivo).
    Risposta: Stato + {"audit": {"secure", "counts", "findings": [...]}}.
    """
    device = need(("router", "switch"), b["name"])["name"] if b.get("name") else ""
    audit = await do_audit(device)
    log(f"Audit di sicurezza: {len(audit['findings'])} problemi trovati", "ok" if not audit["findings"] else "info")
    return {"audit": audit}


async def do_audit(device: str = "") -> dict:
    """Audit di sicurezza di un dispositivo (vuoto = tutti), con i messaggi in italiano. Usata anche dai livelli."""
    findings = []
    for f in motore.audit(device):
        msg = AUDIT_IT.get(f["code"], f["code"]).replace("{utente}", f.get("user", "?"))
        findings.append({"device": f["device"], "code": f["code"], "severity": f["severity"], "message": msg})
    counts = {k: sum(1 for f in findings if f["severity"] == k) for k in ("high", "medium", "low")}
    return {"secure": not findings, "counts": counts, "findings": findings}


USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
USER_LINE_RE = re.compile(r"^username (\S+)(?: privilege (\d+))?.*? (password|secret) (\d+) ", re.M)


async def leggi_utenti(device: str) -> list[dict]:
    """Utenti locali di un router o switch: niente password, solo come sono salvate.

    storage: "chiaro" (password 0), "reversibile" (password 7: nascosta ma decodificabile), "cifrata" (secret).
    """
    d = motore.rete.dev(device)
    out = []
    for nome, u in (d.utenti if d else {}).items():
        storage = "cifrata" if u["tipo"] == "secret" else ("reversibile" if d.cifra_password else "chiaro")
        out.append({"username": nome, "privilege": int(u.get("privilege", 1)), "storage": storage})
    return out


async def a_users(b):
    """POST /api/device/users — Body: {"name"}. Gli utenti locali di un router o switch.

    Risposta: Stato + {"users": [{"username", "privilege", "storage": "chiaro"|"reversibile"|"cifrata"}]}.
    Le password non escono mai: si dice solo come sono salvate.
    """
    d = need(("router", "switch"), b.get("name", ""))
    return {"users": await leggi_utenti(d["name"])}


async def a_user_remove(b):
    """POST /api/device/users/remove — Body: {"name", "username"}. Cancella un utente locale (no username).

    Rifiuta l'utente con cui il server entra nella console (STATE["credentials"]): toglierlo la chiuderebbe fuori.
    Risposta: Stato + {"users": elenco aggiornato}.
    """
    d = need(("router", "switch"), b.get("name", ""))
    user = str(b.get("username", "")).strip()
    if not USER_RE.match(user):
        raise ValueError("nome utente non valido")
    if (STATE["credentials"].get(d["name"]) or {}).get("username") == user:
        raise ValueError(f"«{user}» è l'utente con cui il gioco entra nella console di {d['name']}: "
                         f"toglierlo la chiuderebbe fuori.")
    if user not in {u["username"] for u in await leggi_utenti(d["name"])}:
        raise ValueError(f"{d['name']} non ha un utente «{user}»")
    await configura(d["name"], [f"no username {user}"], f"rimozione dell'utente {user}")
    log(f"{d['name']}: utente {user} cancellato", "config")
    return {"users": await leggi_utenti(d["name"])}


async def a_power(b):
    """POST /api/device/power — Body: {"name", "on": bool}. Accende o spegne un dispositivo.

    Serve a simulare un guasto o un attacco e vedere se la rete regge.
    """
    d = need(TUTTI, b.get("name", ""))
    on = need_bool(b, "on")
    await motore.power(d["name"], on)
    log(f"{d['name']} {'acceso' if on else 'spento'}", "config")


async def a_hsrp(b):
    """POST /api/router/hsrp — Body: {"name", "port", "group", "virtual_ip", "priority"?, "preempt"?}.

    Due router con lo stesso gruppo e lo stesso virtual_ip fanno da gateway di scorta l'uno all'altro;
    il gateway dei PC diventa virtual_ip. Console: standby version 2 · standby G ip · priority · preempt.
    """
    d = need("router", b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    try:
        group, prio = int(b.get("group")), int(b.get("priority", 100))
        assert 0 <= group <= 4095 and 0 <= prio <= 255
    except Exception:
        raise ValueError("«group» da 0 a 4095, «priority» da 0 a 255")
    vip = ip_of(b.get("virtual_ip"), "IP virtuale")
    preempt = need_bool(b, "preempt") if "preempt" in b else True
    await configura(d["name"], [f"interface {p['n']}", "standby version 2", f"standby {group} ip {vip}",
                          f"standby {group} priority {prio}", f"{'' if preempt else 'no '}standby {group} preempt"],
              f"HSRP su {d['name']}")
    log(f"{d['name']} {short_port(p['n'])}: HSRP gruppo {group}, IP virtuale {vip}, priorità {prio}", "config")


# ───────────────────────────── endpoint: firewall, servizi, scansione, sniffer, limiti ─────────────────────────────
ZONE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,19}$")
LIVELLI_ZONA = {"inside": 100, "dmz": 50, "outside": 0}   # livelli proposti per i nomi di zona classici


async def configura_firewall(device: str, lines: list[tuple[str, str]], what: str) -> None:
    """Righe di configurazione sulla console del firewall: [(comando, modo)] (il modo non serve più e resta per
    compatibilità con le azioni del pacchetto di gioco). ValueError alla prima riga rifiutata."""
    good, out = await console(device, ["enable", "configure terminal", *[c for c, _ in lines], "end"])
    if not good:
        bad = [l for l in out.splitlines() if l.lstrip().startswith(("%", "ERROR"))]
        raise ValueError(f"{what}: il firewall ha rifiutato il comando. " + " ".join(bad[:3])[:300])


def zone_firewall(d: dict) -> dict[str, FW.Zona]:
    """Le zone di un firewall dalla fotografia: nome → Zona (con la rete della sua porta e quelle raggiunte da
    lì con le rotte statiche del firewall: un firewall tra due router protegge anche le LAN dietro ciascuno)."""
    out = {}
    fw = d.get("fw", {})
    for z in fw.get("zones", []):
        p = Topo.port(d, z["port"])
        i = Topo.port_iface(p) if p else None
        dietro = []
        for r in fw.get("routes", []):
            if r["zone"] == z["nameif"]:
                try:
                    dietro.append(ipaddress.IPv4Network(f"{r['net']}/{r['mask']}", strict=False))
                except ValueError:
                    pass
        out[z["nameif"]] = FW.Zona(z["nameif"], z["level"] if z["level"] is not None else 0, i.network if i else None,
                                   str(i.ip) if i else "", tuple(dietro))
    return out


async def a_fw_route(b):
    """POST /api/firewall/route — Body: {"name", "network", "mask", "via", "remove"?}. Rotta statica del firewall.

    Console: [no] route <zona> <rete> <maschera> <via>. La zona è quella della porta nella cui rete sta «via»
    (il router vicino). Le regole vengono ricompilate: la zona ora comprende anche quella rete.
    """
    d = need("firewall", b.get("name", ""))
    net, mask, via = ip_of(b.get("network"), "Rete"), mask_of(b.get("mask")), ip_of(b.get("via"), "Next hop")
    rete = ipaddress.IPv4Network(f"{net}/{mask}", strict=False)
    if str(rete.network_address) != net:
        raise ValueError(f"«{net}» è un indirizzo, non una rete: con la maschera {mask} la rete da scrivere è "
                         f"{rete.network_address}.")
    if any(Topo.port_iface(p) and Topo.port_iface(p).ip == ipaddress.IPv4Address(via) for p in d.get("ports", [])):
        raise ValueError(f"{via} è un indirizzo del firewall stesso: il next hop è il router all'altro capo del cavo.")
    zone = zone_firewall(d)
    zona = next((z.nome for z in zone.values() if z.rete is not None and ipaddress.IPv4Address(via) in z.rete), None)
    if not zona:
        raise ValueError(f"{via} non è nella rete di nessuna zona di {d['name']}: il next hop dev'essere il router "
                         f"collegato a una porta del firewall.")
    neg = "no " if b.get("remove") else ""
    await configura_firewall(d["name"], [(f"{neg}route {zona} {net} {mask} {via}", "global")], f"rotta su {d['name']}")
    log(f"{d['name']}: {'tolta' if neg else 'aggiunta'} rotta {net}/{mask} via {via} ({zona})", "config")
    d = (await motore.refresh()).dev(d["name"])
    try:
        await applica_regole(d)
    except ValueError as e:
        return {"toast": f"Rotta aggiornata, ma le regole non si applicano più: {e}"}


async def applica_regole(d: dict) -> int:
    """Compila le regole salvate del firewall d (STATE["firewall"]) e le scrive sul firewall. Restituisce quante righe."""
    zone = zone_firewall(d)
    regole = [FW.valida_regola(r, zone, i) for i, r in enumerate(STATE["firewall"].get(d["name"], []), 1)]
    acl = FW.compila(zone, regole)
    fw = d.get("fw", {})
    lines: list[tuple[str, str]] = []
    for zona, nome in fw.get("groups", {}).items():   # via le assegnazioni e le ACL generate dal gioco
        if nome.startswith(FW.ACL_PREFISSO):
            lines.append((f"no access-group {nome} in interface {zona}", "global"))
    for nome, righe in fw.get("acls", {}).items():
        if nome.startswith(FW.ACL_PREFISSO):
            lines += [(f"no access-list {nome} extended {r}", "global") for r in righe]
    totale = 0
    for zona, righe in acl.items():
        if not righe:
            continue
        nome = f"{FW.ACL_PREFISSO}{zona}"
        lines += [(f"access-list {nome} extended {r}", "global") for r in righe]
        lines.append((f"access-group {nome} in interface {zona}", "global"))
        totale += len(righe)
    if lines:
        await configura_firewall(d["name"], lines, f"regole di {d['name']}")
    return totale


async def a_fw_zone(b):
    """POST /api/firewall/zone — una porta del firewall diventa una zona (o smette di esserlo).

    Body: {"name", "port", "zone", "level"?, "ip", "mask"?} oppure {"name", "port", "clear": true}.
    zone: nome di una parola (inside, dmz, outside…); level 0-100 (default: inside 100, dmz 50, outside 0).
    Dopo il cambio le regole del giocatore vengono ricompilate (le reti delle zone possono essere cambiate).
    """
    d = need("firewall", b.get("name", ""))
    p = need_port(d, b.get("port", ""))
    if b.get("clear"):
        await configura_firewall(d["name"], [(f"interface {p['n']}", "global"), ("no nameif", ""), ("no security-level", ""),
                              ("no ip address", ""), ("shutdown", ""), ("exit", "")], f"{d['name']} {p['n']}")
        log(f"{d['name']} {short_port(p['n'])}: zona tolta", "config")
    else:
        zona = str(b.get("zone", "")).strip().lower()
        if not ZONE_RE.match(zona):
            raise ValueError("nome della zona: una parola minuscola, es. inside, dmz, outside")
        try:
            level = int(b.get("level", LIVELLI_ZONA.get(zona, 50)))
            assert 0 <= level <= 100
        except Exception:
            raise ValueError("livello di sicurezza da 0 a 100")
        ip, mask = ip_of(b.get("ip"), "IP"), mask_of(b.get("mask", "255.255.255.0"))
        altra = next((z for z in d.get("fw", {}).get("zones", []) if z["nameif"] == zona and z["port"] != p["n"]), None)
        if altra:
            raise ValueError(f"la zona «{zona}» è già sulla porta {short_port(altra['port'])}")
        await configura_firewall(d["name"], [(f"interface {p['n']}", "global"), (f"nameif {zona}", ""), (f"security-level {level}", ""),
                              (f"ip address {ip} {mask}", ""), ("no shutdown", ""), ("exit", "")], f"{d['name']} {p['n']}")
        log(f"{d['name']} {short_port(p['n'])}: zona {zona} (livello {level}), {ip}", "config")
    d = (await motore.refresh()).dev(d["name"])
    try:
        await applica_regole(d)
    except ValueError as e:  # una regola che nomina una zona sparita: la zona è cambiata comunque, lo diciamo
        return {"toast": f"Zona aggiornata, ma le regole non si applicano più: {e}"}


async def a_fw_rules(b):
    """POST /api/firewall/rules — Body: {"name", "rules": [...]}. SOSTITUISCE le regole del firewall.

    Regola: {"from", "to", "action": "permit"|"deny", "protocol"?: ip|tcp|udp|icmp, "port"?, "source"?, "dest"?}.
    from/to sono zone; source/dest vuoti = tutta la zona, oppure any, un IP, una rete a.b.c.d/nn.
    Le regole sono eccezioni al comportamento di base (le zone alte aprono verso le basse); firewall.py le
    compila in access-list del firewall con le risposte. Risposta: Stato + {"acl": {zona: righe}} (cosa è finito sul firewall).
    """
    d = need("firewall", b.get("name", ""))
    rules = b.get("rules")
    if not isinstance(rules, list) or len(rules) > 30:
        raise ValueError("servono da 0 a 30 regole")
    zone = zone_firewall(d)
    regole = [FW.valida_regola(r, zone, i) for i, r in enumerate(rules, 1)]
    vecchie = STATE["firewall"].get(d["name"])
    STATE["firewall"][d["name"]] = regole
    try:
        n = await applica_regole(d)
    except Exception:
        if vecchie is None:
            STATE["firewall"].pop(d["name"], None)
        else:
            STATE["firewall"][d["name"]] = vecchie
        raise
    save()
    log(f"{d['name']}: {len(regole)} regole del firewall ({n} righe ACL)", "config")
    return {"acl": FW.compila(zone, regole)}


SERVIZI = ("http", "https", "ftp", "email")
DNS_NOME_RE = re.compile(r"^(?=.{1,80}$)[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?)+$")


async def a_server_dns(b):
    """POST /api/server/dns — Body: {"name", "on"?: bool, "add"?: [{"name", "ip"}], "remove"?: [nomi]}.

    Il servizio DNS di un server: lo accende o spegne e aggiunge o toglie record A (nome → IP). I record sono
    anche in STATE["dns"], che il client mostra. Risposta: Stato + {"dns": {"on", "records": {nome: ip}}}.
    """
    d = need("server", b.get("name", ""))
    add, rem = b.get("add") or [], b.get("remove") or []
    if not isinstance(add, list) or not isinstance(rem, list) or len(add) + len(rem) > 20:
        raise ValueError("record non validi (al massimo 20 per volta)")
    nuovi = {}
    for r in add:
        nome = str((r or {}).get("name", "")).strip().lower().rstrip(".")
        if not DNS_NOME_RE.match(nome):
            raise ValueError(f"«{nome}» non è un nome valido: parole separate da punti, es. gestione.azienda.test")
        nuovi[nome] = ip_of((r or {}).get("ip"), f"IP di {nome}")
    via = [str(x).strip().lower().rstrip(".") for x in rem]
    acceso = bool(b["on"]) if "on" in b else None
    dns = await motore.server_dns(d["name"], acceso, nuovi, via)
    STATE["dns"][d["name"]] = {"on": dns["on"], "records": dict(dns["records"])}
    save()
    parti = ([f"DNS {'acceso' if dns['on'] else 'spento'}"] if "on" in b else []) + \
            [f"{n} → {ip}" for n, ip in nuovi.items()] + [f"tolto {n}" for n in via]
    log(f"{d['name']}: " + ", ".join(parti), "config")
    return {"dns": STATE["dns"][d["name"]]}


async def leggi_servizi(device: str) -> dict:
    """I servizi di un server: {"http", "https", "ftp", "smtp", "pop3": bool, "ftp_users": [...], "domain"}."""
    return motore.servizi(device)


async def a_services(b):
    """POST /api/server/services — Body: {"name"}. I servizi esposti da un server (porte aperte).

    Risposta: Stato + {"services": {"http", "https", "ftp", "smtp", "pop3": bool, "ftp_users", "domain"}}.
    Porte: http 80, https 443, ftp 21, smtp 25, pop3 110. SSH non è un servizio dei server: è dei router
    (/api/device/harden con «ssh»).
    """
    d = need("server", b.get("name", ""))
    return {"services": await leggi_servizi(d["name"])}


async def a_service(b):
    """POST /api/server/service — Body: {"name", "service": http|https|ftp|email, "on": bool, "domain"?}.

    Accende o spegne un servizio di un server (email = SMTP e POP3 insieme). Risposta: Stato + {"services"}.
    """
    d = need("server", b.get("name", ""))
    svc, on = b.get("service"), need_bool(b, "on")
    if svc not in SERVIZI:
        raise ValueError(f"servizio tra {', '.join(SERVIZI)}")
    dominio = ""
    if svc == "email" and b.get("domain"):
        dominio = str(b["domain"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,60}", dominio):
            raise ValueError("dominio non valido, es. pinguini.local")
    await motore.servizio(d["name"], svc, on, dominio)
    log(f"{d['name']}: servizio {svc} {'acceso' if on else 'spento'}", "config")
    return {"services": await leggi_servizi(d["name"])}


PORTE_NOTE = {21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 443: "HTTPS",
              3389: "RDP", 4444: "porta tipica delle reverse shell", 8080: "HTTP alternativo"}
PORTE_SCANSIONE = [21, 22, 23, 25, 80, 110, 443]


async def nslookup(src: str, nome: str) -> tuple[str, str]:
    """nslookup dal PC src: (IP a cui porta il nome, IP del DNS che ha risposto); "" se non si risolve."""
    return await motore.nslookup(src, nome)


async def sonda_porta(src: str, ip: str, porta: int) -> str:
    """Prova ad aprire una connessione TCP da src verso ip:porta: "aperta", "chiusa" o "filtrata".

    aperta = qualcuno ascolta; chiusa = l'host risponde ma lì non c'è nessuno; filtrata = nessuna risposta
    (un firewall o un'ACL scarta, o l'host non c'è).
    """
    return await motore.porta(src, ip, porta)


def j_scan(b):
    """POST /api/scan — Body: {"from", "to", "ports"?}. Lavoro lungo: scansione delle porte TCP, come nmap.

    from: PC o server da cui partire; to: IP o nome di un dispositivo; ports: lista (default PORTE_SCANSIONE,
    al massimo 10). Risultato del lavoro: {"ok", "message", "scan": {"target", "ip", "ports": [{"port",
    "service", "state": "aperta"|"chiusa"|"filtrata"}]}}. Una porta filtrata costa ~3 s.
    """
    src = need(("pc", "server"), b.get("from", ""))["name"]
    target = str(b.get("to", "")).strip()
    ports = b.get("ports") or PORTE_SCANSIONE
    try:
        ports = [int(x) for x in ports][:10]
        assert all(1 <= x <= 65535 for x in ports)
    except Exception:
        raise ValueError("porte non valide (1-65535, al massimo 10)")

    async def run(job: Job) -> dict:
        t = await motore.refresh()
        ip = target if re.fullmatch(r"[\d.]+", target) else R.resolve(t, "@" + target)
        if not ip:
            raise ValueError(f"{target}: né un IP né un dispositivo con un indirizzo")
        ip = ip_of(ip, "destinazione")
        await motore.settle()
        risultati = []
        for porta in ports:
            job.step(f"Porta {porta} ({PORTE_NOTE.get(porta, 'TCP')}) di {ip}")
            risultati.append({"port": porta, "service": PORTE_NOTE.get(porta, ""), "state": await sonda_porta(src, ip, porta)})
        aperte = [str(r["port"]) for r in risultati if r["state"] == "aperta"]
        log(f"Scansione da {src} verso {ip}: aperte {', '.join(aperte) or 'nessuna'}", "info")
        return {"ok": True, "message": f"{ip}: porte aperte {', '.join(aperte) or 'nessuna'}",
                "scan": {"target": target, "ip": ip, "from": src, "ports": risultati}}
    return start_job(f"Scansione di {target}", run)


TRAFFICO_RE = R.TRAFFICO_RE   # traffico di prova per lo sniffer: «ping IP», «telnet IP porta», «web URL»


def j_sniff(b):
    """POST /api/sniff — Body: {"name", "traffic"?: [{"from", "cmd"}]}. Lavoro lungo: cattura il traffico che passa
    da un dispositivo, come tcpdump o Wireshark.

    Il traffico è quello del livello in corso (campo «traffico» del livello: il «rumore di fondo» della rete,
    anche quello sospetto) più quello eventualmente chiesto nel body («ping IP», «telnet IP porta», «web URL»).
    Nella rete vera è tcpdump sul dispositivo mentre gli host generano il traffico (circa 5 s).
    Risultato: {"ok", "message", "capture": {"device", "frames": [...]}} (formato in docs/API.md).
    """
    d = need(("router", "switch", "firewall", "pc", "server"), b.get("name", ""))
    extra = []
    for v in (b.get("traffic") or [])[:5]:
        src, cmd = str((v or {}).get("from", "")), str((v or {}).get("cmd", "")).strip()
        need(("pc", "server"), src)
        if not TRAFFICO_RE.match(cmd):
            raise ValueError("traffico ammesso: «ping IP», «telnet IP porta», «web http://IP»")
        extra.append((src, cmd))

    async def run(job: Job) -> dict:
        t = await motore.refresh()
        lv = G().livelli_per_numero.get(STATE["rescue"]["level"]) if STATE["rescue"]["status"] == "playing" else None
        if lv is None and STATE.get("mode") == "academy":
            lv = missione_corrente(G())   # una challenge con il suo traffico di fondo («traffico» della missione)
        voci = []
        for src, cmd in (getattr(lv, "traffic", None) or []) + extra:
            try:
                voci.append((src, risolvi_at(t, cmd)))
            except ValueError:
                pass
        if not voci:  # rete senza traffico di fondo: ogni host saluta il suo gateway, così si vede qualcosa
            for h in t.hosts()[:6]:
                if h.get("gw") not in (None, "", "0.0.0.0"):
                    voci.append((h["name"], f"ping {h['gw']}"))
        job.step(f"In ascolto su {d['name']}…")
        frames = await motore.sniff(d["name"], voci)
        log(f"Sniffer su {d['name']}: {len(frames)} pacchetti catturati", "info")
        return {"ok": True, "message": f"{len(frames)} pacchetti catturati su {d['name']}",
                "capture": {"device": d["name"], "frames": frames}}
    return start_job(f"Sniffer su {d['name']}", run)


async def leggi_limiti(device: str) -> dict:
    """Limiti di velocità di un router: {porta: kbps} (policy con «shape average» applicate in uscita)."""
    d = motore.rete.dev(device)
    if not d:
        return {}
    return {p.nome: d.policy.get(p.policy_out) for p in d.porte.values() if p.policy_out}


async def a_ratelimit(b):
    """POST /api/router/ratelimit — Body: {"name", "port"?, "kbps"?}. Limite di velocità in uscita su una porta.

    Con port e kbps > 0: traffic shaping (policy-map con «shape average», service-policy output). Con kbps 0:
    toglie il limite. Solo name: legge i limiti. Risposta: Stato + {"limits": {porta: kbps}}.
    Nella rete vera il limite è applicato davvero (tc tbf sulla porta).
    """
    d = need("router", b.get("name", ""))
    if b.get("port"):
        p = need_port(d, b["port"])
        try:
            kbps = int(b.get("kbps", 0))
            assert 0 <= kbps <= 1000000
        except Exception:
            raise ValueError("kbps da 0 (nessun limite) a 1000000")
        nome = "LIMITE_" + re.sub(r"[^A-Za-z0-9]", "_", p["n"])
        if kbps:
            await configura(d["name"], [f"policy-map {nome}", "class class-default", f"shape average {kbps * 1000}", "exit", "exit",
                                  f"interface {p['n']}", f"service-policy output {nome}"], f"limite su {p['n']}")
            log(f"{d['name']} {short_port(p['n'])}: limite di {kbps} kbps in uscita", "config")
        else:
            await configura(d["name"], [f"interface {p['n']}", f"no service-policy output {nome}", "exit", f"no policy-map {nome}"],
                      f"limite su {p['n']}")
            log(f"{d['name']} {short_port(p['n'])}: limite tolto", "config")
    return {"limits": await leggi_limiti(d["name"])}



# ───────────────────────────── endpoint: gioco ─────────────────────────────
def j_academy_check(b):
    """POST /api/academy/check — lavoro lungo: job_academy_check."""
    return start_job("Verifica missione", job_academy_check)


def j_academy_reset(b):
    """POST /api/academy/reset — lavoro lungo: job_academy_reset. Svuota il tavolo!"""
    return start_job("Nuovo tavolo per l'Accademia", job_academy_reset)


async def job_academy_network(job: Job, inizio: bool = False) -> dict:
    """Lavoro: porta il tavolo alla rete di partenza della missione in corso (la sua topologia, o il tavolo vuoto).
    inizio: è la prima preparazione della missione (needs_setup), non un «Ricomincia la challenge»."""
    g = G()
    m = missione_corrente(g)
    if not m:
        return {"ok": True, "message": g.accademia("tutte_completate")}
    await carica_rete_missione(job, m)
    if inizio:
        return {"ok": True, "message": g.accademia("tavolo_pronto", titolo=m.title)}
    log(g.accademia("rete_ricaricata_diario", titolo=m.title), "info")
    return {"ok": True, "message": g.accademia("rete_ricaricata", titolo=m.title)}


def j_academy_network(b):
    """POST /api/academy/network — Body: {"start": bool}. Lavoro lungo: job_academy_network. Sostituisce la rete che c'è!

    «start»: true quando il client prepara il tavolo all'inizio della missione (needs_setup), false o assente per
    «Ricomincia la challenge»: cambia solo il titolo del lavoro.
    """
    inizio = bool(b.get("start"))
    return start_job("Preparo la challenge" if inizio else "Ricomincio la challenge",
                     lambda job: job_academy_network(job, inizio))


def passo_corrente(b, tipo: str) -> tuple[M.Mission, int, M.Step]:
    """La missione in corso e il passo «step» del body, che deve essere del tipo indicato."""
    m = missione_corrente(G())
    if not m:
        raise ValueError("Non c'è una missione in corso.")
    try:
        i = int(b.get("step"))
        s = m.steps[i]
    except (TypeError, ValueError, IndexError):
        raise ValueError("«step»: il numero di un passo della missione in corso (da 0)")
    if s.kind != tipo:
        raise ValueError(f"il passo {i + 1} non è un passo di tipo «{tipo}»")
    return m, i, s


def g_academy_answer(b):
    """POST /api/academy/answer — Body: {"step": i, "choice": j}. Risponde alla domanda di un passo (da 0).

    Risposta: Stato + {"answer": {"correct": bool, "message": "…"}}. La risposta giusta completa il passo; quella
    sbagliata no (si può riprovare). Non tocca la rete.
    """
    g = G()
    m, i, s = passo_corrente(b, "domanda")
    try:
        scelta = int(b.get("choice"))
        assert 0 <= scelta < len(s.domanda["scelte"])
    except (TypeError, ValueError, AssertionError):
        raise ValueError("«choice»: il numero della risposta scelta (da 0)")
    giusta = scelta == s.domanda["giusta"]
    if giusta:
        segna_passo(m, i)
        log(g.accademia("risposta_giusta_diario", titolo=m.title), "ok")
    msg = (s.domanda["spiegazione"] or g.accademia("risposta_giusta")) if giusta else g.accademia("risposta_sbagliata")
    return {"answer": {"correct": giusta, "message": msg}}


async def a_academy_observe(b):
    """POST /api/academy/observe — Body: {"step": i}. Segue il ping del passo «osserva» passo per passo
    (come /api/trace) e lo mostra al giocatore. Se il ping arriva il passo è fatto.

    Risposta: Stato + {"trace": {…come /api/trace…}, "explanation": "…"}. Dura 10–30 secondi.
    """
    g = G()
    m, i, s = passo_corrente(b, "osserva")
    da, a = s.osserva.get("da", ""), s.osserva.get("a", "")
    if s.osserva.get("coppia"):   # niente nomi nel YAML: la coppia la sceglie il gioco sulla rete che c'è
        coppia = M.COPPIE[s.osserva["coppia"]](motore.topo)
        if not coppia:
            raise ValueError(g.accademia("niente_da_osservare"))
        da, a = coppia
    for nome in (da, a):
        if not motore.topo.dev(nome):
            raise ValueError(g.accademia("manca_dispositivo", nome=nome))
    tr = await do_trace(da, a)
    if tr["delivered"]:
        segna_passo(m, i)
        log(g.accademia("traccia_diario", da=da, a=a), "ok")
    return {"trace": tr, "explanation": s.osserva["spiegazione"]}


def j_rescue_start(b):
    """POST /api/rescue/start — Body: {"level": n}. Lavoro lungo: job_rescue_start. Sostituisce la rete che c'è!

    Se la preparazione fallisce il livello passa allo stato «error» con il motivo in «message».
    """
    try:
        n = int(b.get("level"))
    except Exception:
        raise ValueError("livello non valido")
    if n not in G().livelli_per_numero:
        raise ValueError("livello inesistente")
    async def run(job: Job) -> dict:
        try:
            return await job_rescue_start(job, n)
        except Exception as e:
            STATE["rescue"].update({"status": "error", "message": f"Il livello non è partito: {e}"[:500]})
            save()
            raise
    return start_job(f"Preparo il livello {n}", run)


def j_rescue_check(b):
    """POST /api/rescue/check — lavoro lungo: job_rescue_check."""
    return start_job("Verifica riparazioni", job_rescue_check)


def g_hint(b):
    """POST /api/rescue/hint — Body: {}. Sblocca il prossimo indizio del primo guasto aperto (−costo_indizio su quel guasto).

    Risposta: Stato + {"toast": indizio}.
    """
    lv, f, st = rescue_open_fault()
    g = G()
    if st["hints"] >= len(f.hints):
        raise ValueError(g.soccorso("indizi_finiti"))
    st["hints"] += 1
    log(g.soccorso("indizio_diario", numero=st["hints"], costo=punteggio("costo_indizio")), "hint")
    return {"toast": f.hints[st["hints"] - 1]}


def g_surrender(b):
    """POST /api/rescue/surrender — Body: {}. Rivela la soluzione del primo guasto aperto, che varrà 0 punti.

    Risposta: Stato + {"toast": soluzione}.
    """
    lv, f, st = rescue_open_fault()
    g = G()
    st["surrendered"] = True
    log(g.soccorso("soluzione_diario", titolo=f.title), "hint")
    return {"toast": g.soccorso("soluzione", soluzione=f.solution)}


async def a_campaign_help(b):
    """POST /api/campaign/help — Body: {"help": id}. Compra l'aiuto di un collega del giorno in corso.

    Toglie il costo dal saldo, esegue le sue azioni sulla rete (se ne ha) e restituisce la risposta.
    Se le azioni falliscono il costo viene restituito. Un aiuto già comprato si rilegge gratis.
    Risposta: Stato + {"toast": risposta, "dialog": [battuta del collega]}.
    """
    g = G()
    c, cs = g.campagna, STATE["campaign"]
    giorno = c.corrente(cs["done"])
    if not giorno:
        raise ValueError(g.storia("nessun_giorno"))
    a = giorno.aiuto(str(b.get("help", "")))
    if not a:
        raise ValueError(g.storia("aiuto_inesistente"))
    p = c.personaggi[a.collega]
    dialogo = battute(c, [K.Battuta(a.collega, a.risposta)])
    comprati = cs["helps"].setdefault(str(giorno.n), [])
    if a.id in comprati:
        return {"toast": a.risposta, "dialog": dialogo}
    rs = STATE["rescue"]
    if a.livello is not None and not (rs["level"] == a.livello and rs["status"] == "playing"):
        raise ValueError(g.storia("aiuto_non_ora", collega=p.nome, livello=a.livello))
    if wallet() < a.costo:
        raise ValueError(g.storia("valuta_insufficiente", costo=c.valuta_per(a.costo), saldo=c.valuta_per(wallet())))
    cs["wallet"] -= a.costo
    comprati.append(a.id)
    save()
    if a.azioni:
        try:
            await esegui_azioni(a.azioni, motore.topo)
            motore.links_dirty = True
            await motore.settle()
        except (ValueError, ErroreRete) as e:
            cs["wallet"] += a.costo
            comprati.remove(a.id)
            save()
            raise ValueError(g.storia("aiuto_fallito", collega=p.nome, motivo=str(e)[:200], costo=c.valuta_per(a.costo)))
    log(g.storia("aiuto_diario", icona_collega=p.icona, collega=p.nome, costo=c.valuta_per(a.costo)), "hint")
    return {"toast": a.risposta, "dialog": dialogo}


def g_campaign_reset(b):
    """POST /api/campaign/reset — Body: {}. Riporta la campagna al giorno 1 con il saldo iniziale.

    Non tocca la rete né l'Accademia e il Pronto Soccorso: i giorni le cui attività risultano già fatte
    si richiudono alla prossima verifica (per ripartire davvero da zero: «Ricomincia» nell'Accademia).
    """
    STATE["campaign"] = default_state()["campaign"]
    log(G().storia("ricominciata_diario"), "info")


def g_collaudo_accademia(b):
    """POST /api/collaudo/accademia — solo con PNA_COLLAUDO=1. Body: {"done": [id, …]}.

    Cheat per il debug: segna come completate esattamente le missioni indicate (le altre tornano da fare),
    senza controlli, senza ping e senza punti. Non tocca la rete.
    """
    ids = b.get("done")
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise ValueError("done: serve una lista di id di missioni")
    validi = [m.id for m in G().missioni]
    sconosciuti = [x for x in ids if x not in validi]
    if sconosciuti:
        raise ValueError(f"missioni sconosciute: {', '.join(sconosciuti)}. Disponibili: {', '.join(validi)}")
    STATE["academy"]["done"] = [x for x in validi if x in ids]
    STATE["academy"]["passi"], STATE["academy"]["pronte"] = {}, []   # le missioni ripartono dal loro tavolo
    STATE["mode"] = "academy"
    log(f"🐞 Collaudo: missioni completate = {', '.join(STATE['academy']['done']) or 'nessuna'}", "info")
    save()


def g_mode(b):
    """POST /api/mode — Body: {"mode": "academy" | "rescue"}. Cambia la modalità mostrata; non tocca la rete."""
    if b.get("mode") not in ("academy", "rescue"):
        raise ValueError("modalità non valida: academy o rescue")
    STATE["mode"] = b["mode"]
    save()


# ───────────────────────────── avvio e tabella delle route ─────────────────────────────
async def index(req: Request):
    """GET / — la pagina del client (FRONTEND/index.html)."""
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-store"})


@asynccontextmanager
async def lifespan(app):
    """All'avvio prepara la rete (vera o simulata) e stampa indirizzo e codice; alla chiusura la smonta."""
    try:
        await motore.start()
    except Exception as e:  # noqa: BLE001
        motore.ok, motore.status = False, f"rete non avviata: {e}"
    codice = ACCESS_CODE if LOGIN_REQUIRED else "nessuno (si gioca solo da questo computer)"
    print(f"\n  Penguin Inc. → http://127.0.0.1:{PORT}\n  Codice di accesso: {codice}\n  Rete: {motore.status}"
          f"\n  Client: {FRONTEND}\n  Pacchetto di gioco: {GIOCO.cartella} ({len(G().missioni)} missioni, "
          f"{len(G().livelli)} livelli)\n",
          flush=True)
    if COLLAUDO:
        print("  🐞 MODALITÀ COLLAUDO ATTIVA (PNA_COLLAUDO=1): cheat per il debug accesi. Non usarla in demo.\n",
              flush=True)
    yield
    await motore.stop()


# Ogni endpoint è registrato qui e documentato in docs/API.md. Il wrapper dice che tipo di endpoint è:
# action() modifica la rete, job_action() avvia un lavoro lungo, game_action() tocca solo la partita.
routes = [
    # pagina e file statici
    Route("/", index),
    # Solo assets e script del client: la cartella «gioco» (YAML, con le soluzioni dei livelli) NON è servita.
    Mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets"),
    Mount("/scripts", StaticFiles(directory=FRONTEND / "scripts"), name="scripts"),
    # sessione
    Route("/api/health", health),
    Route("/api/ui", ui),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/state", state),
    Route("/api/mode", game_action(g_mode), methods=["POST"]),
    # dispositivi e cavi
    Route("/api/device/add", action(a_add), methods=["POST"]),
    Route("/api/device/move", action(a_move), methods=["POST"]),
    Route("/api/device/delete", action(a_delete), methods=["POST"]),
    Route("/api/device/rename", action(a_rename), methods=["POST"]),
    Route("/api/link", action(a_link), methods=["POST"]),
    Route("/api/unlink", action(a_unlink), methods=["POST"]),
    # configurazione
    Route("/api/host/ip", action(a_host_ip), methods=["POST"]),
    Route("/api/router/iface", action(a_iface), methods=["POST"]),
    Route("/api/router/dhcp", action(a_dhcp_pool), methods=["POST"]),
    Route("/api/router/dhcp/remove", action(a_dhcp_remove), methods=["POST"]),
    Route("/api/router/dhcp/exclude", action(a_dhcp_exclude), methods=["POST"]),
    Route("/api/router/route", action(a_route), methods=["POST"]),
    Route("/api/port/state", action(a_port_state), methods=["POST"]),
    Route("/api/switch/vlan", action(a_vlan), methods=["POST"]),
    # console e prove
    Route("/api/cli", action(a_cli), methods=["POST"]),
    Route("/api/host/cmd", action(a_host_cmd), methods=["POST"]),
    Route("/api/ping", action(a_ping), methods=["POST"]),
    # sicurezza
    Route("/api/router/acl", action(a_acl), methods=["POST"]),
    Route("/api/router/acl/remove", action(a_acl_remove), methods=["POST"]),
    Route("/api/host/browse", action(a_browse), methods=["POST"]),
    Route("/api/trace", action(a_trace), methods=["POST"]),
    Route("/api/switch/port-security", action(a_port_security), methods=["POST"]),
    Route("/api/device/harden", action(a_harden), methods=["POST"]),
    Route("/api/device/login", action(a_login), methods=["POST"]),
    Route("/api/device/users", action(a_users), methods=["POST"]),
    Route("/api/firewall/zone", action(a_fw_zone), methods=["POST"]),
    Route("/api/firewall/rules", action(a_fw_rules), methods=["POST"]),
    Route("/api/server/services", action(a_services), methods=["POST"]),
    Route("/api/server/service", action(a_service), methods=["POST"]),
    Route("/api/server/dns", action(a_server_dns), methods=["POST"]),
    Route("/api/firewall/route", action(a_fw_route), methods=["POST"]),
    Route("/api/router/ratelimit", action(a_ratelimit), methods=["POST"]),
    Route("/api/scan", job_action(j_scan), methods=["POST"]),
    Route("/api/sniff", job_action(j_sniff), methods=["POST"]),
    Route("/api/device/users/remove", action(a_user_remove), methods=["POST"]),
    Route("/api/audit", action(a_audit), methods=["POST"]),
    Route("/api/device/power", action(a_power), methods=["POST"]),
    Route("/api/router/hsrp", action(a_hsrp), methods=["POST"]),
    # gioco: Accademia
    Route("/api/academy/check", job_action(j_academy_check), methods=["POST"]),
    Route("/api/academy/reset", job_action(j_academy_reset), methods=["POST"]),
    Route("/api/academy/network", job_action(j_academy_network), methods=["POST"]),
    Route("/api/academy/answer", game_action(g_academy_answer), methods=["POST"]),
    Route("/api/academy/observe", action(a_academy_observe), methods=["POST"]),
    # gioco: Pronto Soccorso
    Route("/api/rescue/start", job_action(j_rescue_start), methods=["POST"]),
    Route("/api/rescue/check", job_action(j_rescue_check), methods=["POST"]),
    Route("/api/rescue/hint", game_action(g_hint), methods=["POST"]),
    Route("/api/rescue/surrender", game_action(g_surrender), methods=["POST"]),

    Route("/api/campaign/help", action(a_campaign_help), methods=["POST"]),
    Route("/api/campaign/reset", game_action(g_campaign_reset), methods=["POST"]),
]
if COLLAUDO:  # senza PNA_COLLAUDO=1 l'endpoint non esiste proprio (404)
    routes.append(Route("/api/collaudo/accademia", game_action(g_collaudo_accademia), methods=["POST"]))


async def no_stale_assets(request: Request, call_next):
    """I file statici vengono riconvalidati a ogni richiesta (costa poco con l'ETag): un aggiornamento arriva subito."""
    resp = await call_next(request)
    if request.url.path.startswith(("/assets/", "/scripts/")):
        resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


async def solo_locale(request: Request, call_next):
    """Senza codice di accesso, risponde solo alle richieste indirizzate a questo computer.

    Blocca l'accesso da Internet (es. un Funnel lasciato acceso) e il «DNS rebinding»: un sito esterno aperto
    nel browser che si fa risolvere in 127.0.0.1 per comandare il gioco. Con un codice di accesso non serve.
    """
    if not LOGIN_REQUIRED and request_host(request) not in LOCAL_HOSTS:
        return JSONResponse({"ok": False, "error": f"Senza codice di accesso Penguin Inc. si usa solo da questo "
                                                   f"computer: apri http://127.0.0.1:{PORT}"}, status_code=403)
    return await call_next(request)


app = Starlette(routes=routes, lifespan=lifespan,
                middleware=[Middleware(BaseHTTPMiddleware, dispatch=solo_locale),
                            Middleware(BaseHTTPMiddleware, dispatch=no_stale_assets)])

if __name__ == "__main__":
    # La porta si apre PRIMA di entrare nel user namespace: il socket resta nella rete del computer (o del
    # container) e risponde da lì, mentre la rete del gioco vive nel namespace, isolata.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((os.environ.get("PNA_HOST", "127.0.0.1"), PORT))
    sock.listen(128)
    if os.environ.get("PNA_RETE", "") != "simulata":
        entra_nel_namespace()
    config = uvicorn.Config(app, log_level="warning")
    asyncio.run(uvicorn.Server(config).serve(sockets=[sock]))
