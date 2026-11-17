# Penguin Inc.: avvio e demo

Guida di una pagina per far partire il gioco e mostrarlo.

## 10 minuti prima

- [ ] **Docker** installato e acceso (`docker info` risponde). Su macOS e Windows: Docker Desktop.
- [ ] Connessione a Internet al **primo** avvio: si costruisce l'immagine (Ubuntu, Mininet, Python). Dopo non serve.
- [ ] Fai una prova completa (avvio + una challenge) e poi riparti da zero: `docker compose down -v`.

## Avvio

**Doppio clic su `Avvia Penguin Inc.command`** (in questa cartella; su Linux `bash "Avvia Penguin Inc.command"`).
Si apre una finestra del Terminale che:

1. controlla che Docker sia installato e acceso;
2. costruisce l'immagine (solo la prima volta) e avvia il gioco con `docker compose up -d`;
3. aspetta che la rete sia pronta e apre il browser sul gioco: in locale **non serve nessun codice**.

Il gioco resta acceso anche chiudendo la finestra. Per fermarlo: `bash "Avvia Penguin Inc.command" ferma`
(oppure `docker compose down`). Se il gioco è già acceso, un secondo doppio clic apre solo il browser.

- macOS dice che il file viene da uno «sviluppatore non identificato» (succede se la cartella è stata scaricata
  come ZIP da GitHub): clic destro sul file → **Apri** → **Apri**. Una volta sola.
- Senza il lanciatore: `docker compose up -d --build`, poi apri http://127.0.0.1:8787.

## Copione della demo (6–7 minuti)

Il punto da far vedere è che ogni azione nel gioco succede davvero su una rete vera: ping, firewall e sniffer
sono traffico vero tra macchine Linux create con Mininet. Prima della demo premi **Nuova partita** (in alto nell'ufficio).

1. **L'idea (30 s).** «Sei un pinguino al primo giorno di lavoro a Penguin Inc, un'azienda di reti e
   sicurezza. Ogni giorno un cliente nuovo; il lavoro si fa al computer, su una rete vera.»
2. **Giorno 1 in ufficio (1 min).** Nella schermata iniziale scrivi il nome del pinguino (compare nei
   dialoghi). Jody, collega senior, si presenta e annuncia le tre challenge del giorno (Invio o clic per andare
   avanti).
3. **Challenge 1.1, la prima LAN (2 min).** Clicca la **scrivania** con il «!»: si apre il monitor: la
   descrizione della challenge dice quali dispositivi usare e come collegarli, il pulsante **Manuale** ha la
   teoria, e i pop-up di Jody spiegano cosa fare passo per passo. Due PC, uno switch, cavi, due IP a scelta
   nella stessa rete (il gioco non impone indirizzi): la rete vera si aggiorna subito e i passi si spuntano da soli (prova a dare a PC2 lo stesso IP di PC1: il
   gioco se ne accorge). Torna in ufficio e chiedi un **consiglio a Jody** (oggi è gratis; dal giorno 2 si paga
   con un caffè della macchinetta). **Guarda il viaggio del ping** (PC1 → SW1 →
   PC2 e ritorno, con l'ARP spiegato), rispondi alla domanda «serve un router?», poi **Verifica**: ping veri nei
   due sensi e coriandoli.
4. **Challenge 1.2 e 1.3 (2 min, o preparate prima).** Il giorno 1 continua sulla stessa rete (pop-up «Ora
   tocca a te!»: da qui niente lista dei passi, solo l'obiettivo, quanti obiettivi sono raggiunti e i consigli di
   Jody). 1.2: accanto alla LAN il giocatore costruisce una rete esterna (uno switch e un server) e la collega
   con un router. 1.3: un secondo reparto con il suo switch e i suoi PC, in una rete diversa, collegato al
   router. Alla fine, in ufficio, Jody fa rapporto e il giorno 2 comincia.
5. **Giorno 2 (facoltativo: parte da una sua rete, `giorno2.yaml`, con i due reparti già funzionanti).** 2.1: un secondo router, la sala server con due
   server e un PC di prova in DHCP, rotte statiche. 2.2: un server DNS e i nomi gestione/posta/dns.azienda.test
   (scheda del server → DNS; campo DNS nei PC). 2.3: un firewall tra i due router, con zone, rotte e regole:
   la porta 22 della gestione solo dall'amministratore, posta e DNS per tutti.
6. **Giorno 3 (parte da `giorno3.yaml`: la rete del cliente Krill & Co., tutta aperta).** 3.1: due firewall
   (Internet | DMZ con il server WEB | rete interna), il sito aperto da Internet solo sulla porta 80. 3.2: la
   sottorete VPN dietro un firewall su R2 (solo i servizi web, non i PC). 3.3: il cloud dietro un altro firewall
   su R2 (la rete interna lo usa, lui non entra).
7. **Giorno 4 (parte da `giorno4.yaml`), DEFCON.** Si apre con l'**allarme** e la war room: il Capo e la Foca;
   l'ufficio resta in allarme tutto il giorno e a ogni challenge il Capo ti convoca nella **war room** (lui a
   capotavola; lì si parla soltanto, si gioca in ufficio). Con lo
   **sniffer** si scoprono tre attacchi: 4.1 una macchina che prova le password del server dei file (si trova
   l'indirizzo e la si scollega), 4.2 un DDoS da una sottorete dove lavorano anche colleghi (bloccarla è
   sbagliato: limite di velocità sul router), 4.3 una reverse shell da PC2 verso Internet sulla porta 4444 (ACL
   che blocca l'attaccante, PC2 resta in rete, rapporto con l'indirizzo). Poi il finale: il Capo ti
   **promuove** analista della sicurezza. In sottofondo *Stumble Around*; dall'attacco delle foche *Face The Facts*
   (🔊/🔇 in basso a destra).

Per stare nei tempi si possono preparare le giornate prima della demo (stessa partita, stesso browser): il gioco
riprende dal giorno a cui eri arrivato.

### Comandi del livello 4 (provati dal vivo)

Nella console di **R1** (Pronto Soccorso, livello 4), tutti insieme:

```
enable
configure terminal
ip access-list extended GESTIONALE
deny tcp 192.168.1.0 0.0.0.255 host 192.168.0.3 eq 80
permit ip any any
exit
interface GigabitEthernet0/1
ip access-group GESTIONALE out
exit
enable secret Ghiacci2026
end
write memory
```

Cosa fanno, in una frase: dalla sala d'attesa (192.168.1.0/24) il web verso il gestionale (192.168.0.3, porta 80)
viene bloccato, tutto il resto passa; e R1 ha finalmente una password di amministratore.

Per far vedere che il gioco non si fa ingannare: invece dei comandi sopra blocca **tutto** il traffico della sala
(`deny ip 192.168.1.0 0.0.0.255 any`): la verifica risponde «hai bloccato anche traffico che deve passare».

## Se qualcosa va storto

| Problema | Cosa fare |
|---|---|
| Il lanciatore dice che Docker è spento | Avvia Docker Desktop (o `sudo systemctl start docker` su Linux) e riprova |
| La build si ferma scaricando i pacchetti | Serve Internet solo la prima volta. Il `docker-compose.yml` usa già la rete dell'host per la build: se non basta, controlla proxy e DNS del computer |
| «La porta 8787 è occupata» | Un altro programma la usa: chiudilo, oppure cambia la porta in `docker-compose.yml` (`127.0.0.1:8790:8787`) |
| L'indicatore «Rete» dice «rete simulata» | Il container non riesce a creare la rete vera (Mininet): guarda `docker compose logs`. Il gioco funziona lo stesso, con le prove calcolate sul modello |
| Un livello «non è partito» | Riprova; se si ripete, `docker compose logs` mostra l'errore |
| Ripartire da zero | **Nuova partita** nell'ufficio o nella schermata iniziale (giornate, buoni e nome). Per azzerare anche il salvataggio del server: `docker compose down -v` |
| Capire cosa è successo | Registro del server: `docker compose logs` |
| C'è un pulsante giallo 🐞 in basso a sinistra | Il server è in modalità collaudo (`PNA_COLLAUDO=1`, cheat per il debug): riavvialo senza la variabile prima della demo |

Avviare un livello o premere «Ricomincia» **sostituisce la rete che c'è sul tavolo**.
