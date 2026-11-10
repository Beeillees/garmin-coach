# Changelog

## 0.9.3
- **Treppe zählt jetzt als Höhenmeter-Training.** `floor_climbing`/`stair_climbing` ist eine eigene
  Sportart-Gruppe `treppe` (bisher `kraft_hiit`). Die Höhenmeter kommen aus Garmins Stockwerken
  (`floorsClimbed` × 3,048 m); die FIT-Datei enthält dort nur barometrisches Rauschen (0–30 m pro
  Einheit, tatsächlich rund 800–870 Hm pro Stunde).
- `list_activities`, `get_activity`, `weekly_summary` und `review_data` liefern für Treppe die
  richtigen `hoehenmeter` und zusätzlich `stockwerke`.
- `review_data`: neuer Block `vertikal` (Höhenmeter Laufen + Gehen + Treppe, Lauf-Hm pro km).
- `query_sql`: neue Funktion `treppen_hm(a.data [, s.ascent_m])`.
- Laufband-Einheiten haben weiterhin keine Höhenmeter (die Steigung steht nicht in den Daten).

## 0.9.2
- **Gewicht als Tageswert = niedrigster Wert des Tages** (bisher der zuletzt eingetragene). Bei mehreren
  Wiegungen steht der höchste Wert zusätzlich in `gewicht_max_kg`.
- `daily_metrics`: neu `gewicht_7tage` (Mittel der letzten 7 Kalendertage, erst ab 3 Wiegungen) und
  `gewicht_vorwoche_diff` (Unterschied zum 7-Tage-Mittel von vor einer Woche). Tage ohne Wiegung bleiben Lücken.
- `review_data`: neuer Block `gewicht` (Mittel, Vorperiode, Differenz, niedrigster/höchster Wert, Tage mit
  Wiegung); im Monatsrückblick zusätzlich `gewicht_kg_avg` je Woche, in den Tagen `gewicht_kg`.
- HA-Sensor `sensor.garmin_coach_gewicht` zeigt den Tageswert (niedrigster Wert) und hat zusätzlich die
  Attribute `max_kg`, `mittel_7_tage`, `mittel_vorwoche`, `differenz` und `wochen` (Wochenmittel der
  letzten 26 Wochen als `[Montag, Mittel kg, Tage]`).
- Werkzeugbeschreibungen weisen darauf hin, Trends nur über Mittelwerte zu bewerten.
- Der Dashboard-Sensor `sensor.garmin_coach_tageswerte` ist ein SQL-Helper in Home Assistant und liest
  `daily_raw` direkt; die Gewichts-Spalten kommen dort aus der Abfrage, nicht aus dem Add-on.

## 0.9.1
- Trainingsname ohne Präfix „Coach: …“ – erkannt werden Coach-Trainings jetzt nur an der
  Markierung `[garmin-coach]` in der Beschreibung (Trainings aus 0.9.0 weiterhin).
- Doppelte-Prüfung berücksichtigt nur Coach-Trainings.
- Werkzeugbeschreibung enthält Brunos Vorlieben (Ein-/Auslaufen bis Runden-Taste, Pace mit
  Spielraum, kein HF-Ziel, nur Qualitätseinheiten nach Zustimmung).

## 0.9.0
- **Trainings direkt auf die Uhr:** neue MCP-Werkzeuge
  - `create_workout` – strukturierte Laufeinheit (Aufwärmen, Wiederholungen, Pausen, Pace- und
    HF-Ziele, Hinweise) in Garmin Connect anlegen und auf ein Datum einplanen; die Uhr lädt sie beim
    nächsten Sync. `vorschau=True` prüft nur, `ersetzen=True` tauscht ein gleichnamiges Training am
    selben Tag aus.
  - `list_workouts` – geplante Trainings im Garmin-Kalender, live (auch von anderen Apps).
  - `delete_workout` – löscht nur Trainings des Coaches.
- Sicherheit: Alle Trainings des Coaches heißen „Coach: …“ und tragen `[garmin-coach]` in der
  Beschreibung. Löschen/Ersetzen fremder Trainings wird abgelehnt.
- Der MCP-Server nutzt dafür die Garmin-Anmeldung des Sammlers; keine zusätzlichen Zugangsdaten.

## 0.8.5
- **Berichte durchschalten:** Die Bericht-Sensoren haben zusätzlich das Attribut `verlauf` mit den
  vier vorherigen Berichten je Art (neueste zuerst), damit das Dashboard die letzten fünf
  Tages-, Trainings-, Wochen- und Monatsberichte umschalten kann.
- Trainingsbericht-Sensor sortiert nach Start der Einheit statt nach Berichtstag.
- Integration 0.4.1: `text` und `verlauf` werden nicht mehr im HA-Recorder gespeichert.
  **Nach dem Update HA einmal neu starten**, damit die neue Integrationsversion geladen wird.

## 0.8.4
- Berichtsvorlage ohne Kennzahlen-Tabelle: Zahlen zeigt die Dashboard-Karte live aus den Sensoren,
  der Bericht enthält nur die Einschätzung (Überschrift mit Ampel, Einordnung, Stichpunkte).

## 0.8.3
- `save_report(ersetzen=True)` überschreibt einen vorhandenen Bericht (z. B. neu formatiert).
- Feste Markdown-Vorlage für Berichte (Überschrift mit Ampel, Kennzahlen-Tabelle, Stichpunkte),
  damit sie im Berichte-Tab gut lesbar sind.

## 0.8.2
- **Berichte in Home Assistant:** Neue Sensoren `sensor.garmin_coach_bericht_morgen`, `_abend`,
  `_training`, `_woche`, `_monat` mit dem letzten Bericht je Art (Zustand: Tag bzw. Zeitraum,
  Text im Attribut `text`). Für einen Berichte-Tab im Dashboard.
- Nach `save_report` und Tagebucheinträgen werden die HA-Sensoren sofort aufgefrischt.

## 0.8.1
- Sensor `akute_last` hat zusätzlich `acwr_status` und den Zielbereich der chronischen Last
  (`chronisch_min`, `chronisch_max`) für die Leistungsstand-Karte im Dashboard.

## 0.8.0
Garmin Connect als HA-Integration ablösen und Tagebuch im Wortlaut.

- **Sensoren in Home Assistant:** Die mitgelieferte Integration (jetzt "Garmin Coach", Version
  0.4.0) legt neben der MCP-Weiterleitung 44 Sensoren `sensor.garmin_coach_*` an: Bereitschaft,
  HRV, Ruhepuls, Body Battery, Stress, Schlaf, Atmung, Leistung, Prognosen, Gewicht, letzte
  Aktivität, Tagebuch, letzter Sync. Das Add-on liefert sie unter `/ha/state` (nur intern);
  neue Sensoren brauchen künftig keinen HA-Neustart. **Nach dem Update HA einmal neu starten.**
- **Live-Werte von heute:** Neue Option `live_minutes` (Standard 30). Während der Schnellprüfung
  werden Body Battery, Stress, Schritte und Bereitschaft von heute in diesem Abstand aufgefrischt,
  Schlaf und HRV, solange sie für heute fehlen. Kein Eintrag im Sync-Protokoll.
- **Sofort aktuell:** Nach jedem Sync sendet das Add-on das Ereignis `garmin_coach_updated`,
  die Integration holt dann sofort die neuen Werte.
- **Tagebuch im Wortlaut:** Neue Spalte `diary.original` für Brunos Eingabe unverändert;
  `text` bleibt die Zusammenfassung. `log_diary(original=…)`; beim Ergänzen mit `entry_id`
  wird der neue Wortlaut angehängt. `list_diary` zeigt ihn, Rückblicke lassen ihn weg.

## 0.7.0
Gewicht und Running Tolerance kommen dazu.

- **Gewicht:** Wiegungen aus Garmin Connect (manuell eingetragen oder von einer Waage) werden als
  Tageswert `weight` in `daily_raw` gespeichert, ein Abruf pro Zeitraum statt pro Tag.
  - `daily_metrics` zeigt `gewicht_kg`, `bmi` und, wenn vorhanden, `koerperfett_pct`,
    `koerperwasser_pct`, `muskelmasse_kg` (Garmin liefert Gramm, hier in kg).
  - `review_data` enthält Gewicht im Schnitt sowie am Anfang und Ende des Zeitraums.
- **Running Tolerance (Fenix 8):** neuer Schnappschuss `running_tolerance` (letzte 8 Wochen,
  täglich) und Verlauf `running_tolerance_history` (wöchentlich), abrufbar über `garmin_profile`.
- **Einmaliges Nachladen:** Beim ersten vollen Sync nach dem Update werden Gewicht und
  Running-Tolerance-Verlauf für `backfill_days` (mindestens 730 Tage) nachgeladen. Eigene
  Markierung `/data/history_v07_done`; Backfill und übrige Verläufe laufen nicht noch einmal.
  Fehlende Running-Tolerance-Werte vor der Fenix 8 sind kein Fehler.

## 0.6.1
- **Backfill läuft erneut, wenn `backfill_days` größer wird.** Bisher lief der Backfill nur einmal
  und ignorierte spätere Änderungen. Jetzt merkt sich das Add-on die geladene Tiefe: Wer z. B.
  von `730` auf `1500` erhöht, bekommt die älteren Tage nachgeladen (bereits vorhandene Tage und
  FIT-Dateien werden übersprungen). Das gilt auch für die Verläufe von Laktatschwelle, Prognosen usw.

## 0.6.0
Trainingstagebuch und Rückblicke: Was Garmin nicht misst, kommt jetzt mit in die Datenbank, und der
Coach kann Wochen und Monate zusammenfassen.

- **Neue Tabelle `diary` (Trainingstagebuch)** mit festen Feldern: Beine (1 frisch … 5 sehr schwer),
  Energie (1 leer … 5 top), Infektzeichen, Schmerz mit Körperstelle und Stärke (0–10),
  Kohlenhydrate und Flüssigkeit pro Stunde, Freitext; optional mit Bezug auf eine Einheit.
  Eingabe einfach im Chat, Claude trägt ein.
  - Werkzeuge `log_diary` (anlegen oder mit `entry_id` ergänzen), `list_diary`, `delete_diary`.
  - Einträge erscheinen in `daily_metrics` (Feld `tagebuch`) und in `get_activity`.
- **Wochen- und Monatsberichte:** `save_report` kennt jetzt `woche` und `monat` mit Zeitraum
  (`period`, z. B. `2026-W44` bzw. `2026-10`), pro Zeitraum nur ein Bericht, Kurzfassung bis
  12.000 Zeichen. Neue Spalte `coach_reports.period` (wird beim Start automatisch ergänzt).
- **Neues Werkzeug `review_data`:** alles für einen Rückblick in einem Abruf – Umfang je Sportart
  mit Vorperiode, Laufen, Schlüsseleinheiten, Erholung (Bereitschaft, HRV gegen Basis, Ruhepuls,
  Schlaf, Last, VO2max) mit Vorperiode, Tagebuch, Notizen, Berichte des Zeitraums, letzter Rückblick,
  Plan und kommende Wettkämpfe. Woche mit Tagesübersicht, Monat mit Übersicht je Woche.
- `pending_reports` zeigt zusätzlich, ob der fällige Wochen- und Monatsrückblick fehlt.
- `list_reports` gibt den Zeitraum mit aus.

## 0.5.1
- `garmin_profile`: Verläufe kommen verdichtet statt als Rohdaten (der Laktatschwellen-Verlauf war
  über 170.000 Zeichen groß). Laktatschwelle nur an Änderungen, mit Pace; Wettkampfprognosen ein
  Wert pro Woche. `kompakt=False` liefert weiterhin alles.

## 0.5.0
Grundlage für den täglichen Coach: feste Sync-Zeiten, schnelle Erkennung neuer Einheiten,
Trainingsplan und Berichte in der Datenbank.

- **Fester Zeitplan statt alle 6 Stunden.** Neue Optionen:
  - `sync_times` (Standard `05:15`, `19:45`): voller Sync zu festen Uhrzeiten, lokale Zeit.
    Die Zeiten verschieben sich nicht mehr bei einem Neustart des Add-ons.
  - `activity_check_minutes` (Standard 20) und `activity_check_window` (Standard `06:00-22:00`):
    Schnellprüfung auf neue Aktivitäten mit einem einzigen Abruf bei Garmin. Nur wenn etwas Neues
    da ist, werden FIT-Datei, Auswertung und die Tageswerte von heute geholt. `0` schaltet sie ab.
  - `sync_interval_hours` wird nicht mehr verwendet.
  - Beim Start läuft wie bisher einmal ein voller Sync.
- **Neues Werkzeug `sync_now`** (`schnell` oder `voll`): Claude kann frische Daten holen und wartet
  auf das Ergebnis.
- **Verläufe von Laktatschwelle, Wettkampfprognosen und Endurance-Score repariert.** Garmin erlaubt
  höchstens etwa ein Jahr pro Abfrage; 0.4.0 fragte zwei Jahre auf einmal ab, alle drei Abrufe
  scheiterten, und die Markierung „erledigt“ wurde trotzdem gesetzt. Jetzt wird in Jahresstücken
  geladen, und die Markierung gibt es nur, wenn alles geklappt hat. Nach dem Update werden die
  Verläufe automatisch nachgeladen. `garmin_profile` liefert einen Verlauf komplett mit allen Stücken.
- **Laktatschwelle lesbar:** `garmin_profile(source="lactate_threshold")` enthält jetzt `auswertung`
  mit Schwellen-HF, Schwellen-Pace, FTP und W/kg. Garmin liefert das Tempo im Rohwert in 0,1 m/s
  (0,408 = 4:05 min/km).
- **Abgebrochene Sync-Läufe:** Einträge, die nach einem Neustart auf „running“ hängen blieben,
  werden beim Start als „abgebrochen“ markiert. Schnellprüfungen stehen im Sync-Protokoll mit
  `kind = schnell`, aber nur, wenn sie etwas gefunden haben.
- **Trainingsplan und Wettkämpfe:** Tabellen `coach_plan` (Plan als Text, jede Änderung eine neue
  Version) und `races`. Werkzeuge `get_plan`, `set_plan`, `set_race`, `delete_race`.
- **Coach-Berichte:** Tabelle `coach_reports`. `pending_reports` zeigt Einheiten ohne Bericht und ob
  Morgen-/Abendbericht fehlen, `save_report` vermerkt einen Bericht mit Kurzfassung (pro Aktivität
  nur einmal), `list_reports` zeigt frühere Berichte.
- Zeitzonen-Daten im Image, damit der Zeitplan in lokaler Zeit läuft.
- Enthält 0.4.1 (Silvesterlauf-Fix).

## 0.4.1
- FIT-Auswertung: Absturz behoben, wenn in einer Datei bei einer Pulszone der Wert fehlt
  (betraf z. B. den Silvesterlauf 2025).
- Fehlerhafte Dateien werden nach einer Fehlerbehebung erneut versucht, ohne dass alle Dateien
  neu ausgewertet werden (`ERROR_RETRY_VERSION`).

## 0.4.0
Datenbasis vervollständigt: Alles, was in den Rohdaten steckt, kommt jetzt über die Werkzeuge raus.

- **Fehler behoben – Trainingsbereitschaft:** `daily_metrics` zeigte den letzten Wert des Tages
  statt des Morgenwerts. Jetzt:
  - `bereitschaft` = Wert nach dem Aufwachen, `bereitschaft_abend` = letzter Wert des Tages,
    jeweils mit Erholungszeit in Stunden
  - `bereitschaft_delta` (Tagesbilanz) und `regeneration_nacht` (Morgen minus Abend des Vortags)
  - Faktoren der Bereitschaft (Schlaf, HRV, Erholungszeit, Lastverhältnis, Stress- und Schlafverlauf)
  - Neues Werkzeug `readiness_detail`: alle Einträge eines Tages mit Uhrzeit und Anlass, dazu die Einheiten
- **Einheitliche Sportart:** Feld `sport_gruppe` (laufen, rad, schwimmen, kraft_hiit, gehen,
  kein_sport, sonstiges). Filter `sport` akzeptiert Gruppe oder Garmin-Typ. In `query_sql` als
  Funktion `sport_gruppe(type_key, fit_sport)`. Atemübungen zählen nicht mehr zu den Umfängen.
- **`data_overview`:** Sportarten über alle Aktivitäten statt nur über ausgewertete; Fortschritt
  der FIT-Auswertung (ausgewertet, offen, bis wann, Prozent).
- **Aktivitäten ohne FIT-Auswertung vollständig:** Pace, Last, Trainingseffekt, Kadenz, Watt,
  Höhenmeter, Laufdynamik und Pulszonen kommen aus Garmin Connect, Feld `quelle` zeigt woher.
  Leere Felder werden weggelassen.
- **Neue Aktivitätswerte aus Garmin Connect:** steigungsbereinigte Pace (`pace_gap`),
  Body-Battery-Verbrauch, Trainingseffekt-Label, schnellste Abschnitte, Bestzeit, Schweißverlust,
  Atemfrequenz, Temperatur, VO2max-Schätzung, Leistungszonen, Abschnitte (Block `garmin` in `get_activity`).
- **Pulszonen einheitlich** Zone 1–5 (FIT speichert zusätzlich Zeit unter Zone 1, jetzt eigenes Feld).
- **Neue Tageswerte:** Hauttemperatur-Abweichung, SpO2, Atemaussetzer, Stress im Schlaf,
  Body Battery beim Aufwachen und über Nacht, Atmung wach, Puls-Warnungen, Intensitätsminuten,
  Stressminuten je Stufe, Wachzeit, Unruhe, Schlafbeginn/-ende, Hitze-/Höhenakklimatisierung.
- **`weekly_summary`:** ISO-Wochen (Mo–So) mit Wochensumme, alle Aktivitäten (auch nicht
  ausgewertete), Pace beim Laufen, Notizen der Woche.
- **Notizen:** Neue Tabelle `notes` und Werkzeuge `add_note`, `list_notes`, `delete_note`
  (Kategorien krank, verletzt, reise, stress, sonstiges). Notizen erscheinen in `daily_metrics`,
  `weekly_summary` und `get_activity`. Erstes schreibendes Werkzeug; alles andere bleibt nur lesend.
- **Leistungsprofil aus Garmin Connect:** Täglicher Schnappschuss von Laktatschwelle,
  Wettkampfprognosen, Bestzeiten, Pulszonen, Endurance- und Hill-Score, geplanten Trainings und
  aktiven Trainingsplänen (Tabelle `garmin_snapshots`). Einmalig wird die Historie von
  Laktatschwelle, Prognosen und Endurance-Score nachgeladen. Neues Werkzeug `garmin_profile`.
- **Protokoll der MCP-Aufrufe:** Pro Aufruf eine Zeile mit Werkzeug, Zeilenzahl, Dauer, Status
  (ok, leer, abgeschnitten, Fehler) und Parametern (lange SQL nach 300 Zeichen gekürzt).
  Aufrufe über 2 s als Warnung. Zusätzlich Tabelle `mcp_log` (90 Tage).
- Neue Option `log_level` (`info` | `debug`); bei `debug` vollständige Parameter.

## 0.3.0
- **MCP-Server für Claude**: Claude kann jetzt direkt und nur lesend auf `garmin.db` zugreifen.
  - Werkzeuge: `data_overview`, `list_activities`, `get_activity`, `get_activity_timeseries`,
    `daily_metrics`, `weekly_summary`, `describe_schema`, `query_sql` (nur SELECT, schreibgeschützt)
  - Antworten in lesbaren Einheiten (km, min/km, Minuten, Schritte/min)
- **Weiterleitung über Home Assistant**: Das Add-on installiert die kleine Integration
  "Garmin Coach MCP" und richtet sie nach einem HA-Neustart selbst ein. Der MCP-Server ist
  dann über `https://<deine-ha-adresse>/api/webhook/<geheime-id>` erreichbar, ohne neuen Port.
- Die fertige Connector-URL steht im Protokoll des Add-ons.
- Sammler und MCP-Server laufen parallel; der MCP-Server funktioniert auch ohne Garmin-Login
  mit den bereits gespeicherten Daten.
- Neue Option `mcp_enabled` (Standard: an).
- Protokoll zeigt jede Abfrage von Claude in einer Zeile.

## 0.2.0
- **FIT-Auswertung**: Jede FIT-Datei wird automatisch ausgelesen, neue und bereits heruntergeladene.
- Neue Tabellen in `garmin.db`:
  - `fit_sessions`: Gesamtwerte pro Einheit (Distanz, Zeit, Tempo, Puls, Kadenz, Leistung, Laufdynamik, Atemfrequenz, Höhenmeter, Trainingseffekt, Trainingslast, RPE, Gefühl, Pulszonen, Geräte)
  - `fit_laps`: alle Werte pro Runde, inklusive Rundentyp, Workout-Schritt und Zieltempo
  - `fit_records`: Sekundenwerte (Puls, Tempo, Distanz, Höhe, Kadenz, Leistung, Bodenkontakt, vertikale Bewegung, Schrittlänge, Atmung, Temperatur)
  - `fit_workout_steps`, `fit_lengths` (Schwimmbahnen), `fit_sets` (Sätze)
  - `fit_errors`: Dateien, die sich nicht auswerten ließen
- Berechnete Werte pro Einheit: Effizienz (m/min je Herzschlag), Pa:HR-Drift, Tempo-Schwankung, Zieltreffer bei Intervallen, Pulserholung in den ersten 60 s der Trabpausen.
- Kadenz beim Laufen in Schritten pro Minute (FIT speichert pro Fuß).
- Protokoll zeigt pro ausgewerteter Datei eine Zusammenfassung.

## 0.1.4
- Changelog und Dokumentation werden jetzt im Add-on angezeigt.

## 0.1.3
- Ausführlicheres Protokoll: eine Zeile pro Tag mit HRV, Ruhepuls, Schlaf, Body Battery und Trainingsbereitschaft.
- Jede gespeicherte Aktivität erscheint mit Datum, Sportart, Name, Distanz und Dauer.
- Fortschrittsanzeige `[Tag/Gesamt]`, gut beim Backfill.

## 0.1.2
- Fehlen E-Mail oder Passwort, erscheint eine klare Meldung und das Add-on wartet, statt ständig neu zu starten.

## 0.1.1
- Build-Fix: Python-Basisimage direkt im Dockerfile, da neuere Supervisor-Versionen ein Image ohne Python verwenden.

## 0.1.0
- Erste Version: Tageswerte (HRV, Ruhepuls, Schlaf, Body Battery, Stress, Trainingsbereitschaft, Trainingsstatus, VO2max) und FIT-Dateien aller Aktivitäten werden lokal in `/share/garmin-coach/` gespeichert.
- Einmaliger Backfill über `backfill_days`.
- Unterstützung für MFA-Code beim ersten Login.
