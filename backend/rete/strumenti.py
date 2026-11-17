"""Programmi che girano DENTRO i dispositivi della rete vera (lanciati da kernel.py nel loro namespace).

  servizi NOME svc…      i servizi di un server: http (80), https (443), ftp (21), smtp (25), pop3 (110)
  web URL [DNS]          apre una pagina come un browser: risolve il nome con quel DNS, poi HTTP GET
  porta IP PORTA         prova una connessione TCP: aperta, chiusa (rifiutata) o filtrata (nessuna risposta)
  dns NOME SERVER        risolve un nome (record A) chiedendo a quel server DNS
  tcp IP PORTA           traffico di prova per lo sniffer: apre la connessione, scrive una riga, chiude

Solo libreria standard. Le prove stampano una riga JSON.
"""
from __future__ import annotations

import asyncio
import json
import random
import socket
import struct
import sys
import urllib.parse

PORTE = {"http": 80, "https": 443, "ftp": 21, "smtp": 25, "pop3": 110}


def stampa(x: dict) -> None:
    print(json.dumps(x, ensure_ascii=False), flush=True)


# ───── servizi ─────
def pagina(nome: str) -> bytes:
    corpo = (f"<html><head><title>{nome}</title></head><body><h1>{nome}</h1>"
             f"<p>Pagina servita da {nome}, nella rete di Penguin Inc.</p></body></html>").encode()
    return (b"HTTP/1.0 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
            + str(len(corpo)).encode() + b"\r\nConnection: close\r\n\r\n" + corpo)


async def servizi(nome: str, attivi: list[str]) -> None:
    async def web(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(r.readline(), 5)
            w.write(pagina(nome))
            await w.drain()
        except Exception:
            pass
        w.close()

    def saluto(testo: str):
        async def h(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            try:
                w.write(testo.encode())
                await w.drain()
                await asyncio.wait_for(r.readline(), 10)
            except Exception:
                pass
            w.close()
        return h

    gestori = {"http": web, "https": web, "ftp": saluto(f"220 {nome} FTP pronto\r\n"),
               "smtp": saluto(f"220 {nome} ESMTP pronto\r\n"), "pop3": saluto(f"+OK {nome} POP3 pronto\r\n")}
    server = [await asyncio.start_server(gestori[s], "0.0.0.0", PORTE[s]) for s in attivi if s in gestori]
    await asyncio.gather(*(s.serve_forever() for s in server))


# ───── DNS ─────
def risolvi(nome: str, server: str, timeout: float = 2.0) -> str:
    """Record A di «nome» chiesto a «server» (UDP 53). "" se non c'è risposta o il nome non esiste."""
    qid = random.randint(0, 65535)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(x)]) + x.encode() for x in nome.strip(".").split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        r, _ = s.recvfrom(2048)
    except OSError:
        return ""
    finally:
        s.close()
    if len(r) < 12 or struct.unpack(">H", r[:2])[0] != qid:
        return ""
    risposte = struct.unpack(">H", r[6:8])[0]
    i = 12
    while r[i] != 0:
        i += r[i] + 1
    i += 5
    for _ in range(risposte):
        if r[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while r[i] != 0:
                i += r[i] + 1
            i += 1
        tipo, _cl, _ttl, lung = struct.unpack(">HHIH", r[i:i + 10])
        i += 10
        if tipo == 1 and lung == 4:
            return socket.inet_ntoa(r[i:i + 4])
        i += lung
    return ""


# ───── prove ─────
def porta(ip: str, p: int, timeout: float = 2.5) -> str:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, p))
        return "aperta"
    except ConnectionRefusedError:
        return "chiusa"
    except OSError:
        return "filtrata"
    finally:
        s.close()


def web(url: str, dns: str) -> dict:
    u = urllib.parse.urlparse(url)
    host = u.hostname or ""
    ip = host
    try:
        socket.inet_aton(host)
    except OSError:
        if not dns:
            return {"ok": False, "text": f"{host}: il PC non ha un server DNS per risolvere il nome"}
        ip = risolvi(host, dns)
        if not ip:
            return {"ok": False, "text": f"{host}: il DNS {dns} non risolve il nome"}
    p = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((ip, p), timeout=4) as s:
            s.settimeout(4)
            s.sendall(f"GET {u.path or '/'} HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
            dati = b""
            while chunk := s.recv(4096):
                dati += chunk
    except ConnectionRefusedError:
        return {"ok": False, "text": f"{ip}:{p} rifiuta la connessione: lì non c'è un server web acceso", "ip": ip}
    except OSError:
        return {"ok": False, "text": f"{ip}:{p} non risponde (nessuna strada, o un filtro scarta il traffico)", "ip": ip}
    testo = dati.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
    return {"ok": bool(testo), "text": testo, "ip": ip}


def tcp(ip: str, p: int) -> None:
    try:
        with socket.create_connection((ip, p), timeout=2) as s:
            s.sendall(b"USER prova\r\n")
            s.settimeout(1)
            try:
                s.recv(512)
            except OSError:
                pass
    except OSError:
        pass


def main(a: list[str]) -> None:
    if a[0] == "servizi":
        asyncio.run(servizi(a[1], a[2:]))
    elif a[0] == "web":
        stampa(web(a[1], a[2] if len(a) > 2 else ""))
    elif a[0] == "porta":
        stampa({"ok": True, "stato": porta(a[1], int(a[2]))})
    elif a[0] == "dns":
        ip = risolvi(a[1], a[2])
        stampa({"ok": bool(ip), "ip": ip, "server": a[2]})
    elif a[0] == "tcp":
        tcp(a[1], int(a[2]))
        stampa({"ok": True})


if __name__ == "__main__":
    main(sys.argv[1:])
