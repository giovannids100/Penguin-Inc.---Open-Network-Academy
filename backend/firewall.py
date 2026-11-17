"""Firewall: dalle zone e dalle regole del giocatore alle access-list del firewall.

Il modello per il giocatore è quello di un firewall vero:
  - ogni porta del firewall è una ZONA con un nome (inside, dmz, outside…) e un LIVELLO di sicurezza 0-100;
  - di base una zona può aprire connessioni verso le zone con livello più basso, mai verso quelle più alte
    (la rete interna 100 esce, Internet 0 non entra, la DMZ 50 sta in mezzo);
  - le REGOLE sono le eccezioni: «da outside verso dmz, tcp 80 verso 172.16.0.10: permetti», oppure
    «da inside verso outside, tcp 23: nega»;
  - le risposte alle connessioni permesse tornano indietro da sole.

Il firewall del gioco non tiene lo stato delle connessioni (rete/kernel.py lo realizza con nftables senza
conntrack, rete/percorso.py lo spiega allo stesso modo): una zona con un'access-list scarta tutto ciò che
l'access-list non permette, risposte comprese. Per questo il gioco compila le regole in access-list complete:
per ogni zona una lista «PNA_<zona>» applicata in ingresso con, nell'ordine,
  1. il ping verso il firewall stesso (il gateway della zona) e le risposte alle connessioni che altre zone
     possono aprire verso questa (porta di origine = servizio);
  2. le regole del giocatore che partono da questa zona;
  3. il comportamento di base: permit verso ogni zona con livello più basso.
Le risposte al traffico di base sono limitate ai servizi comuni (RITORNI_TCP/UDP e l'echo-reply del ping).

Questo file non tocca la rete: produce le righe della console del firewall, server.py le esegue.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

PROTOCOLLI = ("ip", "tcp", "udp", "icmp")
RITORNI_TCP = (80, 443, 21, 22, 23, 25, 53, 110)   # servizi le cui risposte tornano per il traffico di base
RITORNI_UDP = (53,)
ACL_PREFISSO = "PNA_"


@dataclass
class Zona:
    nome: str                       # nameif del firewall (inside, dmz, outside…)
    livello: int                    # security-level 0-100
    rete: ipaddress.IPv4Network | None  # rete della porta (None se la porta non ha IP)
    ip: str = ""                    # indirizzo del firewall su quella porta (il gateway della zona)
    # le reti raggiunte attraverso questa porta con le rotte statiche del firewall («route zona rete maschera via»):
    # se il firewall sta tra due router, la zona comprende anche le LAN dietro il router di quel lato
    dietro: tuple = ()

    @property
    def reti(self) -> list[ipaddress.IPv4Network]:
        return ([self.rete] if self.rete is not None else []) + [n for n in self.dietro if n != self.rete]


def indirizzo(v: str, rete: ipaddress.IPv4Network | None) -> str:
    """Indirizzo di una regola → sintassi del firewall. "" = la rete della zona; any; un IP → host; una rete → rete maschera."""
    v = (v or "").strip().lower()
    if not v:
        if rete is None:
            raise ValueError("una zona della regola non ha ancora un indirizzo IP")
        return f"{rete.network_address} {rete.netmask}"
    if v == "any":
        return "any"
    if "/" in v:
        n = ipaddress.IPv4Network(v, strict=False)
        return f"{n.network_address} {n.netmask}"
    return f"host {ipaddress.IPv4Address(v)}"


def valida_regola(r: dict, zone: dict[str, Zona], i: int) -> dict:
    """Controlla una regola dell'API e la normalizza. ValueError con un messaggio per il giocatore."""
    w = f"regola {i}"
    if not isinstance(r, dict):
        raise ValueError(f"{w}: formato non valido")
    for k in ("from", "to"):
        if r.get(k) not in zone:
            raise ValueError(f"{w}: la zona «{r.get(k)}» non esiste sul firewall. Zone: {', '.join(zone) or 'nessuna'}")
    if r["from"] == r["to"]:
        raise ValueError(f"{w}: la zona di partenza e quella di arrivo sono uguali")
    if r.get("action") not in ("permit", "deny"):
        raise ValueError(f"{w}: azione permit o deny")
    proto = str(r.get("protocol", "ip")).lower()
    if proto not in PROTOCOLLI:
        raise ValueError(f"{w}: protocollo tra {', '.join(PROTOCOLLI)}")
    port = r.get("port")
    if port in (None, ""):
        port = None
    else:
        if proto not in ("tcp", "udp"):
            raise ValueError(f"{w}: la porta si indica solo con tcp o udp")
        try:
            port = int(port)
            assert 1 <= port <= 65535
        except Exception:
            raise ValueError(f"{w}: porta non valida (1-65535)")
    out = {"from": r["from"], "to": r["to"], "action": r["action"], "protocol": proto, "port": port,
           "source": str(r.get("source") or "").strip(), "dest": str(r.get("dest") or "").strip()}
    for k in ("source", "dest"):
        try:
            indirizzo(out[k], ipaddress.IPv4Network("0.0.0.0/32"))
        except ValueError:
            raise ValueError(f"{w}: indirizzo «{out[k]}» non valido. Esempi: vuoto (tutta la zona), any, "
                             f"172.16.0.10, 172.16.0.0/24")
    return out


def indirizzi(v: str, z: Zona) -> list[str]:
    """Come indirizzo(), ma "" vale tutte le reti della zona: quella della porta e quelle dietro (rotte del firewall)."""
    if (v or "").strip():
        return [indirizzo(v, z.rete)]
    if not z.reti:
        raise ValueError("una zona della regola non ha ancora un indirizzo IP")
    return [indirizzo("", n) for n in z.reti]


def compila(zone: dict[str, Zona], regole: list[dict]) -> dict[str, list[str]]:
    """Zone e regole (già validate) → {zona: righe ACL «permit/deny …»} in sintassi del firewall, nell'ordine giusto."""
    ritorni: dict[str, list[str]] = {z: [] for z in zone}
    proprie: dict[str, list[str]] = {z: [] for z in zone}
    for r in regole:
        zf, zt = zone[r["from"]], zone[r["to"]]
        porta = f" eq {r['port']}" if r["port"] else ""
        for src in indirizzi(r["source"], zf):
            for dst in indirizzi(r["dest"], zt):
                proprie[zf.nome].append(f"{r['action']} {r['protocol']} {src} {dst}{porta}")
                if r["action"] == "permit":   # la risposta: dalla destinazione all'origine, con la porta come origine
                    if r["protocol"] == "icmp":
                        ritorni[zt.nome].append(f"permit icmp {dst} {src} echo-reply")
                    elif r["protocol"] in ("tcp", "udp") and r["port"]:
                        ritorni[zt.nome].append(f"permit {r['protocol']} {dst}{porta} {src}")
                    else:
                        ritorni[zt.nome].append(f"permit {r['protocol']} {dst} {src}")
    base: dict[str, list[str]] = {z: [] for z in zone}
    for z in zone.values():   # ogni zona può fare ping al suo gateway (il firewall stesso)
        if z.rete is not None and z.ip:
            ritorni[z.nome].insert(0, f"permit icmp {indirizzo('', z.rete)} host {z.ip}")
    for a in zone.values():
        for b in zone.values():
            if a.nome == b.nome or a.livello <= b.livello or not a.reti or not b.reti:
                continue
            for na in indirizzi("", a):
                for nb in indirizzi("", b):
                    base[a.nome].append(f"permit ip {na} {nb}")          # da più alto a più basso: di base sì
                    for p in RITORNI_TCP:
                        ritorni[b.nome].append(f"permit tcp {nb} eq {p} {na}")
                    for p in RITORNI_UDP:
                        ritorni[b.nome].append(f"permit udp {nb} eq {p} {na}")
                    ritorni[b.nome].append(f"permit icmp {nb} {na} echo-reply")
    out = {}
    for z in zone:
        righe, visti = [], set()
        for x in ritorni[z] + proprie[z] + base[z]:   # niente doppioni, l'ordine resta
            if x not in visti:
                visti.add(x)
                righe.append(x)
        out[z] = righe
    return out
