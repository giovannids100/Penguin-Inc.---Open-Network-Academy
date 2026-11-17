# Penguin Inc. — Open Network Academy: il gioco e la sua rete vera (Mininet) in un solo container.
# Il container non ha privilegi: il server crea un suo user namespace e lì dentro costruisce la rete.
# Avvio: docker compose up --build   →   http://127.0.0.1:8787

# mnexec di Mininet con il montaggio di /sys in sola lettura (vedi deploy/mnexec.c).
FROM ubuntu:24.04 AS mnexec
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev curl ca-certificates
COPY deploy/mnexec.c /src/mnexec.c
RUN gcc -O2 -Wall -DVERSION='"2.3.0-pna"' /src/mnexec.c -o /mnexec

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        mininet iproute2 net-tools bridge-utils ethtool \
        iputils-ping traceroute nftables tcpdump dnsmasq-base busybox \
        python3 \
    && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-yaml \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages "starlette==0.41.3" "uvicorn==0.32.1"
COPY --from=mnexec /mnexec /usr/bin/mnexec

RUN useradd --create-home --uid 1001 pinguino
WORKDIR /app
COPY --chown=pinguino backend/ backend/
COPY --chown=pinguino frontend/ frontend/
RUN mkdir -p backend/data && chown pinguino backend/data
USER pinguino

ENV PYTHONUNBUFFERED=1 PNA_HOST=0.0.0.0 PNA_PORT=8787 PNA_TMP=/tmp/pna
EXPOSE 8787
CMD ["python3", "backend/server.py"]
