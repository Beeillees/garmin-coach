# Garmin Coach

Home-Assistant-Add-on, das deine Garmin-Daten **lokal** speichert:

- alle Aktivitäten als Original-**FIT-Dateien**
- tägliche Health-Werte: HRV, Ruhepuls, Schlaf, Body Battery, Stress, Trainingsbereitschaft, Trainingsstatus, VO2max

Alles landet in `/share/garmin-coach/` auf deinem Home Assistant:

```
/share/garmin-coach/
├── garmin.db          SQLite-Datenbank (Tageswerte, Aktivitäten, Sync-Protokoll)
└── fit/2026/…fit      FIT-Dateien, nach Jahr sortiert
```

## Installation

1. In Home Assistant: **Einstellungen → Apps → App-Store**, oben rechts **⋮ → Repositories**
2. `https://github.com/Beeillees/garmin-coach` hinzufügen
3. **Garmin Coach** installieren
4. Im Reiter **Konfiguration** E-Mail und Passwort deines Garmin-Kontos eintragen und speichern
5. Starten und das **Protokoll** beobachten

## Einstellungen

| Option | Bedeutung |
|---|---|
| `email`, `password` | Garmin-Connect-Zugang. Nach dem ersten Login werden Tokens gespeichert. |
| `mfa_code` | Nur falls Garmin beim ersten Login einen Code verlangt (siehe unten). |
| `days_back` | Wie viele Tage bei jedem Sync geprüft werden (Standard 30). |
| `backfill_days` | Nachladen alter Daten, z. B. `730` für zwei Jahre, `1500` für vier. Läuft einmal pro Wert; wird die Zahl später erhöht, werden nur die älteren Tage nachgeladen. |
| `sync_interval_hours` | Abstand zwischen zwei Syncs (Standard 6 Stunden). |
| `mcp_enabled` | MCP-Server für Claude an/aus (Standard: an). |
| `log_level` | `info` (Standard) oder `debug`. |

## Claude anbinden

Siehe Reiter **Dokumentation** im Add-on: Nach dem Update auf 0.3.0 einmal Home Assistant
neu starten, dann steht die Connector-URL im Protokoll.

## MFA

Verlangt Garmin einen Bestätigungscode, steht das im Protokoll. Dann entweder den Code
in die Option `mfa_code` eintragen und neu starten, oder ihn innerhalb von 10 Minuten
in die Datei `/share/garmin-coach/mfa_code.txt` schreiben.

## Fahrplan

1. ✅ Sammler: Tageswerte und FIT-Dateien lokal speichern
2. ✅ FIT-Auswertung: Gesamtwerte, Runden, Sekundenwerte, Drift, Effizienz, Zieltreffer, Pulserholung
3. Historie aus dem Garmin-Datenexport importieren
4. ✅ MCP-Server für Claude (über HA-Webhook, ohne neuen Port)
5. Trainings in den Garmin-Kalender schreiben
6. Täglicher Coach-Bericht
7. Dashboard in Home Assistant
