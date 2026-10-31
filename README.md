# Spooly für Home Assistant

Zeigt deine Spulen und Slots aus [Spooly](https://spooly.eu) in Home Assistant an. Die Integration liest nur, sie ändert nichts in Spooly.

[English version below](#spooly-for-home-assistant)

## Was du siehst

- **Je Spule ein Gerät** (`#Nummer Name`, ohne Nummer nur der Name) mit
  - Sensor **Restgewicht** in Gramm. Attribute: Hersteller, Material, Finish, Farbe, Lagerort, Spulengröße und Füllstand in Prozent.
  - Binärsensor **Niedrig**. Er ist an, wenn Spooly die Spule als niedrig oder kritisch meldet (Attribut `level`). Die Schwellen stellst du in Spooly ein, die Integration rechnet nichts selbst.
- **Je Drucker ein Gerät** mit einem Sensor pro Slot. Der Zustand ist der Name der Spule im Slot, bei leerem Slot `unbekannt`. Attribute: Spulen-ID, Nummer, Material, Farbe.
- **Aufrufe heute** (Konto-Diagnose, standardmäßig deaktiviert). Zeigt, wie viele der erlaubten Abrufe verbraucht sind.

Neue Spulen und Slots erscheinen ohne Neustart. Archivierte oder gelöschte Spulen werden `nicht verfügbar`. Ihr Gerät kannst du dann in Home Assistant von Hand löschen.

## Was nicht geht

- **Nur Anzeige.** Keine Schalter, keine Dienste, nichts wird nach Spooly geschrieben.
- **Alle 5 Minuten**, nicht in Echtzeit.
- **Spooly Pro** (die Testphase zählt mit) und die freigeschaltete API sind nötig. Läuft Pro ab, bleiben die Entitäten stehen und melden `nicht verfügbar`, bis Pro wieder aktiv ist.
- **Gleicher Druckername:** Spooly liefert keine Drucker-ID. Zwei Drucker mit demselben Namen sind deshalb nicht zu trennen, es erscheint nur der erste Slot gleichen Namens (mit einer Warnung im Protokoll). Benenne die Drucker in Spooly unterschiedlich.
- Pro Tag sind 10.000 Aufrufe erlaubt, die Integration braucht rund 900.

## Installation

Voraussetzung: Home Assistant 2026.8 oder neuer.

**Mit HACS:** HACS, Menü oben rechts, *Benutzerdefinierte Repositories*, `https://github.com/Blanus-1/spooly-homeassistant` als *Integration* eintragen, Spooly installieren, Home Assistant neu starten.

**Von Hand:** Den Ordner `custom_components/spooly` in dein Konfigurationsverzeichnis kopieren (dorthin, wo `configuration.yaml` liegt), Home Assistant neu starten.

Danach *Einstellungen, Geräte & Dienste, Integration hinzufügen, Spooly*.

## API-Schlüssel

Im Spooly-Web unter **Einstellungen, Geräte & Schlüssel** einen Schlüssel anlegen (Art: Home Assistant). Er beginnt mit `spk_` und wird nur einmal angezeigt. Wird er widerrufen, fragt Home Assistant nach einem neuen.

Derselbe Schlüssel lässt sich nur einmal einrichten.

---

# Spooly for Home Assistant

Shows your spools and printer slots from [Spooly](https://spooly.eu) in Home Assistant. Read only, it never changes anything in Spooly.

## What you get

- **One device per spool** (`#number name`, just the name without a number) with
  - a **Remaining weight** sensor in grams. Attributes: manufacturer, material, finish, color, location, spool size and fill level in percent.
  - a **Low** binary sensor. It is on when Spooly reports the spool as low or critical (attribute `level`). You set the thresholds in Spooly, the integration calculates nothing itself.
- **One device per printer** with a sensor per slot. The state is the name of the spool in the slot, `unknown` for an empty slot. Attributes: spool id, number, material, color.
- **Calls today** (account diagnostic, disabled by default). Shows how much of the allowed API usage is spent.

New spools and slots appear without a restart. Archived or deleted spools become `unavailable`, and you can then delete their device by hand.

## What it does not do

- **Display only.** No switches, no services, nothing is written to Spooly.
- **Every 5 minutes**, not real time.
- **Spooly Pro** (the trial counts) and the enabled API are required. If Pro expires, the entities stay and show `unavailable` until Pro is active again.
- **Same printer name:** Spooly gives no printer id. Two printers with the same name cannot be told apart, only the first slot of that name is shown (with a warning in the log). Give your printers different names in Spooly.
- The daily limit is 10,000 calls, the integration uses about 900.

## Installation

Requires Home Assistant 2026.8 or newer.

**With HACS:** HACS, menu top right, *Custom repositories*, add `https://github.com/Blanus-1/spooly-homeassistant` as *Integration*, install Spooly, restart Home Assistant.

**Manually:** Copy the folder `custom_components/spooly` into your configuration directory (next to `configuration.yaml`) and restart Home Assistant.

Then *Settings, Devices & services, Add integration, Spooly*.

## API key

Create a key in the Spooly web app under **Settings, Devices & keys** (type: Home Assistant). It starts with `spk_` and is shown only once. If it gets revoked, Home Assistant asks for a new one.

The same key can only be set up once.
