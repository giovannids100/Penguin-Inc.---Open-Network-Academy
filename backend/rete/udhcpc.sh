#!/bin/sh
# Script per «busybox udhcpc»: non configura niente, stampa l'indirizzo ricevuto. Lo applica kernel.py
# passando dal modello, così la rete vera e quella del gioco restano uguali.
if [ "$1" = "bound" ] || [ "$1" = "renew" ]; then
    echo "LEASE|$ip|$subnet|$router|$dns"
fi
