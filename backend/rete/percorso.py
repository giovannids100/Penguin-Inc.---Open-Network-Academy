"""La traccia di un ping, passo per passo, calcolata sul modello: per ogni dispositivo da dove entra, da dove
esce e, se viene scartato, perché (la riga dell'ACL, la regola del firewall, la rotta che manca, l'ARP che non
risponde). Le stesse regole le applica kernel.py alla rete vera: il ping vero conferma il verdetto.

Formato (lo stesso di /api/trace, docs/API.md): {"from", "to", "delivered", "blocked_by": {"device", "reason"}
| None, "hops": [{"leg": "andata"|"ritorno", "device", "in", "out", "status": "sent"|"accepted"|"dropped", "note"}]}.
"""
from __future__ import annotations

import ipaddress

from . import acl as A
from .modello import Dispositivo, Rete


class Scarto(Exception):
    def __init__(self, dev: str, motivo: str) -> None:
        super().__init__(motivo)
        self.dev, self.motivo = dev, motivo


def ip_di(rete: Rete, nome: str) -> str:
    """L'indirizzo con cui si raggiunge un dispositivo: quello dell'host, o la prima porta attiva di un router."""
    d = rete.dev(nome)
    if not d:
        return ""
    for p, i in d.interfacce():
        if d.tipo in ("pc", "server") or rete.porta_su(nome, p.nome):
            return str(i.ip)
    return ""


class Tracciatore:
    def __init__(self, rete: Rete) -> None:
        self.rete = rete
        self.dom = rete.domini()
        self.l3 = rete.l3(self.dom)
        self.hsrp = rete.hsrp_attivi(self.dom)
        self.tab = rete.tabelle()
        self.hops: list[dict] = []

    # ───── L2: chi risponde all'ARP e da quali switch passa ─────
    def _arp(self, dev: str, porta: str, ip: str) -> tuple[str, str]:
        dom = self.dom.get(("if", dev, porta))
        for d, p, i in self.l3.get(dom, []):
            if str(i.ip) == ip and (d, p) != (dev, porta):
                return d, p
        for (dm, _), (r, p, vip) in self.hsrp.items():
            if dm == dom and vip == ip:
                return r, p
        raise Scarto(dev, f"ARP: nessuno risponde all'indirizzo {ip} sulla porta {porta} (dispositivo spento, "
                          f"cavo staccato, porta spenta o VLAN diversa)")

    def _switch(self, dev: str, porta: str, arrivo: str, leg: str) -> None:
        """Aggiunge i passi degli switch tra (dev, porta) e il dispositivo «arrivo», nello stesso dominio."""
        dom = self.dom.get(("if", dev, porta))
        visti, coda = {dev}, [(dev, porta.split(".")[0], [])]
        while coda:
            d, p, strada = coda.pop(0)
            v = self.rete.vicino(d, p)
            if not v or not (self.rete.porta_su(d, p) and self.rete.porta_su(*v)):
                continue
            dv = self.rete.dev(v[0])
            if dv.nome == arrivo:
                for h in strada:
                    self.hops.append({"leg": leg, **h})
                return
            if dv.tipo != "switch" or dv.nome in visti:
                continue
            visti.add(dv.nome)
            canali = self.rete._canali(dv, v[1])
            if not any(self.dom.get(n) == dom for n in canali.values()):
                continue
            for q in dv.porte.values():
                if q.nome == v[1]:
                    continue
                if any(self.dom.get(n) == dom for n in self.rete._canali(dv, q.nome).values()):
                    coda.append((dv.nome, q.nome, strada + [{"device": dv.nome, "in": v[1], "out": q.nome,
                                                             "status": "sent", "note": ""}]))

    # ───── L3 ─────
    def _filtro_acl(self, d: Dispositivo, porta: str, verso: str, pkt: dict) -> None:
        nome = getattr(d.porte[porta], f"acl_{verso}")
        if not nome or nome not in d.acl:
            return
        ok, riga = A.valuta(d.acl[nome]["righe"], d.acl[nome]["estesa"], pkt, "router")
        if not ok:
            raise Scarto(d.nome, f"l'ACL {nome} ({'in ingresso' if verso == 'in' else 'in uscita'} su {porta}) "
                                 f"lo scarta: «{riga}»")

    def _firewall(self, d: Dispositivo, pin: str, pout: str, pkt: dict) -> None:
        zi, zo = d.porte[pin], d.porte[pout]
        if not zi.nameif or not zo.nameif:
            raise Scarto(d.nome, f"la porta {pin if not zi.nameif else pout} del firewall non è una zona (manca nameif)")
        nome = d.access_group.get(zi.nameif)
        if nome and nome in d.acl:
            ok, riga = A.valuta(d.acl[nome]["righe"], True, pkt, "fw")
            if not ok:
                raise Scarto(d.nome, f"le regole della zona {zi.nameif} lo scartano: «{riga}»")
            return
        li, lo = zi.livello or 0, zo.livello or 0
        if li <= lo:
            raise Scarto(d.nome, f"dalla zona {zi.nameif} (livello {li}) verso {zo.nameif} (livello {lo}) di base "
                                 f"non si passa: serve una regola")

    def _locale(self, d: Dispositivo, ip: str) -> bool:
        if any(str(i.ip) == ip for _, i in d.interfacce()):
            return True
        return any(r == d.nome and vip == ip for (_, _), (r, _, vip) in self.hsrp.items())

    def _uscita(self, d: Dispositivo, dst: str) -> tuple[str, str]:
        """(porta d'uscita, next hop) da d verso dst. Scarto se non c'è strada."""
        if d.tipo in ("pc", "server"):
            p = next(iter(d.porte.values()))
            i = p.interfaccia()
            if not i:
                raise Scarto(d.nome, f"{d.nome} non ha un indirizzo IP")
            if not self.rete.porta_su(d.nome, p.nome):
                raise Scarto(d.nome, f"la scheda di rete di {d.nome} non è collegata a niente di acceso")
            if ipaddress.IPv4Address(dst) in i.network:
                return p.nome, dst
            if d.gw in ("", "0.0.0.0"):
                raise Scarto(d.nome, f"{dst} è fuori dalla rete di {d.nome} e {d.nome} non ha un gateway")
            return p.nome, d.gw
        r = Rete.cerca(self.tab.get(d.nome, []), dst)
        if not r:
            raise Scarto(d.nome, f"{d.nome} non ha una rotta verso {dst}")
        return r["port"], r["via"] or dst

    def viaggio(self, origine: str, pkt: dict, leg: str) -> str:
        """Porta un pacchetto ({"proto", "src", "dst", "icmp"? | "sport"?, "dport"?}) da «origine» alla sua
        destinazione. Restituisce il dispositivo che lo riceve; Scarto se viene perso."""
        dst_ip = pkt["dst"]
        d, pin = self.rete.dev(origine), None
        for _ in range(32):
            hop = {"leg": leg, "device": d.nome, "in": pin, "out": None, "status": "sent", "note": ""}
            self.hops.append(hop)
            try:
                if pin and d.tipo == "router":
                    self._filtro_acl(d, pin, "in", pkt)
                if pin and self._locale(d, dst_ip):
                    hop["status"] = "accepted"
                    return d.nome
                if pin and d.tipo in ("pc", "server"):
                    raise Scarto(d.nome, f"{d.nome} non inoltra pacchetti destinati ad altri")
                pout, nh = self._uscita(d, dst_ip)
                if d.tipo == "router" and pin:
                    self._filtro_acl(d, pout, "out", pkt)
                if d.tipo == "firewall" and pin:
                    self._firewall(d, pin, pout, pkt)
                hop["out"] = pout
                arrivo, pa = self._arp(d.nome, pout, nh)
            except Scarto as e:
                hop["status"], hop["note"] = "dropped", e.motivo
                raise
            self._switch(d.nome, pout, arrivo, leg)
            d, pin = self.rete.dev(arrivo), pa
        raise Scarto(d.nome, "troppi passaggi: c'è un giro nelle rotte")


PORTE_SERVIZI = {"http": 80, "https": 443, "ftp": 21, "smtp": 25, "pop3": 110}


def in_ascolto(d: Dispositivo, proto: str, porta: int) -> bool:
    """Il dispositivo ha un servizio acceso su quella porta? (i servizi dei server e il loro DNS)"""
    if d.tipo != "server" or not d.acceso:
        return False
    if proto == "udp":
        return porta == 53 and bool((d.dns_server or {}).get("on"))
    return any(on and PORTE_SERVIZI.get(s) == porta for s, on in d.servizi.items())


def _origine(rete: Rete, t: Tracciatore, src: str, dst_ip: str) -> str:
    src_d = rete.dev(src)
    if src_d.tipo in ("pc", "server"):
        i = rete.ip_host(src_d)
        src_ip = str(i.ip) if i else ""
    else:
        r = Rete.cerca(t.tab.get(src, []), dst_ip)
        p = src_d.porte.get(r["port"]) if r else None
        src_ip = p.ip if p else ip_di(rete, src)
    if not src_ip:
        raise Scarto(src, f"{src} non ha un indirizzo IP")
    return src_ip


def traccia(rete: Rete, src: str, dst: str, dst_ip: str = "") -> dict:
    """Ping da src a dst (nomi di dispositivi; oppure dst_ip): andata e ritorno, passo per passo."""
    t = Tracciatore(rete)
    dst_ip = dst_ip or ip_di(rete, dst)
    out = {"from": src, "to": dst, "delivered": False, "blocked_by": None, "hops": t.hops}
    if not dst_ip:
        out["blocked_by"] = {"device": dst, "reason": f"{dst} non ha un indirizzo IP"}
        return out
    try:
        src_ip = _origine(rete, t, src, dst_ip)
        arrivo = t.viaggio(src, {"proto": "icmp", "src": src_ip, "dst": dst_ip, "icmp": "echo"}, "andata")
        t.viaggio(arrivo, {"proto": "icmp", "src": dst_ip, "dst": src_ip, "icmp": "echo-reply"}, "ritorno")
        out["delivered"] = True
    except Scarto as e:
        out["blocked_by"] = {"device": e.dev, "reason": e.motivo}
    return out


def sonda(rete: Rete, src: str, dst_ip: str, porta: int, proto: str = "tcp") -> tuple[str, dict]:
    """Una connessione da src verso dst_ip:porta: ("aperta" | "chiusa" | "filtrata", traccia).

    aperta = la richiesta arriva, qualcuno ascolta e la risposta torna; chiusa = arriva ma lì non ascolta
    nessuno (la risposta di rifiuto torna); filtrata = la richiesta o la risposta si perdono per strada.
    """
    t = Tracciatore(rete)
    out = {"from": src, "to": dst_ip, "delivered": False, "blocked_by": None, "hops": t.hops}
    try:
        src_ip = _origine(rete, t, src, dst_ip)
        arrivo = t.viaggio(src, {"proto": proto, "src": src_ip, "dst": dst_ip, "sport": 49152, "dport": porta}, "andata")
        t.viaggio(arrivo, {"proto": proto, "src": dst_ip, "dst": src_ip, "sport": porta, "dport": 49152}, "ritorno")
        out["delivered"] = True
    except Scarto as e:
        out["blocked_by"] = {"device": e.dev, "reason": e.motivo}
        return "filtrata", out
    return ("aperta" if in_ascolto(rete.dev(arrivo), proto, porta) else "chiusa"), out
