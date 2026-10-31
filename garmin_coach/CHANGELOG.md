# Changelog

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
