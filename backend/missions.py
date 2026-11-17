"""Regole delle missioni dell'Accademia: i controlli sulla rete vera che le missioni usano.

Ogni passo è un controllo sulla *fotografia* (istantaneo, ricalcolato a ogni lettura dello stato) oppure un
*ping* vero eseguito sulla rete quando il giocatore preme «Verifica».
I controlli sono tolleranti: accettano qualsiasi indirizzamento sensato, gli IP suggeriti sono solo un aiuto.

I contenuti delle missioni (testi, punti, ordine dei passi) stanno in accademia.yaml del pacchetto di gioco
(contenuti.py): qui ci sono solo i controlli e i piani di ping, nei registri CONTROLLI e PIANI_PING, che il
YAML richiama per nome. Come aggiungere una missione: docs/ARCHITETTURA.md, «Aggiungere una missione».
Questo file non tocca la rete: lavora solo sulla Topo che riceve.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Callable

import rescue as RS
from rete.topo import Topo, short_port

# Controllo di un passo: (fatto, cosa manca detto al giocatore).
Check = Callable[[Topo], tuple[bool, str]]
# Piano di ping di un passo: le coppie (sorgente, IP di destinazione, etichetta) da provare, la migliore per prima.
PingPlan = Callable[[Topo], list[tuple[str, str, str]]]


@dataclass
class Step:
    """Un passo della missione. Il tipo (kind) dice come si completa:

    controllo  check(t) sulla fotografia: si spunta da solo mentre il giocatore lavora
    ping       piano di ping tollerante (PIANI_PING): ping vero quando il giocatore preme «Verifica»
    verifica   verifiche esatte sulla rete vera (rescue.Check: ping, bloccato, attraversa…) a «Verifica»;
               devono passare tutte
    domanda    una domanda al giocatore: {"testo", "scelte", "giusta", "spiegazione"}; si completa rispondendo
    osserva    il giocatore guarda il viaggio di un ping (traccia passo per passo): {"da", "a", "spiegazione"}
    """
    text: str
    hint: str
    check: Check | None = None
    ping: PingPlan | None = None
    kind: str = "controllo"
    verifiche: list = field(default_factory=list)
    domanda: dict | None = None
    osserva: dict | None = None

    @property
    def interattivo(self) -> bool:
        """Il passo lo completa un'azione del giocatore (risposta, traccia), non la rete."""
        return self.kind in ("domanda", "osserva")

    @property
    def al_verifica(self) -> bool:
        """Il passo si controlla con traffico vero quando il giocatore preme «Verifica»."""
        return self.kind in ("ping", "verifica")


@dataclass
class Mission:
    """Una missione (una «challenge» di un giorno): id, titolo, punti, storia, teoria in pillole
    [(concetto, spiegazione)], passi.

    rete       la topologia di partenza, preparata quando la missione comincia: il nome di un
               file .yaml (nella cartella reti/ del pacchetto di gioco), oppure "vuota" (il tavolo viene svuotato).
               "" = si continua dalla rete che c'è.
    tutorial   True: missione guidata (aiuti dei passi e messaggi su cosa manca). False: il giocatore vede solo
               le condizioni da raggiungere, l'aiuto lo chiede al collega senior (consigli).
    topologia  disegno in testo della topologia da raggiungere (mostrato al giocatore)
    piano      piano di indirizzamento: righe di celle, la prima è l'intestazione
    consigli   i consigli del collega senior, dal più vago al più preciso
    traffic    traffico di fondo della rete per lo sniffer: [(dispositivo, comando)], come il «traffico» dei
               livelli («ping IP», «telnet IP porta», «web http://IP», IP anche come @PC2)
    """
    id: str
    title: str
    points: int
    story: str
    theory: list[tuple[str, str]]
    steps: list[Step] = field(default_factory=list)
    rete: str = ""
    topologia: str = ""
    piano: list[list[str]] = field(default_factory=list)
    consigli: list[str] = field(default_factory=list)
    tutorial: bool = False
    traffic: list[tuple[str, str]] = field(default_factory=list)


def ok(msg: str = "") -> tuple[bool, str]:
    return True, msg


def no(msg: str) -> tuple[bool, str]:
    return False, msg


# ───────────────────────────── controlli riutilizzabili ─────────────────────────────
def lan_switches(t: Topo, min_pcs: int = 1) -> list[str]:
    """Gli switch con almeno min_pcs PC collegati."""
    return [s["name"] for s in t.of("switch") if len([h for h in t.hosts_on(s["name"]) if h["kind"] == "pc"]) >= min_pcs]


def same_net_pairs(t: Topo, members: list[dict]) -> list[tuple[dict, dict, ipaddress.IPv4Interface, ipaddress.IPv4Interface]]:
    """Le coppie di host della lista che stanno nella stessa rete con IP diversi."""
    out = []
    for a in members:
        ia = t.iface(a)
        for b in members:
            ib = t.iface(b)
            if a is not b and ia and ib and ia.network == ib.network and ia.ip != ib.ip:
                out.append((a, b, ia, ib))
    return out


def main_lan(t: Topo) -> str | None:
    """Lo switch con più host indirizzati: la LAN dell'«ufficio» delle prime missioni."""
    best, n = None, 0
    for s in t.of("switch"):
        k = len([h for h in t.hosts_on(s["name"]) if t.iface(h)])
        if k > n:
            best, n = s["name"], k
    return best


def lan_net(t: Topo, sw: str) -> ipaddress.IPv4Network | None:
    """La rete più usata dagli host di uno switch."""
    nets = [t.iface(h).network for h in t.hosts_on(sw) if t.iface(h)]
    return max(set(nets), key=nets.count) if nets else None


def router_port_to(t: Topo, sw: str) -> tuple[dict, dict] | None:
    """(router, porta) del primo router cablato a quello switch."""
    for r, p, _, peer in t.router_ports():
        if peer == sw:
            return r, p
    return None


def gw_ok(t: Topo, h: dict) -> bool:
    """Il gateway dell'host è l'IP di una porta di router, nella stessa rete dell'host."""
    ih = t.iface(h)
    gw = h.get("gw", "")
    if not ih or gw in ("", "0.0.0.0"):
        return False
    try:
        g = ipaddress.IPv4Address(gw)
    except ValueError:
        return False
    if g not in ih.network:
        return False
    return any(i and i.ip == g for _, _, i, _ in t.router_ports())


def lans_behind_routers(t: Topo) -> list[tuple[str, str, ipaddress.IPv4Network]]:
    """(router, switch, rete) per ogni switch cablato a una porta di router che ha un IP."""
    out = []
    for r, p, i, peer in t.router_ports("switch"):
        if i:
            out.append((r["name"], peer, i.network))
    return out


def addressed_pcs_in(t: Topo, net: ipaddress.IPv4Network, sw: str | None = None) -> list[dict]:
    """I PC con un IP dentro net (ed eventualmente collegati allo switch sw)."""
    return [h for h in t.of("pc") if t.iface(h) and t.iface(h).ip in net and (sw is None or t.switch_of(h["name"]) == sw)]


# ───────────────────────────── mission 1 ─────────────────────────────
def m1_switch(t):
    return ok() if t.of("switch") else no("Non c'è ancora nessuno switch.")


def m1_pcs(t):
    n = len(t.of("pc"))
    return ok() if n >= 2 else no(f"PC sul tavolo: {n} su 2.")


def m1_cabled(t):
    return ok() if lan_switches(t, 2) else no("Nessuno switch ha ancora 2 PC collegati.")


def m1_ip(t):
    for sw in lan_switches(t, 2):
        pcs = [h for h in t.hosts_on(sw) if h["kind"] == "pc"]
        if same_net_pairs(t, pcs):
            return ok()
        if len([h for h in pcs if t.iface(h)]) >= 2:
            return no("I due PC hanno un IP ma non sono nella stessa rete: guarda i primi tre numeri e la maschera.")
    return no("Servono 2 PC sullo stesso switch con un indirizzo IP.")


def m1_ping(t):
    for sw in lan_switches(t, 2):
        pcs = [h for h in t.hosts_on(sw) if h["kind"] == "pc"]
        for a, b, _, ib in same_net_pairs(t, pcs):
            return [(a["name"], str(ib.ip), f"{a['name']} → {b['name']}")]
    return []


# ───────────────────────────── mission 2 ─────────────────────────────
def m2_server(t):
    return ok() if t.of("server") else no("Manca il server.")


def m2_cabled(t):
    for s in t.of("server"):
        sw = t.switch_of(s["name"])
        if sw and any(h["kind"] == "pc" for h in t.hosts_on(sw)):
            return ok()
    return no("Il server deve stare sullo stesso switch dei PC.")


def m2_ip(t):
    for s in t.of("server"):
        sw = t.switch_of(s["name"])
        isv = t.iface(s)
        if not sw or not isv:
            continue
        if any(t.iface(h) and t.iface(h).network == isv.network and h is not s for h in t.hosts_on(sw)):
            return ok()
        return no(f"{s['name']} ha un IP ({isv}) ma non è nella rete dei PC.")
    return no("Il server non ha ancora un indirizzo IP.")


def m2_ping(t):
    for s in t.of("server"):
        sw, isv = t.switch_of(s["name"]), t.iface(s)
        if not sw or not isv:
            continue
        for h in t.hosts_on(sw):
            if h["kind"] == "pc" and t.iface(h) and t.iface(h).network == isv.network:
                return [(h["name"], str(isv.ip), f"{h['name']} → {s['name']}")]
    return []


# ───────────────────────────── mission 3 ─────────────────────────────
def m3_router(t):
    return ok() if t.of("router") else no("Manca il router.")


def m3_cabled(t):
    sw = main_lan(t)
    if not sw:
        return no("Prima servono PC con un IP collegati a uno switch.")
    return ok() if router_port_to(t, sw) else no(f"Collega il router allo switch {sw}, dove ci sono i PC.")


def m3_rip(t):
    sw = main_lan(t)
    rp = router_port_to(t, sw) if sw else None
    if not rp:
        return no("Il router non è ancora collegato allo switch dei PC.")
    r, p = rp
    i = Topo.port_iface(p)
    net = lan_net(t, sw)
    if not i:
        return no(f"La porta {p['n']} di {r['name']} non ha un indirizzo IP.")
    if net and i.network != net:
        return no(f"{r['name']} {p['n']} ha {i}, ma i PC sono nella rete {net}.")
    if not p["on"]:
        return no(f"La porta {p['n']} di {r['name']} è spenta (shutdown): accendila.")
    return ok()


def m3_gw(t):
    sw = main_lan(t)
    if not sw:
        return no("Nessuna LAN con PC.")
    pcs = [h for h in t.hosts_on(sw) if h["kind"] in ("pc", "server") and t.iface(h)]
    bad = [h["name"] for h in pcs if not gw_ok(t, h)]
    if not pcs:
        return no("Nessun PC con IP.")
    return no("Gateway mancante o sbagliato su: " + ", ".join(bad)) if bad else ok()


def m3_ping(t):
    sw = main_lan(t)
    for h in (t.hosts_on(sw) if sw else []):
        if h["kind"] == "pc" and gw_ok(t, h):
            return [(h["name"], h["gw"], f"{h['name']} → gateway {h['gw']}")]
    return []


# ───────────────────────────── mission 4 ─────────────────────────────
def m4_lans(t):
    lans = lans_behind_routers(t)
    if len({sw for _, sw, _ in lans}) >= 2 and len({n for _, _, n in lans}) >= 2:
        return ok()
    if len(t.of("switch")) < 2:
        return no("Serve un secondo switch per il nuovo reparto.")
    # Diagnosi precisa: cosa manca sulla porta del router che porta al nuovo switch?
    cablati = {sw for _, sw, _ in lans}
    per_switch: dict[str, list] = {}
    for r, p, i, peer in t.router_ports("switch"):
        per_switch.setdefault(peer, []).append((r, p, i))
    for sw, porte in per_switch.items():
        if len(porte) > 1:
            nomi = ", ".join(short_port(p["n"]) for _, p, _ in porte)
            return no(f"{porte[0][0]['name']} è collegato a {sw} con più cavi ({nomi}): ogni reparto vuole un solo "
                      f"collegamento. Togli il cavo in più e usa una porta libera per il nuovo switch.")
    for s in t.of("switch"):
        if s["name"] in cablati:
            continue
        porte = per_switch.get(s["name"])
        if not porte:
            return no(f"{s['name']} non è collegato a nessun router: collegalo a una porta libera del router.")
        r, p, i = porte[0]
        if not i:
            return no(f"{s['name']} è collegato a {r['name']} {short_port(p['n'])}, ma quella porta non ha un IP: "
                      f"seleziona {r['name']}, clicca {short_port(p['n'])} e dagli un IP di una rete nuova.")
        if not p["on"]:
            return no(f"La porta {short_port(p['n'])} di {r['name']} verso {s['name']} è spenta: accendila.")
        return no(f"La porta {short_port(p['n'])} di {r['name']} verso {s['name']} ha un IP della stessa rete "
                  f"dell'altro reparto: usa una rete diversa (es. 192.168.20.1).")
    return no("Collega il secondo switch a un'altra porta del router e dai a quella porta un IP di una rete diversa.")


def m4_pc(t):
    lans = lans_behind_routers(t)
    nets_with_pc = {n for _, sw, n in lans if addressed_pcs_in(t, n, sw)}
    if len(nets_with_pc) >= 2:
        return ok()
    return no("Nel nuovo reparto serve almeno un PC con un IP della sua rete.")


def m4_gw(t):
    bad = []
    for _, sw, n in lans_behind_routers(t):
        for h in addressed_pcs_in(t, n, sw):
            if not gw_ok(t, h):
                bad.append(h["name"])
    return no("Gateway mancante o sbagliato su: " + ", ".join(bad)) if bad else ok()


def cross_lan_ping(t: Topo, min_lans: int = 2) -> list[tuple[str, str, str]]:
    """Piano di ping tra PC di LAN diverse dietro i router."""
    lans = lans_behind_routers(t)
    groups = []
    for _, sw, n in lans:
        pcs = [h for h in addressed_pcs_in(t, n, sw) if gw_ok(t, h)]
        if pcs:
            groups.append(pcs)
    out = []
    for i, ga in enumerate(groups):
        for gb in groups[i + 1:]:
            a, b = ga[0], gb[0]
            out.append((a["name"], str(t.iface(b).ip), f"{a['name']} → {b['name']}"))
    return out


# ───────────────────────────── mission 5: DHCP ─────────────────────────────
def m5_pool(t):
    for r in t.of("router"):
        for pl in r.get("pools", []):
            if pl["net"] not in ("", "0.0.0.0") and pl["gw"] not in ("", "0.0.0.0"):
                return ok()
            if pl["name"]:
                return no(f"Il pool {pl['name']} su {r['name']} esiste ma manca la rete o il default-router.")
    return no("Nessun router ha ancora un pool DHCP.")


def m5_client(t):
    if any(h.get("dhcp") for h in t.of("pc")):
        return ok()
    return no("Nessun PC è impostato su DHCP.")


def m5_lease(t):
    pools = []
    for r in t.of("router"):
        for pl in r.get("pools", []):
            try:
                pools.append(ipaddress.IPv4Network(f"{pl['net']}/{pl['mask']}", strict=False))
            except ValueError:
                pass
    for h in t.of("pc"):
        i = t.iface(h)
        if h.get("dhcp") and i and any(i.ip in n for n in pools) and gw_ok(t, h):
            return ok()
    if any(h.get("dhcp") for h in t.of("pc")):
        return no("Il PC in DHCP non ha ancora ricevuto un indirizzo dal pool (il pool è nella rete giusta?).")
    return no("Imposta un PC su DHCP.")


def m5_ping(t):
    for h in t.of("pc"):
        if h.get("dhcp") and t.iface(h) and gw_ok(t, h):
            for o in t.of("pc"):
                io = t.iface(o)
                if o is not h and io and io.network != t.iface(h).network:
                    return [(h["name"], str(io.ip), f"{h['name']} (DHCP) → {o['name']}")]
            return [(h["name"], h["gw"], f"{h['name']} (DHCP) → gateway")]
    return []


# ───────────────────────────── mission 6: two sites ─────────────────────────────
def r2r_links(t: Topo) -> list[tuple[dict, dict, dict, dict]]:
    """(router A, sua porta, router B, sua porta) per ogni cavo tra due router."""
    out = []
    for l in t.links:
        a, b = t.dev(l["a"]), t.dev(l["b"])
        if a and b and a["kind"] == "router" and b["kind"] == "router":
            out.append((a, Topo.port(a, l["pa"]), b, Topo.port(b, l["pb"])))
    return out


def m6_routers(t):
    if len(t.of("router")) < 2:
        return no("Serve un secondo router (la nuova sede).")
    return ok() if r2r_links(t) else no("Collega i due router tra loro con un cavo.")


def m6_wan(t):
    for a, pa, b, pb in r2r_links(t):
        ia, ib = Topo.port_iface(pa), Topo.port_iface(pb)
        if not ia or not ib:
            return no(f"Dai un IP a entrambe le porte del collegamento {a['name']}–{b['name']}.")
        if ia.network != ib.network:
            return no(f"{a['name']} ({ia}) e {b['name']} ({ib}) devono stare nella stessa rete.")
        if not pa["on"] or not pb["on"]:
            return no("Una delle due porte del collegamento tra router è spenta.")
        return ok()
    return no("Non c'è ancora un collegamento tra router.")


def m6_lan3(t):
    links = r2r_links(t)
    if not links:
        return no("Prima collega i due router.")
    routers_with_lan = {r for r, sw, n in lans_behind_routers(t) if addressed_pcs_in(t, n, sw)}
    if len(routers_with_lan) >= 2:
        return ok()
    return no("Dietro al secondo router serve una LAN: switch + PC con IP e gateway.")


def m6_routes(t):
    """Ogni router deve conoscere ogni LAN: collegata direttamente o tramite una rotta (statica o dinamica)."""
    lans = {n for _, _, n in lans_behind_routers(t)}
    missing = []
    for r in t.of("router"):
        known = set()
        for rt in r.get("routes", []):
            try:
                known.add(ipaddress.IPv4Network(f"{rt['net']}/{rt['mask']}", strict=False))
            except ValueError:
                pass
        default = ipaddress.IPv4Network("0.0.0.0/0") in known
        for n in lans:
            if n not in known and not default:
                missing.append(f"{r['name']} non conosce {n}")
    if len(t.of("router")) < 2:
        return no("Servono due router.")
    return no("; ".join(missing)) if missing else ok()


def m6_ping(t):
    lans = lans_behind_routers(t)
    by_router: dict[str, list[dict]] = {}
    for r, sw, n in lans:
        by_router.setdefault(r, []).extend(h for h in addressed_pcs_in(t, n, sw) if gw_ok(t, h))
    rs = [v for v in by_router.values() if v]
    if len(rs) >= 2:
        a, b = rs[0][0], rs[-1][0]
        return [(a["name"], str(t.iface(b).ip), f"{a['name']} → {b['name']} (altra sede)")]
    return []


# ───────────────────────────── registri per il YAML ─────────────────────────────
# Le missioni (testi, punti, ordine dei passi) sono in accademia.yaml del pacchetto di gioco. Ogni passo lì
# nomina un controllo o un piano di ping di questi due registri: per un controllo nuovo scrivi la funzione
# qui sopra e aggiungila qui con un nome leggibile.

CONTROLLI: dict[str, Check] = {
    # missione «Due PC che si parlano»
    "switch_presente": m1_switch,
    "due_pc_presenti": m1_pcs,
    "due_pc_sullo_stesso_switch": m1_cabled,
    "pc_nella_stessa_rete": m1_ip,
    # «Il server dell'ufficio»
    "server_presente": m2_server,
    "server_sullo_switch_dei_pc": m2_cabled,
    "server_nella_rete_dei_pc": m2_ip,
    # «La porta verso l'esterno»
    "router_presente": m3_router,
    "router_collegato_alla_lan": m3_cabled,
    "porta_router_configurata": m3_rip,
    "gateway_dei_pc": m3_gw,
    # «Due reparti»
    "seconda_lan_sul_router": m4_lans,
    "pc_nella_seconda_lan": m4_pc,
    "gateway_in_tutte_le_lan": m4_gw,
    # «Indirizzi automatici (DHCP)»
    "pool_dhcp_presente": m5_pool,
    "pc_in_dhcp": m5_client,
    "pc_con_indirizzo_dhcp": m5_lease,
    # «Due sedi»
    "due_router_collegati": m6_routers,
    "collegamento_tra_router_configurato": m6_wan,
    "lan_dietro_il_secondo_router": m6_lan3,
    "rotte_complete": m6_routes,
}

PIANI_PING: dict[str, PingPlan] = {
    "ping_tra_pc_della_lan": m1_ping,
    "ping_da_pc_a_server": m2_ping,
    "ping_da_pc_a_gateway": m3_ping,
    "ping_tra_lan": cross_lan_ping,
    "ping_dal_pc_in_dhcp": m5_ping,
    "ping_tra_sedi": m6_ping,
}


def evaluate(m: Mission, t: Topo, fatti: set[int] | dict | None = None) -> list[dict]:
    """Lo stato di ogni passo per il campo «academy.current.steps» dell'API.

    I controlli sulla fotografia vengono ricalcolati; i passi interattivi (domanda, osserva) sono fatti se
    il loro indice è in «fatti» (salvati nella partita); i passi di traffico (ping, verifica) si controllano
    solo con «Verifica», quindi qui restano da fare. Un controllo non riuscito blocca i passi successivi;
    i passi di traffico no (si fanno tutti insieme alla fine), così una domanda dopo un ping resta giocabile.
    """
    fatti = set(fatti or ())
    out, blocked = [], False
    for i, s in enumerate(m.steps):
        if s.kind == "controllo":
            good, msg = s.check(t)
        elif s.interattivo:
            good = i in fatti
            msg = "" if good else ("Rispondi alla domanda." if s.kind == "domanda" else "Guarda il viaggio del ping.")
        else:
            good, msg = False, "Premi «Verifica»: il gioco prova il traffico vero."
        voce = {"text": s.text, "hint": s.hint, "ok": good and not blocked, "msg": msg,
                "ping": s.al_verifica, "kind": s.kind, "open": not blocked}
        if s.kind == "domanda":
            voce["question"] = {"text": s.domanda["testo"], "choices": s.domanda["scelte"]}
        out.append(voce)
        if not good and s.kind == "controllo":
            blocked = True
    return out


# ───────────────────────────── controlli dichiarativi (dal YAML) ─────────────────────────────
# Un passo può scrivere il controllo direttamente nel YAML, secondo il piano di indirizzamento della missione:
#
#   controllo:
#     dispositivi: {PC1: pc, PC2: pc, SW1: switch}           esistono, con quel nome e quel tipo
#     cavi: [[PC1, SW1], [PC2, SW1]]                         c'è un cavo diretto tra i due
#     cavi_attivi: [[PC1, SW1], [PC2, SW1]]                  …ed è attivo ai due capi (luce verde)
#     niente_cavi: [[SW1, SW2]]                              NON c'è un cavo diretto tra i due
#     indirizzi: {PC1: 10.10.10.11/24}                       IP e maschera esatti del PC/server
#     stessa_rete: [[PC1, PC2], [PC3, PC4]]                  ogni gruppo: tutti con un IP, nella stessa rete,
#                                                            nessuno uguale (qualsiasi rete va bene)
#     gateway: {PC1: 10.10.10.1}                             gateway esatto del PC/server
#     gateway: {PC1: "@R1"}                                  il gateway è l'IP della porta di R1 nella rete
#                                                            di PC1 (qualsiasi indirizzo vada bene)
#     porte_router: {R1: [{verso: SW1, ip: 10.10.10.1/24}]}  la porta del router cablata a «verso» ha
#                                                            quell'IP ed è accesa. Al posto di «ip»:
#                     {verso: SW1, rete_di: PC1}             un IP nella rete di PC1 (diverso dal suo)
#                     {verso: SW-EXT, gateway_di: EXT-TEST}  l'IP che EXT-TEST usa come gateway
#                     {verso: SW1}                           un IP qualsiasi
#     ip_distinti: [PC1, PC2]                                nessun indirizzo duplicato tra questi host
#
# Più chiavi nello stesso controllo: devono passare tutte, nell'ordine scritto (il messaggio è il primo che manca).
TIPI_NOMI = {"pc": "un PC", "switch": "uno switch", "router": "un router", "server": "un server"}


def _cavo(t: Topo, a: str, b: str) -> dict | None:
    return next((l for l in t.links if {l["a"], l["b"]} == {a, b}), None)


def _porta_attiva(t: Topo, dev: str, porta: str) -> bool:
    d = t.dev(dev)
    p = d and t.port(d, porta)
    return bool(p and p.get("on", True) and p.get("up"))


def _ctl_dispositivi(t: Topo, spec: dict) -> tuple[bool, str]:
    for nome, tipo in spec.items():
        d = t.dev(nome)
        if not d:
            return no(f"Manca {nome} ({TIPI_NOMI.get(tipo, tipo)}). Se l'hai già messo, rinominalo «{nome}».")
        if d["kind"] != tipo:
            return no(f"{nome} dovrebbe essere {TIPI_NOMI.get(tipo, tipo)}.")
    return ok()


def _ctl_cavi(t: Topo, coppie: list, attivi: bool) -> tuple[bool, str]:
    for a, b in coppie:
        l = _cavo(t, a, b)
        if not l:
            return no(f"Manca il cavo tra {a} e {b}.")
        if attivi and not (_porta_attiva(t, l["a"], l["pa"]) and _porta_attiva(t, l["b"], l["pb"])):
            return no(f"Il cavo tra {a} e {b} non è ancora attivo: aspetta che la luce diventi verde, "
                      f"o controlla che le porte siano accese.")
    return ok()


def _ctl_niente_cavi(t: Topo, coppie: list) -> tuple[bool, str]:
    for a, b in coppie:
        if _cavo(t, a, b):
            return no(f"C'è un cavo diretto tra {a} e {b}: così il traffico salta il router. Toglilo.")
    return ok()


def _ctl_indirizzi(t: Topo, spec: dict) -> tuple[bool, str]:
    for nome, atteso in spec.items():
        d = t.dev(nome)
        if not d:
            return no(f"Manca {nome}.")
        ih = t.iface(d)
        if not ih:
            return no(f"{nome} non ha ancora un indirizzo IP.")
        if ih != ipaddress.IPv4Interface(atteso):
            return no(f"{nome} ha {ih.ip}/{ih.network.prefixlen}: secondo il piano dovrebbe avere {atteso}.")
    return ok()


def _ctl_stessa_rete(t: Topo, gruppi: list) -> tuple[bool, str]:
    for gruppo in gruppi:
        reti: dict = {}
        for nome in gruppo:
            d = t.dev(nome)
            if not d:
                return no(f"Manca {nome}.")
            ih = t.iface(d)
            if not ih:
                return no(f"{nome} non ha ancora un indirizzo IP.")
            reti[nome] = ih
        primo, ip0 = next(iter(reti.items()))
        for nome, ih in reti.items():
            if ih.network != ip0.network:
                return no(f"{primo} e {nome} non sono nella stessa rete ({ip0.network} e {ih.network}): "
                          f"guarda i primi numeri dell'indirizzo e la maschera.")
        ok_d, msg = _ctl_ip_distinti(t, list(gruppo))
        if not ok_d:
            return ok_d, msg
    return ok()


def _ip_porte_router(t: Topo, router: str) -> list[ipaddress.IPv4Interface]:
    """Gli indirizzi delle porte del router (quelle che ne hanno uno)."""
    return [i for r, _p, i, _v in t.router_ports() if i and r["name"] == router]


def _ctl_gateway(t: Topo, spec: dict) -> tuple[bool, str]:
    for nome, atteso in spec.items():
        d = t.dev(nome)
        if not d:
            return no(f"Manca {nome}.")
        gw = d.get("gw", "") or ""
        if atteso.startswith("@"):
            # il gateway dev'essere l'IP della porta del router nella rete dell'host, qualunque esso sia
            router = atteso[1:]
            ih = t.iface(d)
            if not ih:
                return no(f"{nome} non ha ancora un indirizzo IP.")
            porta = next((i for i in _ip_porte_router(t, router) if ih.ip in i.network), None)
            if not porta:
                return no(f"Nessuna porta di {router} è nella rete di {nome} ({ih.network}).")
            if gw in ("", "0.0.0.0"):
                return no(f"{nome} non ha il gateway: deve essere l'indirizzo di {router} nella sua rete.")
            if gw != str(porta.ip):
                return no(f"{nome} ha il gateway {gw}, ma {router} nella sua rete ha {porta.ip}.")
            continue
        if gw in ("", "0.0.0.0"):
            return no(f"{nome} non ha il gateway: dovrebbe essere {atteso}.")
        if gw != atteso:
            return no(f"{nome} ha il gateway {gw}: dovrebbe essere {atteso}.")
    return ok()


def _ctl_porte_router(t: Topo, spec: dict) -> tuple[bool, str]:
    for router, porte in spec.items():
        r = t.dev(router)
        if not r:
            return no(f"Manca il router {router}.")
        for voce in porte:
            verso = voce["verso"]
            porta = next((mia for mia, vicino, _ in t.neighbors(router) if vicino == verso), None)
            if not porta:
                return no(f"{router} non è ancora collegato a {verso}.")
            p = t.port(r, porta)
            ip = p and t.port_iface(p)
            chi = f"la porta {short_port(porta)} di {router} (verso {verso})"
            if not ip:
                return no(f"La porta {short_port(porta)} di {router} (verso {verso}) non ha ancora un indirizzo IP.")
            if voce.get("ip") and ip != ipaddress.IPv4Interface(voce["ip"]):
                return no(f"La porta {short_port(porta)} di {router} (verso {verso}) ha {ip}: dovrebbe avere {voce['ip']}.")
            if voce.get("rete_di"):
                rif = t.dev(voce["rete_di"])
                ir = rif and t.iface(rif)
                if not ir:
                    return no(f"{voce['rete_di']} non ha ancora un indirizzo IP: {chi} va nella sua rete.")
                if ip.network != ir.network:
                    return no(f"{chi[0].upper() + chi[1:]} ha {ip}, ma {voce['rete_di']} è nella rete {ir.network}.")
                if ip.ip == ir.ip:
                    return no(f"{chi[0].upper() + chi[1:]} ha lo stesso indirizzo di {voce['rete_di']} ({ip.ip}).")
            if voce.get("gateway_di"):
                rif = t.dev(voce["gateway_di"])
                gw = (rif or {}).get("gw", "") or ""
                if gw in ("", "0.0.0.0"):
                    return no(f"{voce['gateway_di']} non ha un gateway: dagli come gateway l'indirizzo di {chi}.")
                if str(ip.ip) != gw:
                    return no(f"{voce['gateway_di']} manda le risposte al suo gateway {gw}, ma {chi} ha {ip.ip}: "
                              f"devono coincidere (clicca {voce['gateway_di']} per vedere la sua configurazione).")
            if not p.get("on", True):
                return no(f"La porta {short_port(porta)} di {router} è spenta: accendila (no shutdown).")
    return ok()


def _ctl_ip_distinti(t: Topo, nomi: list) -> tuple[bool, str]:
    visti: dict = {}
    for nome in nomi:
        d = t.dev(nome)
        ih = d and t.iface(d)
        if not ih:
            continue
        if ih.ip in visti:
            return no(f"{nome} e {visti[ih.ip]} hanno lo stesso indirizzo {ih.ip}: ognuno deve avere il suo.")
        visti[ih.ip] = nome
    return ok()


CONTROLLI_DICHIARATIVI = {
    "dispositivi": _ctl_dispositivi,
    "cavi": lambda t, v: _ctl_cavi(t, v, False),
    "cavi_attivi": lambda t, v: _ctl_cavi(t, v, True),
    "niente_cavi": _ctl_niente_cavi,
    "indirizzi": _ctl_indirizzi,
    "stessa_rete": _ctl_stessa_rete,
    "gateway": _ctl_gateway,
    "porte_router": _ctl_porte_router,
    "ip_distinti": _ctl_ip_distinti,
}


def controllo_dichiarativo(spec: dict) -> Check:
    """Il controllo di un passo scritto nel YAML (dizionario già validato da contenuti.py)."""
    voci = list(spec.items())

    def check(t: Topo) -> tuple[bool, str]:
        for chiave, valore in voci:
            good, msg = CONTROLLI_DICHIARATIVI[chiave](t, valore)
            if not good:
                return good, msg
        return ok()
    return check


# ───────────────────────────── challenge senza nomi (giorni 1 e 2) ─────────────────────────────
# Le descrizioni non danno nomi né indirizzi: questi controlli riconoscono la FORMA della rete (quale switch ha i
# PC, quale router sta in mezzo, dov'è la sala server) e accettano qualsiasi nome e indirizzamento sensato.

def _attivo(t: Topo, l: dict) -> bool:
    return _porta_attiva(t, l["a"], l["pa"]) and _porta_attiva(t, l["b"], l["pb"])


def _pc_di(t: Topo, sw: str) -> list[dict]:
    return [h for h in t.hosts_on(sw) if h["kind"] == "pc"]


def _server_di(t: Topo, sw: str) -> list[dict]:
    return [h for h in t.hosts_on(sw) if h["kind"] == "server"]


def _porta_router_verso(t: Topo, sw: str) -> tuple[dict, dict] | None:
    return router_port_to(t, sw)


def lan_utenti(t: Topo) -> list[str]:
    """Gli switch dei reparti: con dei PC e senza server, collegati a un router."""
    return [s["name"] for s in t.of("switch") if _pc_di(t, s["name"]) and not _server_di(t, s["name"])
            and _porta_router_verso(t, s["name"])]


def sala_server(t: Topo) -> str | None:
    """Lo switch della sala server: almeno due server e un PC, collegato a un router."""
    for s in t.of("switch"):
        if len(_server_di(t, s["name"])) >= 2 and _pc_di(t, s["name"]) and _porta_router_verso(t, s["name"]):
            return s["name"]
    return None


def _rete_porta(t: Topo, sw: str) -> tuple[dict, dict, ipaddress.IPv4Interface | None] | None:
    rp = _porta_router_verso(t, sw)
    if not rp:
        return None
    r, p = rp
    return r, p, Topo.port_iface(p)


def _ip_di(t: Topo, h: dict) -> str:
    i = t.iface(h)
    return str(i.ip) if i else ""


# ── giorno 1 · 1.1
def c_pc_e_switch(t):
    n, s = len(t.of("pc")), len(t.of("switch"))
    if n < 2:
        return no(f"PC sul tavolo: {n} su 2.")
    return ok() if s else no("Manca lo switch.")


def c_pc_collegati_attivi(t):
    for s in t.of("switch"):
        pcs = _pc_di(t, s["name"])
        if len(pcs) < 2:
            continue
        spenti = [h["name"] for h in pcs if not any(_attivo(t, l) for l in t.links
                                                     if {l["a"], l["b"]} == {h["name"], s["name"]})]
        if len(pcs) - len(spenti) >= 2:
            return ok()
        return no("I cavi non sono ancora attivi: aspetta che la luce diventi verde (circa 30 secondi).")
    return no("Collega due PC allo stesso switch.")


def _coppia_lan(t: Topo) -> tuple[dict, dict] | None:
    for sw in lan_switches(t, 2):
        for a, b, _, _ in same_net_pairs(t, _pc_di(t, sw)):
            return a, b
    return None


def v_lan(t, dns=None):
    c = _coppia_lan(t)
    if not c:
        return []
    a, b = c
    return [RS.ping(a["name"], "@" + b["name"]), RS.ping(b["name"], "@" + a["name"])]


# ── giorno 1 · 1.2: la LAN, un router, la rete esterna (uno switch con un server e niente PC)
def _lan_principale(t: Topo) -> str | None:
    for s in t.of("switch"):
        if len(_pc_di(t, s["name"])) >= 2:
            return s["name"]
    return None


def _esterno(t: Topo) -> tuple[str, dict] | None:
    """(switch, server) della rete esterna: uno switch con un server e nessun PC."""
    for s in t.of("switch"):
        srv = _server_di(t, s["name"])
        if srv and not _pc_di(t, s["name"]):
            return s["name"], srv[0]
    return None


def c_rete_esterna(t):
    if _esterno(t):
        return ok()
    if not t.of("server"):
        return no("Manca il server della rete esterna.")
    return no("Il server della rete esterna va collegato a un suo switch, separato da quello dei PC.")


def c_router_tra_lan_ed_esterno(t):
    lan, est = _lan_principale(t), _esterno(t)
    if not lan or not est:
        return no("Sul tavolo non trovo la LAN dei PC e la rete esterna.")
    if not t.of("router"):
        return no("Manca il router.")
    for r in t.of("router"):
        vicini = {v for _, v, _ in t.neighbors(r["name"])}
        if lan in vicini and est[0] in vicini:
            return ok()
    return no("Il router deve essere collegato sia allo switch dei PC sia allo switch della rete esterna.")


def c_porte_lan_ed_esterno(t):
    lan, est = _lan_principale(t), _esterno(t)
    if not lan or not est:
        return no("Sul tavolo non trovo la LAN dei PC e la rete esterna.")
    for sw, ruolo in ((lan, "verso i PC"), (est[0], "verso la rete esterna")):
        x = _rete_porta(t, sw)
        if not x:
            return no(f"Il router non è collegato {ruolo}.")
        r, p, i = x
        if not i:
            return no(f"La porta {short_port(p['n'])} di {r['name']} ({ruolo}) non ha un indirizzo IP.")
        if not p.get("on", True):
            return no(f"La porta {short_port(p['n'])} di {r['name']} ({ruolo}) è spenta: accendila.")
        if sw == lan:
            net = lan_net(t, lan)
            if net and i.network != net:
                return no(f"{r['name']} {short_port(p['n'])} ha {i}, ma i PC sono nella rete {net}.")
        else:
            srv = est[1]
            si = t.iface(srv)
            if not si:
                return no(f"{srv['name']} non ha ancora un indirizzo IP.")
            if si.network != i.network:
                return no(f"La porta di {r['name']} verso la rete esterna ({i}) non è nella rete di {srv['name']} ({si.network}).")
            gw = srv.get("gw", "") or ""
            if gw in ("", "0.0.0.0"):
                return no(f"{srv['name']} non ha il gateway: per rispondere deve mandare i pacchetti al router.")
            if str(i.ip) != gw:
                return no(f"{srv['name']} manda le risposte al gateway {gw}, ma la porta di {r['name']} verso "
                          f"la rete esterna ha {i.ip}: devono coincidere.")
        l = _cavo(t, r["name"], sw)
        if l and not _attivo(t, l):
            return no(f"Il cavo tra {r['name']} e {sw} non è ancora attivo.")
    return ok()


def v_esterno(t, dns=None):
    lan, est = _lan_principale(t), _esterno(t)
    if not lan or not est:
        return []
    srv = est[1]
    pcs = [h for h in _pc_di(t, lan) if t.iface(h)]
    if not pcs:
        return []
    out = [RS.ping(h["name"], "@" + srv["name"]) for h in pcs[:3]]
    if len(pcs) >= 2:
        out.append(RS.ping(pcs[0]["name"], "@" + pcs[1]["name"]))
    out.append(RS.ping(srv["name"], "@" + pcs[0]["name"]))
    return out


# ── giorno 1 · 1.3: due reparti, ognuno sul suo switch collegato al router
def c_due_switch_e_router(t):
    if len(t.of("switch")) < 2:
        return no("Serve un secondo switch per il nuovo reparto.")
    return ok() if t.of("router") else no("Manca il router.")


def c_nuovo_reparto(t):
    """Due switch con dei PC (gli switch con un server, come la rete esterna, non sono reparti)."""
    con_pc = [s["name"] for s in t.of("switch") if _pc_di(t, s["name"]) and not _server_di(t, s["name"])]
    if len(con_pc) >= 2:
        return ok()
    return no("Il nuovo reparto vuole un suo switch, con i suoi PC.")


def _cavi_tra_switch(t: Topo) -> list[tuple[str, str]]:
    out = []
    for l in t.links:
        a, b = t.dev(l["a"]), t.dev(l["b"])
        if a and b and a["kind"] == "switch" and b["kind"] == "switch":
            out.append((a["name"], b["name"]))
    return out


def c_reparti_collegati(t):
    sciolti = [h["name"] for h in t.of("pc") if not t.switch_of(h["name"])]
    if sciolti:
        return no("Questi PC non sono collegati a uno switch: " + ", ".join(sciolti) + ".")
    diretti = _cavi_tra_switch(t)
    if diretti:
        a, b = diretti[0]
        return no(f"C'è un cavo diretto tra {a} e {b}: così i reparti saltano il router. Toglilo.")
    con_pc = [s["name"] for s in t.of("switch") if _pc_di(t, s["name"])]
    if len(con_pc) < 2:
        return no("I PC devono essere divisi su due switch, uno per reparto.")
    senza = [s for s in con_pc if not _porta_router_verso(t, s)]
    if senza:
        return no(f"{senza[0]} non è collegato al router.")
    return ok()


def c_porte_dei_reparti(t):
    reti = []
    for sw in lan_utenti(t):
        r, p, i = _rete_porta(t, sw)
        if not i:
            return no(f"La porta {short_port(p['n'])} di {r['name']} verso {sw} non ha un indirizzo IP.")
        if not p.get("on", True):
            return no(f"La porta {short_port(p['n'])} di {r['name']} verso {sw} è spenta: accendila.")
        reti.append(i.network)
    if len(reti) < 2:
        return no("Servono due reparti, ognuno collegato a una porta del router.")
    if len(set(reti)) < len(reti):
        return no("Due porte del router sono nella stessa rete: ogni reparto vuole la sua.")
    return ok()


def c_ogni_reparto_nella_sua_rete(t):
    for sw in lan_utenti(t) + ([sala_server(t)] if sala_server(t) else []):
        r, p, i = _rete_porta(t, sw)
        if not i:
            return no(f"La porta di {r['name']} verso {sw} non ha ancora un indirizzo.")
        visti = {}
        for h in t.hosts_on(sw):
            ih = t.iface(h)
            if not ih:
                return no(f"{h['name']} non ha ancora un indirizzo IP.")
            if ih.network != i.network:
                return no(f"{h['name']} ({ih}) non è nella rete del suo reparto ({i.network}).")
            if ih.ip in visti or ih.ip == i.ip:
                return no(f"{h['name']} ha un indirizzo già usato ({ih.ip}).")
            visti[ih.ip] = h["name"]
            if not gw_ok(t, h) or h.get("gw") != str(i.ip):
                return no(f"{h['name']} non usa come gateway il router del suo reparto ({i.ip}).")
    return ok()


def v_reparti(t, dns=None):
    gruppi = [[h for h in _pc_di(t, sw) if t.iface(h)] for sw in lan_utenti(t)]
    gruppi = [g for g in gruppi if g]
    if len(gruppi) < 2:
        return []
    a, b = gruppi[0], gruppi[1]
    r = _porta_router_verso(t, lan_utenti(t)[0])[0]["name"]
    out = [RS.through(a[0]["name"], b[0]["name"], r)]
    for h in a[1:]:
        out.append(RS.ping(h["name"], "@" + b[0]["name"]))
    for h in b[1:]:
        out.append(RS.ping(h["name"], "@" + a[0]["name"]))
    return out


# ── giorno 2 · 2.1: la sala server dietro un secondo router, con il DHCP
def c_sala_server(t):
    s = sala_server(t)
    if s:
        return ok()
    for sw in t.of("switch"):
        if _server_di(t, sw["name"]) and not _porta_router_verso(t, sw["name"]):
            return no(f"{sw['name']} non è collegato a un router.")
    return no("Nella sala server servono uno switch, due server e un PC di prova, collegati a un router.")


def c_server_con_ip_fisso(t):
    sw = sala_server(t)
    if not sw:
        return no("Prima prepara la sala server.")
    r, p, i = _rete_porta(t, sw)
    if not i:
        return no(f"La porta di {r['name']} verso {sw} non ha ancora un indirizzo.")
    for s in _server_di(t, sw):
        si = t.iface(s)
        if s.get("dhcp"):
            return no(f"{s['name']} è in DHCP: un server vuole un indirizzo fisso.")
        if not si or si.network != i.network:
            return no(f"{s['name']} non ha un indirizzo della rete della sala server.")
        if s.get("gw") != str(i.ip):
            return no(f"{s['name']} non usa come gateway il router della sala server.")
    return ok()


def c_pc_di_prova_in_dhcp(t):
    sw = sala_server(t)
    if not sw:
        return no("Prima prepara la sala server.")
    fissi = {_ip_di(t, s) for s in _server_di(t, sw)}
    for h in _pc_di(t, sw):
        ih = t.iface(h)
        if not h.get("dhcp"):
            continue
        if not ih:
            return no(f"{h['name']} è in DHCP ma non ha ricevuto un indirizzo: c'è un pool nella sua rete?")
        if str(ih.ip) in fissi:
            return no(f"{h['name']} ha ricevuto {ih.ip}, che è già di un server: escludilo dal pool.")
        if not gw_ok(t, h):
            return no(f"{h['name']} ha un indirizzo ma non il gateway giusto: il pool deve indicarlo.")
        return ok()
    return no("Il PC della sala server deve prendere l'indirizzo in automatico (DHCP).")


def _pc_dhcp_sala(t: Topo) -> dict | None:
    sw = sala_server(t)
    return next((h for h in _pc_di(t, sw) if h.get("dhcp") and t.iface(h)), None) if sw else None


def v_sala_server(t, dns=None):
    sw = sala_server(t)
    if not sw:
        return []
    srv = [s for s in _server_di(t, sw) if t.iface(s)]
    pc = _pc_dhcp_sala(t)
    utenti = [next((h for h in _pc_di(t, u) if t.iface(h)), None) for u in lan_utenti(t)]
    utenti = [u for u in utenti if u]
    if not srv or not pc or not utenti:
        return []
    out = [RS.ping(u["name"], "@" + srv[0]["name"]) for u in utenti]
    out += [RS.ping(pc["name"], "@" + u["name"]) for u in utenti]
    return out


# ── giorno 2 · 2.2: i nomi
NOMI_SERVIZI = ("gestione.azienda.test", "posta.azienda.test")
NOME_DNS = "dns.azienda.test"


def c_tre_server_in_sala(t):
    sw = sala_server(t)
    if not sw:
        return no("Non trovo la sala server.")
    n = len(_server_di(t, sw))
    return ok() if n >= 3 else no(f"Nella sala server ci sono {n} server: ne serve uno in più per i nomi.")


def _primi_utenti(t: Topo) -> list[dict]:
    """Il primo PC con un indirizzo di ogni reparto."""
    out = [next((h for h in _pc_di(t, u) if t.iface(h)), None) for u in lan_utenti(t)]
    return [x for x in out if x]


def v_nomi(t, dns=None):
    sw = sala_server(t)
    sorgenti = [x for x in [_pc_dhcp_sala(t)] + _primi_utenti(t) if x]
    if not sw or not sorgenti:
        return []
    # tutti i nomi dal primo computer; dagli altri un nome ciascuno (ogni nslookup costa 1-2 secondi)
    nomi = list(NOMI_SERVIZI) + [NOME_DNS]
    out = [RS.resolves(sorgenti[0]["name"], n, "resolver" if n == NOME_DNS else "server") for n in nomi]
    for i, s in enumerate(sorgenti[1:]):
        out.append(RS.resolves(s["name"], NOMI_SERVIZI[i % len(NOMI_SERVIZI)], "server"))
    return out


def v_risoluzione_da_un_pc(t, dns=None):
    """Un controllo veloce, da un PC di un reparto: il nome della gestione si risolve in un server."""
    utenti = _primi_utenti(t)
    return [RS.resolves(utenti[-1]["name"], NOMI_SERVIZI[0], "server")] if utenti else []


# ── giorno 2 · 2.3: il firewall tra i due router
def c_firewall_tra_i_router(t):
    fws = t.of("firewall")
    if not fws:
        return no("Manca il firewall.")
    diretti = [(a["name"], b["name"]) for a, _, b, _ in r2r_links(t)]
    if diretti:
        return no(f"C'è ancora un cavo diretto tra {diretti[0][0]} e {diretti[0][1]}: il traffico salterebbe il firewall.")
    for f in fws:
        routers = {v for _, v, _ in t.neighbors(f["name"]) if (t.dev(v) or {}).get("kind") == "router"}
        if len(routers) >= 2:
            return ok()
    return no("Il firewall deve stare in mezzo: collegato a tutti e due i router.")


def _server_con_nome(t: Topo, dns: dict | None, nome: str) -> dict | None:
    """Il server a cui porta il nome, secondo i record DNS creati nel gioco."""
    for rec in (dns or {}).values():
        ip = (rec.get("records") or {}).get(nome)
        if ip:
            return next((s for s in t.of("server") if _ip_di(t, s) == ip), None)
    return None


def v_firewall_gestione(t, dns=None):
    """La porta 22 della gestione passa da una sola postazione dei reparti."""
    gestione = _server_con_nome(t, dns, NOMI_SERVIZI[0])
    utenti = [h for u in lan_utenti(t) for h in _pc_di(t, u) if t.iface(h)]
    if not gestione or not utenti:
        return []
    return [RS.one_of(*[RS.port_passes(h["name"], "@" + gestione["name"], 22, True) for h in utenti])]


def v_firewall_servizi(t, dns=None):
    """Da ogni reparto: il nome della posta si risolve (DNS) e la porta 25 della posta passa."""
    posta = _server_con_nome(t, dns, NOMI_SERVIZI[1])
    if not posta:
        return []
    out = []
    for h in _primi_utenti(t):
        out.append(RS.resolves(h["name"], NOMI_SERVIZI[1], "@" + posta["name"]))
        out.append(RS.port_passes(h["name"], "@" + posta["name"], 25, True))
    return out


def v_firewall_blocco(t, dns=None):
    """Tutto il resto è bloccato: per esempio il ping dai reparti alla posta."""
    posta = _server_con_nome(t, dns, NOMI_SERVIZI[1])
    if not posta:
        return []
    return [RS.blocked(h["name"], posta["name"]) for h in _primi_utenti(t)]


# controlli e piani senza nomi, richiamati dal YAML
CONTROLLI.update({
    "pc_e_switch": c_pc_e_switch,
    "pc_collegati_e_attivi": c_pc_collegati_attivi,
    "rete_esterna": c_rete_esterna,
    "router_tra_lan_ed_esterno": c_router_tra_lan_ed_esterno,
    "nuovo_reparto": c_nuovo_reparto,
    "porte_lan_ed_esterno": c_porte_lan_ed_esterno,
    "due_switch_e_router": c_due_switch_e_router,
    "reparti_collegati_al_router": c_reparti_collegati,
    "porte_dei_reparti": c_porte_dei_reparti,
    "ogni_reparto_nella_sua_rete": c_ogni_reparto_nella_sua_rete,
    "sala_server": c_sala_server,
    "server_con_ip_fisso": c_server_con_ip_fisso,
    "pc_di_prova_in_dhcp": c_pc_di_prova_in_dhcp,
    "tre_server_in_sala": c_tre_server_in_sala,
    "firewall_tra_i_router": c_firewall_tra_i_router,
})

# Piani di verifica: dalla rete che c'è (e dai record DNS creati nel gioco) le verifiche da fare a «Verifica».
PIANI_VERIFICA: dict[str, Callable] = {
    "lan": v_lan,
    "esterno": v_esterno,
    "reparti": v_reparti,
    "sala_server": v_sala_server,
    "nomi": v_nomi,
    "risoluzione_da_un_pc": v_risoluzione_da_un_pc,
    "firewall_gestione": v_firewall_gestione,
    "firewall_servizi": v_firewall_servizi,
    "firewall_blocco": v_firewall_blocco,
}


def _coppia_osserva_lan(t: Topo) -> tuple[str, str] | None:
    c = _coppia_lan(t)
    return (c[0]["name"], c[1]["name"]) if c else None


# Coppie per i passi «osserva» senza nomi: (da, a)
COPPIE: dict[str, Callable] = {
    "lan": _coppia_osserva_lan,
}


# ───────────────────────────── giorno 3: DMZ, VPN e cloud (rete di partenza reti/giorno3.yaml) ─────────────────────────────
# I dispositivi della rete di partenza hanno i nomi della topologia (INTERNET, R1, R2, WEB, INTRANET, PC1, PC2, PC-ESTERNO);
# quelli che aggiunge il giocatore (firewall, switch, il PC della VPN, il cloud) si riconoscono dalla forma.
G3_ESTERNO, G3_WEB, G3_INTRANET, G3_UTENTI = "PC-ESTERNO", "WEB", "INTRANET", ("PC1", "PC2")


def _vicini(t: Topo, nome: str, kind: str | None = None) -> set[str]:
    return {v for _, v, _ in t.neighbors(nome) if kind is None or (t.dev(v) or {}).get("kind") == kind}


def _fw_tra(t: Topo, a: str, b: str) -> tuple[bool, str]:
    """Un firewall collegato sia ad a sia a b, e nessun cavo diretto tra a e b."""
    if not t.dev(a) or not t.dev(b):
        return no(f"Sul tavolo non trovo {a} e {b}: hai cambiato i nomi della rete di partenza? Ricomincia la challenge.")
    if b in _vicini(t, a):
        return no(f"C'è ancora un cavo diretto tra {a} e {b}: il traffico salterebbe il firewall.")
    if any({a, b} <= _vicini(t, f["name"]) for f in t.of("firewall")):
        return ok()
    if not t.of("firewall"):
        return no("Manca il firewall.")
    return no(f"Il firewall deve stare in mezzo: collegato sia a {a} sia a {b}.")


def c_fw_verso_internet(t):
    return _fw_tra(t, "INTERNET", "R1")


def c_fw_interno(t):
    return _fw_tra(t, "R1", "R2")


def _utenti_g3(t: Topo) -> list[str]:
    return [n for n in G3_UTENTI if t.dev(n) and t.iface(t.dev(n))]


def v_dmz_web(t, dns=None):
    """Il sito (porta 80 del server web nella DMZ) si apre da Internet e dalla rete interna; i dipendenti escono."""
    u = _utenti_g3(t)
    if not t.dev(G3_ESTERNO) or not t.dev(G3_WEB) or not u:
        return []
    return [RS.port_passes(G3_ESTERNO, "@" + G3_WEB, 80, True), RS.port_passes(u[0], "@" + G3_WEB, 80, True),
            RS.ping(u[-1], "@" + G3_ESTERNO)]


def v_dmz_isolata(t, dns=None):
    """Da Internet non si entra nella rete interna; dalla DMZ (il server web, se lo violano) nemmeno."""
    u = _utenti_g3(t)
    if not t.dev(G3_ESTERNO) or not t.dev(G3_WEB) or not u:
        return []
    return [RS.blocked(G3_ESTERNO, u[0]), RS.port_passes(G3_ESTERNO, "@" + G3_INTRANET, 80, False),
            RS.blocked(G3_WEB, u[-1])]


def _segmenti_r2(t: Topo) -> list[tuple[str, str, list[dict]]]:
    """(firewall, switch, host) per ogni rete nuova dietro un firewall collegato a R2 (non quello verso R1)."""
    out = []
    for f in t.of("firewall"):
        vicini = _vicini(t, f["name"])
        if "R2" not in vicini or "R1" in vicini:
            continue
        for sw in sorted(_vicini(t, f["name"], "switch")):
            out.append((f["name"], sw, t.hosts_on(sw)))
    return out


def _segmento(t: Topo, kind: str) -> tuple[str, str, dict | None] | None:
    """Il primo segmento dietro un firewall di R2 con un host di quel tipo (pc: la VPN, server: il cloud)."""
    for f, sw, hosts in _segmenti_r2(t):
        scelti = [h for h in hosts if h["kind"] == kind]
        if kind == "server":   # il cloud: si preferisce quello aggiunto come «cloud» (nome CLOUD…)
            scelti.sort(key=lambda h: not h["name"].upper().startswith("CLOUD"))
        if scelti:
            return f, sw, next((h for h in scelti if t.iface(h)), scelti[0])
    return None


def _c_segmento(t: Topo, kind: str, cosa: str) -> tuple[bool, str]:
    if not any(f for f in t.of("firewall") if "R2" in _vicini(t, f["name"]) and "R1" not in _vicini(t, f["name"])):
        return no("Collega un nuovo firewall a una porta libera di R2.")
    seg = _segmento(t, kind)
    if not seg:
        return no(f"Dietro il nuovo firewall serve uno switch, con {cosa} collegato.")
    f, sw, h = seg
    if not t.iface(h):
        return no(f"{h['name']} non ha ancora un indirizzo IP.")
    l = _cavo(t, f, sw)
    if l and not _attivo(t, l):
        return no(f"Il cavo tra {f} e {sw} non è ancora attivo.")
    return ok()


def c_rete_vpn(t):
    return _c_segmento(t, "pc", "il PC di un dipendente collegato in VPN")


def _vpn(t: Topo) -> dict | None:
    seg = _segmento(t, "pc")
    return seg[2] if seg and t.iface(seg[2]) else None


def v_vpn_servizi(t, dns=None):
    """Dalla VPN si aprono i servizi web dell'azienda: il server interno e il sito nella DMZ."""
    v = _vpn(t)
    if not v:
        return []
    return [RS.port_passes(v["name"], "@" + G3_INTRANET, 80, True), RS.port_passes(v["name"], "@" + G3_WEB, 80, True)]


def v_vpn_limitata(t, dns=None):
    """Dalla VPN non si entra nei PC della rete interna, e da Internet non si entra nella VPN."""
    v, u = _vpn(t), _utenti_g3(t)
    if not v or not u:
        return []
    return [RS.blocked(v["name"], u[0]), RS.blocked(G3_ESTERNO, v["name"])]


def c_rete_cloud(t):
    return _c_segmento(t, "server", "il cloud")


def _cloud(t: Topo) -> dict | None:
    seg = _segmento(t, "server")
    return seg[2] if seg and t.iface(seg[2]) else None


def v_cloud_servizi(t, dns=None):
    """Il servizio cloud (porta 80) si usa dalla rete interna e dalla VPN."""
    c, u, v = _cloud(t), _utenti_g3(t), _vpn(t)
    if not c or not u:
        return []
    out = [RS.port_passes(u[0], "@" + c["name"], 80, True)]
    if v:
        out.append(RS.port_passes(v["name"], "@" + c["name"], 80, True))
    return out


def v_cloud_isolato(t, dns=None):
    """Il cloud non apre connessioni verso la rete interna (né ping né il server interno)."""
    c, u = _cloud(t), _utenti_g3(t)
    if not c or not u:
        return []
    return [RS.blocked(c["name"], u[0]), RS.port_passes(c["name"], "@" + G3_INTRANET, 80, False)]


CONTROLLI.update({
    "firewall_verso_internet": c_fw_verso_internet,
    "firewall_interno": c_fw_interno,
    "rete_vpn": c_rete_vpn,
    "rete_cloud": c_rete_cloud,
})
PIANI_VERIFICA.update({
    "dmz_web": v_dmz_web,
    "dmz_isolata": v_dmz_isolata,
    "vpn_servizi": v_vpn_servizi,
    "vpn_limitata": v_vpn_limitata,
    "cloud_servizi": v_cloud_servizi,
    "cloud_isolato": v_cloud_isolato,
})


# ───────────────────────────── giorno 4: attacchi in corso (rete di partenza reti/giorno4.yaml) ─────────────────────────────
# La rete è quella della topologia: i nomi sono noti. Il traffico «sospetto» lo genera il «traffico» delle missioni (sniffer).
G4_COMPROMESSO = "PC3"   # 4.1: tenta gli accessi FTP al server dei file


def _collegato(t: Topo, nome: str) -> bool:
    """L'host ha almeno un cavo attivo (cavo presente e porte accese da tutte e due le parti)."""
    return any(_attivo(t, l) for l in t.links if nome in (l["a"], l["b"]))


def c_macchina_isolata(t):
    if not t.dev(G4_COMPROMESSO):
        return no("Non cancellare la macchina compromessa: va scollegata dalla rete, così si può analizzare dopo.")
    if _collegato(t, G4_COMPROMESSO):
        return no("La macchina che tenta gli accessi è ancora collegata alla rete.")
    scollegati = [h["name"] for h in t.hosts() if h["name"] != G4_COMPROMESSO and not _collegato(t, h["name"])]
    if scollegati:
        return no("Hai scollegato anche chi non c'entra: " + ", ".join(scollegati) + ".")
    return ok()


CONTROLLI.update({"macchina_isolata": c_macchina_isolata})
