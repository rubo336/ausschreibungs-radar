# Ausschreibungs-Radar

Ein konfigurierbarer Radar für öffentliche Ausschreibungen.

Das Werkzeug ruft regelmäßig öffentlich zugängliche Ausschreibungsquellen ab,
bewertet die Einträge anhand eines frei einstellbaren Suchprofils und stellt die
passenden Treffer als verschlüsselte, statische Webseite bereit.

- Läuft automatisch nach Zeitplan über GitHub Actions
- Ergebnis als passwortgeschützte Seite auf GitHub Pages
- Keine Installation und kein eigener Server nötig

## Quellen

- TED (EU-weite Bekanntmachungen)
- Datenservice Öffentlicher Einkauf (Bekanntmachungsservice des Bundes, auch unterschwellige Verfahren)
- cosinex-Vergabemarktplätze mit Open-Data-Schnittstelle
- RSS-Feeds von Vergabeplattformen

## Aufbau

| Datei | Zweck |
|---|---|
| `radar.py` | Abruf, Bewertung, Bestand, Verschlüsselung |
| `vorlage.html` | Oberfläche; entschlüsselt die Daten im Browser |
| `beispielprofil.json` | Neutrales Beispiel für ein Suchprofil |
| `PROFIL.md` | Aufbau eines Suchprofils |
| `.github/workflows/radar.yml` | Zeitplan, Lauf und Veröffentlichung |

## Einrichtung

1. Unter **Settings → Pages** als Quelle **GitHub Actions** wählen.
2. Unter **Settings → Secrets and variables → Actions** zwei Secrets anlegen:
   - `RADAR_PASSWORT`: Passwort für die Seite
   - `RADAR_PROFIL`: das Suchprofil als JSON (Aufbau siehe `PROFIL.md`)
3. Unter **Actions → Radar → Run workflow** einen ersten Lauf starten.

Das Suchprofil wird nicht im Repository gespeichert. Ohne Passwort wird nichts
veröffentlicht. Die Seite und der zwischengespeicherte Bestand sind mit
AES-256-GCM verschlüsselt (Schlüssel per PBKDF2 aus dem Passwort).

## Lokal testen

```
pip install cryptography
RADAR_PASSWORT=test python3 radar.py --demo
```
