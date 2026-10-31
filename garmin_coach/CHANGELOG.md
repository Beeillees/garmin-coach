# Changelog

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
