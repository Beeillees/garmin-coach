# Depot-Daten

Holt Kennzahlen, Kursziele, Leerverkäufe und Termine von [Eulerpool](https://eulerpool.com) nach Home Assistant.

## Schlüssel
Leer lassen, dann liest die App `eulerpool_token:` aus deiner `secrets.yaml`.
Alternativ hier im Feld **token** eintragen.

## Version 0.1 – Prüfmodus
Die App ruft für die Titel unter **probe_titles** alle Eulerpool-Abrufe einmal auf und beendet sich dann.
Ergebnis: Protokoll der App, Sensoren `sensor.depotdaten_probe_<titel>` und die Rohdaten in
`/share/depot-daten/probe/`.
