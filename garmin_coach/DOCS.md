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
