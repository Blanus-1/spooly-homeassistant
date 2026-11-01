# Spooly für Home Assistant

Zeigt deine Spulen und Slots aus [Spooly](https://spooly.eu) in Home Assistant an und kann sie ändern (Gewicht, Slots, Lagerort, Archiv). Dazu kommt die Wartung deiner Drucker.

[English version below](#spooly-for-home-assistant)

## Was du siehst

- **Je Spule ein Gerät** (`#Nummer Name`, ohne Nummer nur der Name) mit
  - Sensor **Restgewicht** in Gramm. Attribute: Hersteller, Material, Finish, Farbe, Lagerort, Spulengröße und Füllstand in Prozent.
  - Binärsensor **Niedrig**. Er ist an, wenn Spooly die Spule als niedrig oder kritisch meldet (Attribut `level`). Die Schwellen stellst du in Spooly ein, die Integration rechnet nichts selbst.
- **Je Drucker ein Gerät** mit einem Sensor pro Slot. Der Zustand ist der Name der Spule im Slot, bei leerem Slot `unbekannt`. Attribute: Spulen-ID, Nummer, Material, Farbe und `slot_id` (nur mit aktivem Slot-Autopilot).
- **Je Drucker aus der Wartung ein Gerät** (es hat den Namen des Druckers, ein zweites Gerät gleichen Namens trägt die Slots) mit
  - einem Sensor **Fällige Wartungen** (Anzahl, Attribut `soon_count` für „bald fällig“),
  - einem Sensor **Druckstunden** in Stunden,
  - **einem Sensor je Wartungsaufgabe** mit dem Zustand `Fällig`, `Bald` oder `OK`. Attribute: `metric`, `interval`, `used`, `remaining`, `eta_days`, `last_done_at`, `snoozed_until`, `default_action`.
- Am Spooly-Gerät: **Filament diesen Monat** in Gramm (zählt im Kalendermonat deiner Home-Assistant-Zeit, am Monatsanfang wieder bei 0) und **Lagerwert** in deiner Währung.
- **Aufrufe heute** (Konto-Diagnose, standardmäßig deaktiviert). Zeigt, wie viele der erlaubten Abrufe verbraucht sind.

Neue Spulen und Slots erscheinen ohne Neustart. Archivierte oder gelöschte Spulen werden `nicht verfügbar`. Ihr Gerät kannst du dann in Home Assistant von Hand löschen.

## Anmeldung

*Einstellungen, Geräte & Dienste, Integration hinzufügen, Spooly*, dann **Mit Spooly anmelden**. Spooly öffnet sich im Browser, du klickst auf „Erlauben“, fertig. Home Assistant sieht dein Passwort nie. Die Rückleitung läuft über [My Home Assistant](https://my.home-assistant.io), das muss auf deiner Instanz eingerichtet sein (bei den meisten ist es das).

Alternativ **Schlüssel eingeben**: In Spooly unter **Einstellungen, Geräte & Schlüssel** einen Schlüssel anlegen (Art: Home Assistant), er beginnt mit `spk_`. Soll Home Assistant auch ändern dürfen, „Darf auch ändern“ einschalten. Bestehende Einträge mit Schlüssel laufen unverändert weiter.

Wird die Verbindung in Spooly widerrufen, fragt Home Assistant nach einer neuen Anmeldung. Ein Konto lässt sich nur einmal einrichten.

## Dienste

Alle Dienste brauchen das Recht zu ändern. Fehlt es, kommt eine Meldung, und Home Assistant bietet die erneute Anmeldung an.

| Dienst | Was er tut |
|---|---|
| `spooly.set_weight` | Restgewicht setzen, entweder `grams` (nur Filament) oder `gross_grams` (Spule auf der Waage, braucht eine bekannte Tara) |
| `spooly.refill` | `grams` zum Restgewicht addieren |
| `spooly.assign_slot` | Spule in einen Slot legen, bei abweichender Farbe mit `confirm_color` |
| `spooly.clear_slot` | Spule aus einem Slot nehmen (sie bleibt in Spooly) |
| `spooly.move` | Spule in einen Lagerort legen, `location` ist der Name wie in Spooly (mit Pfad) oder die Kennung |
| `spooly.archive`, `spooly.restore` | Spule archivieren oder zurückholen |
| `spooly.complete_maintenance` | Wartungsaufgabe als erledigt melden. `task` ist der Sensor der Aufgabe, optional `action` (`replace`, `clean`, `check`) und `notes` |
| `spooly.snooze_maintenance` | Wartungsaufgabe zurückstellen. `task` ist der Sensor der Aufgabe, `days` ist 7, 14 oder 30, optional `reason` |

Die Spule wählst du über ihr Gerät (`device_id`), den Slot und die Wartungsaufgabe über ihren Sensor (`slot`, `task`). `assign_slot` und `clear_slot` gehen nur mit aktivem Slot-Autopilot. Endgültig löschen kann die Integration nichts.

```yaml
action: spooly.set_weight
data:
  device_id: 0123456789abcdef
  gross_grams: 742
```

Einen fertigen Knopf je Aufgabe gibt es mit Absicht nicht, damit nichts aus Versehen als erledigt gilt. Eine Karte mit Rückfrage geht so:

```yaml
type: button
name: Düse gewechselt
entity: sensor.bambu_x1c_duse_wechseln
tap_action:
  action: perform-action
  perform_action: spooly.complete_maintenance
  data:
    task: sensor.bambu_x1c_duse_wechseln
    action: replace
  confirmation:
    text: Wartung wirklich als erledigt melden?
```

## Was nicht geht

- **Alle 5 Minuten**, nicht in Echtzeit. Nach einem Dienst wird sofort neu abgerufen.
- **Spooly Pro** (die Testphase zählt mit) und die freigeschaltete API sind nötig. Läuft Pro ab, bleiben die Entitäten stehen und melden `nicht verfügbar`, bis Pro wieder aktiv ist.
- **Gleicher Druckername:** Spooly liefert keine Drucker-ID. Zwei Drucker mit demselben Namen sind deshalb nicht zu trennen, es erscheint nur der erste Slot gleichen Namens (mit einer Warnung im Protokoll). Benenne die Drucker in Spooly unterschiedlich.
- Pro Tag sind 10.000 Aufrufe erlaubt, die Integration braucht rund 1.450.

## Installation

Voraussetzung: Home Assistant 2026.8 oder neuer.

**Mit HACS:** HACS, Menü oben rechts, *Benutzerdefinierte Repositories*, `https://github.com/Blanus-1/spooly-homeassistant` als *Integration* eintragen, Spooly installieren, Home Assistant neu starten.

**Von Hand:** Den Ordner `custom_components/spooly` in dein Konfigurationsverzeichnis kopieren (dorthin, wo `configuration.yaml` liegt), Home Assistant neu starten.

---

# Spooly for Home Assistant

Shows your spools and printer slots from [Spooly](https://spooly.eu) in Home Assistant and can change them (weight, slots, storage location, archive). It also covers the maintenance of your printers.

## What you get

- **One device per spool** (`#number name`, just the name without a number) with
  - a **Remaining weight** sensor in grams. Attributes: manufacturer, material, finish, color, location, spool size and fill level in percent.
  - a **Low** binary sensor. It is on when Spooly reports the spool as low or critical (attribute `level`). You set the thresholds in Spooly, the integration calculates nothing itself.
- **One device per printer** with a sensor per slot. The state is the name of the spool in the slot, `unknown` for an empty slot. Attributes: spool id, number, material, color and `slot_id` (only with the slot autopilot active).
- **One device per printer from the maintenance plan** (it carries the printer's name, a second device of the same name holds the slots) with
  - a **Due maintenance** sensor (count, attribute `soon_count` for "due soon"),
  - a **Print hours** sensor in hours,
  - **one sensor per maintenance task** with the state `Due`, `Soon` or `OK`. Attributes: `metric`, `interval`, `used`, `remaining`, `eta_days`, `last_done_at`, `snoozed_until`, `default_action`.
- On the Spooly device: **Filament this month** in grams (counts in the calendar month of your Home Assistant time, back to 0 at the start of a month) and **Stock value** in your currency.
- **Calls today** (account diagnostic, disabled by default). Shows how much of the allowed API usage is spent.

New spools and slots appear without a restart. Archived or deleted spools become `unavailable`, and you can then delete their device by hand.

## Signing in

*Settings, Devices & services, Add integration, Spooly*, then **Sign in with Spooly**. Spooly opens in your browser, you click "Allow", done. Home Assistant never sees your password. The redirect goes through [My Home Assistant](https://my.home-assistant.io), which has to be set up on your instance (it is on most).

Or choose **Enter a key**: create a key in Spooly under **Settings, Devices & keys** (type: Home Assistant), it starts with `spk_`. Switch on "Can also change" if Home Assistant should be allowed to change things. Existing entries with a key keep working unchanged.

If the connection is revoked in Spooly, Home Assistant asks you to sign in again. An account can only be set up once.

## Services

All services need the permission to change. If it is missing you get a message, and Home Assistant offers to sign in again.

| Service | What it does |
|---|---|
| `spooly.set_weight` | Set the remaining weight, either `grams` (filament only) or `gross_grams` (spool on the scale, needs a known tare) |
| `spooly.refill` | Add `grams` to the remaining weight |
| `spooly.assign_slot` | Put a spool into a slot, `confirm_color` if the printer reports another color |
| `spooly.clear_slot` | Take the spool out of a slot (it stays in Spooly) |
| `spooly.move` | Put a spool into a storage location, `location` is the name as shown in Spooly (with path) or the id |
| `spooly.archive`, `spooly.restore` | Archive a spool or bring it back |
| `spooly.complete_maintenance` | Report a maintenance task as done. `task` is the sensor of the task, optional `action` (`replace`, `clean`, `check`) and `notes` |
| `spooly.snooze_maintenance` | Postpone a maintenance task. `task` is the sensor of the task, `days` is 7, 14 or 30, optional `reason` |

You pick the spool through its device (`device_id`) and the slot and the maintenance task through their sensors (`slot`, `task`). `assign_slot` and `clear_slot` only work with the slot autopilot active. The integration cannot delete anything for good.

```yaml
action: spooly.set_weight
data:
  device_id: 0123456789abcdef
  gross_grams: 742
```

There is no ready-made button per task on purpose, so nothing counts as done by accident. A card with a confirmation looks like this:

```yaml
type: button
name: Nozzle replaced
entity: sensor.bambu_x1c_replace_nozzle
tap_action:
  action: perform-action
  perform_action: spooly.complete_maintenance
  data:
    task: sensor.bambu_x1c_replace_nozzle
    action: replace
  confirmation:
    text: Really report this maintenance as done?
```

## What it does not do

- **Every 5 minutes**, not real time. After a service call it fetches again right away.
- **Spooly Pro** (the trial counts) and the enabled API are required. If Pro expires, the entities stay and show `unavailable` until Pro is active again.
- **Same printer name:** Spooly gives no printer id. Two printers with the same name cannot be told apart, only the first slot of that name is shown (with a warning in the log). Give your printers different names in Spooly.
- The daily limit is 10,000 calls, the integration uses about 1,450.

## Installation

Requires Home Assistant 2026.8 or newer.

**With HACS:** HACS, menu top right, *Custom repositories*, add `https://github.com/Blanus-1/spooly-homeassistant` as *Integration*, install Spooly, restart Home Assistant.

**Manually:** Copy the folder `custom_components/spooly` into your configuration directory (next to `configuration.yaml`) and restart Home Assistant.
