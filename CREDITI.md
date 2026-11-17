# Crediti

**Penguin Inc. — Open Network Academy** è stato sviluppato dal team **Los Penguinos** per The Big Hack 2026:

- [Pasquale Criscuolo](https://github.com/Paki64)
- [Giovanni Di Stazio](https://github.com/giovannids100)
- [Francesca Morra](https://github.com/fra494)

## Musica

I brani usati nel gioco (`frontend/demo/assets/audio/`) sono di **Zane Little**, dall'album
[*A Bag of Chips [Vol. 1]*](https://zanelittle.bandcamp.com/album/a-bag-of-chips-vol-1) (2022), distribuiti con
licenza [Creative Commons Attribuzione 4.0 Internazionale (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/deed.it):

| File | Brano | Dove si sente |
|---|---|---|
| `stumble_around.mp3` | «Stumble Around» (traccia 5) | quasi tutto il gioco |
| `face_the_facts.mp3` | «Face the Facts» (traccia 6) | dall'attacco delle foche (giorno 4) al finale |

La licenza permette l'uso gratuito, anche commerciale, a patto di citare titolo, autore, fonte e licenza: per
questo i crediti compaiono anche **nel gioco** (titoli di coda nel finale e pulsante «Crediti» della schermata
iniziale, sezione `crediti` di `frontend/demo/gioco/storia.yaml`). Non toglierli. Se i file audio vengono
tagliati o modificati, va aggiunto «modificato» accanto al titolo.

## Software

La rete del gioco è costruita con **[Mininet](https://mininet.org/)** (licenza in
[`deploy/MININET-LICENSE`](deploy/MININET-LICENSE)): il container usa i pacchetti di Ubuntu, più una copia di
`mnexec.c` di Mininet 2.3.0 modificata per funzionare senza privilegi ([`deploy/mnexec.c`](deploy/mnexec.c)).
Il resto della rete sono strumenti standard di Linux: `iproute2`, `nftables`, `tc`, `dnsmasq`, `tcpdump`, BusyBox.
Il server usa [Starlette](https://www.starlette.io/), [Uvicorn](https://www.uvicorn.org/) e
[PyYAML](https://pyyaml.org/).
