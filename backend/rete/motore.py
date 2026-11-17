"""Il motore di rete del gioco: l'unico oggetto che server.py usa per costruire, configurare e provare la rete.

    server.py ──▶ Motore ──▶ modello (la verità) ──▶ kernel (Mininet: la rete vera)
                         └─▶ console (comandi)        └─▶ percorso (le tracce)

Due modi, scelti all'avvio:
  - rete vera (Mininet, nel container): ping, web, porte, DNS, DHCP e sniffer sono traffico vero;
  - rete simulata (Mininet non c'è, per esempio su macOS senza Docker): le stesse prove calcolate sul
    modello da percorso.py. Il gioco è lo stesso; cambia solo da dove arriva la risposta.

Tutti i metodi che toccano la rete presuppongono che il chiamante abbia preso self.lock.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import time
from pathlib import Path

import yaml

from . import kernel as K
from . import percorso as P
from .console import Console, audit
from .modello import SERVIZI, Rete, nome_kernel, nuovo_dispositivo
from .topo import Topo
from .topologia import carica

PORTE_APP = {21: "FTP", 22: "SSH", 23: "TELNET", 25: "SMTP", 53: "DNS", 67: "DHCP", 68: "DHCP", 80: "HTTP",
             110: "POP3", 443: "HTTPS"}
TCPDUMP_RE = re.compile(r"^[\d.]+\s+(\S+)\s+(In|Out)\s+(IP|ARP)\s+(.*)$")


class ErroreRete(RuntimeError):
    """La rete non ha fatto quello che si chiedeva per un motivo che non dipende dal giocatore (→ 502)."""


class Motore:
    def __init__(self, dati: Path) -> None:
        self.file = dati / "rete.json"
        self.lock = asyncio.Lock()
        self.rete = Rete()
        self.console = Console(self.rete, self._ping_console)
        self.kernel: K.Kernel | None = None
        self.topo = Topo()
        self.snap_at = 0.0
        self.ok = False
        self.status = "avvio…"
        self.links_dirty = False

    # ───── avvio e salvataggio ─────
    @property
    def vera(self) -> bool:
        return self.kernel is not None

    async def start(self) -> None:
        if self.file.exists():
            try:
                self._sostituisci(Rete.from_dict(json.loads(self.file.read_text(encoding="utf-8"))))
            except Exception as e:  # noqa: BLE001
                print(f"  rete salvata illeggibile ({e}): si parte da un tavolo vuoto", flush=True)
        ok, perche = K.disponibile() if os.environ.get("PNA_RETE", "") != "simulata" else (False, "PNA_RETE=simulata")
        if ok:
            try:
                self.kernel = K.Kernel()
                await asyncio.to_thread(self.kernel.sync, self.rete, True)
                await self._dhcp_pendenti()
                self.status = "rete vera (Mininet)"
            except Exception as e:  # noqa: BLE001
                self.kernel = None
                self.status = f"rete simulata: Mininet non parte ({e})"[:200]
        else:
            self.status = f"rete simulata ({perche})"
        self.ok = True
        await self.refresh()

    async def stop(self) -> None:
        if self.kernel:
            await asyncio.to_thread(self.kernel.chiudi)

    def _sostituisci(self, rete: Rete) -> None:
        self.rete = rete
        self.console = Console(rete, self._ping_console)

    def salva(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.rete.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    async def applica(self) -> None:
        """Dopo ogni modifica al modello: salva, riallinea la rete vera, rinnova la fotografia."""
        self.salva()
        if self.kernel:
            await asyncio.to_thread(self.kernel.sync, self.rete)
        await self.refresh()

    async def refresh(self) -> Topo:
        self.topo = Topo.parse(self.rete.snapshot())
        self.snap_at = time.time()
        return self.topo

    async def settle(self) -> None:
        """Dopo cavi e porte cambiati: i PC in DHCP ancora senza indirizzo lo chiedono."""
        if self.links_dirty:
            self.links_dirty = False
            await self._dhcp_pendenti()

    def _dev(self, nome: str):
        d = self.rete.dev(nome)
        if not d:
            raise ValueError(f"il dispositivo «{nome}» non esiste")
        return d

    # ───── tavolo ─────
    async def clear(self) -> None:
        self._sostituisci(Rete())
        await self.applica()

    async def carica_topologia(self, file: Path) -> None:
        """Sostituisce la rete con una topologia YAML (gioco/reti/…)."""
        nuova = Rete()
        carica(nuova, Console(nuova), yaml.safe_load(file.read_text(encoding="utf-8")), file.name)
        self._sostituisci(nuova)
        await self.applica()
        await self._dhcp_pendenti()

    async def add_device(self, tipo: str, nome: str, x: int, y: int) -> None:
        self.rete.aggiungi(nuovo_dispositivo(tipo, nome, x, y))
        await self.applica()

    async def move(self, nome: str, x: int, y: int) -> None:
        d = self._dev(nome)
        d.x, d.y = x, y
        self.salva()
        await self.refresh()

    async def delete(self, nome: str) -> None:
        self._dev(nome)
        self.rete.togli(nome)
        self.console.chiudi(nome)
        await self.applica()

    async def rename(self, vecchio: str, nuovo: str) -> None:
        self._dev(vecchio)
        if self.rete.dev(nuovo):
            raise ValueError(f"esiste già un dispositivo chiamato {nuovo}")
        self.rete.rinomina(vecchio, nuovo)
        self.console.chiudi(vecchio)
        if self.kernel:
            self.kernel.rinomina(vecchio, nuovo)
        await self.applica()

    async def link(self, a: str, pa: str, b: str, pb: str) -> None:
        self.rete.collega(a, pa, b, pb)
        self.links_dirty = True
        await self.applica()

    async def unlink(self, dev: str, porta: str) -> None:
        self.rete.scollega(dev, porta)
        self.links_dirty = True
        await self.applica()

    async def power(self, nome: str, on: bool) -> None:
        d = self._dev(nome)
        d.acceso = on
        self.console.chiudi(nome)
        self.links_dirty = True
        await self.applica()

    # ───── PC e server ─────
    async def host_ip(self, nome: str, ip: str, mask: str, gw: str, dns: str | None) -> None:
        d = self._dev(nome)
        p = next(iter(d.porte.values()))
        p.ip, p.mask, d.gw, d.dhcp = ip, mask, "" if gw == "0.0.0.0" else gw, False
        if dns is not None:
            d.dns = dns
        await self.applica()

    async def host_dhcp(self, nome: str) -> str | None:
        """Il PC chiede un indirizzo al DHCP. Restituisce l'indirizzo ricevuto (es. 192.168.1.11/24) o None."""
        d = self._dev(nome)
        d.dhcp = True
        p = next(iter(d.porte.values()))
        p.ip = p.mask = ""
        d.gw = ""
        await self._chiedi_dhcp([nome])
        await self.applica()
        i = p.interfaccia()
        return str(i) if i else None

    async def _dhcp_pendenti(self) -> None:
        senza = [d.nome for d in self.rete.disp.values() if d.tipo in ("pc", "server") and d.dhcp and d.acceso
                 and not next(iter(d.porte.values())).interfaccia()]
        if senza:
            await self._chiedi_dhcp(senza)
            await self.applica()

    async def _chiedi_dhcp(self, nomi: list[str]) -> None:
        if self.kernel:
            await asyncio.to_thread(self.kernel.sync, self.rete)
            risposte = await asyncio.gather(*(asyncio.to_thread(self.kernel.dhcp_client, n,
                                              next(iter(self.rete.dev(n).porte))) for n in nomi))
        else:
            risposte = [self._dhcp_simulato(n) for n in nomi]
        for n, r in zip(nomi, risposte):
            if r:
                d = self.rete.dev(n)
                p = next(iter(d.porte.values()))
                p.ip, p.mask, d.gw = r["ip"], r["mask"], r["gw"]
                if r.get("dns"):
                    d.dns = r["dns"]

    def _dhcp_simulato(self, nome: str) -> dict | None:
        """Il DHCP calcolato sul modello: il primo indirizzo libero di un pool di un router nello stesso dominio L2."""
        dom = self.rete.domini()
        mio = dom.get(("if", nome, next(iter(self.rete.dev(nome).porte))))
        usati = {str(i.ip) for d in self.rete.disp.values() for _, i in d.interfacce()}
        for r in self.rete.disp.values():
            if r.tipo != "router" or not r.acceso:
                continue
            for p, i in r.interfacce():
                if dom.get(("if", r.nome, p.nome)) != mio or not self.rete.porta_su(r.nome, p.nome):
                    continue
                for pl in r.pool.values():
                    try:
                        net = ipaddress.IPv4Network(f"{pl['net']}/{pl['mask']}")
                    except ValueError:
                        continue
                    if i.ip not in net:
                        continue
                    esclusi = [(int(ipaddress.IPv4Address(a)), int(ipaddress.IPv4Address(b))) for a, b in r.esclusi]
                    for da, a in K.intervalli_dhcp(net, esclusi):
                        for v in range(int(ipaddress.IPv4Address(da)), int(ipaddress.IPv4Address(a)) + 1):
                            ip = str(ipaddress.IPv4Address(v))
                            if ip not in usati:
                                return {"ip": ip, "mask": str(net.netmask), "gw": pl.get("gw", ""), "dns": pl.get("dns", "")}
        return None

    async def server_dns(self, nome: str, on: bool | None, add: dict, remove: list[str]) -> dict:
        d = self._dev(nome)
        dns = d.dns_server = d.dns_server or {"on": False, "records": {}}
        if on is not None:
            dns["on"] = on
        for n in remove:
            dns["records"].pop(n, None)
        dns["records"].update(add)
        await self.applica()
        return dns

    def servizi(self, nome: str) -> dict:
        d = self._dev(nome)
        s = d.servizi
        return {"http": bool(s.get("http")), "https": bool(s.get("https")), "ftp": bool(s.get("ftp")),
                "smtp": bool(s.get("smtp")), "pop3": bool(s.get("pop3")), "ftp_users": [],
                "domain": d.dominio_email}

    async def servizio(self, nome: str, svc: str, on: bool, dominio: str = "") -> None:
        d = self._dev(nome)
        for s in (("smtp", "pop3") if svc == "email" else (svc,)):
            if s in SERVIZI:
                d.servizi[s] = on
        if dominio:
            d.dominio_email = dominio
        await self.applica()

    # ───── console ─────
    def _ping_console(self, dev: str, ip: str, n: int) -> tuple[int, int]:
        if self.kernel:
            ric, inv, _ = self.kernel.ping(dev, ip, n)
            return ric, inv
        return (n, n) if self._raggiunge(dev, ip) else (0, n)

    async def cli(self, nome: str, righe: list[str]) -> tuple[bool, str]:
        """Righe nella console di un dispositivo: (nessuna rifiutata, trascrizione)."""
        self._dev(nome)
        self.console.cambiato = False
        ok, out, prompt = await asyncio.to_thread(self.console.esegui, nome, righe)
        if self.console.cambiato:
            self.links_dirty = True
            await self.applica()
        return ok, out + ("\n" + prompt if prompt else "")

    def bloccata(self, nome: str) -> bool:
        """La console chiede utente o password (il giocatore non ha comunicato le credenziali giuste)."""
        d = self.rete.dev(nome)
        return bool(d and self.console.sessione(d).attesa)

    async def unlock(self, nome: str, creds: dict) -> None:
        """Porta la console in modalità privilegiata con le credenziali. ValueError se non bastano."""
        d = self._dev(nome)
        s = self.console.sessione(d)
        if s.attesa == "username":
            if not creds.get("username") or not creds.get("secret"):
                raise ValueError(f"la console di {nome} chiede utente e password")
            await asyncio.to_thread(self.console.esegui, nome, [creds["username"], creds["secret"]])
        elif s.attesa == "password":
            await asyncio.to_thread(self.console.esegui, nome, [creds.get("secret", "")])
        if s.attesa:
            self.console.chiudi(nome)
            raise ValueError(f"login su {nome} non riuscito: credenziali sbagliate?")
        if s.modo == "user":
            righe = ["enable"] + ([creds["enable_secret"]] if (d.enable_secret or d.enable_password) else [])
            if (d.enable_secret or d.enable_password) and not creds.get("enable_secret"):
                raise ValueError(f"per entrare in modalità privilegiata su {nome} serve la password di enable")
            ok, _, _ = await asyncio.to_thread(self.console.esegui, nome, righe)
            if not ok or s.modo == "user":
                raise ValueError(f"login su {nome} non riuscito: password di enable sbagliata?")

    def running_config(self, nome: str) -> str:
        return self.console.running_config(self._dev(nome))

    def audit(self, nome: str = "") -> list[dict]:
        devs = [self._dev(nome)] if nome else [d for d in self.rete.disp.values() if d.tipo in ("router", "switch")]
        return [{**f, "device": d.nome} for d in devs for f in audit(d)]

    # ───── prove ─────
    def _raggiunge(self, src: str, ip: str) -> bool:
        return P.traccia(self.rete, src, "", ip)["delivered"]

    async def ping(self, src: str, ip: str, creds: dict | None = None) -> tuple[bool, str]:
        """Ping vero (4 pacchetti): riuscito se almeno 2 risposte. Nella rete simulata: la traccia sul modello."""
        self._dev(src)
        await self.settle()
        if self.kernel:
            ric, inv, out = await asyncio.to_thread(self.kernel.ping, src, ip, 4)
            return ric >= 2, out
        ok = self._raggiunge(src, ip)
        return ok, (f"PING {ip}: 4 pacchetti inviati, {4 if ok else 0} ricevuti (rete simulata)")

    async def trace(self, src: str, dst: str) -> dict:
        """La traccia sul modello (dove passa e perché viene scartato); nella rete vera il ping la conferma."""
        await self.settle()
        t = P.traccia(self.rete, src, dst)
        if self.kernel and t["hops"]:
            ip = P.ip_di(self.rete, dst)
            if ip:
                ric, _, _ = await asyncio.to_thread(self.kernel.ping, src, ip, 2)
                if bool(ric) != t["delivered"]:
                    t["delivered"] = bool(ric)
                    if ric:
                        t["blocked_by"] = None
                    elif not t["blocked_by"]:
                        t["blocked_by"] = {"device": src, "reason": "il ping vero non ha avuto risposta"}
        return t

    def _dns_di(self, src: str) -> str:
        d = self._dev(src)
        return d.dns if d.dns not in ("", "0.0.0.0") else ""

    def _risolvi_simulato(self, src: str, nome: str) -> tuple[str, str]:
        server = self._dns_di(src)
        if not server:
            return "", ""
        prop = self.rete.proprietario(server)
        if not prop or P.sonda(self.rete, src, server, 53, "udp")[0] != "aperta":
            return "", server
        return self.rete.dev(prop[0]).dns_server.get("records", {}).get(nome, ""), server

    async def nslookup(self, src: str, nome: str) -> tuple[str, str]:
        """(IP a cui porta il nome, IP del DNS interrogato); "" se non si risolve."""
        await self.settle()
        server = self._dns_di(src)
        if self.kernel:
            if not server:
                return "", ""
            r = await asyncio.to_thread(self.kernel.strumento, src, ["dns", nome, server], 10)
            return r.get("ip", ""), server
        return self._risolvi_simulato(src, nome)

    async def browse(self, src: str, url: str) -> tuple[bool, str]:
        await self.settle()
        if self.kernel:
            r = await asyncio.to_thread(self.kernel.strumento, src, ["web", url, self._dns_di(src)], 20)
            return bool(r.get("ok")), str(r.get("text", r.get("errore", "")))[:4000]
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        ip = host if re.fullmatch(r"[\d.]+", host) else self._risolvi_simulato(src, host)[0]
        if not ip:
            return False, f"{host}: il nome non si risolve"
        stato, _ = P.sonda(self.rete, src, ip, u.port or (443 if u.scheme == "https" else 80))
        prop = self.rete.proprietario(ip)
        if stato == "aperta":
            return True, f"<h1>{prop[0] if prop else ip}</h1><p>Pagina ricevuta (rete simulata)</p>"
        return False, f"{ip}: " + ("lì non c'è un server web acceso" if stato == "chiusa" else "nessuna risposta")

    async def porta(self, src: str, ip: str, porta: int) -> str:
        """"aperta", "chiusa" o "filtrata"."""
        await self.settle()
        if self.kernel:
            r = await asyncio.to_thread(self.kernel.strumento, src, ["porta", ip, str(porta)], 10)
            return r.get("stato", "filtrata")
        return P.sonda(self.rete, src, ip, porta)[0]

    async def host_cmd(self, src: str, cmd: str) -> str:
        """Il prompt dei comandi di PC e server: ping, ipconfig, tracert, arp, nslookup."""
        d = self._dev(src)
        t = cmd.split()
        c = t[0].lower()
        if c == "ipconfig":
            i = self.rete.ip_host(d)
            return (f"Scheda {next(iter(d.porte))}:\n   Indirizzo IP . . . . . : {i.ip if i else '0.0.0.0'}\n"
                    f"   Maschera . . . . . . . : {i.netmask if i else '0.0.0.0'}\n"
                    f"   Gateway predefinito  . : {d.gw or '0.0.0.0'}\n   Server DNS . . . . . . : {d.dns or '-'}\n"
                    f"   DHCP . . . . . . . . . : {'sì' if d.dhcp else 'no'}")
        if c == "nslookup" and len(t) > 1:
            ip, server = await self.nslookup(src, t[1])
            return (f"Server:  {server or 'nessuno'}\nAddress: {server}\n\n" +
                    (f"Name:    {t[1]}\nAddress: {ip}" if ip else f"*** {t[1]}: nome non trovato"))
        if c == "ping" and len(t) > 1:
            ip = t[-1]
            ok, out = await self.ping(src, ip)
            return out
        if c == "tracert" and len(t) > 1:
            if self.kernel:
                return await asyncio.to_thread(self.kernel.comando, src, ["traceroute", "-n", "-w", "1", "-q", "1",
                                                                          "-m", "15", t[-1]], 30)
            tr = P.traccia(self.rete, src, "", t[-1])
            passi = [h["device"] for h in tr["hops"] if h["leg"] == "andata"]
            return "\n".join(f"{i + 1}  {x}" for i, x in enumerate(passi)) or "nessun passo"
        if c == "arp":
            if self.kernel:
                return await asyncio.to_thread(self.kernel.comando, src, ["ip", "neigh"], 5)
            return "(tabella ARP: disponibile con la rete vera)"
        raise ValueError("Comandi ammessi: ping, ipconfig, tracert, arp, nslookup")

    async def sniff(self, dev: str, voci: list[tuple[str, str]]) -> list[dict]:
        """Il traffico che passa da «dev» mentre gli altri fanno le «voci» [(host, "ping IP" | "telnet IP [porta]" |
        "web URL")]: righe come quelle di tcpdump, in italiano."""
        await self.settle()
        if self.kernel:
            traffico = []
            for src, cmd in voci:
                t = cmd.split()
                if t[0] == "ping":
                    traffico.append((src, ["ping", "-c", "3", "-i", "0.4", "-W", "1", t[1]]))
                elif t[0] == "telnet":
                    traffico.append((src, [*K.STRUMENTI, "tcp", t[1], t[2] if len(t) > 2 else "23"]))
                elif t[0] == "web":
                    traffico.append((src, [*K.STRUMENTI, "web", t[1], self._dns_di(src)]))
            out = await asyncio.to_thread(self.kernel.cattura, dev, 4.0, traffico)
            return self._righe_tcpdump(dev, out)
        return self._sniff_simulato(dev, voci)

    def _nome_ip(self, ip: str) -> str:
        p = self.rete.proprietario(ip)
        return p[0] if p else ""

    def _righe_tcpdump(self, dev: str, out: str) -> list[dict]:
        d = self._dev(dev)
        porte = {nome_kernel(p): p for p in d.porte}
        righe = []
        for riga in out.splitlines():
            m = TCPDUMP_RE.match(riga.strip())
            if not m or m.group(1) == "br0":
                continue
            ifc, verso, fam, resto = m.groups()
            porta = porte.get(ifc, ifc)
            if fam == "ARP":
                continue
            mm = re.match(r"(\d+\.\d+\.\d+\.\d+)(?:\.(\d+))? > (\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?: (.*)$", resto)
            if not mm:
                continue
            src, sp, dst, dp, info = mm.groups()
            proto = "ICMP" if info.startswith("ICMP") else "TCP" if info.startswith("tcp") else "UDP" if "UDP" in info or info.startswith("udp") else "IP"
            sp, dp = (int(sp) if sp else None), (int(dp) if dp else None)
            servizio = dp if dp in PORTE_APP else sp if sp in PORTE_APP else dp
            righe.append({"n": len(righe) + 1, "device": dev, "in": porta if verso == "In" else None,
                          "out": porta if verso == "Out" else None, "src": src, "src_dev": self._nome_ip(src),
                          "dst": dst, "dst_dev": self._nome_ip(dst), "proto": proto, "port": servizio,
                          "app": PORTE_APP.get(servizio or 0, "") if proto != "ICMP" else
                          ("echo request" if "request" in info else "echo reply" if "reply" in info else ""),
                          "status": "sent"})
        return righe

    def _sniff_simulato(self, dev: str, voci: list[tuple[str, str]]) -> list[dict]:
        righe = []
        for src, cmd in voci:
            t = cmd.split()
            if t[0] == "ping":
                ip = t[1]
                tr, proto, porta = P.traccia(self.rete, src, "", ip), "ICMP", None
            else:
                if t[0] == "web":
                    from urllib.parse import urlparse
                    u = urlparse(t[1])
                    ip = u.hostname or ""
                    if not re.fullmatch(r"[\d.]+", ip):
                        ip = self._risolvi_simulato(src, ip)[0]
                    porta = u.port or 80
                else:
                    ip, porta = t[1], int(t[2]) if len(t) > 2 else 23
                if not ip:
                    continue
                _, tr = P.sonda(self.rete, src, ip, porta)
                proto = "TCP"
            for h in tr["hops"]:
                if h["device"] != dev:
                    continue
                andata = h["leg"] == "andata"
                s_ip, d_ip = (P.ip_di(self.rete, src), ip) if andata else (ip, P.ip_di(self.rete, src))
                righe.append({"n": len(righe) + 1, "device": dev, "in": h["in"], "out": h["out"], "src": s_ip,
                              "src_dev": self._nome_ip(s_ip), "dst": d_ip, "dst_dev": self._nome_ip(d_ip),
                              "proto": proto, "port": porta, "app": PORTE_APP.get(porta or 0, "") if porta else
                              ("echo request" if andata else "echo reply"),
                              "status": "dropped" if h["status"] == "dropped" else "sent"})
        return righe
