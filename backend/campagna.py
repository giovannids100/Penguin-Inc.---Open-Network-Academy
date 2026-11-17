"""Campagna: la storia a giorni che lega missioni dell'Accademia e livelli del Pronto Soccorso.

Una campagna è una sequenza di giorni. Ogni giorno:
  - chiede di completare alcune missioni dell'Accademia e/o alcuni livelli del Pronto Soccorso;
  - ha i dialoghi dei personaggi a inizio giornata («prima») e a fine giornata («dopo»);
  - a fine giornata dà una ricompensa in valuta di gioco (in Penguins Inc. i buoni caffè);
  - offre aiuti dei colleghi a pagamento: una risposta (testo) ed eventualmente azioni vere sulla rete
    vera (il collega «mette le mani» sul tavolo al posto del giocatore).

Il giorno corrente è il primo non ancora completato; un giorno si completa quando tutte le sue attività
sono fatte, anche se fatte prima (per esempio da chi aveva già giocato l'Accademia senza campagna).

Questo file definisce solo i dati e le regole: niente testi (stanno in campagna.yaml del pacchetto di
gioco, letto da contenuti.py) e niente chiamate alla rete (le esegue server.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Personaggio:
    id: str
    nome: str
    ruolo: str = ""
    icona: str = ""


@dataclass
class Battuta:
    """Una riga di dialogo: chi parla (id di un personaggio) e cosa dice."""
    chi: str
    testo: str


@dataclass
class Aiuto:
    """L'aiuto di un collega, comprato con la valuta di gioco.

    azioni: operazioni sulla rete, nello stesso formato delle iniezioni dei guasti (vedi esegui_azioni in
    server.py): ("cli", dispositivo, righe) | ("hostip", argomenti) | ("add", tipo, nome, x, y) |
    ("link", a, b) | ("portip", router, verso, ip, maschera). [] = l'aiuto è solo la risposta.
    livello: se indicato, l'aiuto si può chiedere solo mentre si gioca quel livello del Pronto Soccorso.
    """
    id: str
    collega: str
    costo: int
    richiesta: str
    risposta: str
    azioni: list = field(default_factory=list)
    livello: int | None = None


@dataclass
class Giorno:
    n: int
    titolo: str
    missioni: list[str]          # id delle missioni dell'Accademia da completare
    livelli: list[int]           # numeri dei livelli del Pronto Soccorso da completare
    buoni: int                   # ricompensa a fine giornata
    prima: list[Battuta] = field(default_factory=list)
    dopo: list[Battuta] = field(default_factory=list)
    aiuti: list[Aiuto] = field(default_factory=list)

    def fatto(self, missioni_fatte, livelli_vinti) -> bool:
        """Tutte le attività del giorno sono completate?"""
        return all(m in missioni_fatte for m in self.missioni) and all(n in livelli_vinti for n in self.livelli)

    def aiuto(self, aid: str) -> Aiuto | None:
        return next((a for a in self.aiuti if a.id == aid), None)


@dataclass
class Campagna:
    valuta: dict                 # {"nome", "singolare", "icona"}
    buoni_iniziali: int
    personaggi: dict[str, Personaggio]
    giorni: list[Giorno]
    finale: list[Battuta] = field(default_factory=list)

    def corrente(self, fatti: list[int]) -> Giorno | None:
        """Il giorno in corso: il primo non ancora completato (None = campagna finita o vuota)."""
        return next((g for g in self.giorni if g.n not in fatti), None)

    def valuta_per(self, quanti: int) -> str:
        """«1 buono caffè», «3 buoni caffè»."""
        return f"{quanti} {self.valuta['singolare'] if quanti == 1 else self.valuta['nome']}"


CAMPAGNA_VUOTA = Campagna({"nome": "buoni", "singolare": "buono", "icona": "🎟️"}, 0, {}, [])
