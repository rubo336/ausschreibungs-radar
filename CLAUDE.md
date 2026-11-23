# Verbindliche Regeln für alle Sitzungen

Diese Regeln gelten ausnahmslos für jede Arbeit in diesem Repository.

## 1. Vertraulichkeit – das Repository ist öffentlich

- Niemand darf aus Code, Commits, Dateien, Issues, Actions-Protokollen oder der
  veröffentlichten Seite Rückschlüsse auf die nutzende Organisation oder auf
  Personen ziehen können.
- **Nie ins Repository** (weder in Dateien noch in Commits, Issues, Kommentare
  oder Branch-Namen):
  - Namen von Personen, Firmen oder Projekten
  - persönliche Mailadressen
  - Suchprofile: Stichworte, Regionen, Ausschlusswörter, Gewichtungen
  - Trefferlisten
  - Zwischenspeicher (Caches)
  - Bestandsdaten (z. B. bereits gesehene Ausschreibungen)
- Das **echte Suchprofil** kommt ausschließlich über ein GitHub-Secret bzw. eine
  Umgebungsvariable. Im Code steht nur ein neutrales Beispielprofil aus einem
  fachfremden Bereich (z. B. Straßenbeleuchtung).
- **Actions-Protokolle** enthalten nur Zahlen (Anzahl abgerufen, Anzahl passend,
  Fehler je Quelle) – keine Titel, Auftraggeber oder Stichworte.
- Die **veröffentlichte Seite** auf GitHub Pages ist clientseitig verschlüsselt;
  das Passwort kommt aus einem Secret. Unverschlüsselte Ergebnisse werden nie
  veröffentlicht oder committet.
- **Commit-Nachrichten** sind sachlich und neutral.
- Vor jedem Commit prüfen: Enthält die Änderung etwas aus der Liste oben?
  Falls ja oder unklar: nicht committen, sondern nachfragen.

## 2. Zielarchitektur

- Python-Skript, möglichst nur mit der Standardbibliothek.
- Läuft per GitHub Actions nach Zeitplan.
- Ergebnis als statische, verschlüsselte HTML-Seite auf GitHub Pages.
- Kein dauerhaft laufender Server, keine Installation bei Nutzenden.

## 3. Arbeitsweise

- Der Eigentümer ist kein Entwickler: in kleinen Schritten arbeiten,
  Entscheidungen kurz auf Deutsch erklären.
- Vor Entscheidungen, die der Eigentümer treffen sollte, nachfragen.
- Oberfläche und Erklärungen auf Deutsch.
