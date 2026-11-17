"""Regole ACL: dalla riga di testo («permit tcp any host 10.0.0.5 eq 80») a una regola che si confronta con un
pacchetto e che si traduce in nftables.

Due dialetti, che cambiano solo il modo di scrivere le reti:
  - «router» (router e switch): rete + wildcard, «192.168.1.0 0.0.0.255»;
  - «fw» (firewall): rete + maschera, «192.168.1.0 255.255.255.0».
Una regola standard guarda solo l'origine; una estesa protocollo, origine, destinazione e porte.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

PORTE_NOMI = {"ftp-data": 20, "ftp": 21, "ssh": 22, "telnet": 23, "smtp": 25, "domain": 53, "dns": 53,
              "tftp": 69, "www": 80, "http": 80, "pop3": 110, "https": 443}
ICMP_TIPI = ("echo", "echo-reply", "unreachable", "time-exceeded")
PROTOCOLLI = ("ip", "icmp", "tcp", "udp")


class RegolaNonValida(ValueError):
    """Una riga ACL che non si capisce: il messaggio dice cosa non va."""


@dataclass
class Indirizzo:
    rete: ipaddress.IPv4Network      # 0.0.0.0/0 = any

    def __contains__(self, ip: str) -> bool:
        return ipaddress.IPv4Address(ip) in self.rete

    def nft(self) -> str:
        return "" if self.rete.prefixlen == 0 else str(self.rete if self.rete.prefixlen < 32 else self.rete.network_address)


@dataclass
class Porte:
    op: str                          # eq, neq, lt, gt, range
    a: int
    b: int = 0

    def __contains__(self, p: int | None) -> bool:
        if p is None:
            return False
        return {"eq": p == self.a, "neq": p != self.a, "lt": p < self.a, "gt": p > self.a,
                "range": self.a <= p <= self.b}[self.op]

    def nft(self) -> str:
        return {"eq": f"{self.a}", "neq": f"!= {self.a}", "lt": f"< {self.a}", "gt": f"> {self.a}",
                "range": f"{self.a}-{self.b}"}[self.op]


@dataclass
class Regola:
    testo: str
    azione: str                      # permit, deny
    proto: str = "ip"
    src: Indirizzo | None = None
    dst: Indirizzo | None = None
    sport: Porte | None = None
    dport: Porte | None = None
    icmp: str = ""

    def corrisponde(self, p: dict) -> bool:
        """p: {"proto", "src", "dst", "sport"?, "dport"?, "icmp"?} (icmp = echo o echo-reply)."""
        if self.proto != "ip" and self.proto != p["proto"]:
            return False
        if self.src and p["src"] not in self.src:
            return False
        if self.dst and p["dst"] not in self.dst:
            return False
        if self.sport and p.get("sport") not in self.sport:
            return False
        if self.dport and p.get("dport") not in self.dport:
            return False
        if self.icmp and p.get("icmp") != self.icmp:
            return False
        return True

    def nft(self) -> str:
        """La regola in nftables (senza il verdetto)."""
        parti = []
        if self.src and self.src.nft():
            parti.append(f"ip saddr {self.src.nft()}")
        if self.dst and self.dst.nft():
            parti.append(f"ip daddr {self.dst.nft()}")
        if self.proto in ("tcp", "udp"):
            parti.append(f"meta l4proto {self.proto}")
            if self.sport:
                parti.append(f"{self.proto} sport {self.sport.nft()}")
            if self.dport:
                parti.append(f"{self.proto} dport {self.dport.nft()}")
        elif self.proto == "icmp":
            parti.append("meta l4proto icmp")
            if self.icmp in ("echo", "echo-reply"):
                parti.append(f"icmp type {'echo-request' if self.icmp == 'echo' else 'echo-reply'}")
            elif self.icmp == "unreachable":
                parti.append("icmp type destination-unreachable")
            elif self.icmp == "time-exceeded":
                parti.append("icmp type time-exceeded")
        return " ".join(parti)


def _ip(v: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(v)
    except ValueError:
        raise RegolaNonValida(f"«{v}» non è un indirizzo IP")


def _rete(ip: str, m: str, dialetto: str) -> ipaddress.IPv4Network:
    """Rete da indirizzo + wildcard (router) o maschera (fw). Una wildcard non contigua non è ammessa."""
    mm = int(_ip(m))
    if dialetto == "router":
        mm ^= 0xFFFFFFFF
    bits = bin(mm).count("1")
    if mm != (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF:
        raise RegolaNonValida(f"«{m}» non è una {'wildcard' if dialetto == 'router' else 'maschera'} valida")
    return ipaddress.IPv4Network(f"{ip}/{bits}", strict=False)


def _indirizzo(t: list[str], dialetto: str) -> Indirizzo:
    if not t:
        raise RegolaNonValida("manca un indirizzo")
    x = t.pop(0)
    if x == "any":
        return Indirizzo(ipaddress.IPv4Network("0.0.0.0/0"))
    if x == "host":
        if not t:
            raise RegolaNonValida("dopo «host» serve un indirizzo")
        return Indirizzo(ipaddress.IPv4Network(f"{_ip(t.pop(0))}/32"))
    _ip(x)
    if t and t[0].count(".") == 3:
        return Indirizzo(_rete(x, t.pop(0), dialetto))
    return Indirizzo(ipaddress.IPv4Network(f"{x}/32"))


def _porta(v: str) -> int:
    if v in PORTE_NOMI:
        return PORTE_NOMI[v]
    if v.isdigit() and 0 < int(v) < 65536:
        return int(v)
    raise RegolaNonValida(f"porta «{v}» non valida")


def _porte(t: list[str]) -> Porte | None:
    if not t or t[0] not in ("eq", "neq", "lt", "gt", "range"):
        return None
    op = t.pop(0)
    if not t:
        raise RegolaNonValida(f"dopo «{op}» serve una porta")
    a = _porta(t.pop(0))
    if op == "range":
        if not t:
            raise RegolaNonValida("«range» vuole due porte")
        return Porte(op, a, _porta(t.pop(0)))
    return Porte(op, a)


def leggi(testo: str, estesa: bool, dialetto: str = "router") -> Regola:
    """Una riga «permit …» / «deny …» → Regola. RegolaNonValida se non si capisce."""
    t = testo.lower().split()
    if not t or t[0] not in ("permit", "deny"):
        raise RegolaNonValida("una regola comincia con permit o deny")
    r = Regola(" ".join(testo.split()), t.pop(0))
    if not estesa:
        r.src = _indirizzo(t, dialetto)
    else:
        if not t:
            raise RegolaNonValida("manca il protocollo (ip, icmp, tcp, udp)")
        proto = t.pop(0)
        if proto not in PROTOCOLLI:
            raise RegolaNonValida(f"protocollo «{proto}» non gestito: ip, icmp, tcp o udp")
        r.proto = proto
        r.src = _indirizzo(t, dialetto)
        if proto in ("tcp", "udp"):
            r.sport = _porte(t)
        r.dst = _indirizzo(t, dialetto)
        if proto in ("tcp", "udp"):
            r.dport = _porte(t)
        if proto == "icmp" and t and t[0] in ICMP_TIPI:
            r.icmp = t.pop(0)
    t = [x for x in t if x not in ("log", "established")]
    if t:
        raise RegolaNonValida(f"non capisco «{' '.join(t)}»")
    return r


def valuta(righe: list[str], estesa: bool, pacchetto: dict, dialetto: str = "router") -> tuple[bool, str]:
    """Le righe di un'ACL applicate a un pacchetto: (passa, la riga decisiva o «deny implicito»).

    Le righe che non si capiscono si saltano (sono già state rifiutate quando sono state scritte).
    """
    for riga in righe:
        try:
            r = leggi(riga, estesa, dialetto)
        except RegolaNonValida:
            continue
        if r.corrisponde(pacchetto):
            return r.azione == "permit", r.testo
    return False, "deny implicito alla fine dell'ACL"
