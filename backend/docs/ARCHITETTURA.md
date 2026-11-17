# Architettura di Penguin Inc.

Come è fatto il backend dentro, e come si estende senza rompere niente. Il contratto HTTP verso i client è in
[API.md](API.md).

## Indice

- [Il quadro](#il-quadro)
- [I file](#i-file)
- [Il pacchetto di gioco (`contenuti.py`)](#il-pacchetto-di-gioco-contenutipy)
- [Il viaggio di una richiesta](#il-viaggio-di-una-richiesta)
- [Il motore di rete (`rete/`)](#il-motore-di-rete-rete)
- [La rete vera: Mininet senza privilegi](#la-rete-vera-mininet-senza-privilegi)
- [Lo stato della partita](#lo-stato-della-partita)
- [Come estendere](#come-estendere): endpoint, tipo di dispositivo, rete di partenza, missione, livello, un altro client
- [Cose da sapere sulla rete](#cose-da-sapere-sulla-rete)
- [Sicurezza](#sicurezza)

## Il quadro

```
 frontend/demo/gioco/*.yaml (o un altro pacchetto)       frontend/demo (o un altro client)
        │  letti e validati da contenuti.py,                     │  HTTP + JSON
        ▼  ricaricati quando cambiano                            ▼
 missioni, livelli, messaggi, punteggi ──────────────▶ backend/server.py
                                      motore di gioco: missions.py, rescue.py, firewall.py
                                                             │  Motore (rete/motore.py)
                                                             ▼
                      rete/console.py ──▶ rete/modello.py ◀── rete/topologia.py (reti di partenza YAML)
                      (la console dei router)    │  la verità: dispositivi, cavi, configurazioni
                                                 ├──▶ rete/percorso.py (la traccia di un pacchetto)
                                                 ▼
                                         rete/kernel.py ──▶ Mininet: un namespace Linux per dispositivo,
                                                            veth, bridge, nftables, tc, dnsmasq, tcpdump
```

Quattro idee reggono tutto:

1. **Il modello è la fonte di verità.** `rete/modello.py` tiene dispositivi, porte, cavi e configurazioni; la
   console e gli endpoint cambiano il modello, e `rete/kernel.py` lo riporta sulla rete vera (rifà solo i
   dispositivi cambiati). La fotografia che vedono il gioco e il client (`Topo`) esce dal modello.
2. **Si verifica con la rete, non con la configurazione.** Una missione o un guasto è risolto quando un
   **ping vero** passa (o una pagina web si apre, o una porta risponde), non quando la configurazione
   assomiglia a quella attesa: qualsiasi soluzione corretta vale.
3. **Una cosa alla volta.** Un unico lock (`motore.lock`) serializza le operazioni sulla rete, e chi arriva
   mentre è occupata si mette in coda (`asyncio.Lock` serve in ordine di arrivo) per al massimo `CODA_S`
   secondi, poi riceve 409.
4. **Il motore è generico, il gioco è nei dati.** Il backend non contiene testi di gioco: missioni, livelli,
   messaggi, punteggi, dispositivi e reti di partenza arrivano da un pacchetto di file YAML, e il client è una
   cartella statica qualsiasi. Un altro gioco sulle reti riusa il backend cambiando pacchetto e client.

## I file

Nel backend (`backend/`):

| File | Responsabilità | Dipende da |
|---|---|---|
| `server.py` | HTTP (Starlette): login, endpoint, lavori lunghi, punteggio, salvataggio della partita, servizio del client | tutti gli altri |
| `rete/` | il motore di rete: modello, console, traccia, realizzazione con Mininet, fotografia (`Topo`). Vedi [Il motore di rete](#il-motore-di-rete-rete) | `pyyaml`; Mininet solo nella rete vera |
| `contenuti.py` | legge, valida e ricarica il pacchetto di gioco YAML; lo trasforma negli oggetti del motore | `campagna`, `missions`, `rescue`, `pyyaml` |
| `campagna.py` | motore della campagna: giorni, personaggi, dialoghi, aiuti dei colleghi (`Campagna`, `Giorno`, `Aiuto`) | – |
| `missions.py` | motore dell'Accademia: i controlli sulla rete, nei registri `CONTROLLI` e `PIANI_PING` | `rete.topo.Topo` |
| `rescue.py` | motore del Pronto Soccorso: verifiche (`Check`), guasti (`Fault`), livelli (`Level`) | `rete.topo.Topo` |
| `firewall.py` | dal modello del giocatore (zone con livello, regole «chi apre connessioni verso chi») alle access-list del firewall, risposte comprese | – |
| `data/state.json`, `data/rete.json` | la partita e la rete salvate (creati a runtime, fuori da git; nel container stanno nel volume `partita`) | – |

Fuori dal backend:

| Cartella | Cosa contiene |
|---|---|
| `frontend/demo/` | il client: `index.html`, `assets/` (stile), `scripts/` (`app.js`, senza build). Guida: [frontend/demo/README.md](../../frontend/demo/README.md) |
| `frontend/demo/gioco/` | il pacchetto di gioco: `impostazioni.yaml`, `interfaccia.yaml`, `accademia.yaml`, `pronto-soccorso.yaml`, `campagna.yaml`, `storia.yaml`, e `reti/` con le topologie di partenza |
| `Dockerfile`, `docker-compose.yml`, `deploy/` | il deploy: container senza privilegi, profilo seccomp, `mnexec` di Mininet modificato (vedi [La rete vera](#la-rete-vera-mininet-senza-privilegi)) |

`missions.py`, `rescue.py` e `campagna.py` sono **solo dati e funzioni pure**: non toccano la rete, non
conoscono HTTP e non contengono testi di gioco.

## Il pacchetto di gioco (`contenuti.py`)

La cartella è `PNA_GIOCO` (default `../frontend/demo/gioco`); il client servito è `PNA_FRONTEND` (default
`../frontend/demo`). Ogni file del pacchetto è facoltativo: senza, valgono i valori predefiniti di
`contenuti.py` (nessuna missione, nessun livello, i dispositivi e i punteggi di sempre), così il backend
funziona anche per un client che usa solo le API di tavolo, configurazione e sicurezza.

| File | Usato da | Contiene |
|---|---|---|
| `impostazioni.yaml` | server e client | `punteggio`, `dispositivi` (prefisso del nome, nome, icona), `interfaccia` (tempi di aggiornamento, comandi pronti) |
| `interfaccia.yaml` | solo il client, via `GET /api/ui` | i testi dell'interfaccia, raggruppati per zona |
| `storia.yaml` | solo il client, via `GET /api/ui` (in `testi.storia`) | la storyline di Penguin Inc: giornate, dialoghi, buoni caffè, aiuti del collega |
| `accademia.yaml` | server | `missioni` (testi, punti, passi che nominano un controllo di `missions.CONTROLLI` o un piano di `missions.PIANI_PING`) e `messaggi` |
| `pronto-soccorso.yaml` | server | `livelli` (rete di partenza, ticket, guasti con rottura, verifiche, vincolo, indizi, soluzione) e `messaggi` |
| `reti/*.yaml` | server | le topologie di partenza di challenge e livelli (formato in `rete/topologia.py`) |
| `campagna.yaml` | server | la storia a giorni: `valuta`, `buoni_iniziali`, `personaggi`, `giorni` (missioni e livelli da completare, dialoghi `prima`/`dopo`, ricompensa, `aiuti` dei colleghi con costo, risposta e azioni sulla rete), `finale`, `messaggi` |

Come funziona:

- `Contenuti.get()` (chiamata da `G()` in `server.py`) controlla al massimo una volta al secondo se un file è
  cambiato e lo rilegge. Le funzioni del server prendono il pacchetto all'inizio con `g = G()` e lo usano fino
  alla fine, così un lavoro lungo non vede due versioni diverse.
- `carica()` valida tutto e trasforma il YAML negli stessi oggetti di prima (`Mission`, `Step`, `Level`,
  `Fault`, `Check`). Ogni errore è un `ErroreContenuti` con file e punto esatto, in italiano.
- Al primo avvio un errore ferma il server; dopo, il server tiene l'ultima versione buona e mette l'errore in
  `GIOCO.errore`, che arriva al client (`errore_contenuti` nello Stato e in `/api/ui`) e a `/api/health`.
- I messaggi di gioco del server si scrivono con `g.accademia("chiave", segnaposto=…)`,
  `g.soccorso("chiave", …)` e `g.storia("chiave", …)` (campagna). Le chiavi e i testi predefiniti sono in
  `MESSAGGI_ACCADEMIA`, `MESSAGGI_SOCCORSO` e `MESSAGGI_CAMPAGNA`; il YAML può riscriverli. Un segnaposto sconosciuto resta com'è invece di dare errore.
- La cartella del pacchetto **non** è servita al browser: contiene le soluzioni dei livelli.

## Il viaggio di una richiesta

### Un'azione (es. `POST /api/router/iface`)

```
richiesta ─▶ action(a_iface)
               1. cookie valido?                       no → 401
               2. Content-Type JSON e body oggetto?    no → 400
               3. aspetta in coda il lock (motore.lock) oltre CODA_S (180 s) → 409
               4. ha il lock
               5. fotografia più vecchia di 1,5 s? → motore.refresh()
               6. a_iface(body)          ← qui vive la logica dell'endpoint
                    ValueError → 400 con il messaggio
                    ErroreRete → 502
               7. motore.refresh()       ← la risposta mostra la rete DOPO l'azione
               8. rilascia il lock
           ◀─ Stato completo + i campi extra restituiti da a_iface
```

La funzione dell'endpoint (`a_…`) riceve il body già validato come `dict` e fa tre cose: controlla gli
input, chiama il motore di rete (`motore.…` o `configura()` per le righe della console), scrive una riga di diario. Se restituisce un `dict`, quei campi vengono aggiunti
alla risposta (es. `{"console": out}`).

### Un lavoro lungo (es. `POST /api/rescue/start`)

```
richiesta ─▶ job_action(j_rescue_start)
               1. cookie e JSON come sopra
               2. j_rescue_start(body) valida gli input e chiama start_job(titolo, funzione)
               3. start_job: un altro lavoro in corso? → 409 (se la rete è solo occupata, il lavoro aspetta il lock)
                             crea il Job, lo mette in JOB, lancia la funzione in background
           ◀─ {"ok": true, "job": {...}}                      (risposta immediata)

 in background:  prende il lock → funzione(job) → job.step("…") a ogni passo
                 → job.result = {"ok", "message", "win"?} → motore.refresh() → job.done = True
```

Il client segue il lavoro leggendo `GET /api/state` (campo `job`).

### Una lettura (`GET /api/state`)

Non aspetta mai il lock: se la rete è libera e la fotografia ha più di 3 secondi la rinnova, altrimenti restituisce
quella che ha. `public_state()` assembla la risposta; `academy_view()` e `rescue_view()` ricalcolano a ogni
lettura lo stato dei passi e dei guasti, così quello che il giocatore vede è sempre allineato al tavolo.

## Il motore di rete (`rete/`)

| File | Responsabilità |
|---|---|
| `motore.py` | `Motore`: l'unico oggetto che `server.py` usa (istanza globale `motore`). Tiene il lock, il modello, la console e (se c'è) il kernel; ogni modifica passa da `applica()`: salva `data/rete.json`, riallinea la rete vera, rinnova la fotografia. Sceglie all'avvio tra **rete vera** e **rete simulata** |
| `modello.py` | `Rete`, `Dispositivo`, `Porta`, `Cavo`: la rete come dati. Calcola lo stato delle porte (`porta_su`), i domini L2 con VLAN, trunk e sottointerfacce (`domini`), le tabelle di routing con rotte connesse, statiche e **OSPF** (Dijkstra sul grafo dei router adiacenti, `tabelle`), il router attivo di ogni gruppo **HSRP**, e la fotografia per il gioco (`snapshot`) |
| `console.py` | `Console`: la console di router, switch e firewall. Modalità (`>`, `#`, `(config)#`, `(config-if)#`…), abbreviazioni, login locale e password di enable, comandi `show`, `running-config`. Ogni riga cambia il modello; `audit()` legge le debolezze della configurazione |
| `acl.py` | le righe ACL («permit tcp any host 10.0.0.5 eq 80») → regole che si confrontano con un pacchetto e che si traducono in nftables. Due dialetti: `router` (wildcard) e `fw` (maschere) |
| `percorso.py` | `traccia()` e `sonda()`: un ping o una connessione TCP/UDP seguiti passo per passo sul modello (switch, VLAN, rotte, ACL, zone del firewall, ARP), con il motivo di ogni scarto |
| `kernel.py` | `Kernel`: la rete vera con Mininet (vedi sotto) e `entra_nel_namespace()` |
| `strumenti.py` | i programmi che girano dentro i dispositivi: servizi dei server (web, FTP, posta), browser, sonda TCP, client DNS |
| `topologia.py` | `carica()`: una topologia YAML (`gioco/reti/*.yaml`) → modello |
| `topo.py` | `Topo`: la fotografia con le funzioni che servono a missioni e livelli |
| `udhcpc.sh` | lo script del client DHCP: stampa l'indirizzo ricevuto, lo applica il motore passando dal modello |

**Rete vera e rete simulata.** Nella rete vera (container Docker, o Linux con Mininet) ping, web, porte, DNS,
DHCP e sniffer sono traffico vero. Se Mininet non c'è (per esempio su macOS fuori da Docker, o con
`PNA_RETE=simulata`) le stesse prove si calcolano sul modello con `percorso.py`: il gioco è lo stesso, e lo
Stato lo dice nel campo `rete`. La **traccia** (`/api/trace`, il passo «osserva», le verifiche `bloccato`,
`attraversa`, `limitato`) si calcola sempre sul modello, perché sa dire *dove* e *perché*; nella rete vera un
ping vero ne conferma l'esito.

| Metodo di `Motore` | Cosa fa |
|---|---|
| `start()` / `stop()` | ricarica `data/rete.json`, sceglie rete vera o simulata, costruisce la rete |
| `refresh()` | rinnova `motore.topo` dal modello (istantaneo) |
| `settle()` | se `links_dirty` è vero (cavi o porte cambiati), i PC in DHCP ancora senza indirizzo lo chiedono |
| `cli(device, righe)` | righe nella console; restituisce `(tutto_ok, trascrizione)`. Se la configurazione è cambiata riallinea la rete |
| `unlock(device, creds)` / `bloccata(device)` | login sulla console protetta / la console chiede ancora utente o password |
| `ping`, `trace`, `browse`, `porta`, `nslookup`, `host_cmd`, `sniff` | le prove, vere o simulate |
| `carica_topologia(file)`, `clear()` | sostituisce la rete con una topologia / svuota il tavolo |
| `add_device`, `move`, `delete`, `rename`, `link`, `unlink`, `power`, `host_ip`, `host_dhcp`, `server_dns`, `servizio` | le modifiche che non passano dalla console |
| `lock` | serializza tutto. Chi chiama i metodi sopra **deve già averlo**: lo prendono `action`, `start_job` e `state` |

### `Topo`: la fotografia

La rete come la vede il gioco (stesso formato del campo `topology` dell'API). Le funzioni che servono a missioni
e livelli:

| Funzione | Restituisce |
|---|---|
| `dev(nome)` | il dispositivo o `None` |
| `of(kind)` / `hosts()` | i dispositivi di un tipo / tutti i PC e server |
| `Topo.port(dev, nome)` | una porta del dispositivo |
| `Topo.iface(dev)` | il primo IPv4 di un host come `IPv4Interface` (`None` se non ne ha) |
| `Topo.port_iface(porta)` | l'IPv4 di una porta |
| `neighbors(nome)` | `[(mia_porta, vicino, porta_del_vicino), …]` |
| `switch_of(host)` / `hosts_on(switch)` | lo switch di un host / gli host di uno switch |
| `router_ports(kind_filter)` | `[(router, porta, interfaccia, vicino), …]`, eventualmente solo verso un tipo di vicino |
| `free_port(dev, tipo_del_vicino)` | la prima porta libera adatta |
| `next_name(prefisso)` | il primo nome libero, es. `PC4` |

## La rete vera: Mininet senza privilegi

`rete/kernel.py` realizza il modello così:

| Nel gioco | Nella rete vera |
|---|---|
| un dispositivo | un nodo Mininet: un namespace di rete con la sua shell |
| un cavo | una coppia veth; le porte si chiamano come in console, accorciate (`gi0-1`, `fa0-24`, `gi0-0.10`) |
| switch, VLAN, trunk | un bridge Linux con `vlan_filtering` nel namespace dello switch |
| router | inoltro IP, sottointerfacce 802.1Q, rotte statiche e OSPF calcolate dal modello (`ip route … proto 99`), ACL in `nftables` (in ingresso e in uscita per porta), limiti con `tc tbf`, DHCP con `dnsmasq`, IP virtuale HSRP sul router attivo |
| firewall | inoltro IP e `nftables` senza stato: zone con livelli di sicurezza e access-group |
| server | servizi web, FTP e posta (`strumenti.py servizi`), DNS con `dnsmasq` |
| PC in DHCP | `busybox udhcpc` |
| ping, traceroute, web, porte, nslookup, sniffer | `ping`, `traceroute`, `strumenti.py`, `tcpdump` dentro il namespace del dispositivo |

`sync()` confronta un'impronta della configurazione di ogni dispositivo con quella applicata e rifà solo quelli
cambiati (uno script `bash` per dispositivo, le regole `nft`, i demoni riavviati se la loro configurazione è
cambiata). Script, regole e registri dei demoni sono in `/tmp/pna/` (`nodi.json` dà il PID di ogni dispositivo).

**Senza privilegi.** Il container gira come utente normale, senza capability e senza `privileged`. All'avvio
`server.py` apre la porta HTTP e poi chiama `entra_nel_namespace()`: crea un *user namespace* (lì dentro è `uid 0`,
fuori resta un utente normale) con una rete e dei mount tutti suoi, e solo lì costruisce la rete del gioco. Il
socket HTTP resta nella rete del container, quindi il browser lo raggiunge. Per farlo servono:

- il profilo seccomp `deploy/seccomp.json`: quello predefinito di Docker più `unshare`, `setns`, `mount`, `umount2`;
- `deploy/mnexec.c`: il programma di Mininet che crea i namespace, modificato perché monti `/sys` in sola lettura
  e non si fermi se non può (in un user namespace dentro Docker `/sys` non si può rimontare in scrittura);
- che i programmi dentro i dispositivi non cambino utente (nel user namespace `setgroups` è vietato): `dnsmasq`
  gira con `--no-daemon`, `tcpdump` con `-Z root`;
- i moduli del kernel dell'host (`veth`, `bridge`, `8021q`, `sch_tbf`, `nf_tables`): si caricano da soli quando
  servono.

Per entrare nella rete a mano: `docker compose exec gioco nsenter -t 1 -U -n -m --preserve-credentials`, e da lì
`nsenter -t <pid> -n …` nel namespace di un dispositivo (PID in `/tmp/pna/nodi.json`).

## Lo stato della partita

`backend/data/state.json`, riscritto a ogni evento (`log()` e `save()` in `server.py`):

```json
{
  "score": 1245,
  "mode": "rescue",
  "academy": { "done": ["lan", "server"] },
  "rescue": {
    "level": 2, "status": "playing", "message": "…", "started": 1794609235.8,
    "faults": { "ospf-network": { "solved": false, "hints": 1, "surrendered": false, "points": 0 } },
    "best": { "1": 175 }
  },
  "campaign": { "done": [1, 2], "wallet": 1, "helps": { "2": ["mezza-rete"] }, "last": 2 },
  "credentials": { "R1": { "username": "admin", "secret": "…", "enable_secret": "…" } },
  "log": [ { "t": 1794609235.8, "msg": "…", "kind": "win", "points": 100 } ]
}
```

La rete è salvata a parte, in `backend/data/rete.json` (il modello: `Rete.to_dict()`), riscritta a ogni modifica.

`campaign`: giorni completati, saldo della valuta di gioco (`null` finché non viene dato quello iniziale), aiuti
comprati per giorno e ultimo giorno chiuso (per mostrare il suo dialogo di fine giornata). Le partite salvate
prima della campagna vengono completate da sole al caricamento.

`credentials` sono le password dei dispositivi **dentro la simulazione**, salvate da `device/harden` e
`device/login` perché il server possa fare il login sulle console protette. Non escono mai dall'API
(`public_state()` non le include) e il file è fuori da git.

Non vengono salvati il token di sessione (al riavvio si rifà il login) e il lavoro in corso (un livello che si
stava preparando resta in `setup`: basta riavviarlo). Per ripartire da zero: server fermo, cancellare i due file
(nel container: `docker compose down -v`).

## Come estendere

### Aggiungere un endpoint

Esempio: accendere o spegnere un dispositivo.

```python
# server.py, sezione "azioni sul tavolo"
async def a_power(b):
    """Accende o spegne un dispositivo.

    Body: {"name": str, "on": bool}
    Risposta: Stato.
    """
    d = need(("router", "switch", "pc", "server"), b.get("name", ""))   # esiste ed è del tipo giusto?
    on = need_bool(b, "on")                                             # input validato o ValueError → 400
    await motore.power(d["name"], on)                                   # il motore cambia il modello e la rete
    log(f"{d['name']} {'acceso' if on else 'spento'}", "config")        # una riga nel diario

# nella tabella `routes`, nel gruppo giusto
Route("/api/device/power", action(a_power), methods=["POST"]),
```

Lista di controllo:

1. **Validate tutto** con gli aiutanti già pronti: `need()` (dispositivo e tipo), `need_port()`, `need_bool()`,
   `ip_of()`, `mask_of()`. Ogni errore dell'utente è un `ValueError` con un messaggio in italiano per il
   giocatore.
2. **Non costruite righe della console con testo libero dell'utente**: solo valori validati (IP, numeri, nomi
   di porta presi dalla topologia). Per configurare usate `configura(device, righe, cosa)` (o
   `configura_firewall()` per il firewall), che aggiunge `enable` / `configure terminal` / `end`, fa il login
   se la console è protetta e trasforma un rifiuto della console in un 400 leggibile. Per comandi che non
   configurano (es. `show`) usate `console(device, comandi)`, che fa solo il login. **Mai `motore.cli()`
   direttamente** da un endpoint: su un dispositivo protetto i comandi finirebbero nel prompt di login.
3. Se la console non sa fare quello che serve, il comando va aggiunto in `rete/console.py` (che cambia il
   modello) e, se ha un effetto sul traffico, in `rete/kernel.py` (che lo realizza) e in `rete/percorso.py`
   (che lo spiega): le tre parti devono dire la stessa cosa.
4. `motore.links_dirty = True` se l'azione cambia cavi o porte e i PC in DHCP devono riprovare.
5. `log(...)` con il `kind` giusto (`build`, `config`, `ok`, `fail`, `win`, `hint`, `info`). Mai password nel diario.
6. Se il client ha bisogno di qualcosa oltre allo Stato, restituite un `dict` (es. `{"console": out}`).
7. Se può durare più di qualche secondo, fatene un **lavoro**: `job_action` + `start_job`, con `job.step()`
   a ogni passo (vedi `j_rescue_check`).
8. Documentatelo in [API.md](API.md): tabella "In breve" e sezione dell'endpoint.

### Aggiungere un tipo di dispositivo

I tipi (router, switch, PC, server, firewall, cloud) si configurano in `impostazioni.yaml` → `dispositivi`
(prefisso del nome, nome, icona). Per un **tipo nuovo** (es. laptop) serve codice:

1. `rete/modello.py`: `TIPI`, `PORTE_TIPO` (le sue porte) e `MODELLO`; come lo realizza `rete/kernel.py`.
2. `contenuti.py`, `KINDS` e `IMPOSTAZIONI_PREDEFINITE["dispositivi"]`: il tipo e i suoi valori predefiniti;
   poi la voce in `impostazioni.yaml`.
3. Le funzioni che accettano quel tipo: i `need(...)` degli endpoint interessati.
4. `frontend/demo/scripts/app.js`: il disegno in `iconSvg()` e il pannello in `renderInspector()`.

### Aggiungere una rete di partenza

Le reti di partenza sono file YAML in `frontend/demo/gioco/reti/` (formato completo in `rete/topologia.py`):

```yaml
dispositivi:
  - nome: R1
    tipo: router                 # router, switch, pc, server, firewall
    pos: [420, 270]              # posizione sul tavolo
    config: |                    # router, switch e firewall: righe della console, in configurazione
      interface GigabitEthernet0/0
       ip address 192.168.1.1 255.255.255.0
       no shutdown
  - nome: PC1
    tipo: pc
    pos: [300, 450]
    ip: 192.168.1.10/24          # oppure «dhcp»
    gateway: 192.168.1.1
  - nome: WEB
    tipo: server
    pos: [600, 450]
    ip: 192.168.1.20/24
    servizi: [http, https]       # i servizi accesi (gli altri spenti)
cavi:
  - [R1, GigabitEthernet0/0, SW1, GigabitEthernet0/1]
```

Un router con porte diverse da quelle standard le elenca in `porte:` (per esempio R2 di `giorno3.yaml`, con
quattro porte Ethernet). Un errore nel file (un cavo verso una porta che non esiste, una riga di configurazione
che la console rifiuta) blocca il caricamento con un messaggio preciso. Per crearne una si può anche costruire la
rete nel gioco e partire dalla `running-config` di ogni dispositivo.

### Aggiungere una challenge (missione) di un giorno

I giorni della storia sono fatti di **challenge**, che sono missioni dell'Accademia: in `accademia.yaml` (una voce
di `missioni`, con id tipo `g1-lan`) e nell'elenco `monitor: {modalita: academy, missioni: [...]}` del giorno in
`storia.yaml` (e in `campagna.yaml`, per la campagna del server). L'intestazione di `accademia.yaml` spiega ogni
campo; esempio completo: il giorno 1 (`g1-lan`, `g1-esterno`, `g1-reparti`).

**Una topologia per giorno** (decisione del team, 15/11): il giorno 1 parte dal tavolo vuoto (`rete: vuota` nella
1.1) e le challenge dopo continuano dalla rete del giocatore; ogni giorno successivo parte da una sua rete, nella
prima challenge del giorno (`rete: giorno2.yaml` nella 2.1, `giorno3.yaml` nella 3.1, `giorno4.yaml` nella 4.1), e
le altre challenge del giorno continuano da lì. Nei giorni 3 e 4 i dispositivi della rete di partenza hanno nomi
fissi (INTERNET, R1, R2, WEB, …): i controlli li usano, mentre riconoscono dalla forma quelli aggiunti dal
giocatore (firewall, switch, il PC della VPN, il cloud). In `giorno3.yaml` R2 ha più porte Ethernet di un router
standard (firewall interno, LAN, VPN, cloud).

**Tavolo di partenza (`rete:`)**: `rete: vuota` vuol dire «si parte dal tavolo vuoto» (se sul tavolo c'è
qualcosa, il gioco lo toglie); altrimenti la topologia è un file `.yaml` in `frontend/demo/gioco/reti/`, che il
server carica (`motore.carica_topologia`). Il tavolo si prepara quando la challenge comincia: alla fine
della challenge precedente (dentro il lavoro di «Verifica»), oppure quando il giocatore si siede al computer e il
tavolo non è ancora stato preparato (`needs_setup` nello Stato, `STATE["academy"]["pronte"]` nella partita); poi
con «Ricomincia la challenge» (`POST /api/academy/network`). Senza `rete:` la challenge continua dalla rete che c'è.
Il validatore rifiuta un `rete:` il cui file non esiste. Un errore da far trovare al giocatore (es. un gateway
sbagliato) si mette direttamente nel file.

**Tutorial o no (`tutorial:`)**: `tutorial: true` è una challenge guidata (si vedono gli «aiuto» dei passi e i
messaggi su cosa manca). Senza, il giocatore vede solo le condizioni da raggiungere: niente aiuti, e una «Verifica»
non riuscita dice solo quale condizione manca. L'aiuto lo chiede a Jody: i `consigli`.

**Consigli**: si chiedono parlando con Jody in ufficio, uno alla volta. Nei giorni con `consigli_gratis: true`
(`storia.yaml`) sono gratis; negli altri il giocatore prende un caffè alla macchinetta con un buono e lo dà a Jody
in cambio del consiglio. Il pannello sul monitor mostra solo quelli già avuti.

**Passi**: oltre ai controlli registrati in `missions.py` e ai piani di ping, un passo può essere scritto tutto
nel YAML:

- `controllo: nome` di un controllo di `missions.CONTROLLI`. Per le challenge **senza nomi** (giorni 1 e 2: la
  descrizione dà solo l'obiettivo) ci sono controlli che riconoscono la forma della rete, qualunque nome e
  indirizzo scelga il giocatore: `pc_e_switch`, `pc_collegati_e_attivi`, `router_tra_lan_ed_esterno`,
  `porte_lan_ed_esterno`, `due_switch_e_router`, `reparti_collegati_al_router`, `porte_dei_reparti`,
  `ogni_reparto_nella_sua_rete`, `sala_server`, `server_con_ip_fisso`, `pc_di_prova_in_dhcp`,
  `tre_server_in_sala`, `firewall_tra_i_router` (sezione «challenge senza nomi» di `missions.py`). Giorno 3:
  `firewall_verso_internet`, `firewall_interno`, `rete_vpn`, `rete_cloud`; giorno 4: `macchina_isolata`.
- `verifica: [{piano: nome}]`: un piano di `missions.PIANI_VERIFICA` sceglie da solo i dispositivi sulla rete che
  c'è e restituisce le verifiche da fare (`lan`, `esterno`, `reparti`, `sala_server`, `nomi`, `firewall`; giorno 3:
  `dmz_web`, `dmz_isolata`, `vpn_servizi`, `vpn_limitata`, `cloud_servizi`, `cloud_isolato`).
  Verifiche nuove: `risolve: {da, nome, verso}` (nslookup; `verso` = `@X`, `server` o `resolver`) e
  `porta: {da, a, porta, passa: true|false}` (il traffico arriva al server anche se lì nessuno ascolta, come un
  SSH «simulato», oppure lo scarta un firewall). `osserva: {coppia: lan}` sceglie da solo i due PC.
- `controllo:` dichiarativo: `dispositivi`, `cavi`, `cavi_attivi`, `niente_cavi`, `indirizzi`, `stessa_rete`,
  `gateway`, `porte_router`, `ip_distinti` (`missions.CONTROLLI_DICHIARATIVI`). Il giorno 1 non impone
  indirizzi: `stessa_rete: [[PC1, PC2]]` (stessa rete, IP diversi), `gateway: {PC1: "@R1"}` (la porta di R1
  nella rete del PC), `porte_router` con `rete_di: PC1` o `gateway_di: EXT-TEST` al posto di `ip`, e le
  verifiche puntano ai dispositivi per nome (`ping: {da: PC1, a: "@PC2"}`). `indirizzi` e `gateway` con un
  IP restano per le challenge che vogliono un piano preciso.
  Si spunta da solo; più chiavi insieme devono passare tutte.
- `verifica:` una lista di verifiche esatte sul traffico vero, le stesse dei livelli (vedi [Le verifiche](#le-verifiche)),
  eseguite con «Verifica».
- `domanda:` `{testo, scelte, giusta, spiegazione}`: il giocatore risponde nel pannello (`POST /api/academy/answer`).
  I passi interattivi (domande, osserva) si controllano prima delle verifiche sul traffico: una domanda ancora
  senza risposta ferma la «Verifica», anche se è l'ultimo passo.
- `traffico:` (della missione, non di un passo) `[{da, comando}]`: il traffico di fondo che lo **sniffer** vede
  durante la challenge, come il `traffico` dei livelli (`ping IP`, `telnet IP porta`, `web http://IP`, IP anche
  come `@NOME`). Il giorno 4 lo usa per gli attacchi da scoprire (forza bruta, DDoS, reverse shell). Ogni
  dispositivo esegue un comando per cattura.
- `aiuto:` (come si fa) è facoltativo e si vede solo nelle challenge `tutorial`.
- `osserva:` `{da, a, spiegazione}`: il giocatore guarda il viaggio del ping passo per passo
  (`POST /api/academy/observe`, la stessa traccia di `/api/trace`).

Il pannello del client Penguin Inc mostra la `storia` (la descrizione: deve contenere tutto quello che serve,
nomi, collegamenti e indirizzi; con `|-` gli a capo restano), il pulsante «Manuale» con la `teoria` e i
`consigli` già avuti. La lista dei passi c'è solo nelle challenge `tutorial`; nelle altre si vede quanti
obiettivi sono raggiunti, più i passi interattivi (`domanda`, `osserva`) quando si aprono. `topologia` e `piano`
restano nell'API ma il client non li mostra più. I pop-up di Jody sul monitor sono in `storia.yaml` (`guida`
nella giornata: `inizio` e `passi`).

Prima di pubblicare una challenge: giocatela tutta dal vivo con `PNA_COLLAUDO=1` (anche l'API va bene, come fa
il client), compresa la rete che viene caricata alla fine.

### Aggiungere una missione (vecchio stile, con i controlli in Python)

In `frontend/demo/gioco/accademia.yaml`, una voce nella lista `missioni`. Le missioni senza `rete:` continuano
dalla rete lasciata dalla precedente. L'intestazione del file spiega ogni campo.

```yaml
  - id: nome-breve
    titolo: Titolo
    punti: 150
    storia: La storia in una frase.
    teoria:
      - concetto: Concetto
        spiegazione: Spiegazione in una frase.
    passi:
      - testo: Cosa fare
        aiuto: Come farlo
        controllo: switch_presente          # un nome di missions.CONTROLLI
      - testo: Prova finale
        aiuto: Premi «Verifica».
        ping: ping_tra_pc_della_lan         # un nome di missions.PIANI_PING
```

Se serve un controllo che non esiste ancora, si scrive in `missions.py` e si registra con un nome leggibile:

- Un **controllo** è `funzione(t: Topo) -> (bool, str)`: `(True, "")` se il passo è fatto, altrimenti
  `(False, "cosa manca, detto al giocatore")`. Viene chiamato a ogni lettura dello stato, quindi deve essere
  veloce e non toccare la rete. Va aggiunto a `CONTROLLI`.
- Un **piano di ping** è `funzione(t: Topo) -> [(da, ip, etichetta), …]`: le coppie da provare, la migliore
  per prima (il server ne prova al massimo due). Lista vuota = "non trovo dispositivi pronti". Va aggiunto a
  `PIANI_PING`.
- Siate tolleranti: accettate qualsiasi indirizzamento sensato, gli IP suggeriti nel testo sono solo un aiuto.

### Aggiungere un livello

In `frontend/demo/gioco/pronto-soccorso.yaml`, una voce nella lista `livelli`. Non serve codice: rete,
rottura e verifiche si compongono tutte nel YAML. L'intestazione del file spiega ogni campo.

```yaml
  - numero: 5
    titolo: Titolo
    rete: livello5.yaml                    # la topologia di partenza, in gioco/reti/
    ticket: |-
      Da: …
      «Sintomi raccontati da un utente.»
    guasti:
      - id: id-guasto
        cosa_era_rotto: Cosa era rotto (mostrato quando è riparato).
        sintomo: Sintomo visto dall'utente (mostrato finché è aperto).
        rompi:
          - comandi:
              dispositivo: R2
              righe: [enable, configure terminal, "…", end]
        verifiche:
          - ping: {da: R1, a: 192.168.1.1}       # fallisce col guasto, passa a guasto riparato
        vincolo:                                 # facoltativo: vieta le scorciatoie
          ip_esatto: {dispositivo: SRV1, ip: 192.168.0.4}
        indizi: [indizio 1, indizio 2, indizio 3]
        soluzione: La soluzione.
    verifiche_finali:
      - ping: {da: PC1, a: "@PC2"}               # controllo di tutta la rete alla fine
    lezione: La morale, mostrata alla fine.
```

- Rotture possibili: `comandi` (righe della console), `spegni_porta` e `ip_host` (indirizzo di un PC o server). Una
  lista vuota `rompi: []` vuol dire "la falla c'è già nella rete di partenza" (tipico delle sfide di sicurezza).
- `"@PC2"` (nelle destinazioni e in `ip_host`) significa "l'IP che PC2 ha in quel momento": nelle reti di
  partenza i PC in `ip: dhcp` ricevono gli indirizzi dal router e l'ordine non è garantito.
- **Reti costruite a passi**: al posto di `rete` (o dopo) si scrive
  `costruisci`, una lista di azioni eseguite da `esegui_azioni()`: `aggiungi`, `collega`, `ip_host`,
  `ip_porta`, `comandi`, `zona_firewall` e `regole_firewall` (le regole finiscono nel pannello del firewall,
  come se le avesse scritte il collega di prima). Esempio completo: il livello 9 «La vetrina» (DMZ).
- **Traffico di fondo** (`traffico`): quello che lo sniffer vede durante il livello, anche quello sospetto.
  Esempio: il livello 8 «Il canale nascosto» (reverse shell).
- **Il livello si collauda in sviluppo**: con il server avviato con `PNA_COLLAUDO=1`, all'avvio del livello
  rifiuta la rete di partenza se non passa le verifiche di traffico legittimo e un guasto se non ne fa fallire
  almeno una. I guasti con `rompi: []` (la falla c'è già) sono esclusi dal controllo della rete di partenza.
  Senza `PNA_COLLAUDO` (in gioco) questi controlli si saltano. Ogni livello nuovo o cambiato va avviato almeno
  una volta con `PNA_COLLAUDO=1 docker compose up -d` e completato.
- `vincolo` scatta solo quando le verifiche passano già: serve a rifiutare le riparazioni che violano il ticket,
  non a suggerire la soluzione (il suo messaggio viene mostrato al giocatore).

#### Le verifiche

Nel YAML (`verifiche` dei guasti e `verifiche_finali` dei livelli) e il loro corrispondente nel motore
(`rescue.py`):

| YAML | Motore | Passa quando | Tipo |
|---|---|---|---|
| `ping: {da, a}` | `ping(src, dst)` | il ping da `da` ad `a` (IP o `"@DISPOSITIVO"`) arriva | traffico legittimo |
| `web: {da, url}` | `web(src, url)` | il browser di `da` riceve la pagina | traffico legittimo |
| `web_bloccato: {da, url}` | `web_blocked(src, url)` | il browser di `da` **non** riceve la pagina | sicurezza |
| `bloccato: {da, a, fermato_da}` | `blocked(src, dst, at)` | un ping da `da` al dispositivo `a` **non** arriva (con `fermato_da`: è scartato proprio da quel dispositivo) | sicurezza |
| `audit_pulito: {dispositivo, gravita}` | `audit_clean(device, worst)` | l'audit non trova falle di gravità `gravita` (alta, media, bassa) o superiore | sicurezza |
| `audit_pulito: {dispositivo, falle: [codici]}` | `audit_clean(device, codes=[…])` | l'audit non trova falle con quei codici (es. `NO_ENABLE_SECRET`, `USER_CREDENTIAL_REVERSIBLE`, `NO_SERVICE_PASSWORD_ENCRYPTION`): così più guasti dello stesso livello controllano falle diverse dello stesso router | sicurezza |
| `resiste: {spento, verifiche}` | `survives(device, *verifiche)` | con `spento` spento tutte le verifiche interne passano; poi lo riaccende | resistenza |
| `porta: {da, a, porta, aperta}` | `port(src, dst, porta, aperta)` | da `da` la porta TCP di `a` risponde (`aperta: true`, traffico legittimo) oppure no (`aperta: false`: chiusa o filtrata da un firewall); si prova come lo scanner, aprendo una connessione TCP | legittimo / sicurezza |
| `attraversa: {da, a, via}` | `through(src, dst, via)` | il ping da `da` al dispositivo `a` arriva **passando da** `via` (la traccia passo per passo): per esempio la comunicazione tra due reparti attraversa il router | traffico legittimo |
| `limitato: {da, a}` | `limited(src, dst)` | il ping da `da` al dispositivo `a` arriva e, all'andata, esce da una porta di router con un limite di velocità (`/api/router/ratelimit`, letto dalla configurazione) | sicurezza |

Come le usa il motore (`run_check` in `server.py`):

1. **Rete di partenza** (solo con `PNA_COLLAUDO=1`): devono passare solo le verifiche di traffico legittimo
   (`ping`, `web`): quelle di sicurezza all'inizio falliscono per definizione.
2. **Dopo la rottura** (solo con `PNA_COLLAUDO=1`): almeno una verifica di ogni guasto deve fallire,
   altrimenti il guasto non si vede.
3. **«Verifica» del giocatore**: un guasto è riparato quando passano **tutte** le sue verifiche.

Per le falle di sicurezza scrivete **prima** le verifiche di sicurezza e **poi** quelle di traffico legittimo:
così, se il giocatore blocca troppo (per esempio stacca un cavo o blocca tutto il traffico), il messaggio è
«hai bloccato anche traffico che deve passare» invece del sintomo originale. Le verifiche di traffico
legittimo sono anche la vostra protezione contro le scorciatoie: elencate tutto quello che deve continuare a
funzionare.

Esempio completo: il livello 4 «Il registro elettronico» in `pronto-soccorso.yaml` (filtrare il web dall'aula
verso un server, lasciando il resto, e mettere la password a un router).

### Aggiungere un giorno alla campagna

Solo YAML, in `campagna.yaml`: un giorno elenca le `missioni` (id di `accademia.yaml`) e i `livelli` (numeri di
`pronto-soccorso.yaml`) da completare, i dialoghi `prima` e `dopo` (righe `- personaggio: testo`), la ricompensa
in `buoni` e gli `aiuti`. Il validatore controlla che missioni, livelli e personaggi esistano.

Un aiuto può essere solo una risposta oppure fare qualcosa sulla rete con le `azioni`, eseguite da
`esegui_azioni()` in `server.py` (la stessa funzione che inietta i guasti dei livelli): `comandi`,
`spegni_porta`, `ip_host`, più `aggiungi` (un dispositivo, se non c'è), `collega` (un cavo sulla prima porta
libera, se non c'è già) e `ip_porta` (IP sulla porta del router verso un dispositivo, trovata dai cavi). Nelle
righe della console `@PC3` diventa l'IP attuale di PC3, utile con gli indirizzi dati dal DHCP. Un aiuto con `livello`
si compra solo mentre si gioca quel livello: le sue azioni presuppongono la rete del livello.

Collaudo: comprate l'aiuto dal vivo (`POST /api/campaign/help`) sulla rete per cui è scritto. Se le azioni
falliscono il gioco restituisce i buoni, ma quelle già eseguite restano.

### Usare l'API da un altro gioco

Il backend è pensato per essere riusato: tavolo, configurazione, console e sicurezza sono generici, e il motore
di missioni e livelli prende i contenuti da un pacchetto YAML. Per un altro gioco:

- **Altri contenuti, stesso client**: un'altra cartella di YAML e `PNA_GIOCO=/percorso/pacchetto`.
- **Altro client**: una cartella con `index.html`, `assets/` e `scripts/`, e `PNA_FRONTEND=/percorso/client`.
  Il client legge testi e impostazioni da `GET /api/ui` e gioca con le altre API. Stessa origine del server,
  quindi niente da configurare per i cookie.
- **Client su un'altra origine** (per esempio un gioco su un altro sito): serve il middleware CORS di Starlette
  con l'origine esatta e `allow_credentials=True`, e un codice di accesso (`PNA_CODE`): senza codice il server
  accetta solo richieste indirizzate a questo computer. Indebolisce la protezione contro le richieste da altri
  siti: fatelo solo se serve davvero.

Gli endpoint "gioco" (`academy/*`, `rescue/*`) usano il motore di missioni e livelli; quelli di **tavolo,
configurazione, console e sicurezza** vanno bene per qualsiasi gioco su reti, anche senza pacchetto.

### Costruire una sfida di sicurezza

Gli endpoint di sicurezza sono mattoni, non sfide: il contenuto lo scrivete voi. Il motore dei livelli
(`rescue.py`, contenuti in `pronto-soccorso.yaml`) li usa attraverso le verifiche descritte in [Aggiungere un livello](#aggiungere-un-livello):

| Parte della sfida | In `pronto-soccorso.yaml` |
|---|---|
| rete di partenza | `rete:` (una topologia di `gioco/reti/`) o `costruisci:` |
| la falla da chiudere | `rompi: []` (la rete di partenza è già aperta) o `comandi`: un'ACL `permit any`, una porta senza port-security |
| il traffico legittimo deve passare | `ping`, `web` |
| il traffico vietato deve essere bloccato | `bloccato` (traccia: `delivered: false`), `web_bloccato` |
| la configurazione deve essere sicura | `audit_pulito` |
| la rete deve reggere a un guasto | `resiste: {spento: R1, verifiche: [...]}`, per esempio con HSRP verso l'IP virtuale |

Le verifiche usano le stesse funzioni degli endpoint (`do_trace`, `do_browse`, `do_audit`), quindi quello che
il gioco controlla è esattamente quello che il giocatore può provare da solo con `/api/trace`,
`/api/host/browse` e `/api/audit`.

## Cose da sapere sulla rete

Comportamenti della rete già gestiti nel codice: se scrivete codice nuovo tenetene conto.

| Cosa | Come è gestito |
|---|---|
| Le porte dei router e dei firewall nascono spente, come sugli apparati veri | `no shutdown` nelle reti di partenza e nei form (`/api/router/iface` accende la porta) |
| Una rotta statica entra in tabella solo se il next hop sta in una rete collegata a una porta attiva | `modello.tabelle()`; `a_route` controlla la tabella e spiega perché manca |
| OSPF non ha timer: le rotte si ricalcolano a ogni modifica | `modello._ospf()`: adiacenze tra router con porte nello stesso dominio L2 e nella stessa rete, poi Dijkstra |
| Il firewall non tiene lo stato delle connessioni | `firewall.py` compila anche le regole di ritorno (per le connessioni permesse e i servizi comuni del traffico di base); kernel e traccia applicano le stesse regole |
| Un'access-list su una zona del firewall blocca anche il ping al firewall stesso (il gateway) | ogni access-list di zona inizia con «permit icmp <rete> host <IP del firewall>» |
| Un'access-list applicata ma non definita non blocca niente (come sui router veri) | `kernel._nft_router()` e `percorso._filtro_acl()` la ignorano |
| Il client DHCP e il server DHCP partono insieme: la prima offerta può perdersi | `udhcpc` prova 6 volte, una al secondo |
| Nel user namespace un programma non può cambiare utente o gruppo (`setgroups` è vietato) | `dnsmasq --no-daemon`, `tcpdump -Z root`; i servizi dei server sono script Python che non cambiano utente |
| Dopo un cambio di rete un PC ricorda il vecchio indirizzo MAC del gateway (per esempio con HSRP) | `kernel.sync()` svuota la cache ARP dei PC e dei server dopo ogni modifica |
| La rete simulata non ha `arp` né `traceroute` veri | `host_cmd` li ricostruisce dal modello (o lo dice) |
| Port-security si configura ma la rete vera non conta i dispositivi sulla porta | le verifiche dei livelli non si basano sul blocco della port-security |
| Un'ACL accodata a una con lo stesso nome somma le regole | `a_acl` cancella prima quella vecchia, così l'endpoint sostituisce |
| Dopo l'hardening con utenti la console chiede utente e password: i comandi finirebbero nel login | credenziali in `STATE["credentials"]`, `console()` + `motore.unlock()` fanno il login |
| Un `ip_host` che cambia il gateway di un server rompe anche le risposte che il server manda fuori dalla sua LAN | i guasti dello stesso livello non devono toccare i dispositivi delle verifiche degli altri guasti |

## Sicurezza

- Il container non ha privilegi (vedi [La rete vera](#la-rete-vera-mininet-senza-privilegi)): la rete del gioco
  vive in un user namespace, e anche chi riuscisse a uscire dai comandi permessi sull'host sarebbe un utente
  normale. La porta è aperta solo su `127.0.0.1`.
- Codice di accesso facoltativo (`PNA_CODE`). **Senza codice** non c'è login, e il middleware `solo_locale`
  risponde 403 a ogni richiesta il cui header `Host` non sia `127.0.0.1`, `localhost` o `::1`: blocca chi arriva
  da fuori e il *DNS rebinding* (un sito esterno aperto nel browser che si fa risolvere in 127.0.0.1 per
  comandare il gioco). **Con codice** vale il login qui sotto, e si può giocare da fuori.
- Login (con codice): confronto a tempo costante, 1 s di attesa a ogni errore, limite di 8 errori per indirizzo e 40 in tutto
  ogni 5 minuti. L'indirizzo del client è l'ultimo valore di `X-Forwarded-For`, cioè quello aggiunto dal proxy.
- Cookie `HttpOnly` e `SameSite=Strict`, e tutte le scritture richiedono `Content-Type: application/json`:
  un altro sito non può mandare comandi a nome del giocatore.
- Comandi verso la rete: i form generano righe della console solo da valori validati; la console accetta righe
  ASCII stampabili (max 15 × 160 caratteri) e le passa all'interprete (`rete/console.py`), **mai a una shell**; il
  prompt dei PC accetta solo `ping`, `ipconfig`, `tracert`, `arp`, `nslookup` con argomenti validati, e li esegue
  come liste di argomenti, senza shell. Tutto resta dentro la rete del gioco.
- Le password dei dispositivi simulati (`device/harden`, `device/login`) sono salvate in `backend/data/state.json` per
  fare il login sulle console; non escono mai dall'API e non finiscono nel diario.
- Chi ha il codice di accesso comanda la rete del gioco (può anche svuotarla): sceglietelo lungo.
  
