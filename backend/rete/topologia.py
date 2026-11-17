"""Le topologie di partenza (gioco/reti/*.yaml) → modello della rete.

Formato:
  dispositivi:
    - nome: R1
      tipo: router | switch | pc | server | firewall
      pos: [x, y]
      porte: [...]            facoltativo: porte diverse da quelle standard del tipo
      ip: 192.168.1.10/24     PC e server: indirizzo fisso, oppure «dhcp»
      gateway: 192.168.1.1    PC e server
      dns: 192.168.1.53       PC e server
      servizi: [http, ftp]    server: i servizi accesi (gli altri spenti)
      config: |               router, switch e firewall: righe della console, in modalità configurazione
        interface GigabitEthernet0/0
         ip address 192.168.1.1 255.255.255.0
         no shutdown
  cavi:
    - [R1, GigabitEthernet0/0, SW1, GigabitEthernet0/1]
"""
from __future__ import annotations

import ipaddress

from .console import Console
from .modello import SERVIZI, TIPI, Rete, nuovo_dispositivo


class TopologiaNonValida(ValueError):
    pass


def carica(rete: Rete, console: Console, dati: dict, origine: str = "topologia") -> None:
    """Aggiunge a «rete» (vuota) i dispositivi e i cavi di una topologia. TopologiaNonValida se qualcosa non va."""
    if not isinstance(dati, dict) or not isinstance(dati.get("dispositivi"), list):
        raise TopologiaNonValida(f"{origine}: serve l'elenco «dispositivi»")
    config = []
    for i, v in enumerate(dati["dispositivi"], 1):
        w = f"{origine}, dispositivo {i}"
        if not isinstance(v, dict) or v.get("tipo") not in TIPI or not str(v.get("nome", "")).strip():
            raise TopologiaNonValida(f"{w}: servono «nome» e «tipo» ({', '.join(TIPI)})")
        pos = v.get("pos") or [300, 300]
        d = nuovo_dispositivo(v["tipo"], str(v["nome"]), int(pos[0]), int(pos[1]), v.get("porte"))
        if v["tipo"] in ("pc", "server"):
            ip = str(v.get("ip") or "").strip()
            if ip == "dhcp":
                d.dhcp = True
            elif ip:
                try:
                    i_ = ipaddress.IPv4Interface(ip)
                except ValueError:
                    raise TopologiaNonValida(f"{w}: «ip» non valido: {ip}")
                p = next(iter(d.porte.values()))
                p.ip, p.mask = str(i_.ip), str(i_.netmask)
            d.gw = str(v.get("gateway") or "")
            d.dns = str(v.get("dns") or "")
        if v["tipo"] == "server" and "servizi" in v:
            d.servizi = {s: s in (v.get("servizi") or []) for s in SERVIZI}
        try:
            rete.aggiungi(d)
        except ValueError as e:
            raise TopologiaNonValida(f"{w}: {e}")
        if v.get("config"):
            config.append((d.nome, str(v["config"]).splitlines()))
    for i, c in enumerate(dati.get("cavi") or [], 1):
        if not isinstance(c, list) or len(c) != 4:
            raise TopologiaNonValida(f"{origine}, cavo {i}: [dispositivo, porta, dispositivo, porta]")
        try:
            rete.collega(*map(str, c))
        except ValueError as e:
            raise TopologiaNonValida(f"{origine}, cavo {i}: {e}")
    for nome, righe in config:
        ok, testo, _ = console.esegui(nome, ["enable", "configure terminal", *righe, "end"])
        if not ok:
            errori = [l for l in testo.splitlines() if l.startswith(("%", "ERROR"))]
            raise TopologiaNonValida(f"{origine}, configurazione di {nome}: {' '.join(errori[:3])}")
        console.chiudi(nome)
