"""Depot-Daten 0.1 – Prüfmodus.

Ruft für jeden Titel aus probe_titles alle infrage kommenden Eulerpool-Abrufe einmal auf,
speichert die Rohantworten unter /share/depot-daten/probe/ und legt je Titel einen Sensor
sensor.depotdaten_probe_<titel> mit einer Kurzfassung an. Der Schlüssel taucht nie im Protokoll auf.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")
SECRETS_FILE = Path("/homeassistant/secrets.yaml")
SECRETS_FILE_ALT = Path("/config/secrets.yaml")
OUT_DIR = Path("/share/depot-daten/probe")
BASE = "https://api.eulerpool.com/api/1/"
HA_API = "http://supervisor/core/api"

ENDPOINTS = [
    # Bewertung
    ("metrics", "equity/metrics/{id}"),
    ("key-figures", "equity/key-figures/{id}"),
    ("valuation-history", "equity/valuation-history/{id}"),
    ("estimates", "equity/estimates/{id}"),
    ("forecast", "equity/forecast/{id}"),
    ("market-cap", "equity/market-cap/{id}"),
    # Analysten
    ("price-target", "equity/price-target/{id}"),
    ("price-target-consensus", "equity/price-target-consensus/{id}"),
    ("price-target-news", "equity/price-target-news/{id}"),
    ("price-target-history", "equity-extended/price-target-history/{id}"),
    ("recommendations", "research/recommendations/{id}"),
    ("analyst-grades", "equity/analyst-grades/{id}"),
    ("upgrades", "equity/upgrades/{id}"),
    # Leerverkäufe
    ("short-interest-positions", "equity/short-interest-positions/{id}"),
    ("short-interest", "equity-extended/short-interest/{id}"),
    ("short-volume", "equity/short-volume/{id}"),
    # Termine
    ("earnings-by-symbol", "calendar/earnings-by-symbol/{id}"),
    ("earnings-calendar", "equity-extended/earnings-calendar/{id}"),
    ("dividends", "equity/dividends/{id}"),
    ("splits", "equity/splits/{id}"),
    # Insider
    ("insider-trades", "equity/insider-trades/{id}"),
    ("insider-trades-eu", "equity/insider-trades-eu/{id}"),
    # Qualität / Kurs
    ("quality-scores", "equity/quality-scores/{id}"),
    ("quotes", "equity/quotes/{id}"),
    ("returns", "equity/returns/{id}"),
    ("price-change", "equity/price-change/{id}"),
    ("shares-float", "equity/shares-float/{id}"),
    # Sonstiges
    ("profile", "equity/profile/{id}"),
    ("overview", "equity/overview/{id}"),
    ("peers", "equity/peers/{id}"),
    ("swot", "equity/swot/{id}"),
    ("segments", "equity/segments/{id}"),
    ("esg-rating", "equity/esg-rating/{id}"),
    ("coverage", "equity/coverage/{id}"),
    ("search-path", "equity/search/{id}"),
    ("search-query", "equity/search?q={id}"),
]

log = logging.getLogger("depot_daten")


def load_options():
    try:
        return json.loads(OPTIONS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def load_token(opts):
    tok = (opts.get("token") or "").strip()
    if tok:
        return tok, "Einstellungen"
    for f in (SECRETS_FILE, SECRETS_FILE_ALT):
        try:
            for line in f.read_text().splitlines():
                m = re.match(r"^\s*eulerpool_token\s*:\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip("'\""), f"secrets.yaml"
        except OSError:
            continue
    return None, None


def fetch(path, token):
    sep = "&" if "?" in path else "?"
    url = BASE + path + sep + urllib.parse.urlencode({"token": token})
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ha-depot-daten/0.1"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read() or b""
        status = e.code
    except Exception as e:  # Netz, Zeitüberschreitung
        return 0, str(e).replace(token, "***"), None, time.time() - t0
    text = body.decode("utf-8", "replace").replace(token, "***")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    return status, text, data, time.time() - t0


def shape(data):
    """Kurzbeschreibung der Struktur: Schlüssel bzw. Listenlänge + Schlüssel des ersten Eintrags."""
    if isinstance(data, dict):
        return "{" + ",".join(list(data.keys())[:14]) + "}"
    if isinstance(data, list):
        first = data[0] if data else None
        inner = ",".join(list(first.keys())[:12]) if isinstance(first, dict) else type(first).__name__
        return f"[{len(data)}× {inner}]"
    return type(data).__name__


def empty(data):
    if data is None:
        return True
    if isinstance(data, (list, dict)) and not data:
        return True
    return False


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def set_state(entity_id, state, attrs):
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if not tok:
        log.warning("Kein SUPERVISOR_TOKEN – Sensor %s nicht gesetzt", entity_id)
        return
    body = json.dumps({"state": state, "attributes": attrs}).encode()
    req = urllib.request.Request(f"{HA_API}/states/{entity_id}", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        log.warning("Sensor %s: %s", entity_id, e)


def probe(titles, token):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    calls = 0
    for title in titles:
        tdir = OUT_DIR / slug(title)
        tdir.mkdir(exist_ok=True)
        res = {}
        ok = 0
        for name, tmpl in ENDPOINTS:
            path = tmpl.format(id=urllib.parse.quote(title))
            status, text, data, dt = fetch(path, token)
            calls += 1
            (tdir / f"{name}.json").write_text(text if text else "")
            if status == 200 and not empty(data):
                ok += 1
                verdict = "ok"
            elif status == 200:
                verdict = "leer"
            else:
                verdict = f"HTTP {status}"
            excerpt = re.sub(r"\s+", " ", text or "")[:160]
            line = f"{verdict} · {len(text or '')} B · {shape(data) if data is not None else '-'}"
            res[name] = {"ergebnis": line, "auszug": excerpt}
            log.info("%-14s %-26s %s", title, name, line)
            time.sleep(0.4)
        summary[title] = res
        attrs = {"friendly_name": f"Depot-Daten Probe {title}", "titel": title,
                 "stand": datetime.now().isoformat(timespec="seconds"), "icon": "mdi:test-tube"}
        for name, r in res.items():
            attrs[name] = r["ergebnis"]
            attrs[f"{name}_auszug"] = r["auszug"]
        set_state(f"sensor.depotdaten_probe_{slug(title)}", f"{ok}/{len(ENDPOINTS)}", attrs)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    log.info("Fertig: %d Abrufe, Rohdaten in %s", calls, OUT_DIR)
    return calls


def main():
    opts = load_options()
    logging.basicConfig(level=getattr(logging, str(opts.get("log_level", "info")).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    token, src = load_token(opts)
    if not token:
        log.error("Kein Eulerpool-Schlüssel gefunden (Feld token leer und kein eulerpool_token in secrets.yaml).")
        set_state("sensor.depotdaten_status", "kein Schlüssel", {"friendly_name": "Depot-Daten Status"})
        return
    log.info("Schlüssel aus %s geladen.", src)
    titles = opts.get("probe_titles") or ["NVDA"]
    set_state("sensor.depotdaten_status", "Probe läuft", {"friendly_name": "Depot-Daten Status", "icon": "mdi:database-sync"})
    calls = probe(titles, token)
    set_state("sensor.depotdaten_status", "Probe fertig",
              {"friendly_name": "Depot-Daten Status", "icon": "mdi:database-check", "abrufe": calls,
               "stand": datetime.now().isoformat(timespec="seconds")})


if __name__ == "__main__":
    main()
