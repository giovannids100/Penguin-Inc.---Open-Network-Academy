# Client demo di Penguin Inc.

Il client web del gioco: la pagina che il giocatore apre nel browser. Parla con il backend solo attraverso
l'API HTTP ([backend/docs/API.md](../../backend/docs/API.md)), quindi si può sostituire con un client diverso
senza toccare il backend.

```
frontend/demo/
├── index.html        lo scheletro della pagina (senza testi: li mette lo script)
├── assets/           stile e risorse grafiche
│   ├── style.css     struttura e regole di base dell'interfaccia
│   ├── storia.css    l'ufficio di Penguin Inc: scena, dialoghi, cartelli
│   ├── monitor.css   l'aspetto «Penguin Inc» del monitor (cornice, finestre, cursori, caratteri in pixel)
│   └── penguin/      pixel art di Penguin Inc: scene, ritratti, sprite, interfaccia
├── scripts/          il codice del client (JavaScript senza build)
│   ├── app.js        il «monitor»: il tavolo (la rete vera), missioni e livelli
│   └── storia.js     l'ufficio di Penguin Inc: movimenti, dialoghi, giornate, buoni caffè
└── gioco/            il pacchetto di gioco: la parte «ludica», tutta in YAML
    ├── impostazioni.yaml    punteggi, dispositivi che si possono aggiungere, tempi, comandi suggeriti
    ├── interfaccia.yaml     tutti i testi dell'interfaccia (pulsanti, aiuti, conferme, avvisi…)
    ├── accademia.yaml       le missioni guidate e i messaggi delle loro verifiche
    ├── pronto-soccorso.yaml i livelli da riparare: rete, ticket, guasti, verifiche, indizi, soluzioni
    ├── campagna.yaml        la campagna servita dall'API (/api/campaign): personaggi, giorni, buoni, aiuti
    ├── storia.yaml          la storyline: personaggi, giornate, dialoghi, aiuti del collega
    └── reti/                le topologie di partenza di challenge e livelli, in YAML (formato in backend/rete/topologia.py)

## La storyline: Penguin Inc

Il gioco si apre con la **schermata iniziale**: titolo e scelta del nome del protagonista (pinguino con la
cravatta blu); il nome compare nei dialoghi (`{nome}` in `storia.yaml`). Poi si entra nell'ufficio di
**Penguin Inc** (pixel art): il protagonista si muove con un clic; **Jody**, collega senior (cravatta blu e
rossa), affida il lavoro del giorno e aiuta in cambio di **buoni caffè** (uno per ogni giornata finita), che
sono l'unica «moneta» del gioco: i punti non si mostrano. Sedersi alla scrivania apre il «monitor»: è
l'interfaccia di sempre, sul tavolo: una rete vera, costruita con Mininet. Nei primi giorni la porta del Capo (orso polare)
è chiusa: lui e la Foca (l'antagonista) compaiono solo nel finale della storia scritta finora, che anticipa la
parte cyber.

I giorni sono fatti di **challenge** (missioni dell'Accademia in `accademia.yaml`), per esempio:

| Challenge | Si parte da | Cosa si fa | Come si verifica |
|---|---|---|---|
| 1.1 La prima LAN (tutorial) | tavolo vuoto | due PC e uno switch, nomi, cavi, indirizzi 10.10.10.0/24 | controlli sul piano, ping nei due sensi, viaggio del ping passo per passo, domanda «serve un router?» |
| 2.1 Una sala server | `reti/giorno2.yaml` (due reparti dietro R1, con una porta libera) | secondo router, due server con IP fisso, un PC in DHCP | i reparti e la sala server si raggiungono |
| 3.1 Una DMZ per il sito | `reti/giorno3.yaml` | due firewall, zone, rotte, regola per la porta 80 di WEB | porta 80 di WEB da Internet e da dentro; Internet e DMZ non entrano nella rete interna |
| 3.2 Lavorare da casa | rete della 3.1 | firewall su R2, switch e PC della VPN | dalla VPN porta 80 di INTRANET e WEB sì, PC interni no; Internet non entra nella VPN |
| 3.3 Servizi nel cloud | rete della 3.2 | firewall su R2, switch e cloud | porta 80 del cloud da dentro e dalla VPN; il cloud non entra |
| 4.1 Tentativi a raffica | `reti/giorno4.yaml` | sniffer vicino a FILE, trovare 192.168.10.13 (PC3) e scollegarlo | domanda, PC3 senza cavo attivo, gli altri raggiungono i server |
| 4.2 Un'ondata di richieste | rete della 4.1 | sniffer vicino a WEB, limite di velocità sulla strada 192.168.20.0/24 → WEB | domanda, `limitato`, i colleghi della filiale aprono ancora il sito |
| 4.3 Una porta sul retro | rete della 4.2 | sniffer su PC2, ACL contro 203.0.113.66 | domande, la porta 4444 verso l'attaccante non passa, PC2 usa ancora la rete |

Il tavolo di partenza di una challenge è vuoto (la 1.1: se sul tavolo c'è qualcosa, il gioco lo toglie
quando ci si siede al computer) o una topologia in `gioco/reti/`, che il gioco carica alla fine della challenge
precedente o con **↺ Ricomincia la challenge**. La 1.1 è un **tutorial** guidato passo per passo; nelle altre si
vedono solo topologia, piano e condizioni da raggiungere. Per un aiuto si va a **parlare con Jody** in ufficio:
tre consigli per challenge, dal più vago al più preciso. Il giorno 1 sono gratis; dal giorno 2 costano un caffè,
che si prende alla **macchinetta** con un buono (🎟️ buoni, ☕ caffè in mano, in alto). Come scrivere una challenge:
[ARCHITETTURA.md](../../backend/docs/ARCHITETTURA.md#aggiungere-una-challenge-missione-di-un-giorno).
Una giornata con `allarme: true` (il giorno 4, DEFCON) si apre con la war room e tiene l'ufficio in allarme
(luci rosse, «DEFCON 1» nell'HUD) finché non è finita; dopo il dialogo `inizio` Jody dice `dopo_allarme`.
Con `war_room: true` con il Capo si parla nella **war room** (`scene/war_room.png`, lui seduto a capotavola,
`sprite/boss_seduto.png`): il protagonista entra, ascolta, e si torna nell'ufficio per giocare; l'apertura della
giornata (`war_room_inizio`) e il primo briefing si dicono lì. Con `briefing: {id challenge: battute}` la porta del Capo è aperta: quando una challenge diventa quella in
corso il Capo convoca il giocatore (battute di `convocazione`, poi la war room) e spiega
cosa succede. Una volta per challenge; sul monitor un avviso rimanda in ufficio, e cliccando il Capo si
risente il briefing della challenge in corso.
Dopo l'ultima giornata c'è il **finale**: il giocatore va nell'ufficio del Capo (`finale`) e riceve la
**promozione** (`promozione`: cartello con il nuovo ruolo, coriandoli, poi le battute di `dopo`).
**Musica** (`musica` in `storia.yaml`, file MP3 in `assets/audio/`): `gameplay` (*Stumble Around*) per quasi
tutto il gioco, `attacco` (*Face the Facts*) da quando attaccano le foche (inizio del giorno 4) fino al finale,
con una dissolvenza tra le due. Parte al primo clic (i browser non la fanno partire da sola); il tasto 🔊/🔇 in
basso a destra la spegne e la scelta resta nel browser. I brani sono di Zane Little, licenza CC BY 4.0: vanno
citati (vedi [`CREDITI.md`](../../CREDITI.md)).
**Titoli di coda** (`crediti` in `storia.yaml`): team e crediti della musica, dopo la promozione e dal pulsante
«Crediti» della schermata iniziale.
Il progresso della storia (nome, giornata, buoni spesi) è salvato nel browser; missioni e livelli sul server.
**Nuova partita** (in alto nell'ufficio, o nella schermata iniziale) riparte dal giorno 1, svuota il tavolo e
fa scegliere di nuovo il nome. Senza `storia.yaml` il
client torna quello di prima, con le schede Accademia e Pronto Soccorso.

`campagna.yaml` è un'altra cosa: la campagna a giorni gestita dal **backend** (giorni, buoni caffè e aiuti
dei colleghi salvati sul server, aiuti che possono cambiare la rete, livelli cyber 5–9), esposta dall'API
(`campaign` nello Stato, `/api/campaign/*`) per altri client. Il client demo oggi usa `storia.yaml`.
```

## Collaudo: cheat per il debug

Avviando il server con **`PNA_COLLAUDO=1`** compare in basso a sinistra un pulsante giallo **🐞** (o **F9**)
con i cheat per provare la storia senza rigiocarla ogni volta:

| Cheat | Cosa fa |
|---|---|
| Vai al giorno 1 / 2 / 3 / 4 / Finale | riparte da quella giornata, con quelle prima già finite (buoni guadagnati compresi) |
| Salta la challenge | segna come fatta solo la challenge in corso; la storia passa alla prossima (che parte dalla rete che c'è, o dalla sua topologia se ne ha una) |
| Completa la giornata | segna come fatto il lavoro di oggi; in ufficio parte subito il rapporto a Jody |
| Buoni caffè −1 / +1 | toglie o regala un buono |
| Salta i dialoghi | tutti i dialoghi vengono saltati (le domande Sì/No restano) |
| Camminata veloce | i personaggi camminano 4 volte più veloci |
| Esc | salta il dialogo in corso |
| Azzera la storia | riparte dal giorno 1 nel browser, senza svuotare il tavolo |

«Vai al giorno», «Salta» e «Completa» segnano le missioni dell'Accademia sul server (`POST /api/collaudo/accademia`,
che esiste solo in collaudo) ma **non costruiscono la rete**: il tavolo resta com'è. Senza la
variabile non c'è né il pulsante né l'endpoint. Il server lo ricorda all'avvio: non usarlo in demo.

```bash
PNA_COLLAUDO=1 docker compose up -d
```

## Modificare il gioco senza toccare il codice

Tutto quello che il giocatore legge, e le regole di punteggio, stanno nei file di `gioco/`. Ogni file inizia
con un commento che spiega i suoi campi.

| Voglio cambiare… | File |
|---|---|
| un testo dell'interfaccia (pulsanti, aiuti «?», conferme, legenda) | `interfaccia.yaml` |
| la storia, la teoria o i passi di una missione; aggiungere una missione | `accademia.yaml` |
| il ticket, gli indizi o la soluzione di un livello; aggiungere un livello | `pronto-soccorso.yaml` |
| i dialoghi, le giornate, i buoni caffè, l'aiuto del collega | `storia.yaml` |
| la campagna del backend (giorni, buoni e aiuti salvati sul server, per altri client) | `campagna.yaml` |
| i messaggi che compaiono durante le verifiche («Missione completata!», «Ancora da sistemare»…) | sezione `messaggi` in fondo a `accademia.yaml` e `pronto-soccorso.yaml` |
| i punti, il costo degli indizi, il bonus (calcolati dal server, ma il client non li mostra) | `impostazioni.yaml` → `punteggio` |
| i dispositivi che si possono aggiungere, il loro nome o la loro icona | `impostazioni.yaml` → `dispositivi` |
| ogni quanto si aggiorna la pagina, i comandi pronti della console | `impostazioni.yaml` → `interfaccia` |

**A gioco acceso:** il server rilegge i file da solo quando li salvi. Per i testi dell'interfaccia basta
ricaricare la pagina; missioni, livelli e messaggi valgono dalla prossima verifica.

**Se sbagli qualcosa** (un rientro, un nome che non esiste), il gioco continua con l'ultima versione buona e
mostra in cima alla pagina un riquadro rosso con il file e il punto esatto dell'errore, per esempio
`accademia.yaml, missione 1 («lan»), passo 1: il controllo «switch_che_non_esiste» non esiste. Disponibili: …`.
Lo stesso messaggio è in `GET /api/health` e nel registro del server. Al primo avvio, invece, un errore
ferma il server con lo stesso messaggio: meglio accorgersene subito che a metà demo.

### Le regole del YAML in breve

- Il **rientro** conta: usa spazi, mai tabulazioni, e allinea le voci dello stesso livello.
- Un testo con `:` seguito da spazio, o che inizia con `{`, `[`, `"` o `#`, va tra virgolette doppie.
- Testi lunghi: `>-` seguito dal testo a capo (le righe vengono unite). Testi su più righe (i ticket): `|-`.
- I segnaposto tra graffe (`{nome}`, `{punti}`) li riempie il gioco: lasciali com'è.
- Nei testi dei pannelli si può usare `<b>…</b>` e `<br>`; negli avvisi e nelle conferme no.

### Cosa richiede comunque il codice

- **Un controllo nuovo per le missioni** (per esempio «c'è un server DHCP»): i passi del YAML scelgono tra i
  controlli che esistono già, elencati in `backend/missions.py` (`CONTROLLI` e `PIANI_PING`). Uno nuovo va
  scritto lì in Python. I livelli invece non ne hanno bisogno: le loro verifiche (ping, web, traffico
  bloccato, audit, resistenza a un guasto) si compongono tutte nel YAML.
- **Un tipo di dispositivo nuovo** (oltre a router, switch, PC e server) e le **icone** disegnate sul tavolo
  (`iconSvg()` in `scripts/app.js`).
- L'aspetto grafico: `assets/style.css` (struttura) e `assets/monitor.css` (colori, caratteri, cornice e
  cursori di Penguin Inc, caricato dopo e solo estetico).

## Usare un altro pacchetto di gioco o un altro client

Il backend sceglie le cartelle con due variabili d'ambiente:

- `PNA_GIOCO`: la cartella dei YAML (default `frontend/demo/gioco`). Un'altra cartella = un altro gioco con lo
  stesso client, per esempio una versione per un'altra scuola. Un file mancante prende i valori predefiniti.
- `PNA_FRONTEND`: la cartella del client servito alla radice `/` (default `frontend/demo`). Deve avere
  `index.html`, `assets/` e `scripts/`.

```bash
PNA_GIOCO=/percorso/mio-gioco PNA_FRONTEND=/percorso/mio-client python3 backend/server.py
```

La cartella `gioco/` **non** viene servita al browser: contiene le soluzioni dei livelli. Il client riceve
solo i testi e le impostazioni dell'interfaccia, da `GET /api/ui`.
