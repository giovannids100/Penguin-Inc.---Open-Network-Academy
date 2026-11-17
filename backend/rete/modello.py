"""Il modello della rete: dispositivi, porte, cavi e configurazioni. È la fonte di verità del gioco.

La console (console.py) e gli endpoint cambiano il modello; kernel.py lo realizza con Mininet (namespace, veth,
bridge, nftables…) e percorso.py lo usa per spiegare dove passa un pacchetto. Da qui escono anche:
  - la fotografia per il gioco (snapshot(), stesso formato del campo «topology» dell'API);
  - le tabelle di routing (rotte connesse, statiche e OSPF calcolate sul grafo dei router);
  - i domini L2 (chi sente chi attraverso cavi, switch e VLAN).
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field

TIPI = ("router", "switch", "pc", "server", "firewall")

# Porte di ogni tipo di dispositivo (nomi completi, come nella console).
PORTE_TIPO = {
    "router": [f"GigabitEthernet0/{i}" for i in range(3)],
    "switch": [f"FastEthernet0/{i}" for i in range(1, 25)] + ["GigabitEthernet0/1", "GigabitEthernet0/2"],
    "pc": ["FastEthernet0"],
    "server": ["FastEthernet0"],
    "firewall": [f"GigabitEthernet1/{i}" for i in range(1, 9)],
}
MODELLO = {"router": "Router", "switch": "Switch", "pc": "PC", "server": "Server", "firewall": "Firewall"}
# Le porte di router e firewall nascono spente (come sui router veri); quelle di switch e host accese.
SPENTE_DI_BASE = ("router", "firewall")
FAMIGLIE = (("GigabitEthernet", "gi"), ("FastEthernet", "fa"), ("Ethernet", "eth"), ("Serial", "se"))
SERVIZI = ("http", "https", "ftp", "smtp", "pop3")
ZERO = ("", "0.0.0.0")


def nome_kernel(porta: str) -> str:
    """Nome dell'interfaccia nel kernel (max 15 caratteri, niente «/»): GigabitEthernet0/1.10 → gi0-1.10."""
    for lungo, corto in FAMIGLIE:
        if porta.startswith(lungo):
            return corto + porta[len(lungo):].replace("/", "-")
    return re.sub(r"[^A-Za-z0-9.-]", "-", porta)[:15]


def espandi_porta(testo: str, porte: list[str]) -> str | None:
    """Abbreviazioni della console → nome completo: g0/1, gi0/1, gig 0/1, fa0/1, f0/1, s2/0 …"""
    t = testo.replace(" ", "")
    m = re.match(r"^([A-Za-z-]+)([\d/.]+)$", t)
    if not m:
        return None
    pref, num = m.group(1).lower(), m.group(2)
    for lungo, _ in FAMIGLIE:
        if lungo.lower().startswith(pref) and len(pref) >= 1:
            cand = lungo + num
            base = cand.split(".")[0]
            if base in porte:
                return cand
    return None


@dataclass
class Porta:
    nome: str
    ip: str = ""
    mask: str = ""
    shutdown: bool = False
    # switch
    vlan: int = 1
    trunk: bool = False
    trunk_vlan: list[int] | None = None       # None = tutte
    nativa: int = 1
    psec: dict | None = None                  # {"max", "violation", "sticky"}
    # router
    dot1q: int | None = None                  # sottointerfaccia con encapsulation dot1Q N
    acl_in: str = ""
    acl_out: str = ""
    policy_out: str = ""
    ospf_p2p: bool = False
    ospf_costo: int = 1
    standby: dict = field(default_factory=dict)   # gruppo HSRP → {"ip", "priority", "preempt"}
    # firewall
    nameif: str = ""
    livello: int | None = None
    descrizione: str = ""

    def interfaccia(self) -> ipaddress.IPv4Interface | None:
        if self.ip in ZERO or self.mask in ZERO:
            return None
        try:
            return ipaddress.IPv4Interface(f"{self.ip}/{self.mask}")
        except ValueError:
            return None


@dataclass
class Dispositivo:
    nome: str
    tipo: str
    x: int = 300
    y: int = 300
    acceso: bool = True
    porte: dict[str, Porta] = field(default_factory=dict)
    # PC e server
    gw: str = ""
    dns: str = ""
    dhcp: bool = False
    servizi: dict = field(default_factory=dict)       # http, https, ftp, smtp, pop3 → bool
    dns_server: dict = field(default_factory=lambda: {"on": False, "records": {}})
    dominio_email: str = ""
    # router, switch, firewall
    hostname: str = ""
    rotte: list[list[str]] = field(default_factory=list)         # [rete, maschera, via] (firewall: [zona, rete, maschera, via])
    ospf: dict = field(default_factory=dict)                     # pid → {"router_id", "reti": [[ip, wildcard, area]], "passive": []}
    pool: dict = field(default_factory=dict)                     # nome → {"net", "mask", "gw", "dns"}
    esclusi: list[list[str]] = field(default_factory=list)       # [[da, a]]
    acl: dict = field(default_factory=dict)                      # id → {"estesa": bool, "righe": [testo]}
    access_group: dict = field(default_factory=dict)             # firewall: zona → nome ACL (in ingresso)
    vlan: dict = field(default_factory=lambda: {"1": "default"})  # switch: id → nome
    utenti: dict = field(default_factory=dict)                   # nome → {"privilege", "tipo": "password"|"secret", "valore"}
    enable_secret: str = ""
    enable_password: str = ""
    cifra_password: bool = False
    banner: str = ""
    dominio: str = ""
    ssh: bool = False
    linee: dict = field(default_factory=lambda: {"con": {}, "vty": {}})  # {"login": "", "local", "password", "transport"}
    policy: dict = field(default_factory=dict)                   # nome → kbps (shape average)
    gateway_switch: str = ""

    def porte_fisiche(self) -> list[Porta]:
        return [p for p in self.porte.values() if "." not in p.nome]

    def porta(self, nome: str) -> Porta | None:
        return self.porte.get(nome)

    def interfacce(self) -> list[tuple[Porta, ipaddress.IPv4Interface]]:
        return [(p, i) for p in self.porte.values() if (i := p.interfaccia())]


@dataclass
class Cavo:
    a: str
    pa: str
    b: str
    pb: str

    def capo(self, dev: str, porta: str) -> tuple[str, str] | None:
        """L'altro capo del cavo visto da (dev, porta), o None se il cavo non parte da lì."""
        if (self.a, self.pa) == (dev, porta):
            return self.b, self.pb
        if (self.b, self.pb) == (dev, porta):
            return self.a, self.pa
        return None


def nuovo_dispositivo(tipo: str, nome: str, x: int = 300, y: int = 300, porte: list[str] | None = None) -> Dispositivo:
    d = Dispositivo(nome=nome, tipo=tipo, x=x, y=y, hostname=nome)
    for p in porte or PORTE_TIPO[tipo]:
        d.porte[p] = Porta(p, shutdown=tipo in SPENTE_DI_BASE)
    if tipo == "server":   # come un server appena installato: web, FTP e posta accesi (vanno spenti se non servono)
        d.servizi = {s: True for s in SERVIZI}
    return d


class Rete:
    """Tutti i dispositivi e i cavi, con le funzioni che li interrogano."""

    def __init__(self) -> None:
        self.disp: dict[str, Dispositivo] = {}
        self.cavi: list[Cavo] = []

    # ───── salvataggio ─────
    def to_dict(self) -> dict:
        return {"dispositivi": [asdict(d) for d in self.disp.values()], "cavi": [asdict(c) for c in self.cavi]}

    @classmethod
    def from_dict(cls, dati: dict) -> "Rete":
        r = cls()
        for dd in dati.get("dispositivi", []):
            porte = {k: Porta(**v) for k, v in dd.pop("porte", {}).items()}
            d = Dispositivo(**dd)
            d.porte = porte
            r.disp[d.nome] = d
        r.cavi = [Cavo(**c) for c in dati.get("cavi", [])]
        return r

    # ───── dispositivi e cavi ─────
    def dev(self, nome: str) -> Dispositivo | None:
        return self.disp.get(nome)

    def aggiungi(self, d: Dispositivo) -> None:
        if d.nome in self.disp:
            raise ValueError(f"esiste già un dispositivo chiamato {d.nome}")
        self.disp[d.nome] = d

    def togli(self, nome: str) -> None:
        self.disp.pop(nome, None)
        self.cavi = [c for c in self.cavi if nome not in (c.a, c.b)]

    def rinomina(self, vecchio: str, nuovo: str) -> None:
        d = self.disp.pop(vecchio)
        d.nome = nuovo
        if d.hostname == vecchio:
            d.hostname = nuovo
        self.disp[nuovo] = d
        for c in self.cavi:
            if c.a == vecchio:
                c.a = nuovo
            if c.b == vecchio:
                c.b = nuovo

    def cavo_di(self, dev: str, porta: str) -> Cavo | None:
        return next((c for c in self.cavi if c.capo(dev, porta)), None)

    def collega(self, a: str, pa: str, b: str, pb: str) -> None:
        for d, p in ((a, pa), (b, pb)):
            if not self.dev(d) or not self.dev(d).porta(p):
                raise ValueError(f"{d} non ha la porta {p}")
            if self.cavo_di(d, p):
                raise ValueError(f"La porta {p} di {d} è già occupata")
        self.cavi.append(Cavo(a, pa, b, pb))

    def scollega(self, dev: str, porta: str) -> None:
        self.cavi = [c for c in self.cavi if not c.capo(dev, porta)]

    def vicino(self, dev: str, porta: str) -> tuple[str, str] | None:
        c = self.cavo_di(dev, porta.split(".")[0])
        return c.capo(dev, porta.split(".")[0]) if c else None

    # ───── stato operativo ─────
    def porta_su(self, dev: str, porta: str) -> bool:
        """La porta passa traffico: dispositivo acceso, porta (e porta fisica) non spenta, cavo verso una porta
        accesa di un dispositivo acceso."""
        d = self.dev(dev)
        if not d or not d.acceso:
            return False
        fis = porta.split(".")[0]
        p, pf = d.porta(porta), d.porta(fis)
        if not p or not pf or p.shutdown or pf.shutdown:
            return False
        if d.tipo == "switch" and pf.psec and pf.psec.get("errdisable"):
            return False
        v = self.vicino(dev, fis)
        if not v:
            return False
        dv = self.dev(v[0])
        pv = dv.porta(v[1]) if dv else None
        return bool(dv and dv.acceso and pv and not pv.shutdown)

    # ───── domini L2 ─────
    def _canali(self, d: Dispositivo, porta: str) -> dict:
        """Cosa porta il cavo su questa porta: {None (senza tag) | vlan: nodo}."""
        if d.tipo == "switch":
            p = d.porte[porta]
            if not p.trunk:
                return {None: ("sw", d.nome, p.vlan)}
            vl = [int(v) for v in d.vlan]
            if p.trunk_vlan is not None:
                vl = [v for v in vl if v in p.trunk_vlan]
            out = {v: ("sw", d.nome, v) for v in vl if v != p.nativa}
            out[None] = ("sw", d.nome, p.nativa)
            return out
        out = {None: ("if", d.nome, porta)}
        if d.tipo == "router":
            for s in d.porte.values():
                if s.nome.startswith(porta + ".") and s.dot1q and not s.shutdown:
                    out[s.dot1q] = ("if", d.nome, s.nome)
        return out

    def domini(self) -> dict:
        """Nodo («if», dev, porta) o («sw», switch, vlan) → identificatore del suo dominio di broadcast."""
        padre: dict = {}

        def trova(x):
            padre.setdefault(x, x)
            while padre[x] != x:
                padre[x] = padre[padre[x]]
                x = padre[x]
            return x

        for d in self.disp.values():
            for p in d.porte.values():
                if d.tipo != "switch":
                    trova(("if", d.nome, p.nome))
        for c in self.cavi:
            if not (self.porta_su(c.a, c.pa) and self.porta_su(c.b, c.pb)):
                continue
            ca, cb = self._canali(self.disp[c.a], c.pa), self._canali(self.disp[c.b], c.pb)
            for tag, na in ca.items():
                if tag in cb:
                    padre[trova(na)] = trova(cb[tag])
        return {k: trova(k) for k in list(padre)}

    def l3(self, dom: dict) -> dict:
        """Dominio → [(dispositivo, porta, interfaccia IPv4)] delle porte L3 attive con un indirizzo."""
        out: dict = {}
        for d in self.disp.values():
            if d.tipo == "switch":
                continue
            for p, i in d.interfacce():
                if self.porta_su(d.nome, p.nome):
                    out.setdefault(dom.get(("if", d.nome, p.nome)), []).append((d.nome, p.nome, i))
        return out

    # ───── HSRP ─────
    def hsrp_attivi(self, dom: dict | None = None) -> dict:
        """(dominio, gruppo) → (router, porta, ip virtuale) del router attivo: priorità più alta, poi IP più alto."""
        dom = dom if dom is not None else self.domini()
        cand: dict = {}
        for d in self.disp.values():
            if d.tipo != "router":
                continue
            for p in d.porte.values():
                if not p.standby or not self.porta_su(d.nome, p.nome) or not p.interfaccia():
                    continue
                for g, s in p.standby.items():
                    if not s.get("ip"):
                        continue
                    k = (dom.get(("if", d.nome, p.nome)), str(g))
                    chiave = (int(s.get("priority", 100)), int(p.interfaccia().ip))
                    if k not in cand or chiave > cand[k][0]:
                        cand[k] = (chiave, (d.nome, p.nome, s["ip"]))
        return {k: v[1] for k, v in cand.items()}

    # ───── routing ─────
    def tabelle(self) -> dict[str, list[dict]]:
        """Tabella di routing di ogni router e firewall: [{"net": IPv4Network, "via", "port", "proto"}].

        C = connessa, S = statica (distanza 1), O = OSPF (110, costo = somma dei costi delle porte).
        Una statica entra solo se il next hop sta in una rete connessa attiva.
        """
        dom = self.domini()
        tab: dict[str, list[dict]] = {}
        conn: dict[str, list[tuple[Porta, ipaddress.IPv4Interface]]] = {}
        for d in self.disp.values():
            if d.tipo not in ("router", "firewall"):
                continue
            conn[d.nome] = [(p, i) for p, i in d.interfacce() if self.porta_su(d.nome, p.nome)
                            and (d.tipo != "firewall" or p.nameif)]
            righe = [{"net": i.network, "via": "", "port": p.nome, "proto": "C"} for p, i in conn[d.nome]]
            for r in d.rotte:
                rete, mask, via = (r[1], r[2], r[3]) if d.tipo == "firewall" else (r[0], r[1], r[2])
                try:
                    net = ipaddress.IPv4Network(f"{rete}/{mask}", strict=False)
                    v = ipaddress.IPv4Address(via)
                except ValueError:
                    continue
                porta = next((p.nome for p, i in conn[d.nome] if v in i.network and v != i.ip), None)
                if porta:
                    righe.append({"net": net, "via": via, "port": porta, "proto": "S"})
            tab[d.nome] = righe
        for nome, righe in self._ospf(dom, conn).items():
            note = {(r["net"]) for r in tab[nome]}
            tab[nome] += [r for r in righe if r["net"] not in note]
        return tab

    def _ospf(self, dom: dict, conn: dict) -> dict[str, list[dict]]:
        """Rotte OSPF: adiacenze tra router con porte nello stesso dominio L2 e nella stessa rete, poi Dijkstra."""
        part: dict[str, list[tuple[Porta, ipaddress.IPv4Interface]]] = {}
        for d in self.disp.values():
            if d.tipo != "router" or not d.ospf or not d.acceso:
                continue
            mie = []
            for p, i in conn.get(d.nome, []):
                for proc in d.ospf.values():
                    for ip, wc, _area in proc.get("reti", []):
                        try:
                            mask = int(ipaddress.IPv4Address(wc)) ^ 0xFFFFFFFF
                            base = int(ipaddress.IPv4Address(ip)) & mask
                        except ValueError:
                            continue
                        if int(i.ip) & mask == base and (p, i) not in mie:
                            mie.append((p, i))
            if mie:
                part[d.nome] = mie
        # adiacenze: (router, porta, ip del vicino, costo)
        adj: dict[str, list[tuple[str, str, str, int]]] = {r: [] for r in part}
        for r, mie in part.items():
            passive = {x for proc in self.disp[r].ospf.values() for x in proc.get("passive", [])}
            for p, i in mie:
                if p.nome in passive:
                    continue
                for r2, mie2 in part.items():
                    if r2 == r:
                        continue
                    passive2 = {x for proc in self.disp[r2].ospf.values() for x in proc.get("passive", [])}
                    for p2, i2 in mie2:
                        if p2.nome in passive2:
                            continue
                        if dom.get(("if", r, p.nome)) == dom.get(("if", r2, p2.nome)) and i.network == i2.network:
                            adj[r].append((r2, p.nome, str(i2.ip), max(1, p.ospf_costo)))
        annunci = {r: [i.network for _, i in mie] for r, mie in part.items()}
        for r in part:   # default-information originate: chi ha una rotta di default la annuncia
            for proc in self.disp[r].ospf.values():
                if proc.get("default") and any(x[0] == "0.0.0.0" and x[1] == "0.0.0.0" for x in self.disp[r].rotte):
                    annunci[r].append(ipaddress.IPv4Network("0.0.0.0/0"))
        out: dict[str, list[dict]] = {}
        for sorg in part:
            dist = {sorg: (0, None, None)}   # router → (costo, porta d'uscita, primo next hop)
            coda = [sorg]
            while coda:
                coda.sort(key=lambda x: dist[x][0])
                u = coda.pop(0)
                for v, porta, via, costo in adj[u]:
                    c = dist[u][0] + costo
                    primo = (porta, via) if u == sorg else (dist[u][1], dist[u][2])
                    if v not in dist or c < dist[v][0]:
                        dist[v] = (c, *primo)
                        coda.append(v)
            righe, viste = [], {i.network for _, i in conn.get(sorg, [])}
            for r, (c, porta, via) in sorted(dist.items(), key=lambda x: x[1][0]):
                if r == sorg:
                    continue
                for net in annunci[r]:
                    if net not in viste:
                        viste.add(net)
                        righe.append({"net": net, "via": via, "port": porta, "proto": "O"})
            out[sorg] = righe
        return out

    @staticmethod
    def cerca(tabella: list[dict], ip: str) -> dict | None:
        """La rotta più specifica per ip (a parità: connessa, statica, OSPF)."""
        a = ipaddress.IPv4Address(ip)
        cand = [r for r in tabella if a in r["net"]]
        if not cand:
            return None
        ordine = {"C": 0, "S": 1, "O": 2}
        return max(cand, key=lambda r: (r["net"].prefixlen, -ordine[r["proto"]]))

    # ───── indirizzi ─────
    def ip_host(self, d: Dispositivo) -> ipaddress.IPv4Interface | None:
        for p in d.porte.values():
            i = p.interfaccia()
            if i:
                return i
        return None

    def proprietario(self, ip: str) -> tuple[str, str] | None:
        """(dispositivo, porta) che ha questo indirizzo (anche come IP virtuale HSRP attivo)."""
        for d in self.disp.values():
            for p, i in d.interfacce():
                if str(i.ip) == ip:
                    return d.nome, p.nome
        for (_, _), (r, p, vip) in self.hsrp_attivi().items():
            if vip == ip:
                return r, p
        return None

    # ───── fotografia per il gioco ─────
    def snapshot(self) -> dict:
        """Dispositivi e cavi nel formato del campo «topology» dell'API (docs/API.md)."""
        tab = self.tabelle()
        devs = []
        for d in self.disp.values():
            ports = []
            for p in d.porte.values():
                fis = p.nome.split(".")[0]
                o = {"n": p.nome, "ip": p.ip, "mask": p.mask, "up": self.porta_su(d.nome, p.nome),
                     "on": not p.shutdown, "link": bool(self.cavo_di(d.nome, fis)) and "." not in p.nome, "vlan": None}
                if d.tipo == "switch":
                    o["vlan"] = "trunk" if p.trunk else p.vlan
                    o["psec"] = bool(p.psec)
                if d.tipo == "router":
                    o["acl_in"], o["acl_out"] = p.acl_in, p.acl_out
                ports.append(o)
            o2 = {"name": d.nome, "model": MODELLO[d.tipo], "kind": d.tipo, "x": d.x, "y": d.y,
                  "power": d.acceso, "ports": ports}
            if d.tipo in ("pc", "server"):
                o2.update(gw=d.gw, dhcp=d.dhcp, dns=d.dns)
            if d.tipo == "router":
                o2["routes"] = [{"net": str(r["net"].network_address), "mask": str(r["net"].netmask),
                                 "via": r["via"], "port": r["port"], "proto": r["proto"]} for r in tab.get(d.nome, [])]
                o2["pools"] = [{"name": n, "net": v.get("net", ""), "mask": v.get("mask", ""), "gw": v.get("gw", ""),
                                "dns": v.get("dns", "")} for n, v in d.pool.items()]
                o2["acls"] = [{"id": k, "extended": v["estesa"], "rules": list(v["righe"])} for k, v in d.acl.items()]
            if d.tipo == "firewall":
                o2["fw"] = {"zones": [{"port": p.nome, "nameif": p.nameif, "level": p.livello}
                                      for p in d.porte.values() if p.nameif],
                            "acls": {k: list(v["righe"]) for k, v in d.acl.items()},
                            "groups": dict(d.access_group),
                            "routes": [{"zone": r[0], "net": r[1], "mask": r[2], "via": r[3]} for r in d.rotte]}
            devs.append(o2)
        links = [{"a": c.a, "pa": c.pa, "b": c.b, "pb": c.pb, "cable": "copper"} for c in self.cavi]
        return {"devices": devs, "links": links}
