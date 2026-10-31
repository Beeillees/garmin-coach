# Garmin Coach

Speichert deine Garmin-Daten lokal auf deinem Home Assistant:

- alle Aktivitäten als Original-**FIT-Dateien**
- tägliche Health-Werte: HRV, Ruhepuls, Schlaf, Body Battery, Stress, Trainingsbereitschaft, Trainingsstatus, VO2max

## Speicherort

```
/share/garmin-coach/
├── garmin.db          SQLite-Datenbank (Tageswerte, Aktivitäten, Sync-Protokoll)
└── fit/<Jahr>/…fit    FIT-Dateien, nach Jahr sortiert
```

Die Daten werden nie gelöscht. Der Ordner liegt im Backup von Home Assistant, wenn du "Share" mitsicherst.

## Tabellen in `garmin.db`

| Tabelle | Inhalt |
|---|---|
| `daily_raw` | Tageswerte von Garmin als Rohdaten (HRV, Schlaf, Ruhepuls, Body Battery, Stress, Trainingsbereitschaft, Trainingsstatus, VO2max) |
| `activities` | Aktivitätsliste von Garmin mit Pfad zur FIT-Datei |
| `fit_sessions` | Gesamtwerte pro Einheit aus der FIT-Datei plus berechnete Werte |
| `fit_laps` | Werte pro Runde, inklusive Rundentyp, Workout-Schritt und Zieltempo |
| `fit_records` | Sekundenwerte: Puls, Tempo, Distanz, Höhe, Kadenz, Leistung, Laufdynamik, Atmung, Temperatur |
| `fit_workout_steps` | Schritte eines geplanten Trainings mit Zielvorgaben |
| `fit_lengths` | Schwimmbahnen |
| `fit_sets` | Sätze (Kraft, HIIT) |
| `fit_errors` | FIT-Dateien, die sich nicht auswerten ließen |
| `garmin_snapshots` | Täglicher Schnappschuss: Laktatschwelle, Wettkampfprognosen, Bestzeiten, Pulszonen, Endurance-/Hill-Score, geplante Trainings, Trainingspläne |
| `notes` | Notizen zu Krankheit, Verletzung, Reise, Stress |
| `coach_plan` | Trainingsplan als Text, jede Änderung eine neue Version |
| `races` | Wettkämpfe mit Datum, Distanz, Priorität, Status |
| `coach_reports` | Berichte des Coaches (Morgen, Training, Abend) mit Kurzfassung |
| `mcp_log` | Protokoll der Abfragen von Claude (90 Tage) |
| `sync_log` | Protokoll der Sync-Läufe (`kind`: voll oder schnell) |

### Einheiten

- Tempo in m/s, Distanz in m, Zeiten in s
- Kadenz beim Laufen in Schritten pro Minute, beim Radfahren in Umdrehungen pro Minute
- Schrittlänge und vertikale Bewegung in mm, Bodenkontaktzeit in ms

### Berechnete Werte (`fit_sessions`)

| Spalte | Bedeutung |
|---|---|
| `efficiency` | Meter pro Minute je Herzschlag, nur Bewegungszeit |
| `decoupling_pct` | Pa:HR-Drift: Effizienzverlust von der ersten zur zweiten Hälfte. Unter ~5 % gilt als gute aerobe Ausdauer. Nur bei gleichmäßigen Läufen aussagekräftig. |
| `speed_cv_pct` | Tempo-Schwankung. Hoch bei Intervallen, dann ist die Drift nicht aussagekräftig. |
| `target_hit_pct`, `target_laps` | Anteil der Belastungsrunden im Zieltempo-Bereich eines geplanten Trainings |
| `hr_recovery_60s` | Mittlerer Pulsabfall in den ersten 60 s der Trabpausen |

## Claude anbinden (MCP)

Ab Version 0.3.0 kann Claude deine Daten direkt abfragen, nur lesend.

1. Add-on auf 0.3.0 aktualisieren und starten.
2. Im Protokoll erscheint der Hinweis, Home Assistant **einmal komplett neu zu starten**
   (Einstellungen → System → oben rechts → Neu starten). Das ist nötig, damit HA die
   Integration "Garmin Coach MCP" lädt.
3. Nach dem Neustart richtet das Add-on die Integration selbst ein. Nach spätestens zwei
   Minuten steht im Protokoll die **URL für den Claude-Connector**:
   `https://<deine-ha-adresse>/api/webhook/garmin_coach_...`
4. Auf claude.ai unter **Anpassen → Konnektoren → + → Benutzerdefinierten Konnektor
   hinzufügen** einen Connector "Garmin Coach" mit dieser URL anlegen und verbinden.

Unter Einstellungen → Geräte & Dienste erscheint die Integration "Garmin Coach MCP".
Die Warnung im HA-Protokoll, dass eine benutzerdefinierte Integration nicht getestet ist,
ist normal.

**Die URL ist geheim.** Wer sie kennt, kann deine Trainings- und Gesundheitsdaten lesen.

### Werkzeuge für Claude

| Werkzeug | Inhalt |
|---|---|
| `data_overview` | Datenstand, Zeiträume, Sportarten, letzter Sync |
| `list_activities` | Einheiten mit Kernwerten, filterbar nach Zeitraum und Sportart |
| `get_activity` | Alle Details einer Einheit inkl. Runden, Workout-Schritten, Bahnen, Sätzen, Zusatzwerten aus Garmin Connect |
| `get_activity_timeseries` | Verläufe (z. B. Pulskurve) in wählbarer Auflösung |
| `daily_metrics` | HRV, Ruhepuls, Schlaf, Hauttemperatur, Body Battery, Stress, Bereitschaft morgens/abends, Trainingsstatus, VO2max, Notizen |
| `readiness_detail` | Alle Bereitschafts-Einträge eines Tages mit Anlass und Einheiten |
| `weekly_summary` | Wochenumfänge pro Sportart-Gruppe mit Wochensumme und Notizen |
| `garmin_profile` | Laktatschwelle, Wettkampfprognosen, Bestzeiten, Pulszonen, geplante Trainings |
| `add_note`, `list_notes`, `delete_note` | Notizen anlegen, anzeigen, löschen |
| `get_plan`, `set_plan` | Trainingsplan lesen und speichern, mit Wochentag und Wettkämpfen |
| `set_race`, `delete_race` | Wettkämpfe anlegen, ändern, löschen |
| `pending_reports`, `save_report`, `list_reports` | Welche Einheiten noch keinen Bericht haben; Berichte vermerken und anzeigen |
| `sync_now` | Sofort-Sync (`schnell` oder `voll`) und auf das Ergebnis warten |
| `describe_schema`, `query_sql` | Freie, schreibgeschützte SQL-Abfragen |

## Einstellungen

| Option | Bedeutung |
|---|---|
| `email`, `password` | Garmin-Connect-Zugang. Nach dem ersten Login werden Tokens gespeichert. |
| `mfa_code` | Nur falls Garmin beim ersten Login einen Code verlangt. |
| `days_back` | Wie viele Tage bei jedem Sync geprüft werden (Standard 30). |
| `backfill_days` | Einmaliges Nachladen alter Daten, z. B. `730` für zwei Jahre. Läuft nur einmal. |
| `sync_times` | Uhrzeiten für den vollen Sync in lokaler Zeit (Standard `05:15`, `19:45`). |
| `activity_check_minutes` | Abstand der Schnellprüfung auf neue Aktivitäten in Minuten (Standard 20, `0` = aus). |
| `activity_check_window` | Zeitfenster der Schnellprüfung (Standard `06:00-22:00`). |
| `mcp_enabled` | MCP-Server für Claude an/aus (Standard: an). |
| `log_level` | `info` (Standard) oder `debug` (vollständige Parameter jeder Abfrage). |

## MFA

Verlangt Garmin einen Bestätigungscode, steht das im Protokoll. Dann entweder den Code
in die Option `mfa_code` eintragen und neu starten, oder ihn innerhalb von 10 Minuten
in die Datei `/share/garmin-coach/mfa_code.txt` schreiben.

## Protokoll

Pro Tag erscheint eine Zeile mit den wichtigsten Kennzahlen, pro neuer Aktivität eine
Zeile mit Datum, Sportart, Distanz und Dauer. Einzelne fehlende Werte (z. B. Tage ohne
Uhr) erscheinen als Warnung und stoppen den Sync nicht.

Jede Abfrage von Claude erscheint als eine Zeile, z. B.
`MCP query_sql | 7 Zeilen | 42 ms | ok | sql=SELECT ...`. Status ist `ok`, `leer`,
`abgeschnitten` oder `Fehler`; Abfragen über 2 Sekunden erscheinen als Warnung.
