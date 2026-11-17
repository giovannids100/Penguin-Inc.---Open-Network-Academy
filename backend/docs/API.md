# API HTTP di Penguin Inc.

Questa è l'API con cui la pagina web comanda la rete del gioco. È pensata per essere riusata: qualsiasi client
(un'altra pagina, un altro gioco, uno script, un bot) può costruire e leggere reti vere (Mininet: macchine Linux
con traffico vero) usando solo queste chiamate, senza sapere niente di come la rete è realizzata.

Come funziona dentro e come aggiungere endpoint, missioni o livelli: [ARCHITETTURA.md](ARCHITETTURA.md).

## Indice

- [In breve](#in-breve)
- [Regole comuni](#regole-comuni): indirizzo, login, formato, errori, una cosa alla volta, operazioni lunghe
- [L'oggetto Stato](#loggetto-stato): la fotografia di rete e partita che quasi ogni risposta contiene
- Endpoint
  - [Sessione](#sessione): `login`, `state`, `mode`
  - [Dispositivi e cavi](#dispositivi-e-cavi): `device/add`, `device/move`, `device/delete`, `device/rename`, `link`, `unlink`
  - [Configurazione](#configurazione): `host/ip`, `router/iface`, `router/dhcp`, `router/route`, `port/state`, `switch/vlan`
  - [Console e prove](#console-e-prove): `cli`, `host/cmd`, `ping`
  - [Sicurezza](#sicurezza): `router/acl`, `router/acl/remove`, `host/browse`, `trace`, `switch/port-security`,
    `device/harden`, `device/login`, `device/users`, `device/users/remove`, `audit`, `device/power`, `router/hsrp`
  - [Gioco: Accademia](#gioco-accademia): `academy/check`, `academy/reset`, `academy/network`, `academy/answer`,
    `academy/observe`
  - [Gioco: Pronto Soccorso](#gioco-pronto-soccorso): `rescue/start`, `rescue/check`, `rescue/hint`, `rescue/surrender`
  - [Gioco: Campagna](#gioco-campagna): `campaign/help`, `campaign/reset`
- [Esempi completi](#esempi-completi): curl e JavaScript

## In breve

| Metodo | Percorso | Cosa fa | Tipo |
|---|---|---|---|
| GET | `/api/health` | il server è acceso e la rete è pronta? (senza login) | sessione |
| GET | `/api/ui` | testi e impostazioni dell'interfaccia dal pacchetto di gioco (senza login) | sessione |
| POST | `/api/login` | entra con il codice di accesso | sessione |
| GET | `/api/state` | stato completo: rete, partita, diario | lettura |
| POST | `/api/mode` | passa tra Accademia e Pronto Soccorso | gioco |
| POST | `/api/device/add` | aggiunge router, switch, PC o server | azione |
| POST | `/api/device/move` | sposta un dispositivo sul tavolo | azione |
| POST | `/api/device/delete` | toglie un dispositivo (e i suoi cavi) | azione |
| POST | `/api/device/rename` | rinomina un dispositivo | azione |
| POST | `/api/link` | collega due dispositivi con un cavo | azione |
| POST | `/api/unlink` | stacca il cavo da una porta | azione |
| POST | `/api/host/ip` | IP statico o DHCP su PC e server | azione |
| POST | `/api/router/iface` | IP e accensione di una porta del router | azione |
| POST | `/api/router/dhcp` | crea un pool DHCP sul router | azione |
| POST | `/api/router/route` | aggiunge o toglie una rotta statica | azione |
| POST | `/api/port/state` | accende o spegne una porta (router o switch) | azione |
| POST | `/api/switch/vlan` | mette una porta dello switch in una VLAN | azione |
| POST | `/api/cli` | comandi liberi nella console di router e switch | azione |
| POST | `/api/host/cmd` | ping, ipconfig, tracert… dal prompt di un PC | azione |
| POST | `/api/ping` | ping vero, con esito sì/no | azione |
| POST | `/api/router/acl` | crea o sostituisce un'ACL e la applica a una porta | azione |
| POST | `/api/router/acl/remove` | stacca e cancella un'ACL | azione |
| POST | `/api/host/browse` | apre una pagina web da un PC (prova un servizio, non solo il ping) | azione |
| POST | `/api/trace` | segue un ping passo per passo: dove e perché viene scartato | azione |
| POST | `/api/switch/port-security` | limita i dispositivi ammessi su una porta dello switch | azione |
| POST | `/api/device/harden` | password, utenti, SSH, banner su router e switch | azione |
| POST | `/api/device/login` | comunica le credenziali di un dispositivo protetto | azione |
| POST | `/api/device/users` | utenti locali di router e switch e come è salvata la loro password | azione |
| POST | `/api/device/users/remove` | cancella un utente locale | azione |
| POST | `/api/audit` | trova le falle nella configurazione di router e switch | azione |
| POST | `/api/device/power` | spegne o accende un dispositivo (guasto simulato) | azione |
| POST | `/api/router/hsrp` | gateway di scorta tra due router | azione |
| POST | `/api/firewall/zone` | una porta del firewall diventa una zona: inside, dmz, outside… | azione |
| POST | `/api/firewall/rules` | regole del firewall tra le zone (compilate nelle access-list del firewall) | azione |
| POST | `/api/server/services` | servizi esposti da un server (HTTP, HTTPS, FTP, posta) | azione |
| POST | `/api/server/service` | accende o spegne un servizio di un server | azione |
| POST | `/api/router/ratelimit` | limite di velocità in uscita su una porta di router (rate limiting) | azione |
| POST | `/api/scan` | scansione delle porte TCP di un host, come nmap | lavoro lungo |
| POST | `/api/sniff` | cattura il traffico che passa da un dispositivo, come Wireshark | lavoro lungo |
| POST | `/api/academy/check` | verifica la missione in corso | lavoro lungo |
| POST | `/api/academy/reset` | svuota il tavolo e riparte dalla missione 1 | lavoro lungo |
| POST | `/api/academy/network` | riporta il tavolo alla rete di partenza della missione in corso (la sua topologia) | lavoro lungo |
| POST | `/api/academy/answer` | risponde alla domanda di un passo della missione | gioco |
| POST | `/api/academy/observe` | segue il ping di un passo «osserva» nella simulazione e lo restituisce | azione |
| POST | `/api/rescue/start` | prepara un livello (costruisce e rompe la rete) | lavoro lungo |
| POST | `/api/rescue/check` | verifica le riparazioni | lavoro lungo |
| POST | `/api/rescue/hint` | indizio sul primo guasto aperto | gioco |
| POST | `/api/rescue/surrender` | mostra la soluzione del primo guasto aperto | gioco |
| POST | `/api/campaign/help` | compra l'aiuto di un collega (risposta ed eventuali azioni sulla rete) | azione |
| POST | `/api/campaign/reset` | riporta la campagna al giorno 1 (non tocca la rete) | gioco |
| POST | `/api/collaudo/accademia` | *solo con `PNA_COLLAUDO=1`*: segna le missioni completate (cheat per il debug) | gioco |

I tipi decidono la forma della risposta: **azione** e **gioco** rispondono con lo [Stato](#loggetto-stato)
aggiornato, **lavoro lungo** risponde subito e prosegue in background ([vedi sotto](#operazioni-lunghe-i-lavori)).

## Regole comuni

### Indirizzo

Il server ascolta su `http://127.0.0.1:8787` (porta cambiabile con `PNA_PORT`). Da fuori ci si arriva solo
attraverso un proxy come Tailscale Funnel (`https://<mac>.<tailnet>.ts.net`). Tutti i percorsi qui sotto sono
relativi a quell'indirizzo.

### Login (solo se c'è un codice di accesso)

Il codice di accesso è **facoltativo** e si attiva avviando il server con `PNA_CODE`.

- **Senza codice** (default, e quello del lanciatore in locale): niente login, ogni chiamata funziona subito.
  In cambio il server risponde **solo a richieste indirizzate a questo computer** (`127.0.0.1`, `localhost`,
  `::1`): tutte le altre ricevono **403** con la spiegazione. Così non si arriva al gioco da Internet né da un
  sito esterno aperto nel browser. `GET /api/health` dice `"login": false`.
- **Con codice** (`PNA_CODE=...`, necessario per giocare da altri dispositivi):
  1. `POST /api/login` con `{"code": "<codice>"}`.
  2. La risposta imposta il cookie `pna` (HttpOnly, SameSite=Strict, valido 7 giorni; `Secure` quando si arriva
     in HTTPS). Il browser lo rimanda da solo; con curl serve un cookie jar (`-c` / `-b`).
  3. Ogni altro endpoint `/api/...` senza cookie valido risponde **401** (tranne `GET /api/health`).

  Il cookie vale finché il server non si riavvia: al riavvio tutti devono rifare il login.

Un client che vuole funzionare in entrambi i casi può chiamare sempre `POST /api/login`: senza codice
risponde `{"ok": true}` qualunque cosa riceva.

### Formato delle richieste

- Le letture sono `GET` senza body.
- Tutte le scritture sono `POST` con header **`Content-Type: application/json`** e un **oggetto JSON** come body
  (anche vuoto: `{}`). Senza quell'header la risposta è 400: è una protezione contro i siti esterni che provano
  a mandare form al posto del giocatore.
- I campi sconosciuti vengono ignorati. I campi facoltativi si possono omettere.

### Formato delle risposte

Ogni risposta è JSON con il campo **`ok`**:

```json
{ "ok": true,  ... }
{ "ok": false, "error": "Indirizzo IP non valido: «192.168.1». Scrivilo come quattro numeri tra 0 e 255, es. 192.168.10.1" }
```

I messaggi di `error` sono **in italiano e scritti per il giocatore**: si possono mostrare così come sono.

### Errori

| Codice | Quando | Cosa fare |
|---|---|---|
| 400 | dati non validi (IP, maschera, nome, porta inesistente, dispositivo del tipo sbagliato…) oppure la console ha rifiutato il comando | mostrare `error` al giocatore |
| 401 | *con codice*: manca il login o il server è stato riavviato | rifare `POST /api/login` |
| 403 | *con codice*: codice sbagliato. *Senza codice*: richiesta non indirizzata a questo computer | – / usare `http://127.0.0.1:<porta>` |
| 409 | la rete è occupata da più di 3 minuti, oppure c'è già un altro lavoro lungo in corso | riprovare tra poco |
| 429 | troppi codici sbagliati (8 dallo stesso indirizzo o 40 in tutto in 5 minuti) | aspettare qualche minuto |
| 502 | la rete non ha fatto quello che si chiedeva per un motivo interno (un programma di rete non è partito…) | controllare `docker compose logs`, riprovare |

### Una cosa alla volta

La rete esegue un'operazione per volta. Se arriva un'azione mentre un'altra è in
corso, **va in coda**: il server la esegue appena la rete si libera, in ordine di arrivo, e solo allora risponde
(una richiesta può quindi durare quanto le operazioni che ha davanti). Se l'attesa supera 3 minuti
(`CODA_S` in `server.py`) risponde **409** con il motivo. Il client può guardare `busy` nello Stato per
avvisare il giocatore che il suo comando è in coda. Le letture (`GET /api/state`) non
vengono mai bloccate: se la rete è occupata restituiscono l'ultima fotografia disponibile (`snap_age` dice quanto
è vecchia).

C'è **una sola rete e una sola partita**: tutti i client collegati vedono e modificano la stessa rete e
lo stesso punteggio.

### Operazioni lunghe: i lavori

Preparare un livello richiede circa un minuto; una verifica fino a un minuto (un ping che fallisce aspetta il
suo timeout). Queste operazioni sono **lavori** (*job*):

1. La chiamata risponde subito con `{"ok": true, "job": {...}}` ([oggetto Lavoro](#lavoro-job)).
2. Il lavoro prosegue in background; mentre gira, le azioni vanno in coda dietro di lui, mentre un secondo
   lavoro lungo riceve subito 409 (per esempio due «Verifica» di fila).
3. Il client interroga `GET /api/state` (ogni secondo va bene) e guarda il campo `job`: `steps` racconta cosa
   sta facendo, e quando `done` è `true` il risultato è in `job.result`.

Si può avere un solo lavoro alla volta. L'ultimo lavoro resta visibile in `job` anche dopo la fine, finché non
ne parte un altro: usate `job.id` per non mostrare due volte lo stesso risultato.

## L'oggetto Stato

`GET /api/state` lo restituisce intero, e tutte le azioni e i comandi di gioco lo restituiscono aggiornato
(più qualche campo extra, indicato endpoint per endpoint). Così il client non deve mai ricostruire niente:
ridisegna e basta.

| Campo | Tipo | Significato |
|---|---|---|
| `ok` | bool | sempre `true` |
| `rete` | oggetto | stato della rete: `{"ok": bool, "status": "rete vera (Mininet)"}`, oppure `"rete simulata (…)"` con il motivo |
| `topology` | [Topologia](#topologia) | dispositivi e cavi della rete, più `roles` (dispositivo → ruolo mostrato, es. `"cloud"`) |
| `firewall` | oggetto | firewall → regole del giocatore (come le ha mandate a `firewall/rules`, normalizzate) |
| `snap_age` | numero | secondi trascorsi dall'ultima lettura della topologia |
| `busy` | bool | `true` se la rete sta eseguendo qualcosa (un'azione andrebbe in coda) |
| `job` | [Lavoro](#lavoro-job) o `null` | l'ultimo lavoro lungo, in corso o finito |
| `score` | intero | punti totali della partita |
| `mode` | `"academy"` \| `"rescue"` | modalità mostrata |
| `academy` | [Accademia](#accademia) | missioni e passi |
| `rescue` | [Pronto Soccorso](#pronto-soccorso) | livelli, ticket, guasti |
| `campaign` | [Campagna](#campagna) | giorni, dialoghi, valuta di gioco, aiuti dei colleghi |
| `log` | lista di [Voci del diario](#voce-del-diario) | ultime 30 cose successe, la più recente per prima |
| `errore_contenuti` | stringa | errore nei YAML del pacchetto di gioco (`""` = tutto bene); il gioco intanto usa l'ultima versione buona |

### Topologia

```json
{
  "devices": [ /* Dispositivo */ ],
  "links":   [ { "a": "R1", "pa": "GigabitEthernet0/0", "b": "SW1", "pb": "GigabitEthernet0/1", "cable": "copper" } ]
}
```

**Cavo** (`links[]`): collega la porta `pa` del dispositivo `a` alla porta `pb` del dispositivo `b`.
`cable` è il tipo di cavo: oggi sempre `"copper"` (rame).

**Dispositivo** (`devices[]`):

| Campo | Tipo | Significato |
|---|---|---|
| `name` | stringa | nome univoco, es. `R1`, `PC3` |
| `kind` | stringa | `router`, `switch`, `pc`, `server` o `firewall` |
| `model` | stringa | nome del tipo da mostrare: `Router`, `Switch`, `PC`, `Server`, `Firewall` |
| `x`, `y` | interi | posizione sul tavolo (angolo in alto a sinistra dell'icona, che è circa 50×40) |
| `power` | bool | acceso |
| `ports` | lista di [Porta](#porta) | porte di rete (sui router anche le sottointerfacce, es. `GigabitEthernet0/0.10`) |
| `gw` | stringa | *solo PC e server*: gateway predefinito (`"0.0.0.0"` = nessuno) |
| `dhcp` | bool | *solo PC e server*: `true` se prende l'indirizzo via DHCP |
| `routes` | lista | *solo router*: tabella di routing, `{"net", "mask", "via", "port", "proto"}`. `proto` è la lettera della console: `C` collegata, `S` statica, `O` OSPF |
| `pools` | lista | *solo router*: pool DHCP, `{"name", "net", "mask", "gw"}` |
| `acls` | lista | *solo router*: ACL definite, `{"id", "extended", "rules"}`; `rules` sono le righe così come le mostra la console (`"deny tcp 192.168.1.0 0.0.0.255 host 192.168.0.3 eq www"`) |

#### Porta

| Campo | Tipo | Significato |
|---|---|---|
| `n` | stringa | nome completo, es. `GigabitEthernet0/1`, `FastEthernet0`, `GigabitEthernet0/0.10` (sottointerfaccia) |
| `ip`, `mask` | stringhe | indirizzo e maschera; `""` o `"0.0.0.0"` = nessuno. Un PC che non ha ricevuto risposta dal DHCP mostra `169.254.x.x` |
| `on` | bool | accesa (`false` = `shutdown`) |
| `up` | bool | il collegamento è attivo. Per un collegamento davvero funzionante servono `link`, `on` e `up` |
| `link` | bool | c'è un cavo |
| `vlan` | intero, `"trunk"` o `null` | *solo switch*: VLAN della porta access, oppure `"trunk"` |
| `psec` | bool | *solo switch*: port-security attiva sulla porta |
| `acl_in`, `acl_out` | stringhe | *solo router*: ACL applicata in ingresso / in uscita (`""` = nessuna) |

Come la pagina colora i pallini dei cavi: rosso se `on` è `false`, verde se `up` è `true`, altrimenti arancio.

### Lavoro (`job`)

| Campo | Tipo | Significato |
|---|---|---|
| `id` | stringa | identificativo, cambia a ogni lavoro |
| `title` | stringa | es. `"Preparo il livello 2"` |
| `steps` | lista di stringhe | ultimi 8 passi, l'ultimo è quello in corso |
| `elapsed` | intero | secondi dall'inizio |
| `done` | bool | finito |
| `ok` | bool | finito bene (vale solo con `done`) |
| `result` | oggetto | quando è finito: `{"ok": bool, "message": "…", "win": true?}`. `message` è da mostrare al giocatore; `win` c'è quando una missione o un livello è stato appena completato |

### Accademia

```json
{
  "missions": [ { "id": "g1-lan", "title": "1.1 · La prima LAN", "points": 100, "done": true } ],
  "current": {
    "id": "g1-esterno", "title": "1.2 · Collegare la LAN all'esterno", "points": 150,
    "story": "I due PC si parlano, ma devono raggiungere una rete esterna…",
    "theory": [ ["Router", "Collega reti diverse…"] ],
    "topology": "PC1 ───── SW1 ───── PC2\n           |\n           R1 …",
    "plan": [ ["Segmento", "Configurazione"], ["LAN utenti", "10.10.10.0/24"] ],
    "tips": [ "Controlla che entrambi i PC siano collegati allo switch.", "…" ],
    "has_network": true, "tutorial": false, "needs_setup": false,
    "steps": [
      { "text": "Aggiungi R1 e collegalo allo switch", "hint": "…", "ok": false, "msg": "Manca R1 (un router).",
        "ping": false, "kind": "controllo", "open": true },
      { "text": "Serve un router?", "hint": "…", "ok": false, "msg": "Rispondi alla domanda.", "ping": false,
        "kind": "domanda", "open": true, "question": { "text": "…", "choices": ["…", "…", "…"] } }
    ]
  },
  "finished": false
}
```

- `current` è la missione da giocare (la prima non completata del giorno in corso; `null` quando sono finite tutte,
  e allora `finished` è `true`).
- `topology`, `plan`, `tips`: la topologia da raggiungere (testo), il piano di indirizzamento (righe di celle, la
  prima è l'intestazione) e i consigli del collega senior, dal più vago al più preciso (il client li fa chiedere a
  Jody, in ufficio). `has_network`: la missione ha un tavolo di partenza (una topologia o il tavolo vuoto).
- `needs_setup`: quel tavolo di partenza non è ancora stato preparato. Il client chiama allora
  `POST /api/academy/network` con `{"start": true}` prima che il giocatore cominci (così, per esempio, il giorno 1
  parte davvero dal tavolo vuoto anche se sul tavolo era rimasta un'altra rete).
- `tutorial`: missione guidata. Quando è `false` il client non mostra `hint` e `msg` dei passi, e il messaggio di
  una «Verifica» non riuscita dice solo quale condizione manca, non perché.
- `steps[].kind` dice come si completa il passo: `controllo` (si spunta da solo, ricalcolato a ogni lettura della
  rete), `ping` e `verifica` (traffico vero, solo con `POST /api/academy/check`; per questi `ping` è `true`),
  `domanda` (con `academy/answer`; c'è `question` con testo e scelte, mai la risposta giusta) e `osserva` (con
  `academy/observe`). `msg` spiega cosa manca.
- `open`: nessun controllo precedente manca, quindi il passo si può giocare. Un controllo non fatto blocca i passi
  dopo; i passi di traffico no (si fanno tutti con «Verifica»), così una domanda dopo un ping resta giocabile.

### Pronto Soccorso

```json
{
  "levels": [ { "n": 1, "title": "Primo giorno", "faults": 2, "best": 175 } ],
  "current": {
    "n": 1, "title": "Primo giorno",
    "ticket": "Da: Giulia (PC2)\n«Da stamattina…»",
    "status": "playing",
    "faults": [
      { "id": "srv-subnet", "solved": false, "surrendered": false, "points": 0,
        "hints": ["Il server è acceso e il cavo è a posto…"], "symptom": "Il server 192.168.0.4 non risponde." },
      { "id": "sw-port-shut", "solved": true, "surrendered": false, "points": 100, "hints": [],
        "title": "La porta FastEthernet0/2 di SW1…", "solution": "SW1 → porta FastEthernet0/2 → accendi…" }
    ],
    "message": "Ancora da sistemare: …",
    "lesson": ""
  },
  "hint_cost": 25,
  "fault_points": 100
}
```

- `levels[].best`: record del livello (`null` se mai completato).
- `current.status`: `setup` (in preparazione), `playing`, `won` (completato), `error` (non è partito: il motivo è
  in `message`). `current` è `null` se non è mai stato avviato un livello.
- Un guasto **aperto** mostra solo `symptom`, cioè cosa vede l'utente. Quando è riparato o il giocatore si è
  arreso compaiono `title` (cosa era rotto) e `solution`: il client non può barare perché la soluzione non
  viaggia finché non è il momento.
- `hints`: gli indizi già sbloccati, in ordine.
- `lesson`: morale del livello, presente solo quando `status` è `won`.

### Campagna

La storia a giorni (`campagna.yaml`): ogni giorno chiede di completare missioni e/o livelli, ha i dialoghi dei
personaggi e dà una ricompensa nella valuta di gioco (in Penguins Inc. i buoni caffè), con cui si comprano gli
aiuti dei colleghi.

```json
{
  "enabled": true,
  "currency": { "nome": "buoni caffè", "singolare": "buono caffè", "icona": "☕" },
  "wallet": 1,
  "days": [ { "n": 1, "title": "Il primo giorno", "done": false, "current": true } ],
  "current": {
    "n": 1, "title": "Il primo giorno", "reward": 1,
    "intro": [ { "who": "pinna", "name": "Pinna", "role": "pinguina senior", "icon": "🐧", "text": "Benvenuto…" } ],
    "activities": [ { "kind": "mission", "id": "lan", "title": "Due PC che si parlano", "done": false },
                    { "kind": "level", "n": 5, "title": "Le foche alla porta", "done": false } ],
    "helps": [ { "id": "indirizzi", "colleague": { "id": "tina", "name": "Tina", "role": "sistemista", "icon": "🛠️" },
                 "cost": 1, "request": "Che indirizzi IP devo dare ai PC?", "used": false, "answer": "",
                 "acts": false, "level": null, "available": true, "affordable": true } ]
  },
  "last": { "n": 3, "title": "…", "outro": [ { "who": "pinna", "…": "…" } ] },
  "finished": false,
  "ending": [],
  "characters": { "pinna": { "name": "Pinna", "role": "pinguina senior", "icon": "🐧" } }
}
```

- `enabled`: `false` se il pacchetto non ha una campagna (gli altri campi sono vuoti).
- `current`: il giorno in corso, cioè il primo non completato; `null` a campagna finita (allora `finished` è
  `true` ed `ending` contiene il dialogo finale).
- `activities`: cosa completare. Un'attività fatta prima (anche senza campagna) vale.
- `helps[].answer`: la risposta del collega, presente solo dopo averla comprata (`used`). `acts`: l'aiuto
  cambia anche la rete. `level`: si può chiedere solo mentre si gioca quel livello (`available`).
  `affordable`: il saldo basta.
- `last`: l'ultimo giorno chiuso, con il dialogo di fine giornata (`outro`) da mostrare.
- Il giorno si chiude da solo alla verifica che completa la sua ultima attività: la risposta di
  `academy/check` o `rescue/check` contiene allora **`day_done`** (`{n, title, reward, message, outro}`).
- Quando c'è un giorno di campagna in corso, la missione dell'Accademia da giocare (`academy.current`) è la
  prima non fatta di quel giorno.

### Voce del diario

`{"t": 1794609235.8, "msg": "Cavo PC1 Fa0 ↔ SW1 Fa0/1", "kind": "build", "points": 0}`

`t` è un timestamp Unix; `kind` è uno tra `info`, `build`, `config`, `ok`, `fail`, `win`, `hint`; `points` sono i
punti guadagnati con quell'evento.

## Endpoint

Convenzioni delle tabelle: **obbl.** = obbligatorio. I nomi di porta sono sempre quelli completi
(`GigabitEthernet0/1`, non `Gi0/1`): si leggono da `topology.devices[].ports[].n`. Gli IP sono IPv4 scritti
per esteso; le maschere si possono scrivere `255.255.255.0`, `24` o `/24`.

---

### Sessione

#### `GET /api/health`

Senza login. Dice solo se il server è acceso, se chiede un codice e se la rete è pronta, senza dati della rete
o della partita: `{"ok": true, "app": "penguin-inc", "login": false, "rete": {"ok": true, "status": "rete vera (Mininet)"},
"contenuti": {"ok": true, "errore": ""}}`. `contenuti` dice se i YAML del pacchetto di gioco sono validi. Lo usa
il lanciatore per sapere quando il server è pronto; va bene anche per un monitoraggio.

#### `GET /api/ui`

Senza login. Testi e impostazioni dell'interfaccia, letti dal pacchetto di gioco: `{"ok": true, "testi":
{…interfaccia.yaml…, "storia": {…storia.yaml…}}, "impostazioni": {"dispositivi", "interfaccia", "punteggio"},
"collaudo": false, "errore_contenuti": ""}`. `collaudo` è `true` se il server è avviato con `PNA_COLLAUDO=1`:
il client mostra allora i cheat per il debug. Non contiene dati della partita né le soluzioni scritte in `pronto-soccorso.yaml`. Un
client lo legge all'avvio, prima del login.

`testi.storia` è la storyline di Penguin Inc (`storia.yaml`): giornate, dialoghi, aiuti del collega. Attenzione:
gli aiuti a pagamento possono contenere i comandi che sistemano un livello (oggi il livello 3), quindi chi legge
`/api/ui` li vede. Per una demo va bene; per una gara vera andrebbero spostati sul server.

#### `POST /api/login`

Entra nel gioco.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `code` | stringa | obbl. | codice di accesso |

Risposta: `{"ok": true}` più il cookie `pna`. Errori: 403 codice sbagliato (risponde dopo 1 secondo apposta),
429 troppi tentativi. È l'unico endpoint che non richiede il cookie e non controlla il `Content-Type`.
Senza codice di accesso risponde sempre `{"ok": true}`.

#### `GET /api/state`

Restituisce lo [Stato](#loggetto-stato). Se la fotografia della rete ha più di 3 secondi e la rete è libera, prima la
rinnova dal modello (istantaneo).

#### `POST /api/mode`

Cambia la modalità mostrata. Non tocca la rete.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `mode` | stringa | obbl. | `"academy"` o `"rescue"` |

Risposta: Stato.

---

### Dispositivi e cavi

Tutte rispondono con lo Stato aggiornato. Il gioco gestisce questi tipi di dispositivo (in `backend/rete/modello.py`):

| `kind` | Nomi generati | Porte |
|---|---|---|
| `router` | R1, R2… | GigabitEthernet0/0 – 0/2 (nascono spente) |
| `switch` | SW1, SW2… | FastEthernet0/1 – 0/24, GigabitEthernet0/1 – 0/2 |
| `pc` | PC1, PC2… | FastEthernet0 |
| `server` | SRV1, SRV2… | FastEthernet0 (web, FTP e posta accesi) |
| `firewall` | FW1, FW2… | GigabitEthernet1/1 – 1/8 (nascono spente) |
| `cloud` | CLOUD1… | un `server` con il ruolo `"cloud"` |

#### `POST /api/device/add`

Crea un dispositivo con il primo nome libero.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `kind` | stringa | obbl. | `router`, `switch`, `pc`, `server` |
| `x`, `y` | interi | | posizione sul tavolo (default 300, 300; limitata a 20–2000 e 20–1400) |

Risposta: Stato + **`selected`**: il nome dato al nuovo dispositivo (es. `"PC3"`).

#### `POST /api/device/move`

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | dispositivo |
| `x`, `y` | interi | obbl. | nuova posizione |

#### `POST /api/device/delete`

Toglie il dispositivo e tutti i suoi cavi.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | dispositivo |

#### `POST /api/device/rename`

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | nome attuale |
| `new` | stringa | obbl. | nuovo nome: lettere, numeri, `-` e `_`, massimo 24 caratteri, non già usato |

Risposta: Stato + **`selected`**: il nuovo nome.

#### `POST /api/link`

Collega due dispositivi. Le porte, se non indicate, le sceglie il server: la prima libera, preferendo le
Gigabit per i collegamenti tra router e switch e le FastEthernet per i PC.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `a`, `b` | stringhe | obbl. | i due dispositivi |
| `pa`, `pb` | stringhe | | porta da usare su `a` e su `b` (devono essere libere) |

Errori 400: dispositivi uguali o inesistenti, porta occupata, nessuna porta libera, due PC/server collegati
direttamente (il gioco li fa passare sempre da uno switch).

#### `POST /api/unlink`

Stacca il cavo collegato a una porta (si può indicare indifferentemente l'una o l'altra estremità).

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | dispositivo |
| `port` | stringa | obbl. | porta con il cavo |

---

### Configurazione

Tutte rispondono con lo Stato aggiornato. Gli endpoint dei router e degli switch traducono la richiesta in
comandi della console (indicati in ogni sezione) e li scrivono nella console del dispositivo, come farebbe una
persona: `show running-config` li mostra. Se la console rifiuta una riga la risposta è 400 con la riga
incriminata. La configurazione resta anche dopo uno spegnimento.

**Dispositivi protetti da password.** Se la console chiede utente e password (dopo
[`device/harden`](#post-apideviceharden) con degli utenti, o se il dispositivo era già protetto),
il server fa il login da solo con le credenziali che conosce. Se non le conosce la risposta è 400 con
l'istruzione di usare [`/api/device/login`](#post-apidevicelogin).

#### `POST /api/host/ip`

Configura l'indirizzo di un PC o di un server.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | PC o server |
| `mode` | stringa | | `"static"` (default) o `"dhcp"` |
| `ip` | stringa | obbl. se statico | indirizzo |
| `mask` | stringa | | maschera (default `255.255.255.0`) |
| `gateway` | stringa | | gateway; vuoto = nessun gateway |
| `dns` | stringa | | server DNS; vuoto = lascia quello che c'è |

Con `"dhcp"` il PC chiede subito l'indirizzo (nella rete vera con `udhcpc`, al `dnsmasq` del router). Risposta: Stato + **`toast`**, un messaggio con l'esito
(`"PC4 ha ricevuto 192.168.20.2/24"` oppure il suggerimento di controllare il server DHCP).

#### `POST /api/router/iface`

IP e stato di una porta del router.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `port` | stringa | obbl. | porta, anche sottointerfaccia (`GigabitEthernet0/0.10`) |
| `ip` | stringa | | nuovo indirizzo; vuoto = lascia quello che c'è |
| `mask` | stringa | | maschera (default `255.255.255.0`) |
| `clear_ip` | bool | | con `ip` vuoto, toglie l'indirizzo |
| `on` | bool | | `true` (default) accende, `false` spegne |

Console: `interface <port>` · `ip address <ip> <mask>` (o `no ip address`) · `no shutdown` / `shutdown`.

#### `POST /api/router/dhcp`

Crea un pool DHCP sul router; l'indirizzo del gateway viene escluso dal pool.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `pool` | stringa | | nome del pool (default `LAN`; lettere, numeri, `-`, `_`) |
| `network` | stringa | obbl. | rete, es. `192.168.20.0` |
| `mask` | stringa | | default `255.255.255.0` |
| `gateway` | stringa | obbl. | default-router da dare ai PC; deve stare dentro la rete |
| `dns` | stringa | | server DNS |
| `exclude_from`, `exclude_to` | stringa | | indirizzi da non dare via (es. quelli fissi dei server); `exclude_to` vuoto = uno solo |

Console: `ip dhcp excluded-address <gateway>` [· `ip dhcp excluded-address <da> <a>`] · `ip dhcp pool <pool>` · `network <network> <mask>` ·
`default-router <gateway>` · `dns-server <dns>`.

#### `POST /api/router/route`

Aggiunge o toglie una rotta statica.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `network`, `mask` | stringhe | obbl. | rete di destinazione |
| `via` | stringa | obbl. | next hop (IP del router vicino) |
| `remove` | bool | | `true` toglie la rotta |

Console: `ip route <network> <mask> <via>` (con `no` davanti per toglierla).

#### `POST /api/port/state`

Accende o spegne una porta di un router o di uno switch.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router o switch |
| `port` | stringa | obbl. | porta |
| `on` | bool | obbl. | `true` = `no shutdown`, `false` = `shutdown` |

#### `POST /api/switch/vlan`

Mette una porta dello switch in modalità access nella VLAN indicata (la VLAN viene creata se non esiste).

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | switch |
| `port` | stringa | obbl. | porta |
| `vlan` | intero | obbl. | 1–1005 |

Console: `vlan <vlan>` · `interface <port>` · `switchport mode access` · `switchport access vlan <vlan>`.

---

### Console e prove

#### `POST /api/cli`

Scrive comandi nella console di un router, di uno switch o di un firewall, come farebbe una persona: serve per tutto quello
che i form non coprono (OSPF, sottointerfacce, `show …`).

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router o switch |
| `commands` | lista di stringhe o stringa | obbl. | da 1 a 15 righe di testo semplice, massimo 160 caratteri ciascuna (una stringa viene divisa per righe) |

La console parte dal punto in cui era rimasta: per configurare servono `enable` e `configure terminal`.
I comandi si possono abbreviare (`conf t`, `int g0/0`, `no sh`); `write memory` è accettato ma non serve, la
configurazione resta comunque. Se il dispositivo è protetto, il server fa prima il login (vedi sopra).
Risposta: Stato + **`console`**: la trascrizione, con il prompt prima di ogni comando e i messaggi della console
(`% Invalid input detected at '^' marker.` per una riga che non si capisce). Un comando rifiutato **non** dà
errore HTTP: l'esito è nel testo, come in una console vera. L'interprete è in `backend/rete/console.py`.

#### `POST /api/host/cmd`

Esegue un comando nel prompt di un PC o di un server.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | PC o server |
| `command` | stringa | obbl. | inizia con `ping`, `ipconfig`, `tracert`, `arp` o `nslookup`; poi solo lettere, numeri, `.`, `/`, `-` e spazi (max 60) |

Risposta: Stato + **`console`**: l'output (`ping` e `tracert` sono quelli di Linux, nel namespace del dispositivo). Un ping che fallisce impiega circa 2 secondi.

#### `POST /api/ping`

Ping vero con esito già interpretato: comodo per un client che vuole solo sapere "funziona o no".

| Campo | Tipo | | Significato |
|---|---|---|---|
| `from` | stringa | obbl. | PC, server o router da cui parte |
| `ip` | stringa | obbl. | destinazione |

Risposta: Stato + **`ping`**: `{"ok": bool, "out": "<output di ping>"}`. `ok` è `true` se sono
tornate almeno 2 risposte su 4. Nella rete simulata `out` è un riassunto calcolato sul modello.

---

### Sicurezza

Mattoni per sfide di cybersicurezza, da combinare come si vuole:

- **filtrare**: `router/acl`, `router/acl/remove`, `switch/port-security`;
- **provare** cosa passa e cosa no, e perché: `ping`, `host/browse`, `trace`;
- **irrobustire e controllare**: `device/harden`, `device/login`, `audit`;
- **mettere alla prova la resistenza**: `device/power`, `router/hsrp`.

Una sfida tipica: la rete di partenza ha una falla (un'ACL troppo permissiva, un router senza password); il
giocatore la chiude con questi endpoint; la verifica controlla che il traffico legittimo passi
(`ping`, `host/browse`) e quello vietato no (`trace` restituisce `blocked_by`), o che `audit` non trovi più falle.

#### `POST /api/router/acl`

Crea un'ACL e, se indicata una porta, la applica. Se un'ACL con lo stesso nome esiste già viene
**sostituita** (la console da sola accoderebbe le regole nuove a quelle vecchie). Ogni riga è verificata.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `acl` | stringa | obbl. | nome (una parola: lettere, numeri, `-`, `_`) o numero: 1-99 (o 1300-1999) per le standard, 100-199 (o 2000-2699) per le estese; un numero fuori intervallo dà 400 |
| `type` | stringa | obbl. | `"standard"` (guarda solo l'origine) o `"extended"` (origine, destinazione, protocollo, porta) |
| `rules` | lista | obbl. | da 1 a 30 regole, in ordine: vince la prima che corrisponde, e alla fine c'è sempre un *deny* implicito |
| `port` | stringa | | porta su cui applicarla: l'ACL viene staccata dalle porte di prima e messa solo qui. `""` = staccala da tutte; campo assente = restano le porte di prima |
| `direction` | stringa | | `"in"` (default: traffico che entra nel router da quella porta) o `"out"` (che esce) |

Una **regola**:

| Campo | Tipo | | Significato |
|---|---|---|---|
| `action` | stringa | obbl. | `"permit"` o `"deny"` |
| `source` | stringa | | da dove: `"any"` (default), un indirizzo (`"192.168.1.10"`) o una rete (`"192.168.1.0/24"`) |
| `destination` | stringa | | *solo extended*: verso dove, stesso formato |
| `protocol` | stringa | | *solo extended*: `ip` (default, tutto), `icmp` (ping), `tcp`, `udp` |
| `port` | intero | | *solo extended con tcp/udp*: porta di destinazione (80 web, 443 https, 53 dns, 25 posta…) |

Esempio: dalla LAN 192.168.1.0/24 niente web verso il server, tutto il resto sì.

```json
{"name": "R1", "acl": "FILTRO-WEB", "type": "extended", "port": "GigabitEthernet0/1", "direction": "out",
 "rules": [{"action": "deny", "protocol": "tcp", "source": "192.168.1.0/24", "destination": "192.168.0.3", "port": 80},
           {"action": "permit", "protocol": "ip"}]}
```

Console: `access-list …` o `ip access-list … ` e `ip access-group`. Lo Stato mostra l'ACL in `devices[].acls` e la porta in `acl_in`/`acl_out`.

#### `POST /api/router/acl/remove`

Stacca l'ACL da tutte le porte a cui è applicata e la cancella.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `acl` | stringa | obbl. | nome o numero dell'ACL |

#### `POST /api/host/browse`

Apre un indirizzo web dal browser di un PC o server (Desktop → Web Browser). Serve a provare un **servizio**:
un'ACL può lasciar passare il ping e bloccare il web.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | PC o server |
| `url` | stringa | obbl. | `http://` o `https://` + indirizzo o nome, eventualmente porta e percorso semplice |

Risposta: Stato + **`web`**: `{"ok": bool, "text": "testo della pagina"}`; se non arriva niente `ok` è
`false` e `text` spiega cosa controllare. Dura da 3 a circa 20 secondi.

#### `POST /api/trace`

Segue un ping passo per passo e dice **dove e perché** viene scartato. Il percorso è calcolato sul modello
della rete (`backend/rete/percorso.py`: cavi, VLAN, rotte, ACL, zone del firewall); nella rete vera un ping vero
conferma se arriva davvero.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `from` | stringa | obbl. | PC, server o router di partenza |
| `to` | stringa | obbl. | dispositivo di arrivo (nome, non IP) |

Risposta: Stato + **`trace`**:

```json
{
  "from": "PC2", "to": "SRV1", "delivered": false,
  "blocked_by": { "device": "R1",
    "reason": "l'ACL 101 (in ingresso su GigabitEthernet0/0) lo scarta: «deny icmp 192.168.1.0 0.0.0.255 host 192.168.0.3»" },
  "hops": [
    { "leg": "andata", "device": "PC2", "in": null, "out": "FastEthernet0", "status": "sent", "note": "" },
    { "leg": "andata", "device": "SW2", "in": "FastEthernet0/1", "out": "GigabitEthernet0/1", "status": "sent", "note": "" },
    { "leg": "andata", "device": "R2",  "in": "GigabitEthernet0/1", "out": "GigabitEthernet0/0", "status": "sent", "note": "" },
    { "leg": "andata", "device": "R1",  "in": "GigabitEthernet0/0", "out": null, "status": "dropped", "note": "l'ACL 101 … lo scarta" }
  ]
}
```

- `delivered`: la risposta è tornata al mittente.
- `blocked_by`: il punto in cui il pacchetto è stato scartato, con il motivo in italiano. Per un'ACL o un
  firewall contiene la riga esatta che l'ha bloccato: ottimo come indizio.
- `hops`: il giro del ping, un passo per dispositivo: `leg` è `"andata"` fino alla destinazione, poi
  `"ritorno"`; `status` è `sent`, `accepted` (arrivato) o `dropped` (scartato); `note` spiega uno scarto.

Dura meno di un secondo (nella rete vera più il ping di conferma).

#### `POST /api/switch/port-security`

Limita quanti dispositivi possono usare una porta dello switch: se se ne collega uno in più scatta la
violazione.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | switch |
| `port` | stringa | obbl. | porta |
| `max_mac` | intero | | dispositivi ammessi, 1–64 (default 1) |
| `violation` | stringa | | cosa succede alla violazione: `shutdown` (default, la porta si spegne), `restrict` (scarta e registra), `protect` (scarta in silenzio) |
| `sticky` | bool | | `true` (default): impara da sola i dispositivi ammessi |

Console: `switchport port-security` · `maximum` · `violation` · `mac-address sticky`. Lo Stato mostra `psec: true` sulla porta. Oggi la regola è registrata nella configurazione ma la rete vera non conta i dispositivi sulla porta.

#### `POST /api/device/harden`

Irrobustisce un router o uno switch. Tutti i campi sono facoltativi, ma ne serve almeno uno.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router o switch |
| `enable_secret` | stringa | | password per la modalità privilegiata (4–32 caratteri, senza spazi) |
| `users` | lista | | fino a 5 utenti locali `{"username", "secret", "privilege"?: 0-15}` |
| `ssh` | oggetto | | `{"domain": "lab.local", "modulus"?: 512\|768\|1024\|2048}`: attiva SSH v2 e limita gli accessi remoti a SSH con login locale. Richiede `users` |
| `banner` | stringa | | avviso mostrato a chi si collega (fino a 120 caratteri, senza `#`) |
| `password_encryption` | bool | | `service password-encryption` |

**Attenzione:** con degli `users` anche la **console** chiederà utente e password. Il server salva le
credenziali che passano da qui (l'utente con il privilegio più alto e la password di enable) e da quel momento
fa il login da solo. Le password restano dentro la simulazione: sono salvate solo nel file della partita sul
computer del server e **non vengono mai restituite** dall'API né scritte nel diario.

Console: `enable secret`, `username … privilege … secret …`, `line con 0` / `line vty 0 4` con `login local`,
`banner motd`, `service password-encryption`; con `ssh` anche hostname, dominio, chiavi RSA, `ip ssh version 2`
e `transport input ssh`.

#### `POST /api/device/login`

Comunica al server le credenziali di un dispositivo protetto che non ha configurato lui (a mano dalla console,
o già protetto nella rete di partenza di una sfida). Le prova subito facendo il login: se sono sbagliate
risponde 400 e non le salva.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router o switch |
| `username`, `secret` | stringhe | | utente locale (se la console chiede «Username:») |
| `enable_secret` | stringa | | password di enable (se `enable` chiede «Password:») |

Risposta: Stato + **`toast`** con l'esito.

#### `POST /api/device/users`

Elenca gli utenti locali di un router o switch. Body: `{"name"}`. Le password **non escono mai**: per ogni
utente si dice solo come è salvata.

Risposta: Stato + **`users`**: `[{"username": "gabbiano", "privilege": 15, "storage": "reversibile"}]`.
`storage` è `"chiaro"` (`password 0`), `"reversibile"` (`password 7`: nascosta ma decifrabile) o `"cifrata"`
(`secret`).

#### `POST /api/device/users/remove`

Cancella un utente locale (`no username …`, poi `write memory`).

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router o switch |
| `username` | stringa | obbl. | l'utente da cancellare |

Rifiuta (400) un utente che non esiste e quello con cui il server entra nella console (salvato da
`device/harden` o `device/login`): toglierlo chiuderebbe fuori il gioco. Risposta: Stato + **`users`** aggiornato.

#### `POST /api/audit`

Controlla la configurazione di sicurezza di router e switch (non dei PC). Le password non escono mai dal
dispositivo: si guarda solo come sono salvate.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | | un dispositivo; vuoto = tutti |

Risposta: Stato + **`audit`**:

```json
{ "secure": false, "counts": {"high": 1, "medium": 1, "low": 2},
  "findings": [ {"device": "R1", "code": "NO_ENABLE_SECRET", "severity": "high",
                 "message": "Manca la password di enable (enable secret): chiunque arrivi alla console diventa amministratore."} ] }
```

Codici possibili: `NO_ENABLE_SECRET`, `ENABLE_SECRET_REVERSIBLE`, `ENABLE_SECRET_WEAK_ALGO`,
`ENABLE_PASSWORD_PRESENT`, `USER_CREDENTIAL_REVERSIBLE`, `USER_CREDENTIAL_WEAK_ALGO`, `NO_LOCAL_USERS`,
`NO_SERVICE_PASSWORD_ENCRYPTION`, `NO_BANNER_MOTD`. `message` è in italiano; per le falle sugli utenti contiene
anche il nome dell'utente. L'audit legge la configurazione del modello (`audit()` in `backend/rete/console.py`).

#### `POST /api/device/power`

Spegne o accende un dispositivo: simula un guasto o un attacco per vedere se la rete regge.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | dispositivo |
| `on` | bool | obbl. | `false` spegne, `true` riaccende |

Alla riaccensione un router o switch riparte con la sua configurazione (nel gioco resta sempre). Lo Stato
mostra `power`.

#### `POST /api/router/hsrp`

Configura un gruppo HSRP: due router sulla stessa LAN condividono un IP virtuale che i PC usano come
gateway; se il router attivo si spegne, l'altro prende il suo posto.

| Campo | Tipo | | Significato |
|---|---|---|---|
| `name` | stringa | obbl. | router |
| `port` | stringa | obbl. | porta sulla LAN |
| `group` | intero | obbl. | 0–4095, uguale sui due router |
| `virtual_ip` | stringa | obbl. | IP libero della LAN, uguale sui due router: diventa il gateway dei PC |
| `priority` | intero | | 0–255 (default 100): il più alto è il router attivo |
| `preempt` | bool | | `true` (default): quando torna, il router con priorità più alta riprende il comando |

Console: `standby version 2`, `standby <gruppo> ip|priority|preempt`. Nella rete vera l'IP virtuale sta sul router attivo (priorità più alta tra quelli accesi). Controllo: `cli` con `show standby`.

#### Firewall, servizi, scansione, sniffer e limiti

Il **firewall** (`kind: "firewall"`, si aggiunge con `device/add` e `kind: "firewall"`) ha zone con livelli di
sicurezza; nella rete vera è un namespace con `nftables` senza stato. Nella topologia ha in più **`fw`**:
`{"zones": [{"port", "nameif", "level"}], "acls": {nome: [righe]}, "groups": {zona: ACL}, "routes": [...]}`.
Un **cloud** (`kind: "cloud"` in `device/add`) è un server con il ruolo `"cloud"` in `topology.roles`: un servizio cloud fittizio, che si usa come un server.

`POST /api/firewall/zone` — `{"name", "port", "zone", "level"?, "ip", "mask"?}` oppure `{"name", "port", "clear":
true}`. La porta diventa la zona `zone` (una parola minuscola) con livello di sicurezza `level` 0-100 (default
inside 100, dmz 50, outside 0). Le regole già salvate vengono ricompilate.

`POST /api/firewall/rules` — `{"name", "rules": [...]}`, **sostituisce** le regole. Regola: `{"from", "to",
"action": "permit"|"deny", "protocol"?: ip|tcp|udp|icmp, "port"?, "dest"?, "source"?}`; `from`/`to` sono zone,
`dest`/`source` vuoti = tutta la zona, oppure `any`, un IP, una rete `a.b.c.d/nn`. Le regole sono le eccezioni
al comportamento di base (una zona apre connessioni verso le zone con livello più basso, mai il contrario) e le
risposte tornano da sole: `backend/firewall.py` le compila in un'access-list `PNA_<zona>` per zona (vedi
ARCHITETTURA.md). Risposta: Stato + **`acl`** `{zona: righe}`.

`POST /api/firewall/route` — `{"name", "network", "mask", "via", "remove"?}`: rotta statica del firewall
(`route <zona> <rete> <maschera> <via>`; la zona è quella della porta nella cui rete sta `via`). Una zona
comprende anche le reti delle sue rotte: con il firewall tra due router, «tutta la zona» vale anche per le LAN
dietro il router di quel lato. Le rotte sono in `topology.devices[].fw.routes` `[{"zone", "net", "mask", "via"}]`.

`POST /api/server/dns` — `{"name", "on"?, "add"?: [{"name", "ip"}], "remove"?: [nomi]}`: il servizio DNS di un
server e i suoi record A (nella rete vera: `dnsmasq` sul server). I record sono anche nello Stato, in **`dns`**
`{server: {"on", "records": {nome: ip}}}`. Risposta: Stato + **`dns`** del server.

`POST /api/server/services` — `{"name"}` → Stato + **`services`**: `{"http", "https", "ftp", "smtp", "pop3": bool,
"ftp_users", "domain"}` (porte 80, 443, 21, 25, 110). `POST /api/server/service` — `{"name", "service":
http|https|ftp|email, "on": bool, "domain"?}`. SSH non è un servizio dei server: è dei router (`device/harden`).

`POST /api/router/ratelimit` — `{"name", "port"?, "kbps"?}`. Con `port` e `kbps` > 0 mette un limite in uscita
(traffic shaping, `shape average`); `kbps: 0` lo toglie; solo `name` legge. Risposta: Stato + **`limits`**
`{porta: kbps}`. Nella rete vera il limite rallenta davvero il traffico (`tc tbf` sulla porta del router).

`POST /api/scan` (lavoro) — `{"from", "to", "ports"?}`: da un PC/server prova ad aprire una connessione TCP
su ogni porta (default 21, 22, 23, 25, 80, 110, 443; al massimo 10). Risultato del lavoro: **`scan`**
`{"from", "target", "ip", "ports": [{"port", "service", "state"}]}`, `state` = `aperta` (risponde), `chiusa`
(l'host rifiuta), `filtrata` (nessuna risposta: firewall o ACL). Una porta filtrata costa ~3 s.

`POST /api/sniff` (lavoro) — `{"name", "traffic"?: [{"from", "cmd"}]}`: fa partire il traffico di
fondo del livello in corso (campo `traffico` dei livelli) o della challenge in corso (campo `traffico` delle
missioni dell'Accademia) più quello chiesto (`ping IP`, `telnet IP porta`,
`web http://IP`) e registra i pacchetti che passano dal dispositivo (nella rete vera con `tcpdump`). Senza traffico di fondo ogni host fa ping
al suo gateway. Risultato: **`capture`** `{"device", "frames": [{"n", "src", "src_dev", "dst", "dst_dev",
"proto", "app", "port", "in", "out", "status"}]}`. Dura circa 5 secondi.

#### Cosa (ancora) non si può fare via API

- **Firewall sui PC e sui server**: i firewall sono dispositivi a sé, non c'è un firewall personale sugli host.
- **Wi-Fi**: il gioco ha solo cavi.
- **VPN**: non c'è un tunnel vero; nel gioco una VPN si rappresenta con la sottorete dove arrivano i dipendenti.
- **Port-security**: la configurazione si registra, ma la rete vera non blocca i dispositivi in più sulla porta.

---

### Gioco: Accademia

Le missioni sono in `frontend/demo/gioco/accademia.yaml`; i controlli che usano in [`missions.py`](../missions.py).

#### `POST /api/academy/check`

Lavoro lungo. Controlla i passi della missione in corso sulla rete reale; se sono tutti fatti esegue il ping
vero dell'ultimo passo. Body: `{}`.

Prima i controlli sulla fotografia e i passi interattivi (domanda, osserva), poi il traffico vero: i piani di ping
e le verifiche esatte dei passi `verifica` (le stesse dei livelli: `ping`, `attraversa`, `bloccato`…, vedi
[ARCHITETTURA.md](ARCHITETTURA.md#le-verifiche)).

`job.result`: `{"ok": true, "win": true, "message": "Missione «…» completata!"}` oppure
`{"ok": false, "message": "Passo 3 non ancora fatto: …"}`. Quando la missione è completata `academy.current` passa
alla successiva; **se quella ha una rete di partenza (`rete:` nel YAML), il lavoro la apre subito in Packet
Tracer** (sostituisce il tavolo) e il messaggio lo dice.

#### `POST /api/academy/reset`

Lavoro lungo. **Cancella tutti i dispositivi dal tavolo** e riporta l'Accademia alla
missione 1 (se la missione 1 ha una rete di partenza, la apre). Il punteggio resta. Body: `{}`.

#### `POST /api/academy/network`

Lavoro lungo. Prepara il tavolo di partenza della missione in corso: carica la sua topologia (`gioco/reti/*.yaml`), o
svuota il tavolo (`rete: vuota`, o nessuna rete); azzera i suoi passi interattivi e la segna come preparata
(`needs_setup` diventa `false`). **Sostituisce la rete che c'è sul tavolo.** Body: `{"start": true}` per la prima
preparazione (la chiede il client quando vede `needs_setup`), `{}` per «Ricomincia la challenge»: cambiano solo
il titolo del lavoro e il messaggio.

#### `POST /api/academy/answer`

Gioco. Risponde alla domanda di un passo `domanda` della missione in corso. Body: `{"step": i, "choice": j}`
(indici da 0). Risposta: Stato + `{"answer": {"correct": bool, "message": "…"}}`: la risposta giusta completa il
passo e il messaggio è la spiegazione; quella sbagliata no, e si può riprovare. Non tocca la rete.

#### `POST /api/academy/observe`

Azione. Segue passo per passo il ping di un passo `osserva` (`da` → `a`, scritti nel YAML), come
`/api/trace`. Body: `{"step": i}`. Risposta: Stato + `{"trace": {…come /api/trace…}, "explanation": "…"}`. Se il
ping arriva il passo è fatto. Dura 10–30 secondi; 400 se sul tavolo manca uno dei due dispositivi.

#### `POST /api/collaudo/accademia`

**Esiste solo se il server è avviato con `PNA_COLLAUDO=1`** (altrimenti 404). Cheat per il debug: segna come
completate esattamente le missioni indicate, e tutte le altre tornano da fare. Niente controlli, niente ping,
niente punti; la rete non viene toccata. Body: `{"done": ["lan", "server"]}` (lista anche vuota). Errore
400 se un id non esiste. Risposta: Stato. Lo usa il pannello 🐞 del client (vedi
[frontend/demo/README.md](../../frontend/demo/README.md#collaudo-cheat-per-il-debug)).

---

### Gioco: Pronto Soccorso

I livelli sono in `frontend/demo/gioco/pronto-soccorso.yaml` (motore in [`rescue.py`](../rescue.py)); i punti in
`impostazioni.yaml`. Con i valori predefiniti: `fault_points` (100) per guasto riparato, meno
`hint_cost` (25) per ogni indizio su quel guasto; 0 se il giocatore si è arreso; +50 alla fine se nel livello
non sono stati usati indizi.

#### `POST /api/rescue/start`

Lavoro lungo (circa un minuto). **Sostituisce la rete che c'è sul tavolo.**

| Campo | Tipo | | Significato |
|---|---|---|---|
| `level` | intero | obbl. | numero del livello (`rescue.levels[].n`) |

Passi, visibili in `job.steps`: svuota il tavolo → costruisce la rete del livello → aspetta gli indirizzi DHCP →
controlla con ping veri che la rete funzioni → inietta i guasti → controlla con ping veri che ogni guasto rompa
davvero qualcosa. Se uno di questi controlli fallisce il livello non parte: `rescue.current.status` diventa
`error` e `message` spiega perché (di solito basta riprovare).

Alla fine `rescue.current.status` è `playing` e il ticket è in `rescue.current.ticket`.

#### `POST /api/rescue/check`

Lavoro lungo. Per ogni guasto ancora aperto rifà le verifiche che lo dimostrano: ping, e nei livelli di
sicurezza anche pagine web che devono o non devono aprirsi, traffico che deve essere bloccato, audit, resistenza
allo spegnimento di un dispositivo. Se ora passano tutte, il guasto è riparato, **qualunque sia stata la
riparazione**, a meno che violi il ticket: per esempio se il ticket dice che il server deve avere un certo IP,
dare quell'IP a un altro dispositivo non vale, e bloccare tutto il traffico invece di quello vietato viene
segnalato come «hai bloccato anche traffico che deve passare». Quando tutti i guasti sono riparati fa un
controllo finale su tutta la rete e chiude il livello. Body: `{}`.

`job.result.message` riassume: guasti appena riparati, sintomi ancora presenti (descritti come li vede
l'utente, mai come causa), oppure il livello completato con i punti.

#### `POST /api/rescue/hint`

Sblocca il prossimo indizio del primo guasto aperto (massimo 3 per guasto). Body: `{}`.
Risposta: Stato + **`toast`**: il testo dell'indizio. Errore 400 se non c'è un livello in corso, se non ci sono
guasti aperti o se gli indizi di quel guasto sono finiti.

#### `POST /api/rescue/surrender`

Rivela cosa era rotto e la soluzione del primo guasto aperto; quel guasto varrà 0 punti, ma va comunque
riparato e verificato per chiudere il livello. Body: `{}`.
Risposta: Stato + **`toast`**: la soluzione.

### Gioco: Campagna

I giorni, i personaggi e gli aiuti sono in `frontend/demo/gioco/campagna.yaml` (motore in
[`campagna.py`](../campagna.py)). Non c'è un endpoint per chiudere un giorno: si chiude da solo quando
`academy/check` o `rescue/check` completano la sua ultima attività (campo `day_done` della risposta).

#### `POST /api/campaign/help`

Compra l'aiuto di un collega del giorno in corso.

| Campo | Tipo | Note |
|---|---|---|
| `help` | stringa, obbl. | id dell'aiuto (`campaign.current.helps[].id`) |

Toglie il costo dal saldo, esegue le azioni del collega sulla rete (aggiungere e cablare dispositivi,
configurare una porta, righe della console…) e restituisce la risposta. Un aiuto già comprato si rilegge gratis.
Se le azioni falliscono il costo viene restituito (le azioni già eseguite restano).
Risposta: Stato + **`toast`**: la risposta del collega; **`dialog`**: la stessa come battuta (`[{who, name,
role, icon, text}]`). Errore 400 se l'aiuto non esiste oggi, se il saldo non basta o se l'aiuto vale solo
durante un livello che non si sta giocando. Se la rete è occupata l'aiuto va in coda.

#### `POST /api/campaign/reset`

Riporta la campagna al giorno 1 con il saldo iniziale. Body: `{}`. Non tocca la rete, l'Accademia né il
Pronto Soccorso: i giorni le cui attività risultano già fatte si richiudono alla prossima verifica (per
ripartire davvero da zero c'è anche `academy/reset`).

## Esempi completi

### curl

```bash
# 1. login (salva il cookie in jar.txt). Serve solo se il server ha un codice (PNA_CODE); senza, è innocuo
curl -s -c jar.txt -H 'Content-Type: application/json' \
     -d '{"code":"246810"}' http://127.0.0.1:8787/api/login

# 2. due PC e uno switch, collegati e con un IP
curl -s -b jar.txt -H 'Content-Type: application/json' -d '{"kind":"switch","x":400,"y":200}' http://127.0.0.1:8787/api/device/add
curl -s -b jar.txt -H 'Content-Type: application/json' -d '{"kind":"pc","x":300,"y":350}'     http://127.0.0.1:8787/api/device/add
curl -s -b jar.txt -H 'Content-Type: application/json' -d '{"kind":"pc","x":500,"y":350}'     http://127.0.0.1:8787/api/device/add
curl -s -b jar.txt -H 'Content-Type: application/json' -d '{"a":"PC1","b":"SW1"}' http://127.0.0.1:8787/api/link
curl -s -b jar.txt -H 'Content-Type: application/json' -d '{"a":"PC2","b":"SW1"}' http://127.0.0.1:8787/api/link
curl -s -b jar.txt -H 'Content-Type: application/json' \
     -d '{"name":"PC1","ip":"192.168.10.11","mask":"24"}' http://127.0.0.1:8787/api/host/ip
curl -s -b jar.txt -H 'Content-Type: application/json' \
     -d '{"name":"PC2","ip":"192.168.10.12","mask":"24"}' http://127.0.0.1:8787/api/host/ip

# 3. ping vero: stampa solo l'esito
curl -s -b jar.txt -H 'Content-Type: application/json' \
     -d '{"from":"PC1","ip":"192.168.10.12"}' http://127.0.0.1:8787/api/ping | python3 -c 'import json,sys; print(json.load(sys.stdin)["ping"])'
```

### JavaScript (da una pagina servita dallo stesso server)

```js
// Ogni scrittura: POST con JSON. Restituisce il JSON oppure lancia un errore con il messaggio per il giocatore.
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok || !data.ok) throw Object.assign(new Error(data.error), { status: r.status });
  return data;
}

// Avvia un lavoro lungo e aspetta il risultato, mostrando i passi.
async function runJob(path, body, onStep = console.log) {
  const { job } = await api(path, body);
  for (;;) {
    await new Promise((ok) => setTimeout(ok, 1000));
    const s = await api("/api/state");
    if (s.job.id !== job.id) continue;
    onStep(s.job.steps.at(-1));
    if (s.job.done) return s.job.result;
  }
}

await api("/api/login", { code: "246810" });       // solo se c'è un codice; senza, risponde comunque ok
const result = await runJob("/api/rescue/start", { level: 1 });
console.log(result.message);                       // "Livello 1 pronto: leggi il ticket!"
console.log((await api("/api/state")).rescue.current.ticket);
```

Un client servito da **un altro dominio** non può usare il cookie (è `SameSite=Strict`): conviene servirlo
dallo stesso server, indicandone la cartella con `PNA_FRONTEND` (vedi ARCHITETTURA.md).
