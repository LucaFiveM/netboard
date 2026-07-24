FROM python:3.12-slim

# nmap: Netz-Discovery. iproute2: Erkennung der lokalen Netze.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nmap iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Hier landen die Einstellungen. Als Volume einbinden, sonst sind sie
# nach einem Neubau des Containers weg.
RUN mkdir -p /data
VOLUME ["/data"]

ENV NETBOARD_DATA=/data
EXPOSE 8888

# Läuft als root: nmap braucht das, um MAC-Adressen und Hersteller zu lesen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8888"]
