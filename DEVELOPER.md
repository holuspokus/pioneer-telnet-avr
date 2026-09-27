# Pioneer Telnet AVR – Entwicklungsnotizen

Home-Assistant-Integration für klassische Pioneer-VSX-Receiver mit Telnet.
Referenz: `holuspokus/homebridge-pioneer-avr-2025`, Commit
`0ca97ba7f86f98b2069672d2f1e79d10422e6b9b`.

**Entwicklungsstand.** Die Integration wurde in einer laufenden
Home-Assistant-Installation mit einem VSX-923 eingerichtet. Die Telnet-
Kommunikation und Funktionsgleichheit mit Homebridge sind am physischen
Receiver noch nicht vollständig geprüft. Die Eingangserkennung lieferte im
HA-Optionsdialog erkannte Eingänge; die Verbindungsdauer und Bedienung müssen
dort weiter getestet werden.

## Vorgesehene Darstellung

- Receiver als `media_player` mit Eingängen, Strom, Lautstärke und Listening Modes.
- Optional (standardmässig aktiviert): zusätzliche `light`-Entität `VSX-923 Volume` mit der ursprünglichen
  konfigurierbaren Helligkeits-zu-Lautstärke-Skalierung.
- Ausgewählte Eingänge als eigene `switch`-Entitäten; der Schalter für den
  aktiven Eingang kann den Receiver ausschalten.
- Ein eigener Listening-Mode-Schalter und ein `Telnet connection`-Schalter.
- Optional, standardmässig deaktiviert: ein eigenständig gekoppeltes
  HomeKit-TV für Listening Modes. Es erzeugt keine HA-Entität; der normale
  Receiver-Media-Player bietet Listening Modes weiterhin in HA an.
- Receiver-Suche über `_raop._tcp.local.` und Portprüfung in der Reihenfolge
  23, 24, 8102; Eingangsabfrage über `?RGB01` bis `?RGB60`.
- Optional: Zonenerkennung per `?AP`/`?BP` auf derselben Telnet-Verbindung.
  Jede bestätigte Zone erhält ein eigenes Gerät mit TV-Media-Player,
  Ein/Aus-Schalter und Lautstärke-Lampe. Die Lautstärke-Abbildung verwendet
  den Zahlenbereich 00–81 aus der beigefügten VSX-1120-Protokollreferenz;
  Unterstützung und Eingänge des VSX-923 müssen am Gerät geprüft werden.
- Optional, standardmässig aktiviert: Ab zehn bestätigten Listening Modes nur
  diese und die drei Standardwerte im Receiver-Sound-Modus und in den drei
  Auswahlfeldern anbieten. Das eigene HomeKit-TV bietet alle bekannten und
  gelernten Modi zur manuellen Kanalauswahl an.
- Das Integrationssymbol wird aus `brand/icon.png` geladen.
- Optional und einzeln aktivierbar: Audioformat (`?AST`), rohe Videoinformation
  (`?VST`), Tone/Bass/Treble, MCACC-Speicher als eigenständig gekoppeltes
  HomeKit-TV ohne HA-Entität,
  Phase Control als eigenes TV-Gerät, Virtual Surround Back, Kanalpegel
  und Tuner-Frequenz/Presets mit Bedientasten. Alle Optionen sind zunächst
  ausgeschaltet. Die Abfragen und Befehle verwenden die bestehende
  Telnet-Verbindung; HA-Entitäten für die abgefragten Zusatzfunktionen werden
  nur nach einer gültigen Antwort des jeweiligen Statusbefehls erstellt. Die Kommandos stammen aus der
  beigefügten VSX-1120-Protokollreferenz und sind beim VSX-923 noch zu testen.
- Optionsdialog in Deutsch, Englisch, Französisch, Spanisch, vereinfachtem
  Chinesisch, Japanisch, Türkisch, Italienisch, Portugiesisch, Hindi,
  Arabisch, Ukrainisch, Russisch, Bengalisch und Urdu. Die Texte der neuen
  Sprachen sind ein erster Übersetzungsstand und sollten von Muttersprachlern
  gegengelesen werden.
- Die drei konfigurierbaren Listening-Mode-IDs sind Dropdowns. Bei aktivierter
  Filteroption und mindestens zehn bestätigten Modi enthalten sie die
  bestätigten Modi. Bis dahin zeigen sowohl diese Dropdowns als auch das
  optionale Listening-Mode-TV sowie der Receiver-Sound-Modus alle bekannten
  Modi. Nach einer Änderung die Optionen speichern und erneut öffnen, damit
  die drei Dropdowns aktualisiert werden. Die drei festen
  Standard-IDs 0013, 0112 und 0101 bleiben immer enthalten. Gelernt wird
  unabhängig von der Filteroption. Antwortet der Receiver auf einen
  Moduswechsel mit E04/E06, wird die betreffende ID aus den Listen entfernt
  (ausser den drei festen Standard-IDs). Die Modusnamen stammen aus der
  Referenzliste; der Receiver sendet über SR nur numerische IDs. Nach dem
  Upgrade werden früher gelernte Modi einmalig zurückgesetzt, da ältere
  Versionen SR- und LM-Codes vermischt haben.
- In den Integrationsoptionen löscht `Listening-Mode-Liste beim Speichern
  zurücksetzen` einmalig bestätigte und abgewiesene Modi und stellt wieder
  die Standardliste her. Diese Aktion ist kein dauerhaftes HA-Gerät. Die
  drei Standard-IDs bleiben stets enthalten.
- Display-Sensor: Dekodiert spontan empfangene `FL`-Meldungen
  mit derselben Zeichentabelle wie das Homebridge-Plugin. Die im PDF genannte
  gezielte Abfrage `?FL` ist nur für RS-232 garantiert und wird über Telnet
  nicht erzwungen. Ein Displaytext allein wird keinem `SR`-Code zugeordnet,
  weil er auch andere Anzeigen (etwa Lautstärke oder Eingänge) enthalten kann.
- Der Display-Sensor und `<Receiver> Active input` sind ab Version
  0.1.11-dev immer als Entitäten vorhanden. Der Eingang zeigt den Namen der
  zuletzt über `FN` gemeldeten Quelle, bei ausgeschaltetem Receiver `Off`;
  das Attribut `input_id` enthält ihre
  zweistellige Pioneer-ID. Ein fehlender erster Wert erscheint als unbekannt.
  Audio- und Video-Sensoren werden bei aktivierter Option ebenfalls sofort
  angelegt und bleiben bis zur ersten gültigen `AST`-/`VST`-Antwort unbekannt.
- Der Listening-Mode-Schalter heisst `<Receiver> Listening Mode`. In früheren
  Versionen wurde dieselbe Entität als `Audio <Receiver>` angezeigt; ihre
  eindeutige ID bleibt beim Umbenennen erhalten.
- Die Eingangsschalter tragen in HA nur den Eingangsnamen. Wenn zwei
  ausgewählte Eingänge gleich heissen, ergänzt HA die Pioneer-ID in Klammern.
  Der Name der HA-Entität ist auch die Vorgabe für die HomeKit Bridge; eine
  abweichende Bezeichnung mit Receiver-Suffix muss dort pro Entität gesetzt
  werden. Die eindeutigen IDs bleiben beim Umbenennen erhalten.
- Der Listening-Mode-Schalter übernimmt die über `SR****` gemeldete Einstellung:
  primärer Modus und Ersatzmodus = Ein, alternativer Modus (standardmässig
  `0112`, EXTENDED STEREO) = Aus. `LM****` und Displaytexte sind andere
  Anzeigeinformationen und ändern die Schalterposition nicht. Nach dem
  Einschalten oder Wiederverbinden wird die Einstellung wie im Homebridge-Code
  nach 5,321 Sekunden mit `?S` abgefragt, bei Fehlern höchstens zehnmal mit
  je 1,5 Sekunden Abstand. Dafür wird dieselbe Telnet-Sitzung benutzt.
- Ein Fehler auf einen bereits gesendeten `****SR`-Befehl wird einmal
  asynchron wiederholt. Nach zweimaligem `E06` wird der Modus in der
  gemeinsamen Ausschlussliste gespeichert. Sein HomeKit-Kanal bleibt angelegt,
  ist aber ausgeblendet; in HA entfällt er aus der Auswahl. Die Liste ist in
  den Einstellungen und über die HomeKit-Kanalsichtbarkeit editierbar.
  Displaymeldungen wie `NOT AVAILABLE` ändern die Modusliste nicht.
  Eine `SR****`-Bestätigung lernt den Modus und aktualisiert bei Bedarf die
  Kanäle des Listening-Mode-TVs in der gemeinsamen Bridge.
- Alle HomeKit-TVs bleiben auch bei ausgeschaltetem Receiver
  erreichbar und melden sofort
  `Active = 0`, sobald `PWR1` eintrifft. Beim geordneten Stoppen der
  Integration wird vor dem Abschalten des HAP-Servers
  `SleepDiscoveryMode = NOT_DISCOVERABLE` gemeldet.
- `receiver.ready` wird erst nach dem anfänglichen Status (Power; bei
  eingeschaltetem Receiver zusätzlich Volume, Mute und Eingang) und der
  Eingangssuche gesetzt. Ein frischer, als vollständig gespeicherter
  Eingangs-Cache ersetzt die Suche. Ältere oder teilweise gespeicherte Caches
  werden neu geprüft. Die RGB-Abfragen warten höchstens 45 Sekunden auf
  Antworten; unvollständige Durchläufe werden später wiederholt. Der aktive
  Eingang wird bei fehlendem Namen zusätzlich gezielt abgefragt. HA-Entitäten
  und HomeKit-TVs melden erst ab diesem Punkt einen verfügbaren bzw.
  eingeschalteten Receiver. Bedienbefehle sind bis dahin gesperrt, während
  der Telnet-Verbindungsschalter für den Reconnect erreichbar bleibt.
- Ein Receiver wird erst nach `ready` in die gemeinsame HomeKit-Bridge
  aufgenommen. Wenn noch keiner bereit ist, starten weder Bridge-Treiber
  noch Kopplungsbenachrichtigung. Weitere Receiver können später dynamisch
  hinzugefügt werden, sobald ihre eigene Eingangssuche abgeschlossen ist.
- Audio- und Video-Status werden beim Einschalten mit Abstand über dieselbe
  Telnet-Verbindung abgefragt, sofern die letzte Antwort je Status älter als
  fünf Minuten ist. Unaufgefordert gesendete `AST`- und `VST`-Meldungen werden
  stets unmittelbar übernommen; dafür gilt keine Fünf-Minuten-Sperre.
- Eine `PioneerSharedHomeKit`-Instanz betreibt einen HAP-Bridge-Treiber für alle
  Pioneer-Receiver. Haupt-TV, Listening-Mode-TV und MCACC-TV erhalten stabile
  AIDs aus Entry-ID und Funktion. Dynamisch hinzugefügte Receiver und Eingänge
  lösen `config_changed()` aus. Der gemeinsame Kopplungszustand liegt in
  `/config/.storage/pioneer_homekit_shared.state`; alte Einzelzustände werden
  nicht automatisch gelöscht. Apple Home muss die neue Bridge einmal koppeln.
- Die Option `additionalEntitiesInHa` bestimmt, ob ausgewählte Eingangsschalter,
  der Listening-Mode-Schalter und die separate Volume-Lampe als HA-Entitäten
  oder direkt in dieser Bridge erscheinen. Die verknüpfte Volume-Lampe im
  Haupt-TV bleibt Teil des TVs. Der HA-Media-Player bleibt verfügbar und muss
  bei aktivem Haupt-TV aus der HA HomeKit Bridge ausgeschlossen werden, um
  dieselbe Receiver-Funktion nicht doppelt zu veröffentlichen.
- `telnetSwitchInHa` steuert den Telnet-Verbindungsschalter unabhängig davon.
  Standardmässig ist er in HA; andernfalls verwendet er als Bridge-Zubehör
  dieselbe verzögerte Trennung aus `PioneerReceiver`.

## Noch offen

- Weitere Gegenprüfung sämtlicher Zeitabläufe und Fehlerfälle bei
  Verbindungsabbruch, Queues, Wiederholung, Keepalive und Telnet-Schalter am
  echten Receiver. Ein stiller Socket wird nach 35 Sekunden geschlossen und
  über dieselbe Transportinstanz neu verbunden; das wurde mit einem
  simulierten TCP-Receiver geprüft.
- Der HA-Transport prüft zusätzlich den Homebridge-Fall, dass nach einem
  Keepalive-Schreibvorgang mehr als 60 Sekunden lang keine Antwort eintrifft,
  obwohl der Socket offen bleibt. Die Bonjour-Neusuche bevorzugt die stabile
  RAOP-Kennung vor Host und Namen und übernimmt bei nur einem plausiblen AVR
  auch einen geänderten Host oder Port. Der Endpunkt wird gespeichert. Diese
  Wiederherstellung muss am physischen Receiver noch geprüft werden.
- Der Telnet-Schalter verzögert das Abschalten wie die Homebridge-Vorlage:
  rund fünf Minuten bei eingeschaltetem Receiver, 62 Sekunden nach jüngster
  Bedienung und sonst mindestens 4,9 Sekunden. Verbindungsaufbau zählt als
  eingeschalteter Schalter; erneutes Einschalten einer offenen Verbindung
  erzwingt keinen Reconnect. Diese Abläufe brauchen noch einen Gerätetest.
- Eingangsbenennung und Sichtbarkeit aus HA-Optionen gegen den Receiver und
  die Apple-Home-Darstellung verifizieren.
- HomeKit-spezifische Verknüpfung des Listening-Mode-Schalters mit dem
  Receiver-Gerät verifizieren. Die HA-Gerätezuordnung allein garantiert keine
  verknüpfte HomeKit-Service-Darstellung.
- Mehrzonenprotokoll und verfügbare Eingänge je Zone am VSX-923 prüfen.
- Übersetzungsanzeige der neu hinzugefügten Sprachen im HA-Optionsdialog prüfen.
- Für die optionalen Befehle am VSX-923 prüfen, welche Antworten tatsächlich
  unterstützt werden; die beigefügte Befehlsliste bezieht sich auf den VSX-1120.
- Funktionstest unter Home Assistant und Vergleich am physischen Receiver.
- Gemeinsame Pioneer-Bridge mit mehreren Receivern und nachträglich entdeckten
  Eingängen am echten HomeKit-Client auf Kopplung und Aktualisierung prüfen.

Die Homebridge-Vorlage steht unter der ISC-Lizenz; der Lizenztext liegt bei.
