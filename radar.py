#!/usr/bin/env python3
"""
Ausschreibungs-Radar: holt Bekanntmachungen aus mehreren oeffentlichen Quellen,
bewertet sie anhand eines Suchprofils und schreibt eine verschluesselte,
statische HTML-Seite.

Das Suchprofil steht NICHT im Repository. Es kommt aus
  - der Umgebungsvariablen RADAR_PROFIL (JSON-Text, in GitHub als Secret), oder
  - einer lokalen Datei, angegeben mit --profil (per .gitignore ausgeschlossen).
Ohne beides wird beispielprofil.json verwendet (neutrales Beispiel).

Das Passwort fuer die Seite kommt aus der Umgebungsvariablen RADAR_PASSWORT.

  python3 radar.py                      # normaler Lauf
  python3 radar.py --demo               # Beispieldaten, ohne Netz
  python3 radar.py --profil mein.json   # lokales Profil statt Secret

Protokollausgaben enthalten bewusst nur Zahlen und Quellennamen, keine Titel,
Auftraggeber oder Stichworte.
"""

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

HIER = os.path.dirname(os.path.abspath(__file__))
UA = "ausschreibungs-radar/2.0"

# --------------------------------------------------------------------------
# Technische Grundeinstellungen. Enthalten nichts Profilbezogenes.
# --------------------------------------------------------------------------

GRUNDEINSTELLUNGEN = {
    "zeitfenster_tage": 21,
    "aufbewahrung_tage": 60,
    "quellen": {
        "ted": {
            "aktiv": True,
            "name": "TED (EU-weit)",
            "endpoint": "https://api.ted.europa.eu/v3/notices/search",
            "limit": 250,
        },
        "doe": {
            "aktiv": True,
            "name": "Datenservice Öffentlicher Einkauf",
            # Bulk-Download, ein ZIP je Tag, eine Datei je Bekanntmachung.
            "endpoint": "https://oeffentlichevergabe.de/api/notice-exports",
            "format": "ocds.zip",
            # OCDS enthaelt keine Fristen; fuer gespeicherte Treffer wird das
            # eForms-Paket desselben Tages nachgeladen.
            "fristen_aus_eforms": True,
        },
        "cosinex": {
            "aktiv": True,
            # Open-Data-Schnittstelle der cosinex-Vergabemarktplaetze.
            "nur_national": True,       # EU-weite kommen schon ueber TED und Datenservice
            "ohne_ergebnisse": True,    # Vergabebekanntmachungen ueberspringen
            "satelliten": [
                {"name": "Vergabe.NRW Land",
                 "url": "https://www.evergabe.nrw.de/VMPSatellite/opendata?id=0bb4c8bc-514f-49bf-a490-d93103c377f0"},
                {"name": "Vergabe.NRW Metropole Ruhr",
                 "url": "https://www.vergabe.metropoleruhr.de/VMPSatellite/opendata?id=4985b1a5-d2e8-4611-a4c6-20694a130bb3"},
                {"name": "Vergabe.NRW Westfalen",
                 "url": "https://www.vergabe-westfalen.de/VMPSatellite/opendata?id=d5a5e23b-5059-4a2d-9c2f-409de53790f6"},
                {"name": "Vergabe.NRW Rheinland",
                 "url": "https://www.vmp-rheinland.de/VMPSatellite/opendata?id=35eb26cf-9d6c-4780-903a-89059f382b03"},
                {"name": "Vergabe.NRW Stadt Köln",
                 "url": "https://vergabe.stadt-koeln.de/VMPSatellite/opendata?id=f8f65333-9078-451c-8628-09376a7fbfcb"},
                {"name": "Vergabe.NRW Bau- und Liegenschaftsbetrieb",
                 "url": "https://evergabe.blb.nrw.de/Vergabe/opendata?id=f33b7b4a-082d-4bbb-b176-d8b4ebf75a51"},
            ],
        },
        "feeds": {
            "aktiv": True,
            # RSS/Atom der Landesplattformen. In 'muster' stehen regulaere Ausdruecke
            # fuer Felder im Beschreibungstext; die erste Klammergruppe zaehlt.
            "liste": [
                {
                    "name": "Vergabeplattform Berlin",
                    "url": "https://www.berlin.de/vergabeplattform/veroeffentlichungen/"
                           "bekanntmachungen/feed.rss",
                    "cpv_aus_titel": True,
                    "muster": {
                        "frist": r"Ablauf (?:der )?Angebotsfrist:\s*(\d{2}\.\d{2}\.\d{4})",
                        "frist2": r"Eröffnungstermin:\s*(\d{2}\.\d{2}\.\d{4})",
                        "ort": r"Ausführungsort:\s*([^\n]+)",
                        "art": r"Verfahrensart:\s*([^\n]+)",
                        "veroeffentlicht": r"Online seit:\s*(\d{2}\.\d{2}\.\d{4})",
                    },
                }
            ],
        },
    },
}

# Was ein Profil enthalten kann. Fehlende Teile gelten als leer.
PROFIL_STANDARD = {
    "mindestpunkte": 8,
    "speicher_mindestpunkte": 4,
    "leistung_stark": {},
    "leistung_schwach": {},
    "thema_kern": {},
    "thema_erweitert": {},
    "kontext": [],
    "abzug": {},
    "falsche_freunde": {},
    "ausschluss_titel": [],
    "cpv_bonus": {},
    "cpv_abzug": {},
    "cpv_leistung": [],
    "cpv_thema": [],
    "region_bonus": {},
    "ted_laender": ["DEU"],
    "ted_volltext": [],
}


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def norm(text):
    """Kleinschreibung, Umlaute aufgeloest, damit 'Wärme' und 'waerme' gleich matchen."""
    if not text:
        return ""
    t = str(text).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t)


def pick(value):
    """TED liefert mehrsprachige Felder als dict oder list. Einen String herausziehen."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return pick(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("deu", "ger", "de", "eng", "en"):
            if key in value:
                return pick(value[key])
        for v in value.values():
            got = pick(v)
            if got:
                return got
    return str(value)


def parse_date(value):
    s = pick(value)
    if not s:
        return None
    s = s.strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(s[:10] if pattern != "%Y%m%d" else s[:8], pattern).date()
        except ValueError:
            pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def http_json(url, payload=None, timeout=45, accept="*/*", versuche=3):
    """Bei HTTP 429 warten und erneut versuchen. TED drosselt schnell."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Language": "de,en;q=0.8"}
    if data:
        headers["Content-Type"] = "application/json"
    roh = ""
    for versuch in range(versuche):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                roh = resp.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and versuch < versuche - 1:
                wartezeit = 10
                try:
                    wartezeit = max(3, min(120, int(e.headers.get("Retry-After", "10"))))
                except (TypeError, ValueError):
                    pass
                time.sleep(wartezeit * (versuch + 1))
                continue
            raise
    try:
        return json.loads(roh)
    except json.JSONDecodeError:
        raise ValueError("Antwort ist kein JSON")


def http_roh(url, timeout=30, accept="*/*", grenze=4000):
    """Rohantwort: Status, Header, Rumpf."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": accept, "Accept-Language": "de,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rumpf = resp.read() if grenze is None else resp.read(grenze)
            return resp.status, dict(resp.headers), rumpf
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(1000)
    except Exception as e:
        return None, {}, str(e).encode()


def http_text(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def warn(msg):
    print("  ! " + msg, file=sys.stderr)


def _lokal(element, name):
    """Erstes Unterelement mit diesem lokalen Namen, unabhaengig vom Namensraum."""
    for e in element.iter():
        if e.tag.split("}")[-1] == name:
            return e
    return None


def _lokal_alle(element, name):
    return [e for e in element.iter() if e.tag.split("}")[-1] == name]


def _text(element):
    if element is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(t for t in element.itertext())).strip()


# --------------------------------------------------------------------------
# Verschluesselung (AES-256-GCM, Schluessel per PBKDF2-SHA256 aus dem Passwort).
# Das Format ist mit der Web Crypto API im Browser kompatibel.
# --------------------------------------------------------------------------

PBKDF2_RUNDEN = 600000


def _schluessel(passwort, salz):
    return hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salz, PBKDF2_RUNDEN, dklen=32)


def verschluesseln(klartext_bytes, passwort):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salz, iv = os.urandom(16), os.urandom(12)
    geheim = AESGCM(_schluessel(passwort, salz)).encrypt(iv, klartext_bytes, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "runden": PBKDF2_RUNDEN, "salz": b64(salz), "iv": b64(iv), "daten": b64(geheim)}


def entschluesseln(paket, passwort):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    d = lambda k: base64.b64decode(paket[k])
    schluessel = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), d("salz"),
                                     int(paket.get("runden", PBKDF2_RUNDEN)), dklen=32)
    return AESGCM(schluessel).decrypt(d("iv"), d("daten"), None)


# --------------------------------------------------------------------------
# Profil und Arbeitsdaten
# --------------------------------------------------------------------------

def profil_laden(pfad):
    if pfad:
        with open(pfad, encoding="utf-8") as f:
            roh, herkunft = json.load(f), "lokale Datei"
    elif os.environ.get("RADAR_PROFIL", "").strip():
        try:
            roh, herkunft = json.loads(os.environ["RADAR_PROFIL"]), "Secret"
        except json.JSONDecodeError as e:
            # Position statt Inhalt melden, damit nichts aus dem Profil ins Protokoll geraet
            raise SystemExit("Profil im Secret ist kein gueltiges JSON (Zeile %d, Spalte %d)."
                             % (e.lineno, e.colno))
    else:
        if os.environ.get("GITHUB_ACTIONS"):
            raise SystemExit("Secret RADAR_PROFIL fehlt. Lauf abgebrochen.")
        with open(os.path.join(HIER, "beispielprofil.json"), encoding="utf-8") as f:
            roh, herkunft = json.load(f), "Beispielprofil"
    profil = json.loads(json.dumps(PROFIL_STANDARD))
    for k, v in roh.items():
        if not k.startswith("_"):
            profil[k] = v
    print("Profil: %s" % herkunft)
    return profil


class Arbeitsdaten:
    """Verzeichnis fuer Zwischenspeicher und Bestand. Liegt nie im Repository;
    in GitHub Actions wird es ueber den Actions-Cache von Lauf zu Lauf getragen.
    Der Bestand enthaelt gefilterte Treffer und wird deshalb verschluesselt."""

    def __init__(self, ordner, passwort):
        self.ordner = ordner
        self.passwort = passwort
        os.makedirs(ordner, exist_ok=True)

    def pfad(self, *teile):
        p = os.path.join(self.ordner, *teile)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def json_lesen(self, name, standard=None):
        try:
            with open(self.pfad(name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return standard

    def json_schreiben(self, name, inhalt):
        ziel = self.pfad(name)
        with open(ziel + ".neu", "w", encoding="utf-8") as f:
            json.dump(inhalt, f, ensure_ascii=False)
        os.replace(ziel + ".neu", ziel)

    def bestand_laden(self):
        paket = self.json_lesen("bestand.enc.json")
        if not paket:
            return {}
        if not self.passwort:
            warn("Kein Passwort, Bestand kann nicht gelesen werden. Beginne neu.")
            return {}
        try:
            return json.loads(entschluesseln(paket, self.passwort)).get("eintraege", {})
        except Exception:
            warn("Bestand nicht entschluesselbar (Passwort geaendert?). Beginne neu.")
            return {}

    def bestand_speichern(self, eintraege):
        if not self.passwort:
            warn("Kein Passwort, Bestand wird nicht gespeichert.")
            return
        inhalt = json.dumps({"stand": dt.datetime.now().isoformat(timespec="seconds"),
                             "eintraege": eintraege}, ensure_ascii=False).encode("utf-8")
        self.json_schreiben("bestand.enc.json", verschluesseln(inhalt, self.passwort))


# --------------------------------------------------------------------------
# Quelle 1: TED
# --------------------------------------------------------------------------

# TED benennt Felder nach dem eForms-Vokabular, die Namen aendern sich zwischen
# SDK-Versionen. Pro Rolle werden Kandidaten probiert und gueltige gemerkt.
TED_KANDIDATEN = {
    "nummer":        ["publication-number"],
    "datum":         ["publication-date"],
    "titel":         ["notice-title", "title-proc", "title-lot", "notice-title-lot"],
    "beschreibung":  ["description-proc", "description-lot", "notice-description"],
    "auftraggeber":  ["buyer-name", "organisation-name-buyer", "organisation-name"],
    "ort":           ["place-of-performance", "place-performance-country-lot",
                      "place-performance-town-lot", "buyer-country"],
    "cpv":           ["classification-cpv", "main-classification-proc",
                      "classification-cpv-lot", "additional-classification-proc"],
    "frist":         ["deadline-receipt-tender-date-lot", "deadline-receipt-tender",
                      "deadline-date-lot", "deadline"],
    "frist_teilnahme": ["deadline-receipt-request-date-lot", "deadline-receipt-request"],
    "art":           ["notice-type", "form-type", "procedure-type"],
}

TED_ARTEN = {
    "cn-standard": "Auftragsbekanntmachung",
    "cn-social": "Auftragsbekanntmachung (soziale Dienstleistungen)",
    "cn-desg": "Wettbewerbsbekanntmachung",
    "pin-only": "Vorinformation", "pin-buyer": "Vorinformation",
    "pin-rtl": "Vorinformation zur Fristverkürzung", "pin-tran": "Vorinformation",
    "can-standard": "Vergabebekanntmachung", "can-social": "Vergabebekanntmachung",
    "can-modif": "Auftragsänderung", "veat": "Freiwillige Ex-ante-Transparenz",
    "corr": "Berichtigung", "qu-sy": "Qualifizierungssystem",
}


def art_lesbar(art):
    return TED_ARTEN.get((art or "").strip().lower(), art or "")


def ted_felder_pruefen(endpoint, daten, bekannt=None, pause=2.0):
    """Jeden Kandidaten einzeln anfragen. Was 200 liefert, ist ein gueltiger Feldname.
    Zwischenstaende werden sofort gespeichert."""
    vorgestern = (dt.date.today() - dt.timedelta(days=2)).strftime("%Y%m%d")
    basis = {"query": "publication-date>=%s" % vorgestern, "limit": 1, "scope": "ACTIVE"}
    gueltig = dict(bekannt or {})
    offen = [r for r in TED_KANDIDATEN if r not in gueltig]
    if not offen:
        return gueltig
    print("  Pruefe %d Feldrollen ..." % len(offen))
    for rolle in offen:
        for feld in TED_KANDIDATEN[rolle]:
            time.sleep(pause)
            try:
                http_json(endpoint, dict(basis, fields=["publication-number", feld]), timeout=30)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    continue
                warn("Feldpruefung angehalten (HTTP %s)." % e.code)
                return gueltig
            except Exception as e:
                warn("Feldpruefung angehalten: %s" % type(e).__name__)
                return gueltig
            gueltig[rolle] = feld
            daten.json_schreiben("ted_felder.json", gueltig)
            break
    return {k: v for k, v in gueltig.items() if v}


def hole_ted(cfg, profil, daten, seit):
    q = cfg["quellen"]["ted"]
    woerter = profil.get("ted_volltext") or []
    if not woerter:
        warn("Kein TED-Volltext im Profil, Quelle uebersprungen.")
        return []
    laender = " OR ".join("buyer-country=%s" % c for c in profil.get("ted_laender") or ["DEU"])
    volltext = " OR ".join('FT~"%s"' % w.replace('"', "") for w in woerter)
    query = "(%s) AND (%s) AND publication-date>=%s SORT BY publication-date DESC" % (
        laender, volltext, seit.strftime("%Y%m%d"))

    felder = daten.json_lesen("ted_felder.json", {}) or {}
    if len(felder) < len(TED_KANDIDATEN):
        felder = ted_felder_pruefen(q["endpoint"], daten, felder)
    if not felder:
        warn("TED liefert keine nutzbaren Felder, Quelle uebersprungen.")
        return []

    payload = {"query": query, "fields": sorted(set(felder.values())),
               "limit": q["limit"], "scope": "ACTIVE", "paginationMode": "ITERATION"}
    try:
        antwort = http_json(q["endpoint"], payload)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            warn("TED antwortet mit HTTP %s" % e.code)
            return []
        warn("TED lehnt die gespeicherte Feldliste ab, pruefe erneut.")
        felder = ted_felder_pruefen(q["endpoint"], daten, None)
        if not felder:
            return []
        payload["fields"] = sorted(set(felder.values()))
        try:
            antwort = http_json(q["endpoint"], payload)
        except Exception as e2:
            warn("TED nicht erreichbar: %s" % type(e2).__name__)
            return []
    except Exception as e:
        warn("TED nicht erreichbar: %s" % type(e).__name__)
        return []

    def wert(n, rolle):
        feld = felder.get(rolle)
        return n.get(feld) if feld else None

    treffer = []
    for n in antwort.get("notices", []) or []:
        nummer = pick(wert(n, "nummer"))
        cpvs = wert(n, "cpv") or []
        if isinstance(cpvs, (str, dict)):
            cpvs = [cpvs]
        frist = parse_date(wert(n, "frist"))
        frist_art = "Angebotsfrist"
        if not frist:
            frist = parse_date(wert(n, "frist_teilnahme"))
            frist_art = "Teilnahmefrist" if frist else ""
        treffer.append({
            "id": "ted:" + str(nummer),
            "titel": pick(wert(n, "titel")),
            "beschreibung": pick(wert(n, "beschreibung")),
            "auftraggeber": pick(wert(n, "auftraggeber")),
            "ort": pick(wert(n, "ort")),
            "cpv": list(dict.fromkeys(pick(c) for c in cpvs)),
            "veroeffentlicht": (parse_date(wert(n, "datum")) or dt.date.today()).isoformat(),
            "frist": frist.isoformat() if frist else "",
            "frist_art": frist_art,
            "art": art_lesbar(pick(wert(n, "art"))),
            "quelle": q["name"],
            "url": "https://ted.europa.eu/de/notice/-/detail/%s" % nummer if nummer else "",
        })
    return treffer


# --------------------------------------------------------------------------
# Quelle 2: Datenservice Oeffentlicher Einkauf (Bekanntmachungsservice)
# --------------------------------------------------------------------------

MIME_ZU_FORMAT = {
    "eforms.zip": "application/vnd.bekanntmachungsservice.eforms.zip+zip",
    "ocds.zip": "application/vnd.bekanntmachungsservice.ocds.zip+zip",
    "csv.zip": "application/vnd.bekanntmachungsservice.csv.zip+zip",
}
DOE_FRUEHESTENS = dt.date(2022, 12, 1)
NUTS_MUSTER = re.compile(r"^DE[1-9A-G][0-9A-Z]{0,3}$")


def doe_entpacken(rumpf):
    aus = []
    if rumpf[:2] != b"PK":
        return aus
    try:
        with zipfile.ZipFile(io.BytesIO(rumpf)) as archiv:
            for name in archiv.namelist():
                if name.endswith("/"):
                    continue
                inhalt = archiv.read(name)
                if name.lower().endswith(".json"):
                    try:
                        aus.append(("ocds", name, json.loads(inhalt.decode("utf-8", "replace"))))
                    except json.JSONDecodeError:
                        continue
                elif name.lower().endswith(".xml"):
                    aus.append(("xml", name, inhalt))
    except zipfile.BadZipFile:
        warn("Antwort war kein lesbares ZIP.")
    return aus


def eforms_auswerten(inhalt, dateiname, quellenname):
    """Minimale eForms-Auswertung (auch fuer EU-Bekanntmachungen der cosinex-Plattformen)."""
    try:
        wurzel = ET.fromstring(inhalt)
    except ET.ParseError:
        return None
    projekt = _lokal(wurzel, "ProcurementProject")
    titel = beschreibung = ""
    if projekt is not None:
        n, b = _lokal(projekt, "Name"), _lokal(projekt, "Description")
        titel = (n.text or "").strip() if n is not None else ""
        beschreibung = (b.text or "").strip() if b is not None else ""
    if not titel:
        return None
    cpvs = [e.text.strip() for e in _lokal_alle(wurzel, "ItemClassificationCode")
            if e.text and e.text.strip().isdigit()]
    auftraggeber = ""
    partei = _lokal(wurzel, "ContractingParty")
    if partei is None:
        partei = _lokal(wurzel, "Party")
    if partei is not None:
        pn = _lokal(partei, "PartyName")
        if pn is not None:
            n = _lokal(pn, "Name")
            auftraggeber = (n.text or "").strip() if n is not None else ""
    frist, frist_art = _eforms_fristen(wurzel)
    ort = ""
    adresse = _lokal(wurzel, "RealizedLocation")
    if adresse is None:
        adresse = _lokal(wurzel, "PostalAddress")
    if adresse is not None:
        stadt = _lokal(adresse, "CityName")
        if stadt is not None and stadt.text:
            ort = stadt.text.strip()
    kennung = os.path.splitext(os.path.basename(dateiname))[0]
    return {
        "id": "doe:" + kennung, "titel": titel, "beschreibung": beschreibung,
        "auftraggeber": auftraggeber, "ort": ort, "cpv": cpvs,
        "veroeffentlicht": dt.date.today().isoformat(),
        "frist": frist.isoformat() if frist else "", "frist_art": frist_art,
        "art": "", "quelle": quellenname,
        "url": "https://oeffentlichevergabe.de/ui/de/suche?query=" + urllib.parse.quote(kennung),
    }


def _ocds_kaeuferadresse(r):
    """Die Adresse steht in OCDS nicht beim buyer, sondern in parties[]."""
    buyer = r.get("buyer") or {}
    kennung = buyer.get("id")
    for partei in r.get("parties") or []:
        if not isinstance(partei, dict):
            continue
        if (kennung and partei.get("id") == kennung) or "buyer" in (partei.get("roles") or []):
            return partei.get("address") or {}
    return buyer.get("address") or {}


def _nuts_suchen(objekt, tiefe=0):
    if tiefe > 10:
        return ""
    if isinstance(objekt, str):
        return objekt if NUTS_MUSTER.match(objekt) else ""
    werte = objekt.values() if isinstance(objekt, dict) else objekt if isinstance(objekt, list) else []
    for w in werte:
        gefunden = _nuts_suchen(w, tiefe + 1)
        if gefunden:
            return gefunden
    return ""


def _ocds_frist(tender):
    """Fristen stehen meist je Los in tender.lots[].tenderPeriod. Frueheste nehmen."""
    fristen = []

    def suche(objekt, tiefe=0):
        if tiefe > 6:
            return
        if isinstance(objekt, dict):
            for schluessel, wert in objekt.items():
                if schluessel == "tenderPeriod" and isinstance(wert, dict):
                    d = parse_date(wert.get("endDate"))
                    if d:
                        fristen.append(d)
                suche(wert, tiefe + 1)
        elif isinstance(objekt, list):
            for eintrag in objekt:
                suche(eintrag, tiefe + 1)

    suche(tender)
    return min(fristen) if fristen else None


def doe_aus_ocds(paket, dateiname, quellenname, tag):
    treffer = []
    releases = paket if isinstance(paket, list) else (paket.get("releases") or [])
    for r in releases:
        if not isinstance(r, dict):
            continue
        tender = r.get("tender") or {}
        buyer = r.get("buyer") or {}
        cpvs = []
        haupt = (tender.get("classification") or {}).get("id")
        if haupt:
            cpvs.append(str(haupt))
        for item in tender.get("items") or []:
            cid = (item.get("classification") or {}).get("id")
            if cid:
                cpvs.append(str(cid))
        link = next((d["url"] for d in tender.get("documents") or [] if d.get("url")), "")
        adresse = _ocds_kaeuferadresse(r)
        region = str(adresse.get("region") or "").strip().upper()
        if not NUTS_MUSTER.match(region):
            region = _nuts_suchen(r)
        kennung = r.get("ocid") or r.get("id") or os.path.basename(dateiname)
        frist = _ocds_frist(tender)
        treffer.append({
            "id": "doe:" + str(kennung),
            "titel": tender.get("title") or "",
            "beschreibung": tender.get("description") or "",
            "auftraggeber": buyer.get("name") or "",
            "ort": adresse.get("locality") or "",
            "region": region,
            "cpv": cpvs,
            "veroeffentlicht": (parse_date(r.get("date")) or tag).isoformat(),
            "frist": frist.isoformat() if frist else "",
            "datei": os.path.splitext(os.path.basename(dateiname))[0],
            "paket_tag": tag.isoformat(),
            "art": tender.get("status") or "",
            "quelle": quellenname,
            "url": link or "https://oeffentlichevergabe.de",
        })
    return treffer


def doe_paket_holen(q, tag, daten, fmt=None):
    """Ein Tagespaket laden. Vergangene Tage aendern sich nicht mehr, daher Zwischenspeicher."""
    fmt = fmt or q.get("format", "ocds.zip")
    datei = daten.pfad("pakete", "%s.%s" % (tag.isoformat(), fmt))
    if os.path.exists(datei) and os.path.getsize(datei) > 0:
        with open(datei, "rb") as f:
            return f.read(), True
    url = q["endpoint"] + "?" + urllib.parse.urlencode({"pubDay": tag.isoformat(), "format": fmt})
    status, _, rumpf = http_roh(url, timeout=180, accept=MIME_ZU_FORMAT.get(fmt, "*/*"), grenze=None)
    if status != 200:
        raise RuntimeError("HTTP %s" % status)
    with open(datei, "wb") as f:
        f.write(rumpf)
    return rumpf, False


def doe_pakete_aufraeumen(daten, behalten_ab):
    """Alte Tagespakete loeschen, damit der Zwischenspeicher nicht endlos waechst."""
    ordner = os.path.dirname(daten.pfad("pakete", "x"))
    for name in os.listdir(ordner):
        d = parse_date(name[:10])
        if d and d < behalten_ab:
            os.remove(os.path.join(ordner, name))


def _eforms_fristen(wurzel):
    for behaelter, art in (("TenderSubmissionDeadlinePeriod", "Angebotsfrist"),
                           ("ParticipationRequestReceptionPeriod", "Teilnahmefrist")):
        daten = []
        for el in _lokal_alle(wurzel, behaelter):
            ende = _lokal(el, "EndDate")
            d = parse_date(ende.text) if ende is not None and ende.text else None
            if d:
                daten.append(d)
        if daten:
            return min(daten), art
    return None, ""


def doe_fristen_ergaenzen(cfg, bestand, daten):
    """Fuer gespeicherte Treffer ohne Frist das eForms-Paket desselben Tages laden
    und das Dokument mit demselben Dateinamen auslesen."""
    q = cfg["quellen"]["doe"]
    if not q.get("fristen_aus_eforms", True):
        return
    offen = {}
    for e in bestand.values():
        if e.get("id", "").startswith("doe:") and not e.get("frist") \
                and e.get("datei") and e.get("paket_tag") and not e.get("eforms_geprueft"):
            offen.setdefault(e["paket_tag"], []).append(e)
    gefunden = geprueft = 0
    for tag_text, eintraege in sorted(offen.items()):
        tag = parse_date(tag_text)
        try:
            rumpf, _ = doe_paket_holen(q, tag, daten, fmt="eforms.zip")
            archiv = zipfile.ZipFile(io.BytesIO(rumpf))
        except Exception as e:
            warn("eForms-Paket %s: %s" % (tag_text, e))
            continue
        vorhanden = set(archiv.namelist())
        for eintrag in eintraege:
            name = eintrag["datei"] + ".xml"
            eintrag["eforms_geprueft"] = True
            geprueft += 1
            if name not in vorhanden:
                continue
            try:
                wurzel = ET.fromstring(archiv.read(name))
            except ET.ParseError:
                continue
            frist, art = _eforms_fristen(wurzel)
            if frist:
                eintrag["frist"], eintrag["frist_art"] = frist.isoformat(), art
                gefunden += 1
            if not eintrag.get("region"):
                for el in _lokal_alle(wurzel, "CountrySubentityCode"):
                    code = (el.text or "").strip().upper()
                    if NUTS_MUSTER.match(code):
                        eintrag["region"] = code
                        break
    if geprueft:
        print("  Fristen aus eForms: %d von %d" % (gefunden, geprueft))


def hole_doe(cfg, profil, daten, seit):
    q = cfg["quellen"]["doe"]
    letzter = dt.date.today() - dt.timedelta(days=1)   # nur abgeschlossene Tage
    tag = max(seit, DOE_FRUEHESTENS)
    treffer, fehler, geladen, gecacht = [], 0, 0, 0
    while tag <= letzter:
        try:
            rumpf, aus_cache = doe_paket_holen(q, tag, daten)
        except Exception as e:
            fehler += 1
            warn("%s: %s" % (tag.isoformat(), e))
            if fehler >= 3:
                warn("Nach drei Fehlversuchen abgebrochen.")
                break
            tag += dt.timedelta(days=1)
            continue
        gecacht += aus_cache
        geladen += not aus_cache
        for art, name, inhalt in doe_entpacken(rumpf):
            if art == "ocds":
                treffer += doe_aus_ocds(inhalt, name, q["name"], tag)
            else:
                eintrag = eforms_auswerten(inhalt, name, q["name"])
                if eintrag:
                    eintrag["veroeffentlicht"] = tag.isoformat()
                    treffer.append(eintrag)
        tag += dt.timedelta(days=1)
    print("  %d Tagespakete geladen, %d aus dem Zwischenspeicher" % (geladen, gecacht))
    return treffer


# --------------------------------------------------------------------------
# Quelle 3: cosinex-Vergabemarktplaetze
# --------------------------------------------------------------------------

def vmp_aus_nat(wurzel, kennung, quellenname, satellit_url):
    """Nationale Bekanntmachung im Format NOTICE_NAT (Unterschwelle)."""
    auftrag = _lokal(wurzel, "OBJECT_CONTRACT")
    if auftrag is None:
        return None
    titel = _text(_lokal(auftrag, "TITLE"))
    if not titel:
        return None
    cpvs = sorted({(e.get("CODE") or "").split("-")[0] for e in _lokal_alle(wurzel, "CPV_CODE")
                   if (e.get("CODE") or "")[:8].isdigit()})
    stelle = _lokal(wurzel, "CONTRACTING_BODY")
    region = ""
    for e in _lokal_alle(stelle if stelle is not None else wurzel, "NUTS"):
        code = (e.get("CODE") or "").upper()
        if NUTS_MUSTER.match(code):
            region = code
            break
    auftraggeber = ort = ""
    if stelle is not None:
        auftraggeber = _text(_lokal(stelle, "OFFICIALNAME"))
        ort = _text(_lokal(stelle, "TOWN"))
    frist = parse_date(_text(_lokal(wurzel, "DATE_RECEIPT_TENDERS")))
    return {
        "id": "vmp:" + kennung, "titel": titel,
        "beschreibung": _text(_lokal(auftrag, "SHORT_DESCR")),
        "auftraggeber": auftraggeber, "ort": ort, "region": region, "cpv": cpvs,
        "veroeffentlicht": dt.date.today().isoformat(),
        "frist": frist.isoformat() if frist else "", "art": "", "quelle": quellenname,
        "url": satellit_url.split("/opendata")[0] + "/",
    }


def vmp_paket_holen(satellit, daten):
    """Das Paket ist ein Tagesstand mit allen aktuell veroeffentlichten Bekanntmachungen."""
    kuerzel = re.sub(r"[^a-z0-9]+", "-", norm(satellit["name"])).strip("-")
    heute = dt.date.today().isoformat()
    datei = daten.pfad("cosinex", "%s_%s.zip" % (kuerzel, heute))
    if os.path.exists(datei) and os.path.getsize(datei) > 0:
        with open(datei, "rb") as f:
            return f.read(), True
    status, _, rumpf = http_roh(satellit["url"], timeout=180, grenze=None)
    if status != 200 or rumpf[:2] != b"PK":
        raise RuntimeError("HTTP %s" % status)
    ordner = os.path.dirname(datei)
    for alt in os.listdir(ordner):
        if alt.startswith(kuerzel + "_") and alt.endswith(".zip"):
            os.remove(os.path.join(ordner, alt))
    with open(datei, "wb") as f:
        f.write(rumpf)
    return rumpf, False


def hole_cosinex(cfg, profil, daten, seit):
    q = cfg["quellen"]["cosinex"]
    treffer = []
    for satellit in q.get("satelliten", []):
        name = satellit.get("name", "cosinex")
        try:
            rumpf, _ = vmp_paket_holen(satellit, daten)
        except Exception as e:
            warn("%s: %s" % (name, e))
            continue
        vorher = len(treffer)
        with zipfile.ZipFile(io.BytesIO(rumpf)) as archiv:
            verzeichnis = {}
            if "index.xml" in archiv.namelist():
                for eintrag in _lokal_alle(ET.fromstring(archiv.read("index.xml")), "NOTICE"):
                    verzeichnis[_text(_lokal(eintrag, "FILENAME"))] = {
                        "scope": _text(_lokal(eintrag, "SCOPE")),
                        "regel": _text(_lokal(eintrag, "CONTRACTING_RULE")),
                        "art": _text(_lokal(eintrag, "PROCEDURE_TYPE_LABEL")),
                    }
            for datei in archiv.namelist():
                if datei == "index.xml" or not datei.lower().endswith(".xml"):
                    continue
                info = verzeichnis.get(datei, {})
                if q.get("nur_national", True) and info.get("scope") == "EU":
                    continue
                if q.get("ohne_ergebnisse", True) and re.search(
                        r"vergeben|ex post|ergebnis", info.get("art", ""), re.I):
                    continue
                try:
                    inhalt = archiv.read(datei)
                    wurzel = ET.fromstring(inhalt)
                except ET.ParseError:
                    continue
                kennung = os.path.splitext(datei)[0]
                if wurzel.tag.split("}")[-1] == "NOTICE_NAT":
                    eintrag = vmp_aus_nat(wurzel, kennung, name, satellit["url"])
                else:
                    eintrag = eforms_auswerten(inhalt, datei, name)
                    if eintrag:
                        eintrag["id"] = "vmp:" + kennung
                if not eintrag:
                    continue
                art, regel = info.get("art", ""), info.get("regel", "")
                eintrag["art"] = ("%s (%s)" % (art, regel)) if art and regel else (art or regel)
                treffer.append(eintrag)
        print("  %-42s %5d" % (name, len(treffer) - vorher))
    return treffer


# --------------------------------------------------------------------------
# Quelle 4: RSS/Atom-Feeds
# --------------------------------------------------------------------------

def _feed_text(rohtext):
    """HTML aus einer Feed-Beschreibung in Zeilen umwandeln."""
    import html as html_modul
    if not rohtext:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", rohtext)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_modul.unescape(text)
    zeilen = [re.sub(r"[ \t]+", " ", z).strip() for z in text.split("\n")]
    return "\n".join(z for z in zeilen if z)


CPV_IM_TITEL = re.compile(r"\s*(\d{8})(?:-\d)?\s*$")
ATOM = "{http://www.w3.org/2005/Atom}"


def hole_feeds(cfg, profil, daten, seit):
    treffer = []
    for feed in cfg["quellen"]["feeds"].get("liste", []):
        name = feed.get("name", "Feed")
        try:
            wurzel = ET.fromstring(http_text(feed["url"]))
        except Exception as e:
            warn("Feed %s: %s" % (name, type(e).__name__))
            continue
        eintraege = list(wurzel.iter("item")) or list(wurzel.iter(ATOM + "entry"))
        muster = feed.get("muster") or {}
        vorher = len(treffer)
        for e in eintraege:
            def feld(*namen):
                for n in namen:
                    el = e.find(n)
                    if el is not None:
                        if el.text and el.text.strip():
                            return el.text.strip()
                        if el.get("href"):
                            return el.get("href")
                return ""

            titel = feld("title", ATOM + "title")
            if not titel:
                continue
            link = feld("link", ATOM + "link")
            text = _feed_text(feld("description", ATOM + "summary", ATOM + "content"))
            werte = {}
            for rolle, ausdruck in muster.items():
                gefunden = re.search(ausdruck, text)
                if gefunden:
                    werte[rolle] = gefunden.group(1).strip()
            cpvs = []
            if feed.get("cpv_aus_titel"):
                m = CPV_IM_TITEL.search(titel)
                if m:
                    cpvs.append(m.group(1))
                    titel = CPV_IM_TITEL.sub("", titel).strip()
            frist = parse_date(werte.get("frist") or werte.get("frist2"))
            datum = (parse_date(werte.get("veroeffentlicht"))
                     or parse_date(feld("pubDate", "{http://purl.org/dc/elements/1.1/}date",
                                        ATOM + "updated"))
                     or dt.date.today())
            treffer.append({
                "id": "feed:" + (link or titel)[:180],
                "titel": titel, "beschreibung": text[:1200],
                "auftraggeber": werte.get("auftraggeber", ""), "ort": werte.get("ort", ""),
                "cpv": cpvs, "veroeffentlicht": datum.isoformat(),
                "frist": frist.isoformat() if frist else "", "art": werte.get("art", ""),
                "quelle": name, "url": link,
            })
        print("  %-42s %5d" % (name, len(treffer) - vorher))
    return treffer


# --------------------------------------------------------------------------
# Bestand: was einmal gesehen wurde, bleibt erhalten
# --------------------------------------------------------------------------

TED_PRAEFIX = re.compile(r"^deutschland\s*[–—-]\s*[^–—]+?\s*[–—-]\s*")


def titel_schluessel(titel):
    t = TED_PRAEFIX.sub("", norm(titel))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()[:90]


def dubletten_zusammenfassen(eintraege):
    """Dieselbe Ausschreibung aus mehreren Quellen nur einmal zeigen."""
    gruppen = {}
    for e in eintraege:
        schluessel = titel_schluessel(e.get("titel", ""))
        if len(schluessel) < 12:
            schluessel = e["id"]
        gruppen.setdefault(schluessel, []).append(e)
    ergebnis = []
    for gruppe in gruppen.values():
        if len(gruppe) == 1:
            ergebnis.append(gruppe[0])
            continue
        gruppe.sort(key=lambda e: (bool(e.get("frist")), len(e.get("beschreibung") or ""),
                                   e.get("punkte", 0)), reverse=True)
        haupt = dict(gruppe[0])
        for anderer in gruppe[1:]:
            for feld, wert in anderer.items():
                if wert and not haupt.get(feld):
                    haupt[feld] = wert
        haupt["auch_in"] = sorted({a.get("quelle", "") for a in gruppe[1:]} - {haupt.get("quelle")})
        haupt["punkte"] = max(e.get("punkte", 0) for e in gruppe)
        haupt["grenzfall"] = all(e.get("grenzfall") for e in gruppe)
        haupt["neu"] = any(e.get("neu") for e in gruppe)
        ergebnis.append(haupt)
    return ergebnis


def bestand_ergaenzen(bestand, neue):
    heute = dt.date.today().isoformat()
    neuzugaenge = 0
    for n in neue:
        vorhanden = bestand.get(n["id"])
        if vorhanden is None:
            n["zuerst_gesehen"] = n["zuletzt_gesehen"] = heute
            bestand[n["id"]] = n
            neuzugaenge += 1
            continue
        for schluessel, wert in n.items():
            if wert and not vorhanden.get(schluessel):
                vorhanden[schluessel] = wert
        vorhanden["zuletzt_gesehen"] = heute
    return neuzugaenge


def bestand_aufraeumen(bestand, aufbewahrung_tage):
    """Abgelaufenes entfernen. Massstab ist die Frist, ohne Frist das letzte Auftauchen."""
    heute = dt.date.today()
    grenze = heute - dt.timedelta(days=aufbewahrung_tage)
    behalten, entfernt = {}, 0
    for kennung, eintrag in bestand.items():
        frist = parse_date(eintrag.get("frist"))
        gesehen = parse_date(eintrag.get("zuletzt_gesehen")) or heute
        if (frist < grenze) if frist is not None else (gesehen < grenze):
            entfernt += 1
        else:
            behalten[kennung] = eintrag
    return behalten, entfernt


# --------------------------------------------------------------------------
# Bewertung
# --------------------------------------------------------------------------

def _tabelle(profil, name):
    wert = profil.get(name) or {}
    if isinstance(wert, dict):
        return {k: v for k, v in wert.items() if not k.startswith("_")}
    return {w: 1 for w in wert}


_MUSTER_CACHE = {}


def _kommt_vor(wort, text, falsche):
    """Ein Stichwort gilt nur, wenn es nicht bloss in einem 'falschen Freund' steckt.
    Woerter mit hoechstens fuenf Buchstaben muessen am Wortanfang stehen."""
    w = norm(wort)
    for freund in falsche.get(wort, ()):
        text = text.replace(norm(freund), " ")
    if len(w) <= 5:
        muster = _MUSTER_CACHE.get(w)
        if muster is None:
            muster = _MUSTER_CACHE[w] = re.compile(r"(?<![a-z0-9])" + re.escape(w))
        return muster.search(text) is not None
    return w in text


def bewerte(n, profil):
    """Passung als Kombination aus Leistung und Thema.

    - Volle Punkte nur, wenn eine Leistung UND ein Thema getroffen sind.
    - Schwache Leistungswoerter zaehlen nur zusammen mit einem Kernthema.
    - Erweiterte Themen zaehlen nur mit starker Leistung und einem Kontextwort.
    - Einseitige Treffer werden als Grenzfall knapp unter der Anzeigeschwelle gehalten.
    """
    text = norm(n.get("titel", "") + " " + n.get("beschreibung", ""))
    titel_norm = norm(n.get("titel", ""))
    schwelle = profil.get("mindestpunkte", 8)
    falsche = profil.get("falsche_freunde") or {}

    for wort in profil.get("ausschluss_titel") or []:
        if norm(wort) in titel_norm:
            return None

    def summe(tabelle):
        punkte, treffer = 0.0, []
        for wort, gewicht in tabelle.items():
            if _kommt_vor(wort, text, falsche):
                punkte += gewicht * (1.5 if _kommt_vor(wort, titel_norm, falsche) else 1.0)
                treffer.append(wort)
        return punkte, treffer

    stark, s_treffer = summe(_tabelle(profil, "leistung_stark"))
    schwach, w_treffer = summe(_tabelle(profil, "leistung_schwach"))
    kern, k_treffer = summe(_tabelle(profil, "thema_kern"))
    erweitert, e_treffer = summe(_tabelle(profil, "thema_erweitert"))
    abzug_wort, a_treffer = summe(_tabelle(profil, "abzug"))
    englisch = len(re.findall(r"(?<![a-z])(the|for|of|and|to|in|with|services|support)(?![a-z])",
                              titel_norm))
    if englisch >= 2:
        abzug_wort -= 12
        a_treffer.append("englischsprachig")
    abzug_wort = max(abzug_wort, -36)
    kontext = [w for w in _tabelle(profil, "kontext") if _kommt_vor(w, text, falsche)]

    cpv_tab = _tabelle(profil, "cpv_bonus")
    abzug_tab = _tabelle(profil, "cpv_abzug")
    cpv_bonus, cpv_treffer, cpv_abzug, abzug_label = 0.0, [], 0.0, ""
    cpv_leistung = cpv_thema = False
    for code in {str(c).strip() for c in n.get("cpv", []) if c}:
        beste = None
        for praefix, (label, gewicht) in cpv_tab.items():
            stamm = praefix.rstrip("0")
            if stamm and code.startswith(stamm) and (beste is None or len(stamm) > beste[0]):
                beste = (len(stamm), label, gewicht)
        if beste:
            cpv_bonus += beste[2]
            if beste[1] not in cpv_treffer:
                cpv_treffer.append(beste[1])
        for praefix, (label, gewicht) in abzug_tab.items():
            if code.startswith(praefix) and gewicht < cpv_abzug:
                cpv_abzug, abzug_label = gewicht, label
        cpv_leistung |= any(code.startswith(p) for p in profil.get("cpv_leistung") or [])
        cpv_thema |= any(code.startswith(p) for p in profil.get("cpv_thema") or [])
    if cpv_abzug and cpv_bonus:
        cpv_abzug /= 2

    leistung_stark = bool(stark) or cpv_leistung
    erweitert_gilt = bool(erweitert) and leistung_stark and bool(kontext)
    if not erweitert_gilt:
        erweitert, e_treffer = 0.0, []
    hat_thema = bool(kern) or erweitert_gilt or cpv_thema
    hat_leistung = leistung_stark or (bool(schwach) and bool(kern))

    leistung = stark + schwach
    bonus_kontext = 2 * min(len(kontext), 3)
    punkte = leistung + kern + erweitert + bonus_kontext + abzug_wort + cpv_abzug

    # Regionalbonus: NUTS-Praefix aus dem Profil, nur bei vollem Treffer.
    region = str(n.get("region") or "").upper()
    if not region and NUTS_MUSTER.match(str(n.get("ort") or "").strip().upper()):
        region = str(n.get("ort")).strip().upper()
    region_bonus, region_label = 0, ""
    for praefix, (label, gewicht) in _tabelle(profil, "region_bonus").items():
        if region.startswith(praefix.upper()) and gewicht > region_bonus:
            region_bonus, region_label = gewicht, label

    if hat_leistung and hat_thema:
        punkte += cpv_bonus + region_bonus
        regel = ""
    else:
        if schwach and not stark and not kern:
            regel = "nur schwache Leistung ohne Kernthema"
        elif hat_leistung:
            regel = "nur Leistung"
        elif hat_thema:
            regel = "nur Thema"
        else:
            regel = "weder Leistung noch Thema"
        punkte = min(punkte * 0.5, schwelle - 1)

    n["punkte"] = max(0, int(round(punkte)))
    n["treffer"] = (s_treffer + w_treffer + k_treffer + e_treffer)[:8]
    n["cpv_treffer"] = cpv_treffer[:4]
    n["wertung"] = {
        "leistung": int(round(leistung)), "thema": int(round(kern + erweitert)),
        "kontext": bonus_kontext, "abzug": int(round(abzug_wort)),
        "cpv": int(round(cpv_bonus)) if not regel else 0,
        "region": region_bonus if not regel else 0,
        "region_name": region_label,
        "cpv_abzug": int(round(cpv_abzug)),
        "abzug_grund": ", ".join(a_treffer[:3] + ([abzug_label] if abzug_label else [])),
        "regel": regel,
    }
    return n


# --------------------------------------------------------------------------
# Beispieldaten (fachfremd, erfunden), damit die Oberflaeche ohne Netz pruefbar ist
# --------------------------------------------------------------------------

def demo_daten(profil):
    heute = dt.date.today()
    d = lambda t: (heute + dt.timedelta(days=t)).isoformat()
    roh = [
        ("Umrüstung der Straßenbeleuchtung auf LED im Ortsteil Nord",
         "Austausch von rund 900 Leuchten einschließlich Lichtplanung und Wartungsvertrag.",
         "Gemeinde Musterdorf", "Musterdorf", ["34993000"], 2, 18),
        ("Wartung und Instandhaltung der Lichtsignalanlagen",
         "Rahmenvertrag über vier Jahre für 40 Ampelanlagen im Stadtgebiet.",
         "Stadt Beispielstadt", "Beispielstadt", ["50232200"], 5, 6),
        ("Lichtplanung für den Marktplatz",
         "Konzept für eine energieeffiziente und insektenfreundliche Beleuchtung.",
         "Stadt Musterhausen", "Musterhausen", ["71300000"], 9, 30),
        ("Energiecontrolling der kommunalen Beleuchtung",
         "Aufbau eines Monitorings für Stromverbrauch und Störungen.",
         "Landkreis Beispielkreis", "Beispielkreis", ["71314300"], 1, -2),
    ]
    aus = []
    for i, (t, b, ag, ort, cpv, alt, frist) in enumerate(roh):
        n = {"id": "demo:%d" % i, "titel": t, "beschreibung": b, "auftraggeber": ag, "ort": ort,
             "cpv": cpv, "veroeffentlicht": d(-alt), "frist": d(frist),
             "art": "Auftragsbekanntmachung", "quelle": "Beispielquelle",
             "url": "https://example.org", "neu": i < 2}
        bewertet = bewerte(n, profil)
        if bewertet:
            bewertet["grenzfall"] = bewertet["punkte"] < profil.get("mindestpunkte", 8)
            aus.append(bewertet)
    return aus


# --------------------------------------------------------------------------
# Seite erzeugen
# --------------------------------------------------------------------------

def seite_schreiben(eintraege, pfad, passwort, demo=False, klartext=False):
    nutzdaten = {
        "eintraege": eintraege,
        "stand": dt.datetime.now().strftime("%d.%m.%Y, %H:%M Uhr"),
        "heute": dt.date.today().isoformat(),
        "quellen": sorted({e.get("quelle", "") for e in eintraege}),
        "demo": demo,
    }
    roh = json.dumps(nutzdaten, ensure_ascii=False).encode("utf-8")
    if klartext:
        paket = {"klartext": base64.b64encode(roh).decode("ascii")}
    else:
        paket = verschluesseln(roh, passwort)
    with open(os.path.join(HIER, "vorlage.html"), encoding="utf-8") as f:
        vorlage = f.read()
    seite = vorlage.replace("__PAKET__", json.dumps(paket))
    os.makedirs(os.path.dirname(os.path.abspath(pfad)), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(seite)


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def lauf(args):
    cfg = GRUNDEINSTELLUNGEN
    profil = profil_laden(args.profil)
    passwort = os.environ.get("RADAR_PASSWORT", "")

    if args.klartext and os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit("--klartext ist in GitHub Actions nicht erlaubt.")
    if not passwort and not args.klartext:
        raise SystemExit("RADAR_PASSWORT fehlt. Ohne Passwort wird nichts veroeffentlicht.")

    if args.demo:
        seite_schreiben(demo_daten(profil), args.ausgabe, passwort, demo=True,
                        klartext=args.klartext)
        print("Beispielseite geschrieben.")
        return

    daten = Arbeitsdaten(args.daten, passwort)
    tage = args.tage or cfg["zeitfenster_tage"]
    seit = dt.date.today() - dt.timedelta(days=tage)

    roh = []
    for schluessel, holen in (("ted", hole_ted), ("doe", hole_doe),
                              ("cosinex", hole_cosinex), ("feeds", hole_feeds)):
        q = cfg["quellen"][schluessel]
        if not q.get("aktiv"):
            continue
        print("%s ..." % q.get("name", schluessel))
        try:
            geholt = holen(cfg, profil, daten, seit)
        except Exception as e:
            warn("Quelle %s fehlgeschlagen: %s" % (schluessel, type(e).__name__))
            geholt = []
        print("  -> %d" % len(geholt))
        roh += geholt
    doe_pakete_aufraeumen(daten, seit - dt.timedelta(days=2))
    print("%d Bekanntmachungen abgerufen." % len(roh))

    schwelle_anzeige = profil.get("mindestpunkte", 8)
    schwelle_speicher = min(profil.get("speicher_mindestpunkte", 4), schwelle_anzeige)

    bewertet, gesehen = [], set()
    for n in roh:
        if n["id"] in gesehen:
            continue
        gesehen.add(n["id"])
        eintrag = bewerte(n, profil)
        if eintrag and eintrag["punkte"] >= schwelle_speicher:
            bewertet.append(eintrag)

    bestand = daten.bestand_laden()
    vorher = len(bestand)
    neuzugaenge = bestand_ergaenzen(bestand, bewertet)
    if cfg["quellen"]["doe"].get("aktiv"):
        doe_fristen_ergaenzen(cfg, bestand, daten)

    # Alles im Bestand nach dem aktuellen Profil neu bewerten, damit Profilaenderungen
    # sofort auch fuer aeltere Funde gelten.
    for eintrag in bestand.values():
        eintrag["art"] = art_lesbar(eintrag.get("art"))
        eintrag["cpv"] = list(dict.fromkeys(eintrag.get("cpv") or []))
        neu = bewerte(dict(eintrag), profil)
        if neu is None:
            eintrag["punkte"], eintrag["wertung"] = 0, {"regel": "Ausschlusswort im Titel"}
        else:
            for feld in ("punkte", "treffer", "cpv_treffer", "wertung"):
                eintrag[feld] = neu[feld]

    bestand, entfernt = bestand_aufraeumen(bestand, cfg["aufbewahrung_tage"])
    daten.bestand_speichern(bestand)

    heute = dt.date.today().isoformat()
    anzeige = []
    for eintrag in bestand.values():
        if eintrag.get("punkte", 0) < schwelle_speicher:
            continue
        e = dict(eintrag)
        e["neu"] = e.get("zuerst_gesehen") == heute
        e["grenzfall"] = e["punkte"] < schwelle_anzeige
        for intern in ("datei", "paket_tag", "eforms_geprueft", "zuletzt_gesehen"):
            e.pop(intern, None)
        anzeige.append(e)
    anzeige = dubletten_zusammenfassen(anzeige)
    grenzfaelle = sum(1 for e in anzeige if e["grenzfall"])

    print("%d neu, %d im Bestand (vorher %d), %d entfernt." % (neuzugaenge, len(bestand),
                                                              vorher, entfernt))
    print("%d Treffer, %d Grenzfaelle." % (len(anzeige) - grenzfaelle, grenzfaelle))
    seite_schreiben(anzeige, args.ausgabe, passwort, klartext=args.klartext)
    print("Seite geschrieben.")


def main():
    p = argparse.ArgumentParser(description="Ausschreibungs-Radar")
    p.add_argument("--ausgabe", default=os.path.join(HIER, "seite", "index.html"))
    p.add_argument("--daten", default=os.path.join(HIER, "daten"),
                   help="Ordner fuer Zwischenspeicher und Bestand")
    p.add_argument("--profil", help="Lokale Profildatei statt Secret RADAR_PROFIL")
    p.add_argument("--tage", type=int, help="Zeitfenster in Tagen")
    p.add_argument("--demo", action="store_true", help="Beispieldaten statt Abruf")
    p.add_argument("--klartext", action="store_true",
                   help="Nur lokal: Seite unverschluesselt schreiben")
    args = p.parse_args()
    try:
        lauf(args)
    except SystemExit:
        raise
    except Exception as e:
        # Nur Fehlerart und Codezeile melden, keine Inhalte, die Daten enthalten koennten
        tb = e.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        zeile = tb.tb_lineno if tb else 0
        raise SystemExit("Abbruch: %s in Zeile %d" % (type(e).__name__, zeile))


if __name__ == "__main__":
    main()
