# Netboard

Auto-Discovery-Launchpad fürs Heimnetz. Durchsucht dein Netzwerk, findet
erreichbare Geräte samt offenen Web-Ports (Fritzbox, Proxmox, Portainer, NAS,
Home Assistant …) und macht sie per Klick erreichbar.

**Alles wird in der Weboberfläche eingerichtet.** Konfigurationsdateien musst
du nicht anfassen. Der gesamte Stand liegt serverseitig – Laptop und PC sehen
immer dasselbe.

## Installation

```bash
tar -xzf netboard.tar.gz
cd netboard
docker compose up -d --build
```

Dann im Browser: **http://\<ip-von-sv-docker01\>:8888**

Beim ersten Aufruf führt dich ein **kurzer Assistent** in wenigen Schritten
durch die Einrichtung: Netze (schon erkannt und vorausgewählt), Suchtiefe,
Geräte-Aufnahme (alles automatisch oder selbst auswählen), Darstellung
(Thema + Akzentfarbe) und optionaler Login. Zurück/Weiter, Fortschrittspunkte –
und **alles lässt sich später jederzeit ändern**. Wer es eilig hat, klickt sich
mit den Vorgaben in Sekunden durch.

## Dashboards

Ein Dashboard ist eine **gespeicherte Sicht** auf dieselben Scandaten – keine
zweite Kopie. Deshalb kostet ein Wechsel zwischen Dashboards keinen Request:
gefiltert wird im Browser.

### Bedienung: der Reiter ist das Objekt

Alles wird direkt am Reiter gemacht. Kein Dialog, kein „Sichern“ – Änderungen
greifen sofort.

| Was | Wie |
|---|---|
| Umschalten | Reiter anklicken |
| **Startseite festlegen** | Stern am aktiven Reiter anklicken. Ein Klick. |
| **Umbenennen** | Doppelklick auf den Reiter (oder F2). Enter sichert, Esc verwirft. |
| **Verschieben** | Reiter ziehen. Am Handy: `⋯` → Pfeile. |
| **Ansicht wechseln** | Kacheln/Liste rechts in der Leiste. Ein Klick. |
| Anlegen | `+` – legt an und springt direkt ins Benennen. |
| Alles Weitere | `⋯` öffnet ein Popover direkt am Reiter. |

Der Stern zeigt immer, welches Dashboard beim Laden erscheint – auf jedem Gerät.

Im Popover (`⋯`):

| Einstellung | Bedeutung |
|---|---|
| Name | Sofort wirksam, auch am Reiter. |
| Position | Nach links / rechts – die Touch-Variante des Ziehens. |
| Neue Geräte aufnehmen | **An**: alles Gefundene erscheint hier, neue automatisch. **Aus**: nur was du auswählst („Geräte auswählen …“). |
| Nur Erreichbares | Blendet aus, was gerade nicht antwortet. Hält eine Startseite ruhig. |
| Prüfen, solange offen | Aus / 30 s / 1 min / 5 min / 15 min / 1 h. Greift **nur, solange dieses Dashboard offen ist**. |
| Breite | Schmal / Normal / Breit / **Voll**. Voll nutzt den ganzen Bildschirm – sinnvoll fürs Raster auf einem großen Monitor. |
| Symbolform | Abgerundet / Rund / Eckig. |
| Gliederung | Keine / **Nach Gerät** / **Nach Netz** / **Eigene Ordner**. Bei 30+ Kacheln bringt eine Überschrift Ordnung. „Eigene Ordner“ lässt dich Ordner selbst anlegen und Kacheln über das `···`-Menü hineinlegen. |
| Sortierung | Automatisch (Gateway zuerst) / Name / Gerät / Port. Eine selbst gezogene Anordnung sticht immer. |
| Spalten | Auto / 2 / 3 / 4 / 5 / 6 / 8. Auto richtet sich nach der Breite, feste Spalten geben dir das letzte Wort. |
| Vorschau | Zeigt oben im Popover echte Kacheln mit demselben CSS. Was du dort siehst, steht gleich genauso auf der Seite. |
| Duplizieren / Löschen | Das letzte Dashboard bleibt bestehen. |

### Drei Ansichten

Der Umschalter sitzt rechts in der Reiterleiste – ein Klick. Beschriftet auch
im Popover (`⋯`), falls dir die Symbole nicht reichen.

| Ansicht | Sieht aus wie | Wofür |
|---|---|---|
| **Raster** | Großes Symbol, Name darunter, quadratisch | Die Startseite. Wenig Text, viel Wiedererkennung – man klickt aufs Bild. |
| **Karten** | Symbol links, Name + Gerät · Port, Startpfeil rechts | Wenn du sehen willst, *wo* ein Dienst läuft. |
| **Liste** | Dichte Zeilen mit IP, Hersteller, allen Ports | Technisch. Zum Suchen und Nachschauen. |

### Geräte selbst anordnen

**Kachel greifen und ziehen** – fertig. Eine Linie zeigt, wo sie landet.

Die Reihenfolge gilt für **alle Ansichten**: Was du im Raster nach vorn ziehst,
steht auch in den Karten und in der Liste vorn. In der Liste ziehst du Geräte,
ihre Dienste wandern mit.

Am Handy geht Ziehen nicht – dort steht im `⋯` an jeder Kachel **Nach vorn /
Nach hinten**. Gleiche Wirkung, gleiche Reihenfolge.

Neu gefundene Geräte tauchen immer **hinten** auf und stören deine Anordnung
nicht. Zurücksetzen über `⋯` → **Eigene Anordnung aufheben**.

### Kachelstil und Beschriftung

Zusätzlich zur Ansicht bekommt **jedes Dashboard sein eigenes Design** (`⋯`):

| Kachelstil | Wirkung |
|---|---|
| **Rahmen** | Feine Linie um jede Kachel. Ruhig, klar abgegrenzt. |
| **Schatten** | Kein Rahmen, die Kachel schwebt leicht. Weicher, moderner. |
| **Ohne** | Weder Rahmen noch Fläche – nur Symbol und Name, wie ein App-Dock. Das Symbol wird dabei größer, weil es allein trägt. |

| Beschriftung | Wirkung |
|---|---|
| **Name + Gerät** | Voller Kontext: Dienst, Gerät, Port. |
| **Nur Name** | Nur der Dienst. Ruhiger. |
| **Nur Symbol** | Reines Bilderraster. Der Name steht im Tooltip. |

Zusammen mit **Kachelgröße** (Kompakt/Normal/Groß, ändert die Spaltenzahl
spürbar) ergibt das für jedes Dashboard eine eigene Handschrift – vom dichten
Symbolraster bis zur luftigen Kartenwand.

### Wann wird gesucht?

1. **Einmal beim Start** des Containers.
2. **Auf Knopfdruck** über „Neu scannen“.
3. **Im Takt des geöffneten Dashboards**, falls dort ein Wert gesetzt ist.

Schaut niemand hin, wird nicht gesucht. Auch ein Browser-Tab im Hintergrund
pausiert komplett. Das hält die Last unten und den Server ruhig.

## Sicherheit & optionaler Login

Netboard nimmt Passwörter ernst und geht mit jeder Sorte richtig um:

- **Login-Passwort** (optionaler Schutz der Oberfläche): wird nur als
  **scrypt-Hash** gespeichert – nicht umkehrbar, auch nicht durch Netboard.
- **Dienst-Zugänge** (vSphere-Passwort, Proxmox-Token): die muss der Server
  benutzen, um sich dort anzumelden, lassen sich also nicht hashen. Sie liegen
  **verschlüsselt** auf der Platte (Schlüssel in `data/secret.key`, nur für den
  Eigentümer lesbar) – in `config.json` steht nie Klartext.
- **Nichts davon erreicht den Browser.** Config-Antworten und Backups enthalten
  nur Platzhalter (`has_password`) – auch über die Entwicklertools (F12) ist kein
  Geheimnis sichtbar. Der Login-Cookie ist `HttpOnly`, für JavaScript unlesbar.

**Login einschalten** (Einstellungen → Zugang, oder gleich bei der Einrichtung):
Benutzername und Passwort setzen, Haken bei „Login verlangen". Standardmäßig ist
der Login **aus** – im reinen Heimnetz oft unnötig.

> Wichtig: Netboard selbst spricht HTTP. Wer die Oberfläche von außen erreichbar
> macht, sollte sie über HTTPS (Reverse-Proxy) betreiben – sonst reist das
> Passwort im Klartext.

## Geräte

**Ausblenden – zwei Stufen.** Das `···` an jeder Zeile bietet:

- **Von „\<Dashboard\>“ nehmen** – verschwindet nur hier, andere Dashboards
  behalten es.
- **Überall deaktivieren** – verschwindet auf allen Dashboards und wird gar
  nicht mehr abgefragt. Das spart bei jedem Lauf Zeit. Zurückholen unter
  Einstellungen → Überall deaktiviert.

**Eigene Geräte** (Schnellbutton oben rechts in der Leiste, oder Einstellungen →
Eigene Geräte): Name, IP, Ports. Für Geräte, die kein Ping beantworten und
deshalb nicht gefunden werden. Sie bleiben immer sichtbar; Titel und Symbol holt
Netboard beim nächsten Lauf selbst. Liegt die IP auf einem bereits gefundenen
Gerät, werden die Ports zusammengelegt statt ein zweiter Eintrag angelegt.

**Löschen** geht nur bei eigenen Geräten. Gefundene Geräte lassen sich nicht
löschen – sie sind ja im Netz und wären beim nächsten Lauf zurück. Dafür gibt
es das Deaktivieren.

## Suche, Bangs und Tastatur

Die Leiste über dem Board filtert dein Netz live – und ist zugleich Startrampe:

- **`!g katzen`** sucht sofort bei Google, **`!yt lofi`** bei YouTube usw. Die
  Kürzel (Bangs) pflegst du unter **Einstellungen → Suche**; mitgeliefert sind
  Google, DuckDuckGo, YouTube, GitHub und Wikipedia.
- **`↵` (Enter)** öffnet die erste passende Kachel. Ist keine gemeint, sucht
  Enter beim **Standard-Anbieter**. **`⇧↵`** sucht immer im Web.
- Eine Hinweiszeile unter der Leiste zeigt jederzeit, was Enter gerade tut.

Tastenkürzel (wenn kein Feld aktiv ist):

| Taste | Wirkung |
|-------|---------|
| `/` oder `⌘/Strg K` | Zur Suche springen |
| `1`–`9` | Dashboard wechseln |
| `g` | Neu scannen |
| `?` | Tastatur-Hilfe |
| `Esc` | Filter leeren / schließen |

## Schnelllinks

Nicht alles hat eine IP im LAN: Doku, ein Cloud-Dienst, ein Deeplink in eine
Weboberfläche. Unter **Einstellungen → Links** legst du solche Kacheln von Hand
an – Name und Adresse genügen, `https://` ergänzt Netboard selbst. Wahlweise
dazu ein **Symbol** (Emoji oder Buchstabe) und eine **Farbe**, damit die Kachel
auf einen Blick auffällt.

Ein Schnelllink steht zwischen deinen gefundenen Diensten und verhält sich wie
jede andere Kachel: ziehen, in einen Ordner legen, per `···` bearbeiten oder
entfernen. Als Symbol kannst du ein Emoji/Zeichen setzen **oder das Favicon der
Seite ziehen lassen** (Häkchen im Link-Editor; Netboard holt es serverseitig,
funktioniert daher auch bei LAN-Diensten mit selbst signiertem Zertifikat).
Standardmäßig erscheint ein Link auf allen Dashboards; im Editor kannst du ihn
auf bestimmte beschränken.

## Hinzufügen

Ein Knopf **„＋ Hinzufügen"** oben rechts öffnet alles an einer Stelle, sauber
getrennt in drei Reiter:

- **Link** – eine Verknüpfung zu einer Seite (mit optionalem Favicon als Symbol).
- **Gerät** – ein eigenes Gerät (Name, IP, Ports), das der Suchlauf nicht selbst findet.
- **Ordner** – bündelt Kacheln.

## Ordner

Kacheln in benannte Ordner sortieren – zwei Wege, beide ohne Umwege:

1. **„＋ Hinzufügen → Ordner"** oben rechts. Das Dashboard schaltet dabei
   automatisch auf die Ordner-Ansicht.
2. Kacheln **per Drag & Drop** in einen Ordner ziehen. Zurück nach „Ohne Ordner"
   ziehen löst die Zuordnung. Das `···`-Menü an der Kachel hat ebenfalls
   **In Ordner …**.

**Anzeige – Offen oder Eingeklappt.** Standard ist offen (Ordner als Abschnitt).
„Eingeklappt" zeigt Ordner wie am iPhone – als Kachel mit Mini-Vorschau, die sich
per Klick öffnet. Wählbar **pro Dashboard** (Dashboard-Einstellungen →
Ordner-Ansicht) **oder pro einzelnem Ordner** (✎ am Ordner → Ansicht: Wie
Dashboard / Offen / Eingeklappt). So kann ein Ordner eingeklappt sein, ein
anderer offen.

Ordner umbenennst und ihre Ansicht änderst du über ✎, löschst über ✕ – die
Kacheln bleiben erhalten und wandern zurück nach „Ohne Ordner".

## Live-Werte: Proxmox & ESXi/vCenter

Zeigt CPU und RAM direkt auf der Kachel, die auf einen Virtualisierungs-Host
zeigt – und im `···`-Menü **„Auslastung & Gäste …“** die Liste der VMs/Container
mit Status.

**Proxmox VE** (Einstellungen → Systemwerte → Proxmox VE): Adresse eines Nodes,
eine **Token-ID** (`benutzer@realm!name`) und das **Token-Secret**. Ein reines
Lese-Token genügt – in Proxmox unter Datacenter → Permissions → API Tokens
anlegen und der Rolle `PVEAuditor` zuordnen. Netboard ordnet jeden Node über
`/cluster/status` seiner IP zu; die Kachel auf Port 8006 bekommt seine Werte.

**ESXi/vCenter**: unter Systemwerte → ESXi/vCenter die Zugangsdaten eintragen und
**„Werte auf Kacheln zeigen"** aktivieren – unabhängig davon, ob vSphere auch die
Systemwert-Quelle der Kopfzeile ist. Netboard legt dann pro Host CPU/RAM auf
dessen Kachel und zeigt die VMs im `···`-Menü.

Beides läuft nur lesend, im Hintergrund (alle 20 s), und ein nicht erreichbarer
Host blockiert das Board nie.

**VMs werden automatisch erkannt.** Findet der Suchlauf ein Gerät, das in
Wirklichkeit eine VM oder ein Container auf einem hinterlegten Proxmox/ESXi ist
(egal ob vorher oder nachher eingerichtet), zeigt die Kachel den **Namen der VM**
statt eines faden „VMware .19" – samt CPU/RAM der VM. Proxmox liest die VM-IPs
über den qemu-Gastagent bzw. die LXC-Config, ESXi über die VMware Tools. Fehlt
der Gastagent, bleibt es beim normalen Verhalten.

## Betriebssystem & Updates (per SSH)

Im `···`-Menü eines Geräts öffnet **„System prüfen (SSH) …"** eine Prüfung, die
per SSH Betriebssystem und offene Updates ausliest (Debian/Ubuntu, Fedora/RHEL,
openSUSE, Alpine, Arch). Auf der Kachel erscheint dann ein kleines OS-Symbol mit
Update-Zähler. Die Prüfung ist rein lesend; der Zugang wird verschlüsselt
gespeichert und im Hintergrund aufgefrischt.

**Updates einspielen aus der Oberfläche.** Sind Updates verfügbar, bietet der
Prüf-Dialog **„… Updates installieren"** – nach einer Rückfrage spielt Netboard
sie per SSH ein (nicht-interaktiv) und prüft danach automatisch neu. Ist der
Login aktiv, ist diese Aktion nur angemeldet möglich. Das Konto braucht
root-Rechte auf dem Zielgerät.

## Kacheln gestalten

Jede Kachel lässt sich einzeln gestalten: `···`-Menü → **„Kachel gestalten …"**.
Wählbar sind **Farbe**, **Farbverlauf** (Fade), ein **Hintergrundbild** (per URL)
oder **eigenes CSS** nur für diese Kachel. Die Schrift bleibt dabei **immer
lesbar**: Netboard misst die Helligkeit des Hintergrunds und wählt Textfarbe und
Schatten automatisch; Bild-Kacheln bekommen einen dezenten Schleier. Eine
Vorschau zeigt das Ergebnis; „Zurücksetzen" stellt das Standard-Aussehen wieder her.

## Geräte festsetzen und Scan-Modi

Gefundene Geräte verschwinden normalerweise, sobald sie offline gehen. Wer eins
dauerhaft sehen will, **heftet es an**: es bleibt auf dem Board und wird, wenn es
offline ist, mit seinem letzten Stand **als offline** angezeigt.

- **Anheften** über das `···`-Menü an der Kachel, oder unter
  **Einstellungen → Suchlauf → Geräte auswählen …** (gefundene Geräte ankreuzen).
- **„Alles jetzt festsetzen“** friert den aktuellen Stand ein.
- **Was erscheint** (unter Suchlauf): **Alles Gefundene** (Standard) oder
  **Nur ausgewählte** – dann werden nur angeheftete und eigene Geräte gezeigt,
  der Rest wird gefunden, aber erst nach Auswahl aufgenommen.

## SSH-Terminal im Browser

Standardmäßig **aus**. Einschalten unter Einstellungen → SSH-Terminal.

**Ein Schalter, mehr nicht.** Ist er an, kommst du überall dort an ein
Terminal, wo ein Gerät SSH offen hat:

| Ansicht | Wie |
|---|---|
| **Liste** | Knopf `>_ SSH` in der Zeile. |
| **Raster / Karten** | Kleines `>_`-Zeichen an der Ecke des Symbols – ein Klick öffnet die Sitzung. Zusätzlich steht **SSH öffnen** im `⋯`-Menü der Kachel. |

Ein Gerät mit Weboberfläche bekommt **keine zweite Kachel** fürs Terminal – das
wäre nur Lärm. Nur Geräte, die ausschließlich SSH anbieten, erscheinen als
eigene Terminal-Kachel; sonst würden sie ganz fehlen.

Ein Klick öffnet einen neuen Tab – Benutzername, Passwort, fertig. Ein
vollwertiges Terminal im Browser.

**Warum standardmäßig aus:** Netboard hat keine eigene Anmeldung. Wer die
Oberfläche erreicht, erreicht auch diese Brücke. Das ist im Heimnetz meist
in Ordnung, aber es soll deine Entscheidung sein, nicht meine Voreinstellung.

Zwei fest eingebaute Sperren:

1. **Nur deine Netze.** Ziele außerhalb der eingerichteten Netze werden
   abgewiesen. Sonst ließe sich Netboard als offenes Sprungbrett missbrauchen.
2. **Keine Passwörter.** Sie werden nur für den Aufbau durchgereicht, nie
   gespeichert und nie protokolliert. Gemerkt wird allein der Benutzername
   pro Gerät – damit du ihn nicht jedes Mal tippen musst.

Das Terminal (xterm.js, MIT) liegt lokal bei. Die Seite lädt nichts aus dem
Internet nach.

## Globale Einstellungen

| Einstellung | Bedeutung |
|---|---|
| Netze | Welche Bereiche durchsucht werden. Mehrere durch Komma. Maximal /20 je Netz. |
| Ports | Welche Ports je Gerät geprüft werden. Bereiche wie `8000-8100` erlaubt. |
| Zeitlimit | Wie lange auf einen Dienst gewartet wird (0,5–30 s). |
| Parallele Abfragen | Höher = schneller, belastet schwache Geräte stärker. |
| Symbole der Dienste | Lädt das Favicon jedes Dienstes. |
| Geräte ohne Weboberfläche ausblenden | Versteckt alles ohne anklickbaren Dienst. |
| Bevorzugte Ports | Diese Dienste stehen bei jedem Gerät vorn, in genau dieser Reihenfolge. |
| Wunschnamen | Eigener Name statt Hersteller oder nackter IP. |
| SSH-Terminal erlauben | Schaltet die Terminal-Brücke frei. Standard: aus. |
| SSH-Ports | Wird mitgesucht, aber nicht als Web-Dienst geführt. Standard: 22. |
| Hintergrund | Optik hinter den Kacheln: Schlicht, Getönt, Raster, Schimmer oder **Eigenes Bild** (Upload). |
| Eigenes CSS | Eigene Regeln für die ganze Seite – der Editor ist mit den wichtigsten Variablen (Akzent, Flächen, Text) als Vorlage vorbefüllt. Hochladen oder eintippen; „Auf Standard zurücksetzen“ entfernt sie wieder. |
| Suche | Such-Anbieter (Bangs) und Standard-Anbieter für die Leiste. |
| Backup | Alle Einstellungen als JSON exportieren oder eine Sicherung einspielen (unter **Über**). Das vSphere-Passwort bleibt aus Sicherheitsgründen draußen. |
| Wetter | Kleines Widget in der Leiste, sauber rechts von den Systemwerten. **Standardmäßig aus.** Ort per **Stadtsuche** eingeben. Nur wenn eingeschaltet, fragt der Browser (nicht der Server) open-meteo ab – der einzige Weg nach außen. |

## Wichtig

- **`network_mode: host` ist Pflicht** und funktioniert nur unter Linux. Nur so
  sieht der Scanner das echte LAN inklusive MAC-Adressen und Hersteller.
- Der Container läuft als root – nmap braucht das zum Lesen der MAC-Adressen.
- Einstellungen liegen in `./data/config.json` (Volume). Nicht löschen, sonst
  startet der Assistent erneut.

## Wenn etwas klemmt

**Port 8888 belegt** → in `docker-compose.yml` ergänzen, dann `docker compose up -d --build`:
```yaml
    command: ["uvicorn","app.main:app","--host","0.0.0.0","--port","8899"]
```

**Assistent erkennt kein Netz** → `network_mode: host` greift nicht. Prüfen mit
`docker inspect netboard | grep NetworkMode` (muss `host` sein). Netz notfalls
im Assistenten von Hand eintragen.

**Dienst fehlt** → sein Port steht nicht in der Liste. Einstellungen → Suchlauf → Ports.

**Von vorn beginnen** → `docker compose down && rm -rf ./data && docker compose up -d`

**vCenter/ESXi nur per IP erreichbar, Name (z. B. `vcenter01.fritz.box`) nicht**
→ Docker ersetzt im Container das lokale DNS des Hosts gern durch öffentliche
Server, die keine `.fritz.box`-Namen kennen. Netboard fragt deshalb ersatzweise
den Router der eingerichteten Netze selbst – meist reicht das schon. Wenn nicht,
in `docker-compose.yml` den Router als `dns:` eintragen (z. B. `192.168.178.1`)
oder den Namen unter `extra_hosts:` fest verdrahten. Beides steht dort als
Kommentar bereit.

## Aufbau

```
app/
  main.py      API, Startlauf, Auslieferung
  config.py    Einstellungen: prüfen, atomar speichern, Verweise heil halten
  scanner.py   nmap-Discovery (Netze, Hosts, Ports, SSH)
  enrich.py    HTTP-Abruf für Titel, Schema und Favicon
  sshgw.py     SSH-Brücke: WebSocket <-> asyncssh, mit Zielbeschränkung
  netdns.py    kleiner Fallback-Resolver: lokale Namen über den Router auflösen
  integrations.py  Live-Werte je Kachel: Proxmox-API (Token) und ESXi/vCenter
  secretstore.py   Verschlüsselung ruhender Geheimnisse, Passwort-Hash, Sitzungen
  static/
    index.html komplette Oberfläche, ohne externe Abhängigkeiten
    ssh.html   Terminal-Seite
    vendor/    xterm.js (MIT) – liegt lokal bei, lädt nichts nach
```

Favicons werden serverseitig geholt und zwischengespeichert – ein direkter
Abruf aus dem Browser würde an selbst signierten Zertifikaten scheitern.

`config.py` zieht bei jedem Speichern die Verweise gerade (`normalize`): eine
Startansicht kann nicht auf ein gelöschtes Dashboard zeigen, das letzte
Dashboard lässt sich nicht entfernen, und überall deaktivierte Geräte fliegen
aus allen Dashboard-Listen. So kann keine Bedienfolge einen Zustand
hinterlassen, aus dem man nicht mehr herauskommt.

## Wake-on-LAN

Netboard kennt aus dem Netzwerk-Scan die **MAC-Adressen** deiner Geräte und kann
sie darüber **aufwecken**. Im `···`-Menü eines Geräts erscheint **„Aufwecken
(WoL)"**, sobald eine MAC bekannt ist – ein Klick schickt ein Magic Packet an die
Broadcast-Adresse des Netzes (Ports 9 und 7).

Voraussetzungen: Das Zielgerät muss **Wake-on-LAN im BIOS/UEFI und im
Betriebssystem** aktiviert haben. Damit der Broadcast das LAN erreicht, sollte
der Container im **Host-Netzwerkmodus** laufen (das ist ohnehin nötig, damit der
nmap-Scan das lokale Netz sieht). Ist der Login aktiv, ist das Wecken nur
angemeldet möglich.
