# Changelog

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
