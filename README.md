# Ausschreibungs-Radar

Ein konfigurierbarer Radar für öffentliche Ausschreibungen.

Das Werkzeug ruft regelmäßig öffentlich zugängliche Ausschreibungsquellen ab,
filtert die Einträge anhand eines frei einstellbaren Suchprofils und stellt die
passenden Treffer als verschlüsselte, statische Webseite bereit.

- Läuft automatisch nach Zeitplan über GitHub Actions
- Ergebnis als passwortgeschützte Seite auf GitHub Pages
- Keine Installation und kein eigener Server nötig

Das Suchprofil wird nicht im Repository gespeichert, sondern als Secret
hinterlegt. Der Code enthält nur ein neutrales Beispielprofil.

**Status:** in Vorbereitung.
