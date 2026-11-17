"""Pacchetto di gioco: i contenuti e le impostazioni del gioco, scritti in YAML fuori dal codice.

Il backend è generico (tavolo, configurazione, sicurezza, motore di missioni e livelli); quello che rende
Penguin Inc. un gioco preciso sta in una cartella di file YAML, il «pacchetto di gioco»:

    impostazioni.yaml     punteggi, dispositivi che si possono aggiungere, tempi e comandi suggeriti
    interfaccia.yaml      i testi dell'interfaccia web (li usa solo il frontend)
    accademia.yaml        le missioni guidate e i messaggi dell'Accademia
    pronto-soccorso.yaml  i livelli da riparare e i messaggi del Pronto Soccorso
    campagna.yaml         la storia a giorni: personaggi, dialoghi, valuta di gioco, aiuti dei colleghi
    storia.yaml           la storyline di Penguin Inc (giornate, dialoghi, buoni caffè): la usa solo il
                          frontend, che la riceve dentro i testi dell'interfaccia (/api/ui → testi.storia)

La cartella è PNA_GIOCO, altrimenti ../frontend/demo/gioco. Ogni file è facoltativo: senza, valgono i
valori predefiniti qui sotto (nessuna missione, nessun livello), così il backend funziona anche per un
altro gioco che usa solo l'API.

I file vengono riletti da soli quando cambiano (al massimo una volta al secondo). Se un file ha un errore,
il server continua con l'ultima versione buona e l'errore, in italiano e con il punto esatto, finisce in
`errore` (mostrato da /api/health e nel registro).
"""
from __future__ import annotations

import ipaddress
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import campagna as K
import missions as M
import rescue as R

HERE = Path(__file__).parent
CARTELLA = Path(os.environ.get("PNA_GIOCO") or HERE.parent / "frontend" / "demo" / "gioco")
FILE = ("impostazioni.yaml", "interfaccia.yaml", "accademia.yaml", "pronto-soccorso.yaml", "campagna.yaml",
        "storia.yaml")

# I tipi di dispositivo che il backend sa gestire. «cloud» è un server presentato come servizio cloud.
KINDS = ("router", "switch", "firewall", "pc", "server", "cloud")
GRAVITA = {"alta": "high", "media": "medium", "bassa": "low"}

# ───────────────────────────── valori predefiniti ─────────────────────────────
IMPOSTAZIONI_PREDEFINITE = {
    "punteggio": {"guasto_riparato": 100, "costo_indizio": 25, "bonus_senza_indizi": 50},
    "dispositivi": {
        "router": {"prefisso": "R", "nome": "Router", "icona": "🧭", "articolo": "un router"},
        "switch": {"prefisso": "SW", "nome": "Switch", "icona": "🔀", "articolo": "uno switch"},
        "pc": {"prefisso": "PC", "nome": "PC", "icona": "🖥️", "articolo": "un PC"},
        "server": {"prefisso": "SRV", "nome": "Server", "icona": "🗄️", "articolo": "un server"},
        "firewall": {"prefisso": "FW", "nome": "Firewall", "icona": "🧱", "articolo": "un firewall"},
        "cloud": {"prefisso": "CLOUD", "nome": "Cloud", "icona": "☁️",
                  "articolo": "un servizio cloud"},
    },
    "interfaccia": {
        "aggiornamento_ms": 2500, "aggiornamento_durante_lavoro_ms": 1000, "durata_avvisi_ms": 5000,
        "coriandoli": True,
        "console_router": ["show ip interface brief", "show ip route", "show running-config", "show ip ospf neighbor"],
        "console_switch": ["show vlan brief", "show interfaces status", "show mac address-table", "show running-config"],
        "comandi_pc": ["ping ", "ipconfig", "ipconfig /all", "tracert ", "arp -a"],
    },
}

# Messaggi di gioco del backend. Si possono riscrivere nella sezione «messaggi» di accademia.yaml e
# pronto-soccorso.yaml; i segnaposto tra graffe ({titolo}, {punti}…) vengono sostituiti.
MESSAGGI_ACCADEMIA = {
    "tutte_completate": "Hai completato tutte le missioni!",
    "controllo_in_corso": "Controllo la missione «{titolo}»",
    "passo_mancante": "Passo {numero} non ancora fatto: {passo}. {motivo}",
    "passo_mancante_diario": "«{titolo}»: manca il passo {numero}",
    "niente_da_pingare": "Non trovo due dispositivi pronti per il ping del passo {numero}.",
    "ping_in_corso": "Ping vero: {etichetta} ({ip})",
    "ping_fallito": "Il ping {etichetta} ({ip}) non ha avuto risposta.",
    "ping_fallito_consiglio": "Controlla cavi, IP, maschere e gateway.",
    "ping_fallito_diario": "«{titolo}»: ping fallito",
    "completata": "Missione «{titolo}» completata! +{punti} punti",
    "completata_diario": "🏆 Missione «{titolo}» completata!",
    "svuoto_tavolo": "Svuoto il tavolo",
    "ricominciata": "Tavolo pulito: si parte dalla missione 1.",
    "ricominciata_diario": "Accademia ricominciata da zero: tavolo vuoto",
    "carico_rete": "Carico la rete di partenza di «{titolo}»",
    "rete_pronta": "La rete di «{titolo}» è pronta sul tavolo.",
    "rete_ricaricata": "Si riparte: il tavolo è tornato com'era all'inizio di «{titolo}».",
    "rete_ricaricata_diario": "↺ «{titolo}»: rete di partenza ricaricata",
    "verifica_fallita": "Passo {numero} non ancora a posto: {passo}. Il gioco ha provato: {motivo}.",
    "rispondi": "Rispondi alla domanda.",
    "osserva_prima": "Prima guarda il viaggio del ping.",
    "risposta_giusta": "Esatto!",
    "risposta_sbagliata": "Non proprio: ripensaci e riprova.",
    "risposta_giusta_diario": "✔ «{titolo}»: risposta giusta",
    "traccia_diario": "🔍 Viaggio del ping da {da} a {a} osservato",
    "manca_dispositivo": "Sul tavolo manca {nome}.",
    "niente_da_osservare": "Sul tavolo non trovo ancora due dispositivi pronti per il ping da osservare.",
    "condizione_mancante": "Non ancora: «{passo}» non è a posto. Se ti serve aiuto, chiedi un consiglio al collega.",
    "tavolo_pronto": "Il tavolo è pronto: si comincia «{titolo}».",
    "verifico_condizione": "Verifico: {passo}",
}
MESSAGGI_SOCCORSO = {
    "svuoto_tavolo": "Svuoto il tavolo",
    "costruisco": "Costruisco la rete del livello {numero}: «{titolo}»",
    "controllo_partenza": "Controllo che la rete di partenza funzioni",
    "rompo": "Qualcuno ci mette le mani… 😈",
    "controllo_guasti": "Controllo che i guasti si vedano davvero",
    "pronto": "Livello {numero} pronto: leggi il ticket!",
    "pronto_diario": "🚑 Livello {numero} «{titolo}»: nuovo ticket in arrivo",
    "istruzioni": "Leggi il ticket e trova i guasti. Quando pensi di aver riparato qualcosa premi «Verifica».",
    "avanzo_tempo": "Preparo la rete (ARP, DHCP)",
    "troppo_bloccato": "hai bloccato anche traffico che deve passare ({motivo})",
    "scorciatoia": "il sintomo è sparito ma la riparazione non rispetta il ticket: {motivo}",
    "guasto_riparato_diario": "🔧 Guasto riparato: {titolo}",
    "ancora_da_sistemare": "Ancora da sistemare: {elenco}",
    "riparati_uno": "Hai riparato 1 guasto! ",
    "riparati_piu": "Hai riparato {numero} guasti! ",
    "controllo_finale": "Controllo finale: tutta la rete",
    "finale_fallito": "I guasti del ticket sono a posto, ma qualcos'altro non va: {motivo}. Hai rotto qualcosa riparando?",
    "completato": "Livello completato! {punti} punti",
    "bonus": " (bonus senza indizi +{bonus})",
    "completato_diario": "🏆 Livello {numero} «{titolo}» completato",
    "indizi_finiti": "Indizi finiti per questo guasto: puoi arrenderti per vedere la soluzione.",
    "indizio_diario": "💡 Indizio {numero} (−{costo} sul guasto)",
    "soluzione": "Soluzione: {soluzione} Applicala e premi «Verifica».",
    "soluzione_diario": "🏳️ Soluzione mostrata: {titolo}",
    "nessun_livello": "Nessun livello in corso.",
    "nessun_guasto_aperto": "Non ci sono guasti aperti: premi «Verifica».",
}

MESSAGGI_CAMPAGNA = {
    "giorno_completato": "Giorno {numero} «{titolo}» completato!{premio}",
    "giorno_completato_diario": "{icona} Giorno {numero} «{titolo}» completato{premio}",
    "premio": " +{ricompensa}",
    "campagna_finita_diario": "🎓 Campagna completata!",
    "aiuto_diario": "{icona_collega} {collega} ti ha aiutato (−{costo})",
    "aiuto_in_corso": "{collega} si mette al lavoro…",
    "aiuto_inesistente": "Oggi nessun collega offre questo aiuto.",
    "aiuto_gia_usato": "{collega} ti ha già aiutato su questo: rileggi la sua risposta.",
    "aiuto_non_ora": "{collega} può aiutarti solo durante il livello {livello} del Pronto Soccorso.",
    "valuta_insufficiente": "Non ti bastano: servono {costo}, hai {saldo}.",
    "aiuto_fallito": "{collega} non è riuscito ad aiutarti ({motivo}). Ti ho restituito {costo}.",
    "nessun_giorno": "La campagna è finita: non ci sono altri giorni.",
    "ricominciata_diario": "Campagna ricominciata dal giorno 1",
}


class ErroreContenuti(ValueError):
    """Un file del pacchetto di gioco non è valido. Il messaggio dice file e punto esatto."""


class _Segnaposto(dict):
    def __missing__(self, k):  # un segnaposto sconosciuto resta com'è invece di far fallire il messaggio
        return "{" + k + "}"


@dataclass
class Pacchetto:
    """Il pacchetto di gioco già validato e trasformato negli oggetti del motore."""
    impostazioni: dict
    interfaccia: dict
    missioni: list = field(default_factory=list)      # list[M.Mission]
    livelli: list = field(default_factory=list)       # list[R.Level]
    msg_accademia: dict = field(default_factory=dict)
    msg_soccorso: dict = field(default_factory=dict)
    campagna: K.Campagna = field(default_factory=lambda: K.CAMPAGNA_VUOTA)
    msg_campagna: dict = field(default_factory=dict)

    @property
    def livelli_per_numero(self) -> dict:
        return {lv.n: lv for lv in self.livelli}

    def accademia(self, chiave: str, **valori) -> str:
        return self.msg_accademia.get(chiave, MESSAGGI_ACCADEMIA.get(chiave, chiave)).format_map(_Segnaposto(valori))

    def soccorso(self, chiave: str, **valori) -> str:
        return self.msg_soccorso.get(chiave, MESSAGGI_SOCCORSO.get(chiave, chiave)).format_map(_Segnaposto(valori))

    def storia(self, chiave: str, **valori) -> str:
        """Un messaggio della campagna (sezione «messaggi» di campagna.yaml)."""
        return self.msg_campagna.get(chiave, MESSAGGI_CAMPAGNA.get(chiave, chiave)).format_map(_Segnaposto(valori))


# ───────────────────────────── validazione ─────────────────────────────
def _testo(v, dove: str, vuoto_ok: bool = False) -> str:
    if v is None and vuoto_ok:
        return ""
    if not isinstance(v, (str, int, float)) or isinstance(v, bool) or (str(v).strip() == "" and not vuoto_ok):
        raise ErroreContenuti(f"{dove}: serve un testo")
    return str(v)


def _intero(v, dove: str, minimo: int = 0) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < minimo:
        raise ErroreContenuti(f"{dove}: serve un numero intero (almeno {minimo})")
    return v


def _lista(v, dove: str) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ErroreContenuti(f"{dove}: serve una lista (righe che iniziano con «-»)")
    return v


def _mappa(v, dove: str) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ErroreContenuti(f"{dove}: serve un gruppo di campi «nome: valore»")
    return v


def _una_chiave(v, dove: str, ammesse: tuple) -> tuple[str, dict]:
    """Elementi come «- ping: {da, a}»: un solo tipo, con i suoi campi."""
    if not isinstance(v, dict) or len(v) != 1:
        raise ErroreContenuti(f"{dove}: serve una voce sola, uno tra: {', '.join(ammesse)}")
    tipo, campi = next(iter(v.items()))
    if tipo not in ammesse:
        raise ErroreContenuti(f"{dove}: «{tipo}» non esiste. Possibili: {', '.join(ammesse)}")
    return tipo, _mappa(campi, f"{dove}, {tipo}")


def _messaggi(v, dove: str, predefiniti: dict) -> dict:
    out = {}
    for k, testo in _mappa(v, dove).items():
        if k not in predefiniti:
            raise ErroreContenuti(f"{dove}: «{k}» non è un messaggio conosciuto. Possibili: {', '.join(predefiniti)}")
        out[k] = _testo(testo, f"{dove}, {k}", vuoto_ok=True)
    return out


def _impostazioni(dati: dict, dove: str) -> dict:
    imp = {k: dict(v) if isinstance(v, dict) else v for k, v in IMPOSTAZIONI_PREDEFINITE.items()}
    imp["dispositivi"] = {k: dict(v) for k, v in IMPOSTAZIONI_PREDEFINITE["dispositivi"].items()}
    for k, v in _mappa(dati.get("punteggio"), f"{dove}, punteggio").items():
        if k not in imp["punteggio"]:
            raise ErroreContenuti(f"{dove}, punteggio: «{k}» non esiste. Possibili: {', '.join(imp['punteggio'])}")
        imp["punteggio"][k] = _intero(v, f"{dove}, punteggio, {k}")
    disp = dati.get("dispositivi")
    if disp is not None:
        imp["dispositivi"] = {}
        for kind, d in _mappa(disp, f"{dove}, dispositivi").items():
            w = f"{dove}, dispositivi, {kind}"
            if kind not in KINDS:
                raise ErroreContenuti(f"{w}: tipo sconosciuto. Possibili: {', '.join(KINDS)}")
            d = _mappa(d, w)
            base = dict(IMPOSTAZIONI_PREDEFINITE["dispositivi"][kind])
            for k, v in d.items():
                if k not in base:
                    raise ErroreContenuti(f"{w}: «{k}» non esiste. Possibili: {', '.join(base)}")
                base[k] = _testo(v, f"{w}, {k}")
            if not re.fullmatch(r"[A-Za-z]{1,6}", base["prefisso"]):
                raise ErroreContenuti(f"{w}, prefisso: solo lettere, al massimo 6")
            imp["dispositivi"][kind] = base
    for k, v in _mappa(dati.get("interfaccia"), f"{dove}, interfaccia").items():
        if k not in imp["interfaccia"]:
            raise ErroreContenuti(f"{dove}, interfaccia: «{k}» non esiste. Possibili: {', '.join(imp['interfaccia'])}")
        atteso = imp["interfaccia"][k]
        if isinstance(atteso, bool):
            if not isinstance(v, bool):
                raise ErroreContenuti(f"{dove}, interfaccia, {k}: serve true o false")
        elif isinstance(atteso, int):
            v = _intero(v, f"{dove}, interfaccia, {k}", 200)
        else:
            v = [_testo(x, f"{dove}, interfaccia, {k}") for x in _lista(v, f"{dove}, interfaccia, {k}")]
        imp["interfaccia"][k] = v
    return imp


def _file_rete(rete: str, dove: str, cartella: Path) -> str:
    """Il nome di una topologia di partenza: un file .yaml della cartella reti/ (formato in backend/rete/topologia.py)."""
    if not re.fullmatch(r"[\w.-]+\.yaml", rete):
        raise ErroreContenuti(f"{dove}: il nome di un file .yaml nella cartella «reti» (es. giorno2.yaml)")
    if not (cartella / "reti" / rete).exists():
        raise ErroreContenuti(f"{dove}: il file reti/{rete} non esiste")
    return rete


def _missioni(dati: dict, dove: str, cartella: Path) -> list:
    out, visti = [], set()
    for i, m in enumerate(_lista(dati.get("missioni"), f"{dove}, missioni"), 1):
        m = _mappa(m, f"{dove}, missione {i}")
        mid = _testo(m.get("id"), f"{dove}, missione {i}, id")
        w = f"{dove}, missione {i} («{mid}»)"
        if not re.fullmatch(r"[a-z0-9-]{1,30}", mid):
            raise ErroreContenuti(f"{w}, id: solo lettere minuscole, numeri e «-»")
        if mid in visti:
            raise ErroreContenuti(f"{w}, id: c'è già un'altra missione con questo id")
        visti.add(mid)
        teoria = []
        for k, t in enumerate(_lista(m.get("teoria"), f"{w}, teoria"), 1):
            t = _mappa(t, f"{w}, teoria {k}")
            teoria.append((_testo(t.get("concetto"), f"{w}, teoria {k}, concetto"),
                           _testo(t.get("spiegazione"), f"{w}, teoria {k}, spiegazione")))
        passi = [_passo(p, f"{w}, passo {k}") for k, p in enumerate(_lista(m.get("passi"), f"{w}, passi"), 1)]
        if not passi:
            raise ErroreContenuti(f"{w}: servono dei passi")
        rete = _testo(m.get("rete"), f"{w}, rete", vuoto_ok=True)
        if rete and rete != "vuota":
            _file_rete(rete, f"{w}, rete", cartella)
        tutorial = m.get("tutorial", False)
        if not isinstance(tutorial, bool):
            raise ErroreContenuti(f"{w}, tutorial: true (missione guidata) o false")
        piano = [[_testo(c, f"{w}, piano") for c in _lista(r, f"{w}, piano")] for r in _lista(m.get("piano"), f"{w}, piano")]
        consigli = [_testo(c, f"{w}, consigli") for c in _lista(m.get("consigli"), f"{w}, consigli")]
        traffico = _traffico(m.get("traffico"), w)
        out.append(M.Mission(mid, _testo(m.get("titolo"), f"{w}, titolo"), _intero(m.get("punti", 0), f"{w}, punti"),
                             _testo(m.get("storia"), f"{w}, storia", vuoto_ok=True), teoria, passi,
                             rete=rete, topologia=_testo(m.get("topologia"), f"{w}, topologia", vuoto_ok=True),
                             piano=piano, consigli=consigli, tutorial=tutorial, traffic=traffico))
    return out


def _traffico(v, w: str) -> list[tuple[str, str]]:
    """«traffico» di un livello o di una missione: [{da, comando}] → [(dispositivo, comando)]."""
    out = []
    for j, x in enumerate(_lista(v, f"{w}, traffico"), 1):
        x = _mappa(x, f"{w}, traffico {j}")
        cmd = _testo(x.get("comando"), f"{w}, traffico {j}, comando")
        if not R.TRAFFICO_RE.match(cmd):
            raise ErroreContenuti(f"{w}, traffico {j}, comando: «{cmd}» non va bene. Possibili: "
                                  f"«ping IP», «telnet IP porta», «web http://IP» (IP anche come @PC2)")
        out.append((_testo(x.get("da"), f"{w}, traffico {j}, da"), cmd))
    return out


TIPI_PASSO = ("controllo", "ping", "verifica", "domanda", "osserva")


def _passo(p, w: str) -> M.Step:
    """Un passo di missione: «testo», «aiuto» e uno solo tra controllo, ping, verifica, domanda, osserva."""
    p = _mappa(p, w)
    # «aiuto» (come si fa) è facoltativo: le missioni non guidate non lo mostrano, l'aiuto lo dà il collega
    testo, aiuto = _testo(p.get("testo"), f"{w}, testo"), _testo(p.get("aiuto"), f"{w}, aiuto", vuoto_ok=True)
    tipi = [k for k in TIPI_PASSO if k in p]
    if len(tipi) != 1:
        raise ErroreContenuti(f"{w}: serve uno solo tra {', '.join(TIPI_PASSO)}")
    tipo, v = tipi[0], p[tipi[0]]
    if tipo == "controllo":
        if isinstance(v, dict):
            return M.Step(testo, aiuto, M.controllo_dichiarativo(_controllo_dichiarativo(v, f"{w}, controllo")))
        if v not in M.CONTROLLI:
            raise ErroreContenuti(f"{w}: il controllo «{v}» non esiste. Disponibili: {', '.join(M.CONTROLLI)}, "
                                  f"oppure scrivilo nel YAML con: {', '.join(M.CONTROLLI_DICHIARATIVI)}")
        return M.Step(testo, aiuto, M.CONTROLLI[v])
    if tipo == "ping":
        if v not in M.PIANI_PING:
            raise ErroreContenuti(f"{w}: il piano di ping «{v}» non esiste. Disponibili: {', '.join(M.PIANI_PING)}")
        return M.Step(testo, aiuto, ping=M.PIANI_PING[v], kind="ping")
    if tipo == "verifica":
        verifiche = [_verifica(x, f"{w}, verifica {k}") for k, x in enumerate(_lista(v, f"{w}, verifica"), 1)]
        if not verifiche:
            raise ErroreContenuti(f"{w}, verifica: serve almeno una verifica (ping, attraversa, bloccato…)")
        return M.Step(testo, aiuto, kind="verifica", verifiche=verifiche)
    if tipo == "domanda":
        d = _mappa(v, f"{w}, domanda")
        scelte = [_testo(s, f"{w}, domanda, scelte") for s in _lista(d.get("scelte"), f"{w}, domanda, scelte")]
        if len(scelte) < 2:
            raise ErroreContenuti(f"{w}, domanda: servono almeno due «scelte»")
        giusta = _intero(d.get("giusta"), f"{w}, domanda, giusta", 1)
        if giusta > len(scelte):
            raise ErroreContenuti(f"{w}, domanda, giusta: il numero della scelta giusta, da 1 a {len(scelte)}")
        return M.Step(testo, aiuto, kind="domanda", domanda={
            "testo": _testo(d.get("testo"), f"{w}, domanda, testo"), "scelte": scelte, "giusta": giusta - 1,
            "spiegazione": _testo(d.get("spiegazione"), f"{w}, domanda, spiegazione", vuoto_ok=True)})
    o = _mappa(v, f"{w}, osserva")
    spiegazione = _testo(o.get("spiegazione"), f"{w}, osserva, spiegazione", vuoto_ok=True)
    if o.get("coppia") is not None:   # senza nomi: la coppia la sceglie il gioco (missions.COPPIE)
        coppia = _testo(o.get("coppia"), f"{w}, osserva, coppia")
        if coppia not in M.COPPIE:
            raise ErroreContenuti(f"{w}, osserva, coppia: «{coppia}» non esiste. Possibili: {', '.join(M.COPPIE)}")
        return M.Step(testo, aiuto, kind="osserva", osserva={"coppia": coppia, "spiegazione": spiegazione})
    return M.Step(testo, aiuto, kind="osserva", osserva={
        "da": _testo(o.get("da"), f"{w}, osserva, da"), "a": _testo(o.get("a"), f"{w}, osserva, a"),
        "spiegazione": spiegazione})


def _ip_con_maschera(v, w: str) -> str:
    try:
        return str(ipaddress.IPv4Interface(_testo(v, w)))
    except ValueError:
        raise ErroreContenuti(f"{w}: serve un indirizzo con la maschera, es. 10.10.10.11/24")


def _ip(v, w: str) -> str:
    try:
        return str(ipaddress.IPv4Address(_testo(v, w)))
    except ValueError:
        raise ErroreContenuti(f"{w}: serve un indirizzo IP, es. 10.10.10.1")


def _coppie(v, w: str) -> list:
    out = []
    for c in _lista(v, w):
        c = [_testo(x, w) for x in _lista(c, w)]
        if len(c) != 2:
            raise ErroreContenuti(f"{w}: ogni voce è una coppia di dispositivi, es. [PC1, SW1]")
        out.append(c)
    return out


def _controllo_dichiarativo(v: dict, w: str) -> dict:
    """Valida un controllo scritto nel YAML (vedi missions.CONTROLLI_DICHIARATIVI) e lo normalizza."""
    out = {}
    for k, x in v.items():
        wk = f"{w}, {k}"
        if k not in M.CONTROLLI_DICHIARATIVI:
            raise ErroreContenuti(f"{w}: «{k}» non esiste. Possibili: {', '.join(M.CONTROLLI_DICHIARATIVI)}")
        if k == "dispositivi":
            out[k] = {_testo(n, wk): _testo(t, wk) for n, t in _mappa(x, wk).items()}
            for n, t in out[k].items():
                if t not in KINDS:
                    raise ErroreContenuti(f"{wk}, {n}: il tipo deve essere uno tra {', '.join(KINDS)}")
        elif k in ("cavi", "cavi_attivi", "niente_cavi"):
            out[k] = _coppie(x, wk)
        elif k == "indirizzi":
            out[k] = {_testo(n, wk): _ip_con_maschera(i, f"{wk}, {n}") for n, i in _mappa(x, wk).items()}
        elif k == "gateway":
            # un IP esatto, oppure «@R1»: la porta di R1 nella rete dell'host, qualunque indirizzo abbia
            out[k] = {_testo(n, wk): (_testo(i, f"{wk}, {n}") if str(i).startswith("@") else _ip(i, f"{wk}, {n}"))
                      for n, i in _mappa(x, wk).items()}
        elif k == "stessa_rete":
            out[k] = [[_testo(n, wk) for n in _lista(g, wk)] for g in _lista(x, wk)]
            if not out[k] or any(len(g) < 2 for g in out[k]):
                raise ErroreContenuti(f"{wk}: una lista di gruppi di almeno due dispositivi, es. [[PC1, PC2]]")
        elif k == "porte_router":
            out[k] = {}
            for r, porte in _mappa(x, wk).items():
                voci = []
                for j, p in enumerate(_lista(porte, f"{wk}, {r}"), 1):
                    p = _mappa(p, f"{wk}, {r}, porta {j}")
                    wj = f"{wk}, {r}, porta {j}"
                    voce = {"verso": _testo(p.get("verso"), f"{wj}, verso")}
                    if p.get("ip") is not None:
                        voce["ip"] = _ip_con_maschera(p.get("ip"), f"{wj}, ip")
                    for chiave in ("rete_di", "gateway_di"):   # facoltativi: la porta senza un IP imposto
                        if p.get(chiave) is not None:
                            voce[chiave] = _testo(p.get(chiave), f"{wj}, {chiave}")
                    voci.append(voce)
                out[k][_testo(r, wk)] = voci
        else:
            out[k] = [_testo(n, wk) for n in _lista(x, wk)]
    if not out:
        raise ErroreContenuti(f"{w}: il controllo è vuoto")
    return out


def _verifica(v, dove: str) -> R.Check:
    if isinstance(v, dict) and set(v) == {"piano"}:   # verifiche che scelgono da sole i dispositivi
        nome = _testo(v["piano"], f"{dove}, piano")
        if nome not in M.PIANI_VERIFICA:
            raise ErroreContenuti(f"{dove}, piano: «{nome}» non esiste. Possibili: {', '.join(M.PIANI_VERIFICA)}")
        return R.plan(nome)
    tipo, c = _una_chiave(v, dove, ("ping", "attraversa", "web", "web_bloccato", "bloccato", "audit_pulito", "resiste",
                                    "porta", "risolve", "limitato"))
    w = f"{dove}, {tipo}"
    if tipo == "limitato":
        return R.limited(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"))
    if tipo == "risolve":
        return R.resolves(_testo(c.get("da"), f"{w}, da"), _testo(c.get("nome"), f"{w}, nome"),
                         _testo(c.get("verso", "server"), f"{w}, verso"))
    if tipo == "attraversa":
        return R.through(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"),
                         _testo(c.get("via"), f"{w}, via"))
    if tipo == "porta" and "passa" in c:   # passa: true = non filtrata (SSH «simulato»), false = filtrata
        if not isinstance(c.get("passa"), bool):
            raise ErroreContenuti(f"{w}, passa: true (il traffico arriva) o false (lo scarta un firewall)")
        return R.port_passes(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"),
                             _intero(c.get("porta"), f"{w}, porta", 1), c["passa"])
    if tipo == "porta":
        aperta = c.get("aperta")
        if not isinstance(aperta, bool):
            raise ErroreContenuti(f"{w}, aperta: true (deve rispondere) o false (chiusa o filtrata)")
        return R.port(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"),
                      _intero(c.get("porta"), f"{w}, porta", 1), aperta)
    if tipo == "ping":
        return R.ping(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"))
    if tipo in ("web", "web_bloccato"):
        f = R.web if tipo == "web" else R.web_blocked
        return f(_testo(c.get("da"), f"{w}, da"), _testo(c.get("url"), f"{w}, url"))
    if tipo == "bloccato":
        return R.blocked(_testo(c.get("da"), f"{w}, da"), _testo(c.get("a"), f"{w}, a"),
                         at=_testo(c.get("fermato_da"), f"{w}, fermato_da", vuoto_ok=True))
    if tipo == "audit_pulito":
        g = c.get("gravita", "alta")
        if g not in GRAVITA:
            raise ErroreContenuti(f"{w}, gravita: alta, media o bassa")
        falle = [_testo(x, f"{w}, falle") for x in _lista(c.get("falle"), f"{w}, falle")]
        for x in falle:
            if not re.fullmatch(r"[A-Z][A-Z_]{2,40}", x):
                raise ErroreContenuti(f"{w}, falle: «{x}» non è un codice dell'audit (es. NO_ENABLE_SECRET)")
        return R.audit_clean(_testo(c.get("dispositivo"), f"{w}, dispositivo"), GRAVITA[g], falle)
    interne = [_verifica(x, f"{w}, verifica {k}") for k, x in enumerate(_lista(c.get("verifiche"), f"{w}, verifiche"), 1)]
    if not interne:
        raise ErroreContenuti(f"{w}: servono delle verifiche da far passare con il dispositivo spento")
    return R.survives(_testo(c.get("spento"), f"{w}, spento"), *interne)


def _rompi(v, dove: str) -> tuple:
    tipo, c = _una_chiave(v, dove, ("comandi", "spegni_porta", "ip_host"))
    w = f"{dove}, {tipo}"
    if tipo == "comandi":
        righe = [_testo(r, f"{w}, righe") for r in _lista(c.get("righe"), f"{w}, righe")]
        if not righe:
            raise ErroreContenuti(f"{w}: servono delle righe della console")
        return ("cli", _testo(c.get("dispositivo"), f"{w}, dispositivo"), righe)
    if tipo == "spegni_porta":
        return R.shut(_testo(c.get("dispositivo"), f"{w}, dispositivo"), _testo(c.get("porta"), f"{w}, porta"))
    return ("hostip", {"device": _testo(c.get("dispositivo"), f"{w}, dispositivo"), "mode": "static",
                       "ip": _testo(c.get("ip"), f"{w}, ip"), "mask": _testo(c.get("maschera", "255.255.255.0"), f"{w}, maschera"),
                       "gateway": _testo(c.get("gateway", "0.0.0.0"), f"{w}, gateway")})


AZIONI = ("comandi", "spegni_porta", "ip_host", "aggiungi", "collega", "ip_porta", "zona_firewall", "regole_firewall")


def _azione(v, dove: str) -> tuple:
    """Un'azione sulla rete di un aiuto: le stesse di «rompi» più aggiungi, collega e ip_porta."""
    tipo, c = _una_chiave(v, dove, AZIONI)
    w = f"{dove}, {tipo}"
    if tipo in ("comandi", "spegni_porta", "ip_host"):
        return _rompi(v, dove)
    if tipo == "aggiungi":
        kind = c.get("tipo")
        if kind not in KINDS:
            raise ErroreContenuti(f"{w}, tipo: uno tra {', '.join(KINDS)}")
        nome = _testo(c.get("nome"), f"{w}, nome")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", nome):
            raise ErroreContenuti(f"{w}, nome: solo lettere, numeri, - e _")
        return ("add", kind, nome, _intero(c.get("x", 300), f"{w}, x"), _intero(c.get("y", 300), f"{w}, y"))
    if tipo == "collega":
        return ("link", _testo(c.get("a"), f"{w}, a"), _testo(c.get("b"), f"{w}, b"))
    if tipo == "zona_firewall":
        zona = _testo(c.get("zona"), f"{w}, zona")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,19}", zona):
            raise ErroreContenuti(f"{w}, zona: una parola minuscola, es. inside, dmz, outside")
        for k in ("ip", "maschera"):
            try:
                ipaddress.IPv4Address(str(c.get(k, "255.255.255.0" if k == "maschera" else "")))
            except ValueError:
                raise ErroreContenuti(f"{w}, {k}: serve un indirizzo come 172.16.0.1")
        livello = _intero(c.get("livello", {"inside": 100, "dmz": 50, "outside": 0}.get(zona, 50)), f"{w}, livello")
        return ("fwzone", _testo(c.get("dispositivo"), f"{w}, dispositivo"), _testo(c.get("verso"), f"{w}, verso"),
                zona, min(livello, 100), str(c["ip"]), str(c.get("maschera", "255.255.255.0")))
    if tipo == "regole_firewall":
        regole = []
        for j, r in enumerate(_lista(c.get("regole"), f"{w}, regole"), 1):
            r = _mappa(r, f"{w}, regola {j}")
            ammesse = {"da": "from", "verso": "to", "azione": "action", "protocollo": "protocol", "porta": "port",
                       "destinazione": "dest", "origine": "source"}
            for k in r:
                if k not in ammesse:
                    raise ErroreContenuti(f"{w}, regola {j}: «{k}» non esiste. Possibili: {', '.join(ammesse)}")
            regola = {ammesse[k]: v for k, v in r.items()}
            regola["action"] = {"permetti": "permit", "nega": "deny"}.get(regola.get("action"), regola.get("action"))
            regole.append(regola)
        return ("fwrules", _testo(c.get("dispositivo"), f"{w}, dispositivo"), regole)
    for k in ("ip", "maschera"):
        try:
            ipaddress.IPv4Address(_testo(c.get(k, "255.255.255.0" if k == "maschera" else None), f"{w}, {k}"))
        except ValueError:
            raise ErroreContenuti(f"{w}, {k}: serve un indirizzo come 192.168.10.1")
    return ("portip", _testo(c.get("dispositivo"), f"{w}, dispositivo"), _testo(c.get("verso"), f"{w}, verso"),
            c["ip"], c.get("maschera", "255.255.255.0"))


def _vincolo(v, dove: str):
    if v is None:
        return None
    tipo, c = _una_chiave(v, dove, ("ip_esatto", "nella_rete"))
    w = f"{dove}, {tipo}"
    if tipo == "ip_esatto":
        return R.host_ip_is(_testo(c.get("dispositivo"), f"{w}, dispositivo"), _testo(c.get("ip"), f"{w}, ip"))
    rete = _testo(c.get("rete"), f"{w}, rete")
    try:
        ipaddress.IPv4Network(rete)
    except ValueError:
        raise ErroreContenuti(f"{w}, rete: serve una rete come 192.168.1.0/24")
    return R.host_in(_testo(c.get("dispositivo"), f"{w}, dispositivo"), rete, _testo(c.get("messaggio"), f"{w}, messaggio"))


def _livelli(dati: dict, dove: str, cartella: Path) -> list:
    out, numeri = [], set()
    for i, lv in enumerate(_lista(dati.get("livelli"), f"{dove}, livelli"), 1):
        lv = _mappa(lv, f"{dove}, livello {i}")
        n = _intero(lv.get("numero"), f"{dove}, livello {i}, numero", 1)
        w = f"{dove}, livello {n}"
        if n in numeri:
            raise ErroreContenuti(f"{w}: c'è già un altro livello con questo numero")
        numeri.add(n)
        guasti, ids = [], set()
        for k, g in enumerate(_lista(lv.get("guasti"), f"{w}, guasti"), 1):
            g = _mappa(g, f"{w}, guasto {k}")
            gid = _testo(g.get("id"), f"{w}, guasto {k}, id")
            wg = f"{w}, guasto «{gid}»"
            if gid in ids:
                raise ErroreContenuti(f"{wg}: c'è già un altro guasto con questo id nel livello")
            ids.add(gid)
            verifiche = [_verifica(x, f"{wg}, verifica {j}") for j, x in enumerate(_lista(g.get("verifiche"), f"{wg}, verifiche"), 1)]
            if not verifiche:
                raise ErroreContenuti(f"{wg}: servono delle verifiche")
            guasti.append(R.Fault(
                gid, _testo(g.get("cosa_era_rotto"), f"{wg}, cosa_era_rotto"), _testo(g.get("sintomo"), f"{wg}, sintomo"),
                [_rompi(x, f"{wg}, rompi {j}") for j, x in enumerate(_lista(g.get("rompi"), f"{wg}, rompi"), 1)],
                [], [_testo(h, f"{wg}, indizi") for h in _lista(g.get("indizi"), f"{wg}, indizi")],
                _testo(g.get("soluzione"), f"{wg}, soluzione"), _vincolo(g.get("vincolo"), f"{wg}, vincolo"),
                checks=verifiche))
        if not guasti:
            raise ErroreContenuti(f"{w}: servono dei guasti")
        rete = _file_rete(_testo(lv["rete"], f"{w}, rete"), f"{w}, rete", cartella) if lv.get("rete") else ""
        costruisci = [_azione(x, f"{w}, costruisci {j}") for j, x in enumerate(_lista(lv.get("costruisci"), f"{w}, costruisci"), 1)]
        if not rete and not costruisci:
            raise ErroreContenuti(f"{w}: serve «rete» (una topologia di reti/) oppure «costruisci» (azioni)")
        traffico = _traffico(lv.get("traffico"), w)
        out.append(R.Level(n, _testo(lv.get("titolo"), f"{w}, titolo"), rete, _testo(lv.get("ticket"), f"{w}, ticket"),
                           guasti, lesson=_testo(lv.get("lezione"), f"{w}, lezione", vuoto_ok=True), traffic=traffico,
                           setup=costruisci,
                           final_checks=[_verifica(x, f"{w}, verifica finale {j}")
                                         for j, x in enumerate(_lista(lv.get("verifiche_finali"), f"{w}, verifiche_finali"), 1)]))
    return sorted(out, key=lambda lv: lv.n)


def _battute(v, dove: str, personaggi: dict) -> list:
    out = []
    for i, b in enumerate(_lista(v, dove), 1):
        if not isinstance(b, dict) or len(b) != 1:
            raise ErroreContenuti(f"{dove}, battuta {i}: scrivila come «- personaggio: testo»")
        chi, testo = next(iter(b.items()))
        if chi not in personaggi:
            raise ErroreContenuti(f"{dove}, battuta {i}: «{chi}» non è tra i personaggi. "
                                  f"Possibili: {', '.join(personaggi) or 'nessuno (aggiungili in «personaggi»)'}")
        out.append(K.Battuta(chi, _testo(testo, f"{dove}, battuta {i}")))
    return out


def _campagna(dati: dict, dove: str, missioni: list, livelli: list) -> K.Campagna:
    if not dati:
        return K.CAMPAGNA_VUOTA
    val = _mappa(dati.get("valuta"), f"{dove}, valuta")
    valuta = {"nome": _testo(val.get("nome", "buoni"), f"{dove}, valuta, nome"),
              "singolare": _testo(val.get("singolare", val.get("nome", "buono")), f"{dove}, valuta, singolare"),
              "icona": _testo(val.get("icona", "🎟️"), f"{dove}, valuta, icona")}
    personaggi = {}
    for pid, p in _mappa(dati.get("personaggi"), f"{dove}, personaggi").items():
        w = f"{dove}, personaggi, {pid}"
        if not re.fullmatch(r"[a-z0-9_-]{1,20}", str(pid)):
            raise ErroreContenuti(f"{w}: l'id va scritto in minuscolo, senza spazi")
        p = _mappa(p, w)
        personaggi[pid] = K.Personaggio(pid, _testo(p.get("nome"), f"{w}, nome"),
                                        _testo(p.get("ruolo"), f"{w}, ruolo", vuoto_ok=True),
                                        _testo(p.get("icona"), f"{w}, icona", vuoto_ok=True))
    id_missioni = {m.id for m in missioni}
    num_livelli = {lv.n for lv in livelli}
    giorni, numeri = [], set()
    for i, gd in enumerate(_lista(dati.get("giorni"), f"{dove}, giorni"), 1):
        gd = _mappa(gd, f"{dove}, giorno {i}")
        n = _intero(gd.get("numero"), f"{dove}, giorno {i}, numero", 1)
        w = f"{dove}, giorno {n}"
        if n in numeri:
            raise ErroreContenuti(f"{w}: c'è già un altro giorno con questo numero")
        numeri.add(n)
        ms = [_testo(x, f"{w}, missioni") for x in _lista(gd.get("missioni"), f"{w}, missioni")]
        for m in ms:
            if m not in id_missioni:
                raise ErroreContenuti(f"{w}, missioni: «{m}» non è in accademia.yaml. Possibili: {', '.join(id_missioni)}")
        lvs = [_intero(x, f"{w}, livelli", 1) for x in _lista(gd.get("livelli"), f"{w}, livelli")]
        for x in lvs:
            if x not in num_livelli:
                raise ErroreContenuti(f"{w}, livelli: il livello {x} non è in pronto-soccorso.yaml")
        if not ms and not lvs:
            raise ErroreContenuti(f"{w}: servono delle missioni o dei livelli da completare")
        aiuti, ids = [], set()
        for k, a in enumerate(_lista(gd.get("aiuti"), f"{w}, aiuti"), 1):
            a = _mappa(a, f"{w}, aiuto {k}")
            aid = _testo(a.get("id"), f"{w}, aiuto {k}, id")
            wa = f"{w}, aiuto «{aid}»"
            if aid in ids:
                raise ErroreContenuti(f"{wa}: c'è già un altro aiuto con questo id nel giorno")
            ids.add(aid)
            chi = _testo(a.get("collega"), f"{wa}, collega")
            if chi not in personaggi:
                raise ErroreContenuti(f"{wa}, collega: «{chi}» non è tra i personaggi")
            lv = a.get("livello")
            if lv is not None:
                lv = _intero(lv, f"{wa}, livello", 1)
                if lv not in lvs:
                    raise ErroreContenuti(f"{wa}, livello: il livello {lv} non è tra i livelli di questo giorno")
            aiuti.append(K.Aiuto(aid, chi, _intero(a.get("costo", 0), f"{wa}, costo"),
                                 _testo(a.get("richiesta"), f"{wa}, richiesta"), _testo(a.get("risposta"), f"{wa}, risposta"),
                                 [_azione(x, f"{wa}, azione {j}") for j, x in enumerate(_lista(a.get("azioni"), f"{wa}, azioni"), 1)],
                                 lv))
        giorni.append(K.Giorno(n, _testo(gd.get("titolo"), f"{w}, titolo"), ms, lvs,
                               _intero(gd.get("buoni", 0), f"{w}, buoni"),
                               _battute(gd.get("prima"), f"{w}, prima", personaggi),
                               _battute(gd.get("dopo"), f"{w}, dopo", personaggi), aiuti))
    return K.Campagna(valuta, _intero(dati.get("buoni_iniziali", 0), f"{dove}, buoni_iniziali"), personaggi,
                      sorted(giorni, key=lambda g: g.n), _battute(dati.get("finale"), f"{dove}, finale", personaggi))


def carica(cartella: Path = CARTELLA) -> Pacchetto:
    """Legge e valida il pacchetto di gioco. ErroreContenuti con file e punto esatto se qualcosa non va."""
    dati = {}
    for nome in FILE:
        p = cartella / nome
        if not p.exists():
            dati[nome] = {}
            continue
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            dove = f", riga {mark.line + 1}, colonna {mark.column + 1}" if mark else ""
            raise ErroreContenuti(f"{nome}{dove}: il file non è YAML valido ({getattr(e, 'problem', e)}). "
                                  f"Controlla rientri, due punti e virgolette.")
        dati[nome] = _mappa(d, nome)
    acc, soc, cam = dati["accademia.yaml"], dati["pronto-soccorso.yaml"], dati["campagna.yaml"]
    missioni, livelli = _missioni(acc, "accademia.yaml", cartella), _livelli(soc, "pronto-soccorso.yaml", cartella)
    return Pacchetto(
        impostazioni=_impostazioni(dati["impostazioni.yaml"], "impostazioni.yaml"),
        interfaccia={**dati["interfaccia.yaml"], "storia": dati["storia.yaml"]},
        missioni=missioni,
        livelli=livelli,
        msg_accademia=_messaggi(acc.get("messaggi"), "accademia.yaml, messaggi", MESSAGGI_ACCADEMIA),
        msg_soccorso=_messaggi(soc.get("messaggi"), "pronto-soccorso.yaml, messaggi", MESSAGGI_SOCCORSO),
        campagna=_campagna(cam, "campagna.yaml", missioni, livelli),
        msg_campagna=_messaggi(cam.get("messaggi"), "campagna.yaml, messaggi", MESSAGGI_CAMPAGNA),
    )


class Contenuti:
    """Il pacchetto di gioco corrente, ricaricato da solo quando i file cambiano."""

    def __init__(self, cartella: Path = CARTELLA) -> None:
        self.cartella = cartella
        self.errore = ""
        self._firma: tuple = ()
        self._controllato = 0.0
        self.pacchetto = carica(cartella)  # al primo avvio un errore ferma il server: meglio subito che a metà demo
        self._firma = self._leggi_firma()

    def _leggi_firma(self) -> tuple:
        return tuple((n, (self.cartella / n).stat().st_mtime if (self.cartella / n).exists() else 0) for n in FILE)

    def get(self) -> Pacchetto:
        """Il pacchetto aggiornato. Se un file è cambiato lo rilegge; se ha errori tiene la versione buona."""
        if time.time() - self._controllato < 1:
            return self.pacchetto
        self._controllato = time.time()
        firma = self._leggi_firma()
        if firma != self._firma:
            self._firma = firma
            try:
                self.pacchetto = carica(self.cartella)
                self.errore = ""
                print(f"  Pacchetto di gioco ricaricato da {self.cartella}", flush=True)
            except ErroreContenuti as e:
                self.errore = str(e)
                print(f"  ERRORE nel pacchetto di gioco (resta la versione precedente): {e}", flush=True)
        return self.pacchetto
