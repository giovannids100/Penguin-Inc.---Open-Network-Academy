"""Livelli del Pronto Soccorso: si costruisce una rete che funziona, la si rompe apposta, il giocatore la ripara.

Ogni guasto ha: come iniettarlo, le verifiche che lo dimostrano (almeno una deve FALLIRE dopo l'iniezione e
tutte devono PASSARE dopo la riparazione), un controllo facoltativo contro le scorciatoie, tre indizi e la
soluzione. Vale qualsiasi riparazione che fa passare le verifiche, purché il ticket sia rispettato.

Le verifiche (classe Check, create con le funzioni ping, blocked, web, web_blocked, audit_clean, survives)
coprono sia i guasti classici («il ping deve passare») sia le falle di sicurezza («questo traffico deve
essere bloccato», «nessuna falla grave», «la rete regge se si spegne R1»). Una falla di sicurezza può non
avere iniezione: la rete di partenza è già insicura, e il giocatore deve proteggerla.

I livelli veri e propri stanno in pronto-soccorso.yaml del pacchetto di gioco (contenuti.py li trasforma
negli oggetti di questo file). Come aggiungere un livello: docs/ARCHITETTURA.md, «Aggiungere un livello».
Questo file non tocca la rete: definisce il motore, il server lo esegue.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable

from rete.topo import Topo

# Controllo anti-scorciatoia: (rispettato, motivo mostrato al giocatore se non lo è).
Require = Callable[[Topo], tuple[bool, str]]

SEVERITY = {"low": 1, "medium": 2, "high": 3}  # gravità delle falle dell'audit
# Traffico di fondo di un livello (visto dallo sniffer): «ping IP», «telnet IP [porta]», «web URL»; IP anche «@PC2».
TRAFFICO_RE = re.compile(r"^(ping [\d.@\w-]+|telnet [\d.@\w-]+( \d{1,5})?|web https?://[\w.@-]+(:\d+)?(/[\w./-]*)?)$")
GRAVITA = {"low": "bassa", "medium": "media", "high": "alta"}


@dataclass
class Check:
    """Una verifica eseguita sulla rete vera. Si crea con le funzioni qui sotto, non a mano.

    kind: ping | blocked | through | limited | web | web_blocked | audit_clean | survives | port | resolve | one | plan
    (resolve: url = nome, dst = "@X", "server" o "resolver"; one: inner, ne deve passare esattamente una;
    plan: device = nome di un piano di missions.PIANI_VERIFICA, che sceglie da solo i dispositivi)
    Le verifiche «funzionali» (ping, web) controllano il traffico legittimo: devono passare già sulla rete
    di partenza, prima dell'iniezione. Le altre controllano la sicurezza e di solito all'inizio falliscono.
    """
    kind: str
    src: str = ""        # dispositivo di partenza (ping, blocked, web, web_blocked)
    dst: str = ""        # IP o "@DISPOSITIVO" per ping; nome di dispositivo per blocked
    url: str = ""        # web, web_blocked
    device: str = ""     # audit_clean, survives
    worst: str = "high"  # audit_clean: gravità a partire dalla quale una falla fa fallire la verifica
    codes: list = field(default_factory=list)  # audit_clean: se indicati, contano solo le falle con questi codici
    at: str = ""         # blocked: se indicato, il pacchetto deve essere scartato proprio da questo dispositivo
    inner: list = field(default_factory=list)  # survives: verifiche da far passare con «device» spento
    port: int = 0        # port: la porta TCP da provare
    open: bool = True    # port: True = deve essere aperta (traffico legittimo), False = chiusa o filtrata
    passa: bool | None = None  # port: True = non filtrata (arriva al server, anche se lì nessuno ascolta),
                               # False = filtrata da un firewall/ACL; None = vale «open»

    @property
    def functional(self) -> bool:
        return self.kind in ("ping", "web", "through", "resolve", "plan") or (self.kind == "port" and self.open)

    def label(self) -> str:
        """Descrizione per chi segue il lavoro (job.step) e per i messaggi."""
        return {
            "ping": f"{self.src} raggiunge {self.dst.lstrip('@')}",
            "blocked": f"{self.src} NON raggiunge {self.dst}" + (f" (bloccato da {self.at})" if self.at else ""),
            "through": f"{self.src} raggiunge {self.dst} passando da {self.at}",
            "web": f"{self.src} apre {self.url}",
            "web_blocked": f"{self.src} NON apre {self.url}",
            "audit_clean": (f"{self.device} senza le falle {', '.join(self.codes)}" if self.codes else
                            f"{self.device} senza falle di gravità {GRAVITA[self.worst]}"
                            + (" o superiore" if self.worst != "high" else "")),
            "survives": f"la rete regge con {self.device} spento",
            "port": f"{self.src} → {self.dst.lstrip('@')}:{self.port} " + (
                "passa" if self.passa is True else "bloccata" if self.passa is False
                else "aperta" if self.open else "non raggiungibile"),
            "resolve": f"{self.src} risolve {self.url}",
            "one": "una sola postazione: " + (self.inner[0].label() if self.inner else ""),
            "plan": f"verifica «{self.device}»",
            "limited": f"il traffico da {self.src} a {self.dst} passa da un limite di velocità",
        }[self.kind]


def ping(src: str, dst: str) -> Check:
    """Il ping da src verso dst (IP o "@DISPOSITIVO") deve passare."""
    return Check("ping", src=src, dst=dst)


def blocked(src: str, dst: str, at: str = "") -> Check:
    """Un ping da src al dispositivo dst NON deve arrivare (verificato con la traccia: /api/trace).

    Con at, deve essere scartato proprio da quel dispositivo (es. il router con l'ACL), non perso per strada.
    """
    return Check("blocked", src=src, dst=dst, at=at)


def through(src: str, dst: str, via: str) -> Check:
    """Un ping da src al dispositivo dst deve arrivare PASSANDO da via (verificato con la traccia): per esempio
    la comunicazione tra due reparti deve attraversare il router, non scorciatoie tra gli switch."""
    return Check("through", src=src, dst=dst, at=via)


def web(src: str, url: str) -> Check:
    """Il browser di src deve ricevere la pagina url."""
    return Check("web", src=src, url=url)


def web_blocked(src: str, url: str) -> Check:
    """Il browser di src NON deve ricevere la pagina url."""
    return Check("web_blocked", src=src, url=url)


def audit_clean(device: str, worst: str = "high", codes: list | None = None) -> Check:
    """L'audit di sicurezza di device non deve trovare falle di gravità worst (low, medium, high) o superiore.

    Con codes conta solo le falle con quei codici (es. ["USER_CREDENTIAL_REVERSIBLE"]), qualunque gravità:
    così due guasti dello stesso livello possono controllare falle diverse dello stesso router.
    """
    return Check("audit_clean", device=device, worst=worst, codes=list(codes or []))


def port(src: str, dst: str, porta: int, aperta: bool) -> Check:
    """Da src, la porta TCP porta di dst (IP o "@DISPOSITIVO") deve essere aperta (aperta=True) oppure no
    (chiusa o filtrata da un firewall). Si prova con lo scanner (/api/scan): una connessione Telnet."""
    return Check("port", src=src, dst=dst, port=porta, open=aperta)


def port_passes(src: str, dst: str, porta: int, passa: bool) -> Check:
    """Da src, il traffico verso la porta TCP porta di dst passa (passa=True: arriva, anche se lì non ascolta
    nessuno, come un SSH «simulato») oppure viene scartato da un firewall (passa=False)."""
    return Check("port", src=src, dst=dst, port=porta, passa=passa)


def resolves(src: str, nome: str, verso: str = "server") -> Check:
    """Da src, «nslookup nome» deve rispondere con l'IP di verso: "@X" (l'IP di X), "server" (un server
    qualsiasi sul tavolo) o "resolver" (il DNS stesso che risponde)."""
    return Check("resolve", src=src, url=nome, dst=verso)


def one_of(*inner: Check) -> Check:
    """Esattamente una delle verifiche inner deve passare (es. una sola postazione entra nella gestione)."""
    return Check("one", inner=list(inner))


def limited(src: str, dst: str) -> Check:
    """Il traffico da src al dispositivo dst arriva e, lungo la strada, esce da una porta di un router con un
    limite di velocità (/api/router/ratelimit). Si controlla con la traccia di un ping e la configurazione."""
    return Check("limited", src=src, dst=dst)


def plan(nome: str) -> Check:
    """Le verifiche di un piano di missions.PIANI_VERIFICA: sceglie i dispositivi sulla rete che c'è (le
    challenge non impongono nomi)."""
    return Check("plan", device=nome)


def survives(device: str, *inner: Check) -> Check:
    """Con device spento, tutte le verifiche inner devono passare (es. HSRP: i PC escono dal router di scorta).

    Il dispositivo viene sempre riacceso alla fine.
    """
    return Check("survives", device=device, inner=list(inner))


@dataclass
class Fault:
    """Un guasto da iniettare nella rete di un livello."""
    id: str
    title: str          # cosa era rotto (mostrato quando è riparato o alla resa)
    symptom: str        # cosa non funziona, detto come lo direbbe un utente
    inject: list[tuple]  # ("cli", dispositivo, [righe della console]) | ("hostip", {"device", "mode"?, "ip", "mask", "gateway"; "@PC1" = IP attuale di PC1}); [] = falla già presente
    pings: list[tuple[str, str]]  # scorciatoia per ping(): (dispositivo sorgente, IP o "@DISPOSITIVO")
    hints: list[str]
    solution: str
    require: Require | None = None
    checks: list[Check] = field(default_factory=list)  # verifiche in più (sicurezza, web, resistenza)

    def all_checks(self) -> list[Check]:
        """Tutte le verifiche del guasto: i ping abbreviati in «pings» e quelle in «checks»."""
        return [ping(s, d) for s, d in self.pings] + self.checks


@dataclass
class Level:
    """Un livello: rete di partenza, ticket, guasti, ping finali e morale."""
    n: int
    title: str
    build: str           # la topologia di partenza: un file .yaml di gioco/reti/ ("" = si costruisce con «setup»)
    ticket: str
    faults: list[Fault]
    final_pings: list[tuple[str, str]] = field(default_factory=list)
    lesson: str = ""
    final_checks: list[Check] = field(default_factory=list)  # controllo finale oltre ai ping
    traffic: list[tuple[str, str]] = field(default_factory=list)  # traffico di fondo per lo sniffer: (dispositivo, comando)
    setup: list = field(default_factory=list)  # azioni che costruiscono (o completano) la rete, dopo la topologia se c'è

    def all_final_checks(self) -> list[Check]:
        return [ping(s, d) for s, d in self.final_pings] + self.final_checks


def host_in(dev: str, net: str, what: str) -> Require:
    """Anti-scorciatoia: l'host dev deve esistere e avere un IP dentro net; altrimenti mostra what."""
    n = ipaddress.IPv4Network(net)

    def req(t: Topo) -> tuple[bool, str]:
        d = t.dev(dev)
        i = t.iface(d) if d else None
        if not d:
            return False, f"{dev} non esiste più: il ticket chiede di ripararlo, non di toglierlo."
        if not i or i.ip not in n:
            return False, f"{what}"
        return True, ""
    return req


def host_ip_is(dev: str, ip: str) -> Require:
    """Anti-scorciatoia: l'host dev deve avere esattamente quell'IP (quello promesso dal ticket)."""
    def req(t: Topo) -> tuple[bool, str]:
        d = t.dev(dev)
        i = t.iface(d) if d else None
        if not i or str(i.ip) != ip:
            return False, f"Il ticket dice che {dev} deve avere l'indirizzo {ip}."
        return True, ""
    return req


def shut(dev: str, port: str) -> tuple:
    """Iniezione pronta: spegne una porta (shutdown)."""
    return ("cli", dev, ["enable", "configure terminal", f"interface {port}", "shutdown", "end"])


# I livelli sono in pronto-soccorso.yaml del pacchetto di gioco: contenuti.py li legge e li trasforma
# negli oggetti Level, Fault e Check qui sopra.


def resolve(t: Topo, target: str) -> str | None:
    """'@PC2' → l'IP che PC2 ha adesso (None se non ne ha); qualsiasi altro valore è già un IP."""
    if not target.startswith("@"):
        return target
    d = t.dev(target[1:])
    i = t.iface(d) if d else None
    return str(i.ip) if i else None
