"""La fotografia della rete (dispositivi, porte, cavi) con le funzioni che servono a missioni e livelli.

La produce il modello (modello.Rete.snapshot()); il formato è quello del campo «topology» dell'API (docs/API.md).
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

ZERO = ("", "0.0.0.0")  # valori che vogliono dire «nessun indirizzo»


def short_port(name: str) -> str:
    """Nome corto di una porta, come nella console: GigabitEthernet0/1 → Gi0/1, FastEthernet0 → Fa0."""
    for long, short in (("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("Serial", "Se"), ("Ethernet", "Eth")):
        if name.startswith(long):
            return short + name[len(long):]
    return name


@dataclass
class Topo:
    """La fotografia della rete, con le funzioni che servono a missioni e livelli.

    devices e links hanno lo stesso formato del campo «topology» dell'API (docs/API.md), più «kind».
    """
    devices: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)

    @classmethod
    def parse(cls, data: dict) -> "Topo":
        """Costruisce la Topo dalla fotografia del modello (modello.Rete.snapshot())."""
        return cls(data["devices"], data["links"])

    def dev(self, name: str) -> dict | None:
        """Il dispositivo con quel nome, o None."""
        return next((d for d in self.devices if d["name"] == name), None)

    def of(self, kind: str) -> list[dict]:
        """Tutti i dispositivi di un tipo (router, switch, pc, server)."""
        return [d for d in self.devices if d["kind"] == kind]

    def hosts(self) -> list[dict]:
        """Tutti i PC e i server."""
        return [d for d in self.devices if d["kind"] in ("pc", "server")]

    @staticmethod
    def port(d: dict, name: str) -> dict | None:
        """La porta con quel nome (completo) del dispositivo d, o None."""
        return next((p for p in d["ports"] if p["n"] == name), None)

    @staticmethod
    def iface(d: dict) -> ipaddress.IPv4Interface | None:
        """Il primo IPv4 configurato di un host (169.254.x.x vuol dire che il DHCP non ha risposto: nessun indirizzo)."""
        for p in d["ports"]:
            if p["ip"] not in ZERO and p["mask"] not in ZERO and not p["ip"].startswith("169.254."):
                try:
                    return ipaddress.IPv4Interface(f"{p['ip']}/{p['mask']}")
                except ValueError:
                    return None
        return None

    @staticmethod
    def port_iface(p: dict) -> ipaddress.IPv4Interface | None:
        """L'IPv4 di una porta (indirizzo + maschera), o None."""
        if p["ip"] in ZERO or p["mask"] in ZERO:
            return None
        try:
            return ipaddress.IPv4Interface(f"{p['ip']}/{p['mask']}")
        except ValueError:
            return None

    def neighbors(self, name: str) -> list[tuple[str, str, str]]:
        """(mia_porta, vicino, porta_del_vicino) per ogni cavo del dispositivo."""
        out = []
        for l in self.links:
            if l["a"] == name:
                out.append((l["pa"], l["b"], l["pb"]))
            elif l["b"] == name:
                out.append((l["pb"], l["a"], l["pa"]))
        return out

    def switch_of(self, host: str) -> str | None:
        """Lo switch a cui è collegato l'host, o None."""
        for _, peer, _ in self.neighbors(host):
            d = self.dev(peer)
            if d and d["kind"] == "switch":
                return peer
        return None

    def hosts_on(self, switch: str) -> list[dict]:
        """Gli host collegati direttamente allo switch."""
        return [d for d in self.hosts() if self.switch_of(d["name"]) == switch]

    def router_ports(self, kind_filter: str | None = None) -> list[tuple[dict, dict, ipaddress.IPv4Interface | None, str | None]]:
        """(router, porta, interfaccia IPv4 o None, vicino o None) per ogni porta di ogni router.

        Con kind_filter restituisce solo le porte cablate verso un dispositivo di quel tipo.
        """
        out = []
        for r in self.of("router"):
            peers = {mp: peer for mp, peer, _ in self.neighbors(r["name"])}
            for p in r["ports"]:
                peer = peers.get(p["n"])
                if kind_filter:
                    pd = self.dev(peer) if peer else None
                    if not pd or pd["kind"] != kind_filter:
                        continue
                out.append((r, p, self.port_iface(p), peer))
        return out

    def free_port(self, d: dict, peer_kind: str) -> str | None:
        """La prima porta libera di d adatta a collegare un dispositivo di tipo peer_kind (sottointerfacce escluse).

        Switch: Gigabit verso router e switch, FastEthernet verso gli host. Router: Gigabit, poi Fast, poi seriali.
        """
        if d["kind"] == "switch":
            fams = ["GigabitEthernet", "FastEthernet"] if peer_kind in ("router", "switch") else ["FastEthernet", "GigabitEthernet"]
        elif d["kind"] == "router":
            fams = ["GigabitEthernet", "FastEthernet", "Serial"]
        elif d["kind"] == "firewall":
            fams = ["GigabitEthernet"]
        else:
            fams = ["FastEthernet", "GigabitEthernet", "Ethernet"]
        for fam in fams:
            for p in d["ports"]:
                if p["n"].startswith(fam) and "." not in p["n"] and not p["link"]:
                    return p["n"]
        return None

    def next_name(self, prefix: str) -> str:
        """Il primo nome libero con quel prefisso: PC1, PC2, …"""
        used = {d["name"] for d in self.devices}
        i = 1
        while f"{prefix}{i}" in used:
            i += 1
        return f"{prefix}{i}"

    def to_json(self) -> dict:
        """La forma pubblica della topologia (campo «topology» dello Stato)."""
        return {"devices": self.devices, "links": self.links}
