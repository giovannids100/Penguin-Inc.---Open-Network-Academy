# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Regole di lavoro (obbligatorie)

- **Lingua:** parla sempre in italiano con il team (risposte, commit message, commenti, documentazione).
- **Branch:** si lavora su `main`. Prima di iniziare fai `git pull --rebase`.

## Contesto

**Penguin Inc. — Open Network Academy** è un serious game educativo sul networking e la cybersicurezza per
giocatori alle prime armi, nato a **The Big Hack 2026** (Codemotion × Futuro Remoto, Napoli) per la challenge
C4 Consorzio Clara. Si gioca su una **rete vera** costruita con **Mininet**: ogni dispositivo è un namespace
Linux, i ping e le connessioni sono traffico vero.

## Cos'è

```
Browser ──HTTP──▶ server.py ──▶ rete/motore.py ──▶ modello della rete ──▶ Mininet (namespace Linux)
```

- `backend/server.py`: HTTP (Starlette), endpoint, lavori lunghi, punteggio, motore dei livelli.
- `backend/rete/`: il motore di rete.
  - `modello.py`: la rete come dati (dispositivi, porte, cavi, configurazioni), tabelle di routing (statiche e
    OSPF), domini L2 e VLAN, la fotografia per il gioco. È la fonte di verità.
  - `console.py`: la console di router, switch e firewall (sintassi da riga di comando standard: `interface`,
    `ip address`, `ip route`, `access-list`, `router ospf`, `nameif`…), con login, `show` e `running-config`.
  - `percorso.py`: la traccia di un pacchetto passo per passo, con il motivo di ogni scarto.
  - `kernel.py`: realizza il modello con Mininet (veth, bridge con VLAN, `nftables`, `tc`, `dnsmasq`, servizi).
  - `strumenti.py`: i programmi che girano dentro i dispositivi (servizi dei server, web, sonda TCP, DNS).
  - `motore.py`: la facciata che usa `server.py`; sceglie tra rete vera e rete **simulata** (senza Mininet le
    prove si calcolano sul modello).
  - `topologia.py`: le reti di partenza YAML (`frontend/demo/gioco/reti/*.yaml`) → modello.
- `missions.py`, `rescue.py`, `campagna.py`, `firewall.py`: motore di gioco (controlli, verifiche, giorni e
  aiuti, compilazione delle regole del firewall), niente testi. `contenuti.py` carica il pacchetto di gioco.
- La storia è **Penguin Inc**, in due forme da riunire: `storia.yaml` + `scripts/storia.js` è quella che il
  client demo mostra (ufficio in pixel art, Jody, giornate 1–4, progresso nel browser); `campagna.yaml` è la
  campagna del backend, esposta dall'API (`campaign` nello Stato, `/api/campaign/*`) ma non ancora usata dal client.
- `PNA_COLLAUDO=1` = modalità sviluppo: cheat di debug nel client e controlli all'avvio dei livelli.
- `frontend/demo/`: client (`index.html`, `assets/`, `scripts/app.js`); `frontend/demo/gioco/*.yaml`: **tutta la
  parte ludica** (testi, missioni, livelli, punteggi, dispositivi, reti di partenza). I contenuti si cambiano lì,
  non nel codice.
- `Dockerfile`, `docker-compose.yml`, `deploy/`: il deploy (container senza privilegi, profilo seccomp,
  `mnexec` di Mininet modificato). `Avvia Penguin Inc.command`: lanciatore da doppio clic.

Documentazione da leggere prima di toccare il backend: [`backend/docs/API.md`](backend/docs/API.md) (contratto
HTTP) e [`backend/docs/ARCHITETTURA.md`](backend/docs/ARCHITETTURA.md) (come funziona, come aggiungere endpoint,
missioni, livelli e reti). Demo: [`DEMO.md`](DEMO.md). Tienile aggiornate quando cambi il codice.

## Avvio e prove

- Avvio: `docker compose up -d --build` e http://127.0.0.1:8787 (oppure doppio clic su `Avvia Penguin Inc.command`).
- Senza Docker: `python3 backend/server.py`; senza Mininet la rete è simulata (`PNA_RETE=simulata` la forza).
- Senza `PNA_CODE` non c'è login e il server risponde solo a richieste per `127.0.0.1`/`localhost`; con
  `PNA_CODE` serve il login (necessario per giocare da altri dispositivi). `PNA_HOST` è l'indirizzo di ascolto.
- La partita è in `backend/data/state.json` e la rete in `backend/data/rete.json` (nel container: volume
  `partita`; `docker compose down -v` riparte da zero).
- Non esistono test automatici: le prove si fanno con curl sugli endpoint o nel browser. Il collaudo
  (`PNA_COLLAUDO=1`) all'avvio di ogni livello controlla che la rete di partenza funzioni e che ogni guasto si veda.
- Debug della rete vera: `/tmp/pna/` nel container (script, regole nft, log dei demoni, `nodi.json` con il PID
  di ogni dispositivo); `docker compose exec gioco nsenter -t 1 -U -n -m --preserve-credentials` entra nel
  namespace del server.

## Cose da non fare

- **Non dare privilegi al container** (`privileged`, capability): il gioco è pensato per girare senza.
- **Niente input del giocatore in una shell:** i comandi della console passano dall'interprete
  (`rete/console.py`), mai a `bash` dentro i dispositivi.
- **Niente contenuti di terzi** nelle missioni e nei livelli: le sfide le scriviamo noi.
- **Non pubblicare il gioco su Internet** senza che il team lo chieda, e mai senza `PNA_CODE`.
