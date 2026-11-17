#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#  Avvia Penguin Inc. — Open Network Academy
#
#  macOS: doppio clic su questo file.      Linux: bash "Avvia Penguin Inc.command"
#
#  1. controlla che Docker sia installato e acceso
#  2. costruisce (la prima volta) e avvia il gioco con docker compose
#  3. aspetta che la rete sia pronta e apre il browser
#  Il gioco resta acceso anche chiudendo questa finestra: per fermarlo,
#  bash "Avvia Penguin Inc.command" ferma   (oppure: docker compose down)
#
#  Variabili:
#    PNA_BROWSER=0    non aprire il browser
#    PNA_COLLAUDO=1   cheat per il debug e controlli all'avvio dei livelli (sviluppo)
# ──────────────────────────────────────────────────────────────────────────────

set -u
cd "$(dirname "$0")" || exit 1
# Il doppio clic non sempre carica il PATH completo: aggiungiamo dove di solito sta docker.
export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

URL="http://127.0.0.1:8787"
INTERATTIVO=""; [ -t 0 ] && INTERATTIVO=1

if [ -t 1 ]; then B=$'\e[1m'; V=$'\e[32m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B=""; V=""; R=""; C=""; N=""; fi
passo()   { printf '\n%s▸ %s%s\n' "$B" "$1" "$N"; }
ok()      { printf '  %s✔%s %s\n' "$V" "$N" "$1"; }
errore()  { printf '  %s✖ %s%s\n' "$R" "$1" "$N"; }
esci() {  # con un terminale aspetta Invio, così la finestra non sparisce
  if [ -n "$INTERATTIVO" ]; then printf '\n'; read -r -p "Premi Invio per chiudere questa finestra… " _; fi
  exit "$1"
}
apri() {
  [ "${PNA_BROWSER:-1}" = "0" ] && return
  if command -v open >/dev/null 2>&1; then open "$1"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 & fi
}
salute() { curl -s -m 2 "$URL/api/health" 2>/dev/null; }

printf '%s\n  🐧  Penguin Inc.%s — Open Network Academy: la rete la costruisci qui, ma vive davvero (Mininet)\n' "$B$C" "$N"

passo "Controllo Docker"
if ! command -v docker >/dev/null 2>&1; then
  errore "Manca Docker: installa Docker Desktop (macOS, Windows) o Docker Engine (Linux), poi riapri questo file."
  esci 1
fi
if ! docker info >/dev/null 2>&1; then
  errore "Docker è installato ma spento (o il tuo utente non può usarlo): avvialo e riapri questo file."
  esci 1
fi
ok "$(docker --version)"

if [ "${1:-}" = "ferma" ]; then
  passo "Fermo il gioco"
  docker compose down && ok "Gioco fermato (la partita resta salvata)."
  esci 0
fi

if salute | grep -q '"penguin-inc"'; then
  ok "Il gioco è già acceso su $URL"
  apri "$URL"
  esci 0
fi

passo "Avvio il gioco"
printf '    (la prima volta costruisce l'"'"'immagine: qualche minuto)\n'
if ! docker compose up -d --build; then
  errore "docker compose non è riuscito ad avviare il gioco (vedi sopra)."
  esci 1
fi
PRONTO=""
for i in $(seq 1 90); do
  if salute | grep -q '"penguin-inc"'; then PRONTO=1; break; fi
  [ $((i % 5)) -eq 0 ] && printf '    …%ss\n' "$i"
  sleep 1
done
if [ -z "$PRONTO" ]; then
  errore "Il gioco non risponde. Ultime righe del registro:"
  docker compose logs --tail 15 | sed 's/^/      /'
  esci 1
fi
ok "Gioco acceso: $(salute | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"

apri "$URL"
printf '\n%s  ┌──────────────────────────────────────────────────────────┐%s\n' "$C" "$N"
printf '%s  │%s  Gioca su:  %s%-45s%s%s│%s\n' "$C" "$N" "$B" "$URL" "$N" "$C" "$N"
printf '%s  └──────────────────────────────────────────────────────────┘%s\n' "$C" "$N"
printf '\n  Il gioco resta acceso anche se chiudi questa finestra.\n'
printf '  Per fermarlo:  bash "Avvia Penguin Inc.command" ferma\n'
esci 0
