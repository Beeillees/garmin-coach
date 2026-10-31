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
| `sync_log` | Protokoll der Sync-Läufe |

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

## Einstellungen

| Option | Bedeutung |
|---|---|
| `email`, `password` | Garmin-Connect-Zugang. Nach dem ersten Login werden Tokens gespeichert. |
| `mfa_code` | Nur falls Garmin beim ersten Login einen Code verlangt. |
| `days_back` | Wie viele Tage bei jedem Sync geprüft werden (Standard 30). |
| `backfill_days` | Einmaliges Nachladen alter Daten, z. B. `730` für zwei Jahre. Läuft nur einmal. |
| `sync_interval_hours` | Abstand zwischen zwei Syncs (Standard 6 Stunden). |

## MFA

Verlangt Garmin einen Bestätigungscode, steht das im Protokoll. Dann entweder den Code
in die Option `mfa_code` eintragen und neu starten, oder ihn innerhalb von 10 Minuten
in die Datei `/share/garmin-coach/mfa_code.txt` schreiben.

## Protokoll

Pro Tag erscheint eine Zeile mit den wichtigsten Kennzahlen, pro neuer Aktivität eine
Zeile mit Datum, Sportart, Distanz und Dauer. Einzelne fehlende Werte (z. B. Tage ohne
Uhr) erscheinen als Warnung und stoppen den Sync nicht.
