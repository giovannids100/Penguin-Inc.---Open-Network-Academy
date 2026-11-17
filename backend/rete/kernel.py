"""La rete vera: realizza il modello con Mininet e gli strumenti di Linux.

  - ogni dispositivo è un nodo Mininet (un namespace di rete con la sua shell);
  - ogni cavo è una coppia veth; le porte si chiamano come in console, accorciate (gi0-1, fa0-24);
  - switch: un bridge Linux con le VLAN (vlan_filtering), porte access e trunk;
  - router: inoltro IP, sottointerfacce 802.1Q, rotte statiche e OSPF (calcolate dal modello), ACL in
    nftables, limiti di velocità con tc, DHCP con dnsmasq, IP virtuale HSRP sul router attivo;
  - firewall: zone con livelli di sicurezza e access-group in nftables (senza stato: le risposte le permettono le regole, vedi firewall.py);
  - server: i servizi (web, FTP, posta) con strumenti.py, il DNS con dnsmasq.

sync() confronta il modello con quello che c'è e rifà solo i dispositivi cambiati. Tutto gira dentro il user
namespace del server (vedi entra_nel_namespace()): nessun privilegio sull'host.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import acl as A
from .modello import Dispositivo, Rete, nome_kernel

QUI = Path(__file__).parent
TMP = Path(os.environ.get("PNA_TMP", "/tmp/pna"))
STRUMENTI = [sys.executable, "-I", str(QUI / "strumenti.py")]
PROTO_ROTTE = "99"   # le rotte scritte dal gioco (statiche e OSPF), per toglierle senza toccare le connesse


def disponibile() -> tuple[bool, str]:
    """La rete vera si può usare? (sì/no, perché)."""
    try:
        import mininet.node  # noqa: F401
    except ImportError:
        return False, "Mininet non è installato: si gioca con la rete simulata"
    for prog in ("ip", "nft", "tc", "dnsmasq", "tcpdump", "mnexec"):
        if not shutil.which(prog):
            return False, f"manca il programma «{prog}»: si gioca con la rete simulata"
    if os.geteuid() != 0:
        return False, "il server non è nel suo user namespace: si gioca con la rete simulata"
    return True, ""


def entra_nel_namespace() -> bool:
    """Diventa root in un user namespace nuovo, con una rete e dei mount tutti suoi. Va chiamata all'avvio,
    quando il processo ha un solo thread e DOPO aver aperto la porta HTTP (il socket resta nella rete di prima).

    Le capability valgono solo dentro il namespace: sull'host il server resta un utente normale.
    """
    if not hasattr(os, "unshare"):   # macOS, Windows o Python < 3.12: si gioca con la rete simulata
        return False
    if os.geteuid() == 0:
        return True
    uid, gid = os.getuid(), os.getgid()
    try:
        os.unshare(os.CLONE_NEWUSER)
        Path("/proc/self/setgroups").write_text("deny")
        Path("/proc/self/uid_map").write_text(f"0 {uid} 1")
        Path("/proc/self/gid_map").write_text(f"0 {gid} 1")
        os.unshare(os.CLONE_NEWNET | os.CLONE_NEWNS)
        subprocess.run(["ip", "link", "set", "lo", "up"], check=False)
        return True
    except OSError as e:
        print(f"  user namespace non disponibile ({e}): si gioca con la rete simulata", flush=True)
        return False


def _firma(x) -> str:
    return hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _catena(nome: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", nome)


def intervalli_dhcp(net: ipaddress.IPv4Network, esclusi: list[tuple[int, int]]) -> list[tuple[str, str]]:
    """Gli indirizzi assegnabili di una rete meno gli esclusi, come intervalli [(da, a)]."""
    out, inizio = [], None
    for h in net.hosts():
        v = int(h)
        libero = not any(a <= v <= b for a, b in esclusi)
        if libero and inizio is None:
            inizio = v
        if not libero and inizio is not None:
            out.append((inizio, v - 1))
            inizio = None
    if inizio is not None:
        out.append((inizio, int(net.broadcast_address) - 1))
    return [(str(ipaddress.IPv4Address(a)), str(ipaddress.IPv4Address(b))) for a, b in out]


class Kernel:
    def __init__(self) -> None:
        from mininet.node import Node
        self.Node = Node
        self.nodi: dict[str, object] = {}         # dispositivo → nodo Mininet
        self.cavi: dict[tuple, tuple] = {}        # (a, pa, b, pb) → (if_a, if_b)
        self.firme: dict[str, str] = {}
        self.processi: dict[tuple[str, str], subprocess.Popen] = {}
        self.firme_proc: dict[tuple[str, str], str] = {}
        self.contatore = 0
        TMP.mkdir(parents=True, exist_ok=True)

    # ───── esecuzione ─────
    def esegui(self, dev: str, cmd: list[str] | str, timeout: float = 30) -> tuple[int, str]:
        """Un comando nel namespace del dispositivo: (codice d'uscita, output)."""
        n = self.nodi[dev]
        if isinstance(cmd, str):
            cmd = ["bash", "-c", cmd]
        p = n.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
            return 124, out or ""
        return p.returncode, out or ""

    def avvia(self, dev: str, nome: str, cmd: list[str], firma: str) -> None:
        """Un programma che resta acceso nel dispositivo (dnsmasq, servizi). Lo riavvia se la firma è cambiata."""
        k = (dev, nome)
        if self.firme_proc.get(k) == firma and k in self.processi and self.processi[k].poll() is None:
            return
        self.ferma(dev, nome)
        log = open(TMP / f"{_catena(dev)}-{nome}.log", "w")
        self.processi[k] = self.nodi[dev].popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        self.firme_proc[k] = firma

    def ferma(self, dev: str, nome: str | None = None) -> None:
        for k in [k for k in self.processi if k[0] == dev and (nome is None or k[1] == nome)]:
            p = self.processi.pop(k)
            self.firme_proc.pop(k, None)
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(3)
                except subprocess.TimeoutExpired:
                    p.kill()

    # ───── nodi e cavi ─────
    def _nodo(self, d: Dispositivo) -> None:
        self.contatore += 1
        n = self.Node(f"n{self.contatore}", inNamespace=True)
        self.nodi[d.nome] = n
        inoltro = "1" if d.tipo in ("router", "firewall") else "0"
        self.esegui(d.nome, f"ip link set lo up; sysctl -qw net.ipv4.ip_forward={inoltro}; "
                            f"sysctl -qw net.ipv4.conf.all.rp_filter=0; sysctl -qw net.ipv4.icmp_echo_ignore_broadcasts=1")
        if d.tipo == "switch":
            self.esegui(d.nome, "ip link add br0 type bridge vlan_filtering 1 stp_state 0 && ip link set br0 up")

    def togli_nodo(self, nome: str) -> None:
        self.ferma(nome)
        n = self.nodi.pop(nome, None)
        self.firme.pop(nome, None)
        for k in [k for k in self.cavi if nome in (k[0], k[2])]:
            self.cavi.pop(k)
        if n is not None:
            try:
                n.terminate()
            except Exception:
                pass

    def rinomina(self, vecchio: str, nuovo: str) -> None:
        if vecchio in self.nodi:
            self.nodi[nuovo] = self.nodi.pop(vecchio)
        self.firme.pop(vecchio, None)
        for k in list(self.processi):
            if k[0] == vecchio:
                self.processi[(nuovo, k[1])] = self.processi.pop(k)
                self.firme_proc[(nuovo, k[1])] = self.firme_proc.pop(k, "")
        for k in list(self.cavi):
            if vecchio in (k[0], k[2]):
                v = self.cavi.pop(k)
                self.cavi[(nuovo if k[0] == vecchio else k[0], k[1], nuovo if k[2] == vecchio else k[2], k[3])] = v

    def _cavo(self, k: tuple) -> None:
        a, pa, b, pb = k
        self.contatore += 1
        ta, tb = f"va{self.contatore}", f"vb{self.contatore}"
        ia, ib = nome_kernel(pa), nome_kernel(pb)
        subprocess.run(["ip", "link", "add", ta, "type", "veth", "peer", "name", tb], check=True)
        subprocess.run(["ip", "link", "set", ta, "netns", str(self.nodi[a].pid)], check=True)
        subprocess.run(["ip", "link", "set", tb, "netns", str(self.nodi[b].pid)], check=True)
        self.esegui(a, ["ip", "link", "set", ta, "name", ia])
        self.esegui(b, ["ip", "link", "set", tb, "name", ib])
        self.cavi[k] = (ia, ib)
        self.firme.pop(a, None)
        self.firme.pop(b, None)

    def _stacca(self, k: tuple) -> None:
        ia, _ = self.cavi.pop(k)
        if k[0] in self.nodi:
            self.esegui(k[0], ["ip", "link", "del", ia])
        self.firme.pop(k[0], None)
        self.firme.pop(k[2], None)

    # ───── allineamento ─────
    def sync(self, rete: Rete, forza: bool = False) -> bool:
        """Porta la rete vera allo stato del modello. True se è cambiato qualcosa."""
        cambiato = False
        for nome in [n for n in self.nodi if n not in rete.disp]:
            self.togli_nodo(nome)
            cambiato = True
        for d in rete.disp.values():
            if d.nome not in self.nodi:
                self._nodo(d)
                cambiato = True
        voluti = {(c.a, c.pa, c.b, c.pb) for c in rete.cavi}
        for k in [k for k in self.cavi if k not in voluti]:
            self._stacca(k)
            cambiato = True
        for k in voluti - set(self.cavi):
            self._cavo(k)
            cambiato = True
        tab = rete.tabelle()
        hsrp = rete.hsrp_attivi()
        modello = {x["nome"]: x for x in rete.to_dict()["dispositivi"]}
        for d in rete.disp.values():
            stato = {"d": modello[d.nome],
                     "cavi": sorted(v for k, v in self._porte_cablate(d.nome).items()),
                     "rotte": [(str(r["net"]), r["via"], r["port"], r["proto"]) for r in tab.get(d.nome, [])],
                     "hsrp": sorted(str(x) for x in hsrp.values() if x[0] == d.nome)}
            f = _firma(stato)
            if forza or self.firme.get(d.nome) != f:
                self._configura(rete, d, tab.get(d.nome, []), [x for x in hsrp.values() if x[0] == d.nome])
                self.firme[d.nome] = f
                cambiato = True
        if cambiato:
            for d in rete.disp.values():
                if d.tipo in ("pc", "server"):
                    self.esegui(d.nome, "ip neigh flush all", 5)
            # per chi fa debug: nsenter -t 1 -U -n -m --preserve-credentials nsenter -t <pid> -n …
            (TMP / "nodi.json").write_text(json.dumps({k: n.pid for k, n in self.nodi.items()}, indent=1))
        return cambiato

    def _porte_cablate(self, dev: str) -> dict[str, str]:
        """Porta del modello → nome dell'interfaccia nel kernel, per le porte con un cavo."""
        out = {}
        for (a, pa, b, pb), (ia, ib) in self.cavi.items():
            if a == dev:
                out[pa] = ia
            if b == dev:
                out[pb] = ib
        return out

    def _configura(self, rete: Rete, d: Dispositivo, rotte: list[dict], hsrp: list[tuple]) -> None:
        cab = self._porte_cablate(d.nome)
        s = ["set +e", "ip neigh flush all 2>/dev/null"]
        if d.tipo == "switch":
            s += self._switch(d, cab)
        else:
            s += self._porte_l3(d, cab, hsrp)
            if d.tipo in ("pc", "server"):
                s += self._host(d)
            else:
                s += [f"ip route flush proto {PROTO_ROTTE}"]
                for r in rotte:
                    if r["proto"] != "C" and r["port"].split(".")[0] in cab:
                        s.append(f"ip route replace {r['net']} via {r['via']} dev {nome_kernel(r['port'])} proto {PROTO_ROTTE}")
        script = TMP / f"{_catena(d.nome)}.sh"
        script.write_text("\n".join(s) + "\n")
        self.esegui(d.nome, ["bash", str(script)], 30)
        if d.tipo == "router":
            self._nft(d.nome, self._nft_router(d, cab))
            self._tc(d, cab)
            self._dhcp_server(d, cab)
        elif d.tipo == "firewall":
            self._nft(d.nome, self._nft_firewall(d, cab))
        elif d.tipo == "server":
            self._servizi(d)
        if not d.acceso:
            self.ferma(d.nome)

    def _porte_l3(self, d: Dispositivo, cab: dict, hsrp: list[tuple]) -> list[str]:
        s = []
        if d.tipo == "router":   # sottointerfacce: si rifanno da zero
            s.append("for i in $(ip -o link show type vlan | awk -F': ' '{print $2}' | cut -d@ -f1); do ip link del $i; done")
        for p in d.porte.values():
            fis = p.nome.split(".")[0]
            if fis not in cab:
                continue
            k = nome_kernel(p.nome)
            if "." in p.nome:
                if not p.dot1q:
                    continue
                s.append(f"ip link add link {nome_kernel(fis)} name {k} type vlan id {p.dot1q}")
            su = d.acceso and not p.shutdown and not d.porte[fis].shutdown
            s.append(f"ip addr flush dev {k}")
            if p.ip and p.mask:
                pref = ipaddress.IPv4Network(f"0.0.0.0/{p.mask}").prefixlen
                s.append(f"ip addr add {p.ip}/{pref} dev {k}")
            for r, porta, vip in hsrp:
                if porta == p.nome and p.mask:
                    pref = ipaddress.IPv4Network(f"0.0.0.0/{p.mask}").prefixlen
                    s.append(f"ip addr add {vip}/{pref} dev {k}")
            s.append(f"ip link set {k} {'up' if su else 'down'}")
        return s

    def _host(self, d: Dispositivo) -> list[str]:
        s = ["ip route del default 2>/dev/null"]
        if d.gw not in ("", "0.0.0.0"):
            s.append(f"ip route add default via {d.gw} 2>/dev/null")
        return s

    def _switch(self, d: Dispositivo, cab: dict) -> list[str]:
        s = []
        for p in d.porte.values():
            if p.nome not in cab:
                continue
            k = cab[p.nome]
            su = d.acceso and not p.shutdown and not (p.psec or {}).get("errdisable")
            s += [f"ip link set {k} master br0", f"bridge vlan del dev {k} vid 1-4094 2>/dev/null"]
            if p.trunk:
                vl = [int(v) for v in d.vlan]
                if p.trunk_vlan is not None:
                    vl = [v for v in vl if v in p.trunk_vlan]
                for v in vl:
                    if v != p.nativa:
                        s.append(f"bridge vlan add dev {k} vid {v}")
                s.append(f"bridge vlan add dev {k} vid {p.nativa} pvid untagged")
            else:
                s.append(f"bridge vlan add dev {k} vid {p.vlan} pvid untagged")
            s.append(f"ip link set {k} {'up' if su else 'down'}")
        return s

    # ───── nftables ─────
    def _nft(self, dev: str, regole: str) -> None:
        f = TMP / f"{_catena(dev)}.nft"
        f.write_text("flush ruleset\n" + regole)
        rc, out = self.esegui(dev, ["nft", "-f", str(f)])
        if rc != 0:
            print(f"  nft su {dev}: {out[:300]}", flush=True)

    @staticmethod
    def _regole_acl(nome: str, v: dict, dialetto: str, permetti: str) -> list[str]:
        out = [f"  chain acl_{_catena(nome)} {{"]
        for riga in v["righe"]:
            try:
                r = A.leggi(riga, v["estesa"], dialetto)
            except A.RegolaNonValida:
                continue
            out.append(f"    {r.nft()} {permetti if r.azione == 'permit' else 'drop'}")
        out += ["    drop", "  }"]
        return out

    def _nft_router(self, d: Dispositivo, cab: dict) -> str:
        usate = {x for p in d.porte.values() for x in (p.acl_in, p.acl_out) if x and x in d.acl}
        r = ["table ip acl {"]
        for nome in usate:
            r += self._regole_acl(nome, d.acl[nome], "router", "return")
        fwd, inp = [], []
        for p in d.porte.values():
            if p.nome.split(".")[0] not in cab:
                continue
            k = nome_kernel(p.nome)
            if p.acl_in in usate:
                fwd.append(f'    iifname "{k}" jump acl_{_catena(p.acl_in)}')
                inp.append(f'    iifname "{k}" jump acl_{_catena(p.acl_in)}')
            if p.acl_out in usate:
                fwd.append(f'    oifname "{k}" jump acl_{_catena(p.acl_out)}')
        r += ["  chain inoltro {", "    type filter hook forward priority 0; policy accept;", *fwd, "  }",
              "  chain ingresso {", "    type filter hook input priority 0; policy accept;", *inp, "  }", "}"]
        return "\n".join(r) + "\n"

    def _nft_firewall(self, d: Dispositivo, cab: dict) -> str:
        zone = [p for p in d.porte.values() if p.nameif and p.nome in cab]
        r = ["table ip fw {"]
        for nome in {g for g in d.access_group.values() if g in d.acl}:
            r += self._regole_acl(nome, d.acl[nome], "fw", "accept")
        fwd = []
        for a in zone:
            for b in zone:
                if a is b:
                    continue
                ka, kb = nome_kernel(a.nome), nome_kernel(b.nome)
                g = d.access_group.get(a.nameif)
                if g and g in d.acl:
                    fwd.append(f'    iifname "{ka}" oifname "{kb}" jump acl_{_catena(g)}')
                elif (a.livello or 0) > (b.livello or 0):
                    fwd.append(f'    iifname "{ka}" oifname "{kb}" accept')
        r += ["  chain inoltro {", "    type filter hook forward priority 0; policy drop;", *fwd, "  }", "}"]
        return "\n".join(r) + "\n"

    def _tc(self, d: Dispositivo, cab: dict) -> None:
        s = []
        for p in d.porte.values():
            if p.nome not in cab:
                continue
            k = nome_kernel(p.nome)
            s.append(f"tc qdisc del dev {k} root 2>/dev/null")
            kbps = d.policy.get(p.policy_out, 0) if p.policy_out else 0
            if kbps:
                s.append(f"tc qdisc add dev {k} root tbf rate {kbps}kbit burst 16kb latency 400ms")
        if s:
            self.esegui(d.nome, "; ".join(s))

    # ───── demoni ─────
    def _dhcp_server(self, d: Dispositivo, cab: dict) -> None:
        if not d.acceso or not d.pool:
            self.ferma(d.nome, "dhcp")
            return
        esclusi = []
        for da, a in d.esclusi:
            try:
                esclusi.append((int(ipaddress.IPv4Address(da)), int(ipaddress.IPv4Address(a))))
            except ValueError:
                pass
        conf, ifaces = [], []
        for p, i in d.interfacce():
            if p.nome.split(".")[0] in cab:
                ifaces.append(nome_kernel(p.nome))
                esclusi.append((int(i.ip), int(i.ip)))
        for nome, pl in d.pool.items():
            try:
                net = ipaddress.IPv4Network(f"{pl['net']}/{pl['mask']}")
            except ValueError:
                continue
            tag = _catena(nome)
            for a, b in intervalli_dhcp(net, esclusi):
                conf.append(f"dhcp-range=set:{tag},{a},{b},{net.netmask},1h")
            if pl.get("gw"):
                conf.append(f"dhcp-option=tag:{tag},3,{pl['gw']}")
            if pl.get("dns"):
                conf.append(f"dhcp-option=tag:{tag},6,{pl['dns']}")
        if not conf or not ifaces:
            self.ferma(d.nome, "dhcp")
            return
        base = TMP / _catena(d.nome)
        conf = ["port=0", "bind-interfaces", "dhcp-authoritative", "no-resolv", "no-hosts",
                f"dhcp-leasefile={base}.leases", *[f"interface={k}" for k in ifaces], *conf]
        f = Path(f"{base}-dhcp.conf")
        f.write_text("\n".join(conf) + "\n")
        self.avvia(d.nome, "dhcp", ["dnsmasq", "--no-daemon", f"--conf-file={f}"], _firma(conf))

    def _servizi(self, d: Dispositivo) -> None:
        if not d.acceso:
            self.ferma(d.nome)
            return
        accesi = sorted(k for k, v in d.servizi.items() if v)
        if accesi:
            self.avvia(d.nome, "servizi", [*STRUMENTI, "servizi", d.nome, *accesi], _firma(accesi))
        else:
            self.ferma(d.nome, "servizi")
        dns = d.dns_server or {}
        i = next((i for _, i in d.interfacce()), None)
        if dns.get("on") and i:
            rec = [f"--host-record={n},{ip}" for n, ip in sorted(dns.get("records", {}).items())]
            cmd = ["dnsmasq", "--no-daemon", "--no-resolv", "--no-hosts", "--port=53", f"--listen-address={i.ip}",
                   "--bind-interfaces", *rec]
            self.avvia(d.nome, "dns", cmd, _firma([str(i.ip), rec]))
        else:
            self.ferma(d.nome, "dns")

    # ───── prove ─────
    def ping(self, dev: str, ip: str, n: int = 4) -> tuple[int, int, str]:
        rc, out = self.esegui(dev, ["ping", "-c", str(n), "-i", "0.2", "-W", "1", ip], 5 + n)
        m = re.search(r"(\d+) packets transmitted, (\d+) received", out)
        return (int(m.group(2)), int(m.group(1)), out) if m else (0, n, out)

    def dhcp_client(self, dev: str, porta: str) -> dict | None:
        """Chiede un indirizzo al DHCP: {"ip", "mask", "gw", "dns"} o None."""
        k = nome_kernel(porta)
        self.esegui(dev, f"ip addr flush dev {k}; ip link set {k} up")
        rc, out = self.esegui(dev, ["busybox", "udhcpc", "-i", k, "-n", "-q", "-t", "6", "-T", "1", "-A", "1", "-f",
                                    "-s", str(QUI / "udhcpc.sh")], 20)
        m = re.search(r"^LEASE\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)$", out, re.M)
        if not m or not m.group(1):
            return None
        return {"ip": m.group(1), "mask": m.group(2), "gw": (m.group(3).split() or [""])[0],
                "dns": (m.group(4).split() or [""])[0]}

    def strumento(self, dev: str, args: list[str], timeout: float = 20) -> dict:
        rc, out = self.esegui(dev, [*STRUMENTI, *args], timeout)
        try:
            return json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"ok": False, "errore": out[-300:]}

    def comando(self, dev: str, cmd: list[str], timeout: float = 20) -> str:
        return self.esegui(dev, cmd, timeout)[1]

    def cattura(self, dev: str, secondi: float, traffico: list[tuple[str, list[str]]]) -> str:
        """tcpdump su tutte le porte del dispositivo mentre gli altri generano il traffico indicato."""
        n = self.nodi[dev]
        cap = n.popen(["timeout", str(secondi), "tcpdump", "-Z", "root", "-i", "any", "-nn", "-l", "-q", "-tt", "-c", "300"],
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        time.sleep(0.6)
        fili = [threading.Thread(target=self.esegui, args=(src, cmd, secondi + 2), daemon=True) for src, cmd in traffico
                if src in self.nodi]
        for f in fili:
            f.start()
        out, _ = cap.communicate(timeout=secondi + 5)
        for f in fili:
            f.join(timeout=1)
        return out or ""

    def chiudi(self) -> None:
        for nome in list(self.nodi):
            self.togli_nodo(nome)
