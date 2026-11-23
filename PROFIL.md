# Aufbau eines Suchprofils

Das Suchprofil ist ein JSON-Objekt. Es wird als Secret `RADAR_PROFIL` hinterlegt
und nie ins Repository geschrieben. Ein neutrales Beispiel steht in
`beispielprofil.json`. Fehlende Teile gelten als leer.

Groß- und Kleinschreibung sowie Umlaute spielen keine Rolle („Wärme“ = „waerme“).
Ein Wort trifft auch, wenn es Teil eines längeren Wortes ist. Wörter mit höchstens
fünf Buchstaben müssen dagegen am Wortanfang stehen.

## Bewertung

Eine Bekanntmachung bekommt volle Punkte nur, wenn sie **eine Leistung und ein
Thema** trifft. Einseitige Treffer werden als Grenzfall gespeichert und
erscheinen nur, wenn auf der Seite „Grenzfälle“ eingeschaltet ist.
Treffer im Titel zählen anderthalbfach.

| Schlüssel | Inhalt |
|---|---|
| `mindestpunkte` | Ab dieser Punktzahl gilt ein Eintrag als Treffer |
| `speicher_mindestpunkte` | Ab dieser Punktzahl wird ein Eintrag als Grenzfall behalten |
| `leistung_stark` | Wort → Punkte. Gesuchte Leistungen, die allein reichen |
| `leistung_schwach` | Wort → Punkte. Allgemeine Leistungen, zählen nur mit einem Kernthema |
| `thema_kern` | Wort → Punkte. Kernthemen |
| `thema_erweitert` | Wort → Punkte. Randthemen, zählen nur mit starker Leistung und einem Kontextwort |
| `kontext` | Liste. Wörter, die den gewünschten Blickwinkel anzeigen; je Treffer 2 Punkte, höchstens 6 |
| `abzug` | Wort → negative Punkte. Fachfremde Leistungen |
| `falsche_freunde` | Wort → Liste längerer Wörter, in denen es steckt, ohne gemeint zu sein |
| `ausschluss_titel` | Liste. Steht eines dieser Wörter im Titel, fällt der Eintrag ganz heraus |
| `cpv_bonus` | CPV-Präfix → [Bezeichnung, Punkte]. Erscheint als Markierung auf der Seite |
| `cpv_abzug` | CPV-Präfix → [Bezeichnung, negative Punkte]. Der stärkste Abzug zählt einmal |
| `cpv_leistung` | Liste von CPV-Präfixen, die als starke Leistung gelten |
| `cpv_thema` | Liste von CPV-Präfixen, die als Thema gelten |
| `ted_laender` | Länderkürzel für die TED-Abfrage, z. B. `["DEU"]` |
| `ted_volltext` | Suchbegriffe für die TED-Abfrage (TED liefert höchstens 250 Einträge) |

Schlüssel, die mit `_` beginnen, werden ignoriert und können für Notizen dienen.

## Profil ändern

Unter **Settings → Secrets and variables → Actions** das Secret `RADAR_PROFIL`
bearbeiten und den vollständigen neuen JSON-Text einfügen. Der nächste Lauf
bewertet auch alle bereits gespeicherten Einträge neu.
