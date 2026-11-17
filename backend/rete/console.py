"""La console dei dispositivi di rete, con la sintassi da riga di comando più diffusa per router e switch
(la stessa di FRR e di molti altri sistemi): modalità, abbreviazioni («conf t», «int g0/0», «no sh»), login e comandi «show».

Ogni riga cambia il modello (modello.py); chi chiama poi riallinea la rete vera (kernel.py). I comandi sono
il sottoinsieme che serve al gioco: interfacce, rotte statiche, OSPF, DHCP, ACL, VLAN e trunk, sottointerfacce,
utenti e password, SSH, banner, limiti di velocità, HSRP, port-security; sul firewall zone (nameif,
security-level), rotte, access-list e access-group.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable

from . import acl as A
from .modello import Dispositivo, Porta, Rete, espandi_porta

INVALIDO = "% Invalid input detected at '^' marker."
INCOMPLETO = "% Incomplete command."
# Chiave della «password 7» dei router: offusca, non cifra (chiunque la decodifica). Il gioco la mostra apposta.
CHIAVE7 = "dsfd;kfoA,.iyewrkldJKDHSUBsgvca69834ncxv9873254k;fg87"


def tipo7(testo: str, seme: int = 2) -> str:
    return f"{seme:02d}" + "".join(f"{ord(c) ^ ord(CHIAVE7[(seme + i) % len(CHIAVE7)]):02X}" for i, c in enumerate(testo))


def segreto5(testo: str) -> str:
    return "$1$pnga$" + hashlib.sha256(testo.encode()).hexdigest()[:22]


def kw(tok: str, parola: str, minimo: int = 1) -> bool:
    """tok è un'abbreviazione valida di parola (almeno «minimo» lettere)."""
    return len(tok) >= minimo and parola.startswith(tok.lower())


class Errore(Exception):
    pass


@dataclass
class Sessione:
    """La console aperta su un dispositivo: resta tra una richiesta e l'altra, come un terminale vero."""
    modo: str = ""              # "" = da decidere (login?), user, enable, config, if, router, dhcp, acl, line, pmap, pmapc, vlan
    ctx: list = field(default_factory=list)
    attesa: str = ""            # username, password, enable
    utente: str = ""


class Console:
    """Esegue righe sulla console di un dispositivo.

    ping_cb(dispositivo, ip, n) → (ricevuti, inviati): il ping vero, fatto da kernel.py.
    """

    def __init__(self, rete: Rete, ping_cb: Callable[[str, str, int], tuple[int, int]] | None = None) -> None:
        self.rete = rete
        self.ping_cb = ping_cb
        self.sessioni: dict[str, Sessione] = {}
        self.cambiato = False

    # ───── ingresso ─────
    def sessione(self, d: Dispositivo) -> Sessione:
        s = self.sessioni.setdefault(d.nome, Sessione())
        if not s.modo:
            self._apri(d, s)
        return s

    def chiudi(self, nome: str) -> None:
        self.sessioni.pop(nome, None)

    def _apri(self, d: Dispositivo, s: Sessione) -> None:
        con = d.linee.get("con", {})
        s.modo, s.ctx, s.utente = "user", [], ""
        if con.get("login") == "local" and d.utenti:
            s.attesa = "username"
        elif con.get("login") == "password" and con.get("password"):
            s.attesa = "password"
        else:
            s.attesa = ""

    def prompt(self, d: Dispositivo, s: Sessione) -> str:
        if s.attesa == "username":
            return "Username: "
        if s.attesa in ("password", "enable"):
            return "Password: "
        h = d.hostname or d.nome
        suff = {"user": ">", "enable": "#", "config": "(config)#", "router": "(config-router)#",
                "dhcp": "(dhcp-config)#", "line": "(config-line)#", "pmap": "(config-pmap)#",
                "pmapc": "(config-pmap-c)#", "vlan": "(config-vlan)#"}
        if s.modo == "if":
            return h + ("(config-subif)#" if any("." in p for p in s.ctx) else "(config-if)#")
        if s.modo == "acl":
            return h + ("(config-ext-nacl)#" if s.ctx[1] else "(config-std-nacl)#")
        return h + suff[s.modo]

    def esegui(self, nome: str, righe: list[str]) -> tuple[bool, str, str]:
        """Righe nella console di «nome»: (nessuna riga rifiutata, trascrizione, prompt finale)."""
        d = self.rete.dev(nome)
        if not d:
            raise ValueError(f"il dispositivo «{nome}» non esiste")
        if not d.acceso:
            return False, f"{nome} è spento: la console non risponde.", ""
        s = self.sessione(d)
        out, ok = [], True
        for riga in righe:
            p = self.prompt(d, s)
            segreta = s.attesa in ("password", "enable")
            out.append(p + ("" if segreta else riga))
            try:
                r = self._riga(d, s, riga.rstrip())
            except Errore as e:
                r, ok = str(e), False
            if r:
                out.append(r)
        return ok, "\n".join(out), self.prompt(d, s)

    # ───── login ─────
    def _login(self, d: Dispositivo, s: Sessione, riga: str) -> str:
        if s.attesa == "username":
            if riga.strip():
                s.utente, s.attesa = riga.strip(), "password"
            return ""
        if s.attesa == "password":
            con = d.linee.get("con", {})
            if con.get("login") == "local":
                u = d.utenti.get(s.utente)
                if u and u["valore"] == riga:
                    s.attesa = ""
                    s.modo = "enable" if int(u.get("privilege", 1)) >= 15 else "user"
                    return ""
                s.attesa = "username"
                return "% Login invalid"
            if riga == con.get("password"):
                s.attesa = ""
                return ""
            return "% Bad passwords"
        if s.attesa == "enable":
            s.attesa = ""
            if riga in (d.enable_secret or d.enable_password,):
                s.modo = "enable"
                return ""
            raise Errore("% Bad secrets")
        return ""

    # ───── una riga ─────
    def _riga(self, d: Dispositivo, s: Sessione, riga: str) -> str:
        if s.attesa:
            return self._login(d, s, riga)
        t = riga.split()
        if not t or riga.lstrip().startswith("!"):
            return ""
        if s.modo in ("user", "enable"):
            return self._exec(d, s, t, riga)
        if kw(t[0], "end") and len(t) == 1:
            s.modo, s.ctx = "enable", []
            return ""
        if kw(t[0], "do", 2) and len(t) > 1:
            return self._exec(d, s, t[1:], " ".join(t[1:]), dentro_config=True)
        if kw(t[0], "exit", 2) and len(t) == 1:
            s.modo, s.ctx = ("enable" if s.modo == "config" else "pmap" if s.modo == "pmapc" else "config"), \
                ([s.ctx[0]] if s.modo == "pmapc" else [])
            return ""
        if s.modo != "config" and s.modo != "pmapc" and self._globale(d, t):
            # come sui router veri: un comando globale scritto in un sottomodo esce dal sottomodo e viene eseguito
            s.modo, s.ctx = "config", []
        f = {"config": self._config, "if": self._interfaccia, "router": self._ospf, "dhcp": self._dhcp,
             "acl": self._acl_modo, "line": self._linea, "pmap": self._pmap, "pmapc": self._pmapc,
             "vlan": self._vlan_modo}[s.modo]
        return f(d, s, t, riga)

    @staticmethod
    def _globale(d: Dispositivo, t: list[str]) -> bool:
        x = t[1] if t[0] == "no" and len(t) > 1 else t[0]
        return any(kw(x, w, 3) for w in ("interface", "hostname", "router", "access-list", "username",
                                          "line", "policy-map", "banner", "route", "enable", "service",
                                          "crypto", "spanning-tree")) or kw(x, "vlan", 4) \
            or (kw(x, "ip", 2) and len(t) > (2 if t[0] == "no" else 1)
                and any(kw(t[2 if t[0] == "no" else 1], w, 3) for w in ("route", "dhcp", "access-list", "domain-name")))

    # ───── exec ─────
    def _exec(self, d: Dispositivo, s: Sessione, t: list[str], riga: str, dentro_config: bool = False) -> str:
        c = t[0].lower()
        if kw(c, "enable", 2) and not dentro_config:
            if s.modo == "enable":
                return ""
            if d.enable_secret or d.enable_password:
                s.attesa = "enable"
                return ""
            s.modo = "enable"
            return ""
        if kw(c, "disable", 4):
            s.modo = "user"
            return ""
        if kw(c, "exit", 2) or kw(c, "logout", 4) or kw(c, "quit", 1):
            self._apri(d, s)
            return ""
        if kw(c, "show", 2):
            if d.tipo != "firewall" and s.modo == "user" and len(t) > 1 and kw(t[1], "running-config", 3):
                raise Errore(INVALIDO)
            return self._show(d, t[1:])
        if kw(c, "ping", 2):
            return self._ping(d, t[1:])
        if kw(c, "traceroute", 3) or kw(c, "tracert", 5):
            raise Errore("% traceroute: usa la traccia del gioco (🔍) per seguire un pacchetto")
        if s.modo == "user":
            raise Errore(INVALIDO)
        if kw(c, "configure", 4):
            if len(t) == 1 or kw(t[1], "terminal"):
                s.modo, s.ctx = "config", []
                return "Enter configuration commands, one per line.  End with CNTL/Z."
            raise Errore(INVALIDO)
        if kw(c, "write", 2) or (kw(c, "copy", 2) and len(t) >= 3):
            return "Building configuration...\n[OK]"
        if kw(c, "clear", 3) or kw(c, "reload", 3) or kw(c, "terminal", 3):
            return ""
        raise Errore(INVALIDO)

    def _ping(self, d: Dispositivo, t: list[str]) -> str:
        if not t:
            raise Errore(INCOMPLETO)
        try:
            ip = str(ipaddress.IPv4Address(t[0]))
        except ValueError:
            raise Errore(f"% Unrecognized host or address, or protocol not running.")
        n = 5
        if len(t) >= 3 and kw(t[1], "repeat", 1) and t[2].isdigit():
            n = max(1, min(int(t[2]), 20))
        ric, inv = self.ping_cb(d.nome, ip, n) if self.ping_cb else (0, n)
        segni = "!" * ric + "." * (inv - ric)
        perc = round(100 * ric / inv) if inv else 0
        rtt = ", round-trip min/avg/max = 0/1/2 ms" if ric else ""
        return (f"Type escape sequence to abort.\nSending {inv}, 100-byte ICMP Echos to {ip}, timeout is 2 seconds:\n"
                f"{segni}\nSuccess rate is {perc} percent ({ric}/{inv}){rtt}")

    # ───── configurazione globale ─────
    def _porte_da(self, d: Dispositivo, testo: str) -> list[str]:
        """«g0/1», «fa0/1 - 4» (range), «GigabitEthernet0/0.10» (sottointerfaccia nuova sui router)."""
        testo = testo.strip()
        m = re.match(r"^(range\s+)?([A-Za-z-]+)\s*(\d+/)?(\d+)\s*-\s*(\d+)$", testo)
        if m:
            pref, slot, a, b = m.group(2), m.group(3) or "", int(m.group(4)), int(m.group(5))
            out = [espandi_porta(f"{pref}{slot}{i}", list(d.porte)) for i in range(a, b + 1)]
            if None in out or not out:
                raise Errore(INVALIDO)
            return out
        testo = re.sub(r"^range\s+", "", testo)
        nome = espandi_porta(testo, list(d.porte))
        if not nome:
            raise Errore(INVALIDO)
        if nome not in d.porte:
            if d.tipo != "router" or "." not in nome:
                raise Errore(INVALIDO)
            d.porte[nome] = Porta(nome)
            self.cambiato = True
        return [nome]

    def _config(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        if d.tipo == "firewall":
            return self._config_fw(d, s, t, riga)
        neg = t[0] == "no"
        a = t[1:] if neg else t
        if not a:
            raise Errore(INCOMPLETO)
        c = a[0].lower()
        if kw(c, "hostname", 3) and not neg:
            if len(a) < 2:
                raise Errore(INCOMPLETO)
            d.hostname = a[1][:24]
            return ""
        if kw(c, "interface", 3):
            if len(a) < 2:
                raise Errore(INCOMPLETO)
            resto = " ".join(a[1:])
            if re.match(r"^vlan\s*\d+$", resto, re.I):
                s.modo, s.ctx = "if", ["Vlan"]
                return ""
            porte = self._porte_da(d, resto)
            if neg:
                for p in porte:
                    if "." in p:
                        d.porte.pop(p, None)
                        self.cambiato = True
                return ""
            s.modo, s.ctx = "if", porte
            return ""
        if kw(c, "ip", 2) and len(a) >= 2:
            return self._config_ip(d, s, a[1:], neg)
        if kw(c, "router", 3):
            if len(a) < 3 or not kw(a[1], "ospf") or not a[2].isdigit():
                raise Errore(INVALIDO if len(a) >= 2 else INCOMPLETO)
            self.cambiato = True
            if neg:
                d.ospf.pop(a[2], None)
                return ""
            d.ospf.setdefault(a[2], {"router_id": "", "reti": [], "passive": [], "default": False})
            s.modo, s.ctx = "router", [a[2]]
            return ""
        if kw(c, "access-list", 3):
            return self._access_list_numerata(d, a[1:], neg)
        if kw(c, "username", 3):
            return self._username(d, a[1:], neg)
        if kw(c, "enable", 2):
            return self._enable(d, a[1:], neg)
        if kw(c, "service", 4):
            if len(a) >= 2 and kw(a[1], "password-encryption", 5):
                d.cifra_password = not neg
                return ""
            return ""   # altri «service …»: accettati, senza effetto
        if kw(c, "banner", 3):
            if neg:
                d.banner = ""
                return ""
            m = re.match(r"^\s*banner\s+motd\s+(\S)(.*)\1\s*$", riga, re.I)
            if not m:
                raise Errore(INVALIDO)
            d.banner = m.group(2).strip()
            return ""
        if kw(c, "crypto", 3):
            if not d.dominio:
                raise Errore("% Please define a domain-name first.")
            d.ssh = not neg
            return "The name for the keys will be: " + f"{d.hostname}.{d.dominio}" + "\n% Generating RSA keys ... [OK]"
        if kw(c, "line", 2):
            if len(a) < 2:
                raise Errore(INCOMPLETO)
            if kw(a[1], "console", 3):
                s.modo, s.ctx = "line", ["con"]
            elif kw(a[1], "vty", 1):
                s.modo, s.ctx = "line", ["vty"]
            elif kw(a[1], "aux", 2):
                s.modo, s.ctx = "line", ["aux"]
            else:
                raise Errore(INVALIDO)
            d.linee.setdefault(s.ctx[0], {})
            return ""
        if kw(c, "policy-map", 3):
            if len(a) < 2:
                raise Errore(INCOMPLETO)
            self.cambiato = True
            if neg:
                d.policy.pop(a[1], None)
                return ""
            d.policy.setdefault(a[1], 0)
            s.modo, s.ctx = "pmap", [a[1]]
            return ""
        if kw(c, "class-map", 3):
            return ""
        if kw(c, "vlan", 2):
            if d.tipo != "switch" or len(a) < 2:
                raise Errore(INVALIDO)
            ids = [x for x in re.split(r"[,\s]+", " ".join(a[1:])) if x]
            if not all(x.isdigit() and 1 <= int(x) <= 4094 for x in ids):
                raise Errore(INVALIDO)
            self.cambiato = True
            for v in ids:
                if neg:
                    if v != "1":
                        d.vlan.pop(v, None)
                else:
                    d.vlan.setdefault(v, f"VLAN{int(v):04d}")
            if not neg and len(ids) == 1:
                s.modo, s.ctx = "vlan", [ids[0]]
            return ""
        if kw(c, "spanning-tree", 3) or kw(c, "cdp", 2) or kw(c, "logging", 3) or kw(c, "clock", 2) \
                or kw(c, "license", 3) or kw(c, "version", 3) or kw(c, "boot", 3) or kw(c, "default", 3) \
                or kw(c, "no", 2) or kw(c, "snmp-server", 3) or kw(c, "ntp", 2):
            return ""
        raise Errore(INVALIDO)

    def _config_ip(self, d: Dispositivo, s: Sessione, a: list[str], neg: bool) -> str:
        c = a[0].lower()
        if kw(c, "route", 3):
            if len(a) < 4:
                raise Errore(INCOMPLETO)
            try:
                rete = ipaddress.IPv4Network(f"{a[1]}/{a[2]}", strict=False)
                via = str(ipaddress.IPv4Address(a[3]))
            except ValueError:
                raise Errore(INVALIDO)
            if str(rete.network_address) != a[1]:
                raise Errore("%Inconsistent address and mask")
            voce = [a[1], str(rete.netmask), via]
            self.cambiato = True
            if neg:
                d.rotte = [r for r in d.rotte if r != voce]
            elif voce not in d.rotte:
                d.rotte.append(voce)
            return ""
        if kw(c, "dhcp", 2):
            if len(a) >= 3 and kw(a[1], "pool", 1):
                self.cambiato = True
                if neg:
                    d.pool.pop(a[2], None)
                    return ""
                d.pool.setdefault(a[2], {"net": "", "mask": "", "gw": "", "dns": ""})
                s.modo, s.ctx = "dhcp", [a[2]]
                return ""
            if len(a) >= 3 and kw(a[1], "excluded-address", 3):
                try:
                    da = str(ipaddress.IPv4Address(a[2]))
                    fino = str(ipaddress.IPv4Address(a[3])) if len(a) > 3 else da
                except ValueError:
                    raise Errore(INVALIDO)
                voce = [da, fino]
                self.cambiato = True
                if neg:
                    d.esclusi = [e for e in d.esclusi if e != voce]
                elif voce not in d.esclusi:
                    d.esclusi.append(voce)
                return ""
            raise Errore(INVALIDO)
        if kw(c, "access-list", 3):
            if len(a) < 3 or not (kw(a[1], "standard") or kw(a[1], "extended")):
                raise Errore(INCOMPLETO)
            estesa, nome = kw(a[1], "extended"), a[2]
            self.cambiato = True
            if neg:
                d.acl.pop(nome, None)
                return ""
            cur = d.acl.setdefault(nome, {"estesa": estesa, "righe": []})
            if cur["estesa"] != estesa:
                raise Errore(f"% A named {'standard' if cur['estesa'] is False else 'extended'} IP access list with this name already exists")
            s.modo, s.ctx = "acl", [nome, estesa]
            return ""
        if kw(c, "domain-name", 8) or (kw(c, "domain", 3) and len(a) >= 3 and kw(a[1], "name")):
            d.dominio = "" if neg else a[-1]
            return ""
        if kw(c, "ssh", 2) or kw(c, "cef", 2) or kw(c, "domain-lookup", 8) or kw(c, "domain", 3) \
                or kw(c, "classless", 3) or kw(c, "http", 2) or kw(c, "flow-export", 3):
            return ""
        if kw(c, "default-gateway", 3) and d.tipo == "switch":
            d.gateway_switch = "" if neg else a[-1]
            return ""
        raise Errore(INVALIDO)

    def _access_list_numerata(self, d: Dispositivo, a: list[str], neg: bool) -> str:
        if not a or not a[0].isdigit():
            raise Errore(INVALIDO)
        n = int(a[0])
        estesa = 100 <= n <= 199 or 2000 <= n <= 2699
        if not (estesa or 1 <= n <= 99 or 1300 <= n <= 1999):
            raise Errore(INVALIDO)
        self.cambiato = True
        if neg and len(a) == 1:
            d.acl.pop(a[0], None)
            return ""
        if len(a) < 2:
            raise Errore(INCOMPLETO)
        if kw(a[1], "remark", 3):
            return ""
        testo = " ".join(a[1:])
        try:
            A.leggi(testo, estesa, "router")
        except A.RegolaNonValida as e:
            raise Errore(f"{INVALIDO} ({e})")
        cur = d.acl.setdefault(a[0], {"estesa": estesa, "righe": []})
        if neg:
            cur["righe"] = [r for r in cur["righe"] if r != " ".join(testo.split())]
        else:
            cur["righe"].append(" ".join(testo.split()))
        return ""

    def _username(self, d: Dispositivo, a: list[str], neg: bool) -> str:
        if not a:
            raise Errore(INCOMPLETO)
        nome = a[0]
        if neg:
            if nome not in d.utenti:
                raise Errore(f"% User {nome} not found")
            d.utenti.pop(nome)
            return ""
        priv, i = 1, 1
        if len(a) > 2 and kw(a[1], "privilege", 3) and a[2].isdigit():
            priv, i = int(a[2]), 3
        if len(a) <= i + 1 or not (kw(a[i], "password", 4) or kw(a[i], "secret", 3)):
            raise Errore(INCOMPLETO)
        tipo = "secret" if kw(a[i], "secret", 3) else "password"
        resto = a[i + 1:]
        if len(resto) >= 2 and resto[0] in ("0", "5", "7"):
            if resto[0] != "0":
                raise Errore("% Il gioco accetta solo password in chiaro (0): verranno protette come chiedi.")
            resto = resto[1:]
        d.utenti[nome] = {"privilege": max(0, min(priv, 15)), "tipo": tipo, "valore": " ".join(resto)}
        return ""

    def _enable(self, d: Dispositivo, a: list[str], neg: bool) -> str:
        if not a or not (kw(a[0], "secret", 3) or kw(a[0], "password", 4)):
            raise Errore(INCOMPLETO)
        segreto = kw(a[0], "secret", 3)
        valore = " ".join(a[2:] if len(a) > 2 and a[1] in ("0", "5", "7") else a[1:])
        if neg:
            if segreto:
                d.enable_secret = ""
            else:
                d.enable_password = ""
            return ""
        if not valore:
            raise Errore(INCOMPLETO)
        if segreto:
            d.enable_secret = valore
        else:
            d.enable_password = valore
        return ""

    # ───── interfaccia ─────
    def _interfaccia(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        if s.ctx == ["Vlan"]:
            return ""   # interfaccia di gestione dello switch: accettata, senza effetto sul gioco
        if d.tipo == "firewall":
            return self._interfaccia_fw(d, s, t, riga)
        neg = t[0] == "no"
        a = t[1:] if neg else t
        if not a:
            raise Errore(INCOMPLETO)
        c = a[0].lower()
        if kw(c, "interface", 3):
            return self._config(d, s, t, riga)
        porte = [d.porte[p] for p in s.ctx]
        self.cambiato = True
        if kw(c, "shutdown", 2):
            for p in porte:
                p.shutdown = not neg
                if not neg and p.psec:
                    p.psec.pop("errdisable", None)
            return ""
        if kw(c, "description", 2):
            for p in porte:
                p.descrizione = "" if neg else " ".join(a[1:])
            return ""
        if kw(c, "duplex", 2) or kw(c, "speed", 2) or kw(c, "mdix", 2) or kw(c, "negotiation", 3):
            return ""
        if kw(c, "ip", 2) and len(a) >= 2:
            return self._if_ip(d, porte, a[1:], neg)
        if kw(c, "service-policy", 3):
            if len(a) < 3 or not kw(a[1], "output", 1):
                raise Errore(INCOMPLETO)
            if not neg and a[2] not in d.policy:
                raise Errore(f"policy map {a[2]} not configured")
            for p in porte:
                p.policy_out = "" if neg else a[2]
            return ""
        if kw(c, "switchport", 2):
            return self._switchport(d, porte, a[1:], neg)
        if kw(c, "encapsulation", 3):
            if d.tipo != "router" or len(a) < 3 or not kw(a[1], "dot1q", 3) or not a[2].isdigit():
                raise Errore(INVALIDO)
            if not all("." in p.nome for p in porte):
                raise Errore("% Configuring IEEE 802.1Q encapsulation on the main interface is not supported")
            for p in porte:
                p.dot1q = None if neg else int(a[2])
            return ""
        if kw(c, "standby", 3):
            return self._standby(d, porte, a[1:], neg)
        raise Errore(INVALIDO)

    def _if_ip(self, d: Dispositivo, porte: list[Porta], a: list[str], neg: bool) -> str:
        c = a[0].lower()
        if kw(c, "address", 2):
            if d.tipo == "switch":
                raise Errore(INVALIDO)
            if neg:
                for p in porte:
                    p.ip = p.mask = ""
                return ""
            if len(a) < 3:
                raise Errore(INCOMPLETO)
            try:
                i = ipaddress.IPv4Interface(f"{a[1]}/{a[2]}")
            except ValueError:
                raise Errore(INVALIDO)
            if i.network.prefixlen < 31 and i.ip in (i.network.network_address, i.network.broadcast_address):
                raise Errore("Bad mask /%d for address %s" % (i.network.prefixlen, a[1]))
            for altra, ia in d.interfacce():
                if altra.nome not in [p.nome for p in porte] and ia.network.overlaps(i.network):
                    raise Errore(f"% {i.network} overlaps with {altra.nome}")
            for p in porte:
                p.ip, p.mask = str(i.ip), str(i.netmask)
            return ""
        if kw(c, "access-group", 8):
            if len(a) < 3 or a[2] not in ("in", "out"):
                raise Errore(INCOMPLETO)
            for p in porte:
                setattr(p, f"acl_{a[2]}", "" if neg else a[1])
            return ""
        if kw(c, "ospf", 2):
            if len(a) >= 3 and kw(a[1], "network") and kw(a[2], "point-to-point"):
                for p in porte:
                    p.ospf_p2p = not neg
                return ""
            if len(a) >= 3 and kw(a[1], "cost") and a[2].isdigit():
                for p in porte:
                    p.ospf_costo = 1 if neg else max(1, int(a[2]))
                return ""
            return ""
        if kw(c, "helper-address", 3) or kw(c, "nat", 2) or kw(c, "proxy-arp", 3):
            return ""
        raise Errore(INVALIDO)

    def _switchport(self, d: Dispositivo, porte: list[Porta], a: list[str], neg: bool) -> str:
        if d.tipo != "switch":
            raise Errore(INVALIDO)
        if not a:
            return ""
        c = a[0].lower()
        if kw(c, "mode", 2):
            if len(a) < 2:
                raise Errore(INCOMPLETO)
            for p in porte:
                p.trunk = (not neg) and kw(a[1], "trunk")
            return ""
        if kw(c, "access", 2):
            if neg:
                for p in porte:
                    p.vlan = 1
                return ""
            if len(a) < 3 or not kw(a[1], "vlan") or not a[2].isdigit() or not 1 <= int(a[2]) <= 4094:
                raise Errore(INVALIDO)
            msg = ""
            if a[2] not in d.vlan:
                d.vlan[a[2]] = f"VLAN{int(a[2]):04d}"
                msg = "% Access VLAN does not exist. Creating vlan " + a[2]
            for p in porte:
                p.vlan = int(a[2])
            return msg
        if kw(c, "trunk", 2):
            if len(a) >= 3 and kw(a[1], "native"):
                for p in porte:
                    p.nativa = 1 if neg else int(a[-1])
                return ""
            if len(a) >= 3 and kw(a[1], "allowed"):
                resto = a[3:] if kw(a[2], "vlan") else a[2:]
                if neg or not resto or resto[0] == "all":
                    for p in porte:
                        p.trunk_vlan = None
                    return ""
                op = resto[0] if resto[0] in ("add", "remove", "except") else ""
                lista = resto[1] if op else resto[0]
                vl = set()
                for pezzo in lista.split(","):
                    if "-" in pezzo:
                        x, y = pezzo.split("-", 1)
                        vl |= set(range(int(x), int(y) + 1))
                    elif pezzo.isdigit():
                        vl.add(int(pezzo))
                for p in porte:
                    cur = set(p.trunk_vlan) if p.trunk_vlan is not None else set(range(1, 4095))
                    nuovo = cur | vl if op == "add" else cur - vl if op == "remove" else \
                        set(range(1, 4095)) - vl if op == "except" else vl
                    p.trunk_vlan = sorted(nuovo)
                return ""
            if len(a) >= 2 and kw(a[1], "encapsulation"):
                return ""
            raise Errore(INVALIDO)
        if kw(c, "port-security", 2):
            for p in porte:
                if neg and len(a) == 1:
                    p.psec = None
                    continue
                p.psec = p.psec or {"max": 1, "violation": "shutdown", "sticky": False}
                if len(a) >= 3 and kw(a[1], "maximum") and a[2].isdigit():
                    p.psec["max"] = int(a[2])
                elif len(a) >= 3 and kw(a[1], "violation"):
                    p.psec["violation"] = next((v for v in ("shutdown", "restrict", "protect") if kw(a[2], v)), "shutdown")
                elif len(a) >= 3 and kw(a[1], "mac-address") and kw(a[2], "sticky"):
                    p.psec["sticky"] = not neg
            return ""
        if kw(c, "nonegotiate", 2) or kw(c, "voice", 2):
            return ""
        raise Errore(INVALIDO)

    def _standby(self, d: Dispositivo, porte: list[Porta], a: list[str], neg: bool) -> str:
        if not a:
            raise Errore(INCOMPLETO)
        if kw(a[0], "version", 1):
            return ""
        if not a[0].isdigit() or len(a) < 2:
            raise Errore(INVALIDO)
        g = a[0]
        for p in porte:
            if neg and len(a) == 1:
                p.standby.pop(g, None)
                continue
            st = p.standby.setdefault(g, {"ip": "", "priority": 100, "preempt": False})
            if kw(a[1], "ip"):
                st["ip"] = "" if neg else a[2]
            elif kw(a[1], "priority") and len(a) > 2:
                st["priority"] = 100 if neg else int(a[2])
            elif kw(a[1], "preempt"):
                st["preempt"] = not neg
        return ""

    # ───── sottomodi ─────
    def _ospf(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        proc = d.ospf[s.ctx[0]]
        self.cambiato = True
        if kw(a[0], "network", 3):
            if len(a) < 5 or not kw(a[3], "area"):
                raise Errore(INCOMPLETO)
            try:
                ipaddress.IPv4Address(a[1])
                ipaddress.IPv4Address(a[2])
            except ValueError:
                raise Errore(INVALIDO)
            voce = [a[1], a[2], a[4]]
            if neg:
                proc["reti"] = [r for r in proc["reti"] if r[:2] != voce[:2]]
            elif voce not in proc["reti"]:
                proc["reti"].append(voce)
            return ""
        if kw(a[0], "router-id", 3):
            proc["router_id"] = "" if neg else a[1]
            return ""
        if kw(a[0], "passive-interface", 3):
            p = espandi_porta(" ".join(a[1:]), list(d.porte))
            if not p:
                raise Errore(INVALIDO)
            proc["passive"] = [x for x in proc["passive"] if x != p] + ([] if neg else [p])
            return ""
        if kw(a[0], "default-information", 3):
            proc["default"] = not neg
            return ""
        if kw(a[0], "log-adjacency-changes", 3) or kw(a[0], "auto-cost", 3):
            return ""
        raise Errore(INVALIDO)

    def _dhcp(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        pool = d.pool[s.ctx[0]]
        self.cambiato = True
        try:
            if kw(a[0], "network", 3):
                if neg:
                    pool["net"] = pool["mask"] = ""
                    return ""
                n = ipaddress.IPv4Network(f"{a[1]}/{a[2] if len(a) > 2 else '255.255.255.0'}", strict=False)
                pool["net"], pool["mask"] = str(n.network_address), str(n.netmask)
                return ""
            if kw(a[0], "default-router", 3):
                pool["gw"] = "" if neg else str(ipaddress.IPv4Address(a[1]))
                return ""
            if kw(a[0], "dns-server", 3):
                pool["dns"] = "" if neg else str(ipaddress.IPv4Address(a[1]))
                return ""
        except (ValueError, IndexError):
            raise Errore(INVALIDO)
        if kw(a[0], "domain-name", 3) or kw(a[0], "lease", 3):
            return ""
        raise Errore(INVALIDO)

    def _acl_modo(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        nome, estesa = s.ctx
        cur = d.acl[nome]
        self.cambiato = True
        if t[0] == "no":
            if len(t) == 2 and t[1].isdigit():
                i = int(t[1]) // 10 - 1
                if 0 <= i < len(cur["righe"]):
                    cur["righe"].pop(i)
                return ""
            testo = " ".join(t[1:])
            cur["righe"] = [r for r in cur["righe"] if r != testo]
            return ""
        if kw(t[0], "remark", 3):
            return ""
        if t[0].isdigit():
            t = t[1:]
        testo = " ".join(t)
        try:
            A.leggi(testo, estesa, "router")
        except A.RegolaNonValida as e:
            raise Errore(f"{INVALIDO} ({e})")
        cur["righe"].append(testo)
        return ""

    def _linea(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        ln = d.linee.setdefault(s.ctx[0], {})
        if kw(a[0], "login", 3):
            ln["login"] = "" if neg else ("local" if len(a) > 1 and kw(a[1], "local") else "password")
            return ""
        if kw(a[0], "password", 3):
            ln["password"] = "" if neg else " ".join(a[1:])
            return ""
        if kw(a[0], "transport", 3):
            ln["transport"] = "" if neg else " ".join(a[2:] if len(a) > 2 and kw(a[1], "input") else a[1:])
            return ""
        if kw(a[0], "exec-timeout", 3) or kw(a[0], "logging", 3) or kw(a[0], "privilege", 3) or kw(a[0], "history", 3):
            return ""
        raise Errore(INVALIDO)

    def _pmap(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        if kw(t[0], "class", 2) and len(t) >= 2:
            s.modo, s.ctx = "pmapc", [s.ctx[0]]
            return ""
        if kw(t[0], "description", 3):
            return ""
        raise Errore(INVALIDO)

    def _pmapc(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        self.cambiato = True
        if kw(a[0], "shape", 2) and len(a) >= 3 and a[2].isdigit():
            d.policy[s.ctx[0]] = 0 if neg else int(a[2]) // 1000
            return ""
        if kw(a[0], "police", 3) and len(a) >= 2:
            v = a[2] if len(a) > 2 and kw(a[1], "rate") else a[1]
            if v.isdigit():
                d.policy[s.ctx[0]] = 0 if neg else int(v) // 1000
                return ""
        if kw(a[0], "bandwidth", 3) or kw(a[0], "priority", 3):
            return ""
        raise Errore(INVALIDO)

    def _vlan_modo(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        if kw(t[0], "name", 2) and len(t) >= 2:
            d.vlan[s.ctx[0]] = t[1][:32]
            return ""
        raise Errore(INVALIDO)

    # ───── firewall ─────
    def _config_fw(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        if not a:
            raise Errore(INCOMPLETO)
        c = a[0].lower()
        if kw(c, "hostname", 3):
            d.hostname = a[1][:24] if len(a) > 1 else d.hostname
            return ""
        if kw(c, "interface", 3):
            porte = self._porte_da(d, " ".join(a[1:]))
            s.modo, s.ctx = "if", porte
            return ""
        self.cambiato = True
        if kw(c, "route", 3):
            if len(a) < 5:
                raise Errore("ERROR: % Incomplete command")
            zona, rete, mask, via = a[1:5]
            if not any(p.nameif == zona for p in d.porte.values()):
                raise Errore(f"ERROR: % «{zona}» non è il nome di una zona (nameif)")
            try:
                n = ipaddress.IPv4Network(f"{rete}/{mask}", strict=False)
                ipaddress.IPv4Address(via)
            except ValueError:
                raise Errore("ERROR: % Invalid input detected")
            voce = [zona, str(n.network_address), str(n.netmask), via]
            if neg:
                d.rotte = [r for r in d.rotte if r != voce]
            elif voce not in d.rotte:
                d.rotte.append(voce)
            return ""
        if kw(c, "access-list", 3):
            if len(a) < 2:
                raise Errore("ERROR: % Incomplete command")
            nome = a[1]
            if neg and len(a) == 2:
                d.acl.pop(nome, None)
                return ""
            if len(a) < 4 or not kw(a[2], "extended"):
                raise Errore("ERROR: % Invalid input detected")
            testo = " ".join(a[3:])
            try:
                A.leggi(testo, True, "fw")
            except A.RegolaNonValida as e:
                raise Errore(f"ERROR: % Invalid input detected ({e})")
            cur = d.acl.setdefault(nome, {"estesa": True, "righe": []})
            if neg:
                cur["righe"] = [r for r in cur["righe"] if r != " ".join(testo.split())]
                if not cur["righe"]:
                    d.acl.pop(nome, None)
            else:
                cur["righe"].append(" ".join(testo.split()))
            return ""
        if kw(c, "access-group", 8):
            if len(a) < 5 or a[2] != "in" or not kw(a[3], "interface"):
                raise Errore("ERROR: % Invalid input detected")
            if neg:
                if d.access_group.get(a[4]) == a[1]:
                    d.access_group.pop(a[4])
                return ""
            d.access_group[a[4]] = a[1]
            return ""
        if kw(c, "clear", 3) or kw(c, "inspect", 3) or kw(c, "policy-map", 3) or kw(c, "class-map", 3):
            return ""
        raise Errore("ERROR: % Invalid input detected")

    def _interfaccia_fw(self, d: Dispositivo, s: Sessione, t: list[str], riga: str) -> str:
        neg = t[0] == "no"
        a = t[1:] if neg else t
        porte = [d.porte[p] for p in s.ctx]
        self.cambiato = True
        c = a[0].lower() if a else ""
        if kw(c, "interface", 3):
            return self._config_fw(d, s, t, riga)
        if kw(c, "nameif", 3):
            for p in porte:
                p.nameif = "" if neg else (a[1] if len(a) > 1 else "")
            return ""
        if kw(c, "security-level", 3):
            for p in porte:
                p.livello = None if neg else int(a[1])
            return ""
        if kw(c, "ip", 2) and len(a) >= 2 and kw(a[1], "address", 2):
            if neg:
                for p in porte:
                    p.ip = p.mask = ""
                return ""
            try:
                i = ipaddress.IPv4Interface(f"{a[2]}/{a[3]}")
            except (ValueError, IndexError):
                raise Errore("ERROR: % Invalid input detected")
            for p in porte:
                p.ip, p.mask = str(i.ip), str(i.netmask)
            return ""
        if kw(c, "shutdown", 2):
            for p in porte:
                p.shutdown = not neg
            return ""
        if kw(c, "description", 3) or kw(c, "speed", 2) or kw(c, "duplex", 2):
            return ""
        raise Errore("ERROR: % Invalid input detected")

    # ───── show ─────
    def _show(self, d: Dispositivo, a: list[str]) -> str:
        if not a:
            raise Errore(INCOMPLETO)
        filtro = ""
        if "|" in a:
            i = a.index("|")
            if len(a) > i + 2 and kw(a[i + 1], "include", 1):
                filtro = " ".join(a[i + 2:])
            a = a[:i]
        c = a[0].lower()
        if kw(c, "running-config", 3) or kw(c, "startup-config", 3):
            out = self.running_config(d)
        elif kw(c, "ip", 2) and len(a) >= 2 and kw(a[1], "route", 2):
            out = self._show_route(d)
        elif kw(c, "route", 3) and d.tipo == "firewall":
            out = self._show_route(d)
        elif kw(c, "ip", 2) and len(a) >= 3 and kw(a[1], "interface") and kw(a[2], "brief"):
            out = self._show_brief(d)
        elif kw(c, "interfaces", 3) and len(a) >= 2 and kw(a[1], "status"):
            out = self._show_brief(d)
        elif kw(c, "interface", 3) and d.tipo == "firewall":
            out = self._show_brief(d)
        elif kw(c, "vlan", 1):
            out = self._show_vlan(d)
        elif kw(c, "access-lists", 3) or (kw(c, "ip", 2) and len(a) >= 2 and kw(a[1], "access-lists", 3)):
            out = "\n".join(f"{'Extended' if v['estesa'] else 'Standard'} IP access list {k}\n"
                            + "\n".join(f"    {10 * (i + 1)} {r}" for i, r in enumerate(v["righe"]))
                            for k, v in d.acl.items())
        elif kw(c, "ip", 2) and len(a) >= 3 and kw(a[1], "ospf") and kw(a[2], "neighbor", 3):
            out = self._show_ospf(d)
        elif kw(c, "mac", 2) or kw(c, "mac-address-table", 4):
            out = "          Mac Address Table\n-------------------------------------------\n\nVlan    Mac Address       Type        Ports"
        elif kw(c, "version", 2):
            out = f"{d.hostname} — Penguin Inc. network OS (Linux, Mininet)"
        elif kw(c, "standby", 3):
            out = "\n".join(f"{p.nome} - Group {g}\n  Virtual IP address is {st['ip']}\n  Priority {st['priority']}"
                            for p in d.porte.values() for g, st in p.standby.items())
        elif kw(c, "ip", 2) and len(a) >= 3 and kw(a[1], "dhcp") and kw(a[2], "pool"):
            out = "\n".join(f"Pool {n} :\n network {v['net']} {v['mask']}\n default-router {v['gw']}" for n, v in d.pool.items())
        else:
            raise Errore(INVALIDO)
        if filtro:
            out = "\n".join(l for l in out.splitlines() if filtro in l)
        return out

    def _show_route(self, d: Dispositivo) -> str:
        tab = self.rete.tabelle().get(d.nome, [])
        righe = ["Codes: C - connected, S - static, O - OSPF", "", "Gateway of last resort is " +
                 next((r["via"] for r in tab if r["net"].prefixlen == 0), "not set"), ""]
        for r in sorted(tab, key=lambda r: (int(r["net"].network_address), r["net"].prefixlen)):
            if r["proto"] == "C":
                righe.append(f"C    {r['net']} is directly connected, {r['port']}")
            else:
                dist = "[1/0]" if r["proto"] == "S" else "[110/2]"
                righe.append(f"{r['proto']}    {r['net']} {dist} via {r['via']}, {r['port']}")
        return "\n".join(righe)

    def _show_brief(self, d: Dispositivo) -> str:
        righe = [f"{'Interface':<26}{'IP-Address':<16}{'OK?':<4}{'Method':<7}{'Status':<22}Protocol"]
        for p in d.porte.values():
            su = self.rete.porta_su(d.nome, p.nome)
            stato = "administratively down" if p.shutdown else ("up" if su else "down")
            righe.append(f"{p.nome:<26}{(p.ip or 'unassigned'):<16}{'YES':<4}{'manual':<7}{stato:<22}{'up' if su else 'down'}")
        return "\n".join(righe)

    def _show_vlan(self, d: Dispositivo) -> str:
        if d.tipo != "switch":
            raise Errore(INVALIDO)
        righe = [f"{'VLAN':<5}{'Name':<33}{'Status':<10}Ports", "---- " + "-" * 32 + " --------- " + "-" * 30]
        for v, nome in sorted(d.vlan.items(), key=lambda x: int(x[0])):
            porte = [p.nome.replace("FastEthernet", "Fa").replace("GigabitEthernet", "Gi")
                     for p in d.porte.values() if not p.trunk and p.vlan == int(v)]
            righe.append(f"{v:<5}{nome:<33}{'active':<10}{', '.join(porte)}")
        return "\n".join(righe)

    def _show_ospf(self, d: Dispositivo) -> str:
        righe = ["Neighbor ID     Pri   State           Dead Time   Address         Interface"]
        vicini = set()
        for r in self.rete._ospf(self.rete.domini(), {n: [(p, i) for p, i in x.interfacce() if self.rete.porta_su(n, p.nome)]
                                                      for n, x in self.rete.disp.items() if x.tipo == "router"}).get(d.nome, []):
            vicini.add((r["via"], r["port"]))
        for via, porta in sorted(vicini):
            righe.append(f"{via:<16}{'1':<6}{'FULL/  -':<16}{'00:00:35':<12}{via:<16}{porta}")
        return "\n".join(righe)

    # ───── running-config ─────
    def running_config(self, d: Dispositivo) -> str:
        if d.tipo == "firewall":
            return self._running_fw(d)
        r = ["!", "version 15.1", f"{'service password-encryption' if d.cifra_password else 'no service password-encryption'}",
             "!", f"hostname {d.hostname or d.nome}", "!"]
        if d.enable_secret:
            r.append(f"enable secret 5 {segreto5(d.enable_secret)}")
        if d.enable_password:
            r.append(f"enable password {'7 ' + tipo7(d.enable_password) if d.cifra_password else d.enable_password}")
        for da, a in d.esclusi:
            r.append(f"ip dhcp excluded-address {da}" + (f" {a}" if a != da else ""))
        for nome, v in d.pool.items():
            r += ["!", f"ip dhcp pool {nome}"]
            if v.get("net"):
                r.append(f" network {v['net']} {v['mask']}")
            if v.get("gw"):
                r.append(f" default-router {v['gw']}")
            if v.get("dns"):
                r.append(f" dns-server {v['dns']}")
        if d.dominio:
            r.append(f"ip domain-name {d.dominio}")
        for u, v in d.utenti.items():
            priv = f" privilege {v['privilege']}" if v.get("privilege", 1) != 1 else ""
            if v["tipo"] == "secret":
                r.append(f"username {u}{priv} secret 5 {segreto5(v['valore'])}")
            elif d.cifra_password:
                r.append(f"username {u}{priv} password 7 {tipo7(v['valore'])}")
            else:
                r.append(f"username {u}{priv} password 0 {v['valore']}")
        if d.ssh:
            r.append("ip ssh version 2")
        if d.tipo == "switch":
            for v, nome in sorted(d.vlan.items(), key=lambda x: int(x[0])):
                if v != "1":
                    r += ["!", f"vlan {v}", f" name {nome}"]
        for nome, kbps in d.policy.items():
            r += ["!", f"policy-map {nome}", " class class-default", f"  shape average {kbps * 1000}"]
        for p in d.porte.values():
            r += ["!", f"interface {p.nome}"]
            if p.descrizione:
                r.append(f" description {p.descrizione}")
            if p.dot1q:
                r.append(f" encapsulation dot1Q {p.dot1q}")
            if d.tipo == "switch":
                if p.trunk:
                    r.append(" switchport mode trunk")
                    if p.trunk_vlan is not None:
                        r.append(f" switchport trunk allowed vlan {','.join(map(str, p.trunk_vlan))}")
                elif p.vlan != 1:
                    r += [f" switchport access vlan {p.vlan}", " switchport mode access"]
                if p.psec:
                    r.append(" switchport port-security")
                    if p.psec.get("max", 1) != 1:
                        r.append(f" switchport port-security maximum {p.psec['max']}")
                    if p.psec.get("violation", "shutdown") != "shutdown":
                        r.append(f" switchport port-security violation {p.psec['violation']}")
                    if p.psec.get("sticky"):
                        r.append(" switchport port-security mac-address sticky")
            elif p.ip:
                r.append(f" ip address {p.ip} {p.mask}")
            elif d.tipo == "router" and "." not in p.nome:
                r.append(" no ip address")
            if p.acl_in:
                r.append(f" ip access-group {p.acl_in} in")
            if p.acl_out:
                r.append(f" ip access-group {p.acl_out} out")
            if p.ospf_p2p:
                r.append(" ip ospf network point-to-point")
            for g, st in p.standby.items():
                r += [f" standby {g} ip {st['ip']}"] + ([f" standby {g} priority {st['priority']}"] if st["priority"] != 100 else []) \
                    + ([f" standby {g} preempt"] if st.get("preempt") else [])
            if p.policy_out:
                r.append(f" service-policy output {p.policy_out}")
            if p.shutdown:
                r.append(" shutdown")
        for pid, proc in d.ospf.items():
            r += ["!", f"router ospf {pid}"]
            if proc.get("router_id"):
                r.append(f" router-id {proc['router_id']}")
            r += [f" passive-interface {x}" for x in proc.get("passive", [])]
            r += [f" network {a} {w} area {ar}" for a, w, ar in proc.get("reti", [])]
            if proc.get("default"):
                r.append(" default-information originate")
        r.append("!")
        for net, mask, via in d.rotte:
            r.append(f"ip route {net} {mask} {via}")
        for k, v in d.acl.items():
            if k.isdigit():
                r += [f"access-list {k} {x}" for x in v["righe"]]
            else:
                r += [f"ip access-list {'extended' if v['estesa'] else 'standard'} {k}"] + [f" {x}" for x in v["righe"]]
        if d.banner:
            r += ["!", f"banner motd ^C{d.banner}^C"]
        for ln, nomi in (("con", "line con 0"), ("vty", "line vty 0 4")):
            cfg = d.linee.get(ln, {})
            r += ["!", nomi]
            if cfg.get("password"):
                r.append(f" password {'7 ' + tipo7(cfg['password']) if d.cifra_password else cfg['password']}")
            if cfg.get("login"):
                r.append(" login local" if cfg["login"] == "local" else " login")
            if cfg.get("transport"):
                r.append(f" transport input {cfg['transport']}")
        r += ["!", "end"]
        return "\n".join(r)

    def _running_fw(self, d: Dispositivo) -> str:
        r = [": Saved", ":", f"hostname {d.hostname or d.nome}", "!"]
        for p in d.porte.values():
            r.append(f"interface {p.nome}")
            if p.nameif:
                r.append(f" nameif {p.nameif}")
            if p.livello is not None:
                r.append(f" security-level {p.livello}")
            r.append(f" ip address {p.ip} {p.mask}" if p.ip else " no ip address")
            if p.shutdown:
                r.append(" shutdown")
            r.append("!")
        for k, v in d.acl.items():
            r += [f"access-list {k} extended {x}" for x in v["righe"]]
        for zona, nome in d.access_group.items():
            r.append(f"access-group {nome} in interface {zona}")
        for z, net, mask, via in d.rotte:
            r.append(f"route {z} {net} {mask} {via} 1")
        return "\n".join(r)


def audit(d: Dispositivo) -> list[dict]:
    """Le debolezze della configurazione di un router o switch: [{"code", "severity", "user"?}]."""
    f = []
    if not d.enable_secret:
        f.append({"code": "NO_ENABLE_SECRET", "severity": "high"})
    if d.enable_password:
        f.append({"code": "ENABLE_PASSWORD_PRESENT", "severity": "medium"})
    for u, v in d.utenti.items():
        if v["tipo"] == "password":
            f.append({"code": "USER_CREDENTIAL_REVERSIBLE", "severity": "high" if not d.cifra_password else "medium",
                      "user": u})
    if not d.utenti:
        f.append({"code": "NO_LOCAL_USERS", "severity": "medium"})
    if not d.cifra_password:
        f.append({"code": "NO_SERVICE_PASSWORD_ENCRYPTION", "severity": "medium"})
    if not d.banner:
        f.append({"code": "NO_BANNER_MOTD", "severity": "low"})
    return f
