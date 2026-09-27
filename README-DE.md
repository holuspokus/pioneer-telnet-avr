# Pioneer Telnet AVR

Dein Pioneer-Receiver spricht Telnet? Diese Integration findet ihn im Netzwerk
und macht Eingänge, Lautstärke, Listening Modes und weitere Funktionen in
Home Assistant und für Apple HomeKit verfügbar.
Die Home-Assistant-Integration ist darauf ausgelegt, auch bei Verbindungsproblemen möglichst
zuverlässig weiterzulaufen und sich selbst wieder mit dem Receiver zu verbinden.

## Kompatibilität

Nicht jeder Pioneer-Receiver unterstützt Telnet. Diese Integration ist für
Modelle gedacht, die **Pioneer-Telnet-Befehle** verstehen, etwa VSX-922,
VSX-923 oder VSX-527. Neuere Modelle können stattdessen ein anderes Protokoll
wie ISCP verwenden; beispielsweise ist ein VSX-LX304 nicht für diese
Integration geeignet. Entscheidend ist das unterstützte Protokoll, nicht
allein der Modellname. Eine manuelle IP-Adresse kann fehlende
Telnet-Unterstützung nicht ersetzen.

## Receiver hinzufügen

Füge **Pioneer Telnet AVR** unter **Einstellungen → Geräte & Dienste →
Integration hinzufügen** hinzu. Die Integration sucht dann nach deinem
Receiver; bei mehreren Treffern wählst du den gewünschten aus. Home Assistant
kann dir den Receiver auch bereits als gefundene Integration vorschlagen.
Falls die Suche nichts findet, kannst du ihn mit seinem Hostnamen oder seiner
IP-Adresse hinzufügen.

![Receiver in der Netzwerksuche auswählen](docs/images/receiver-auswahl.png)

Bei der Einrichtung entscheidest du pro Receiver, ob sein Name auch an den
Namen der Schalter angehängt wird. Danach kannst du das Gerät einem Bereich
zuordnen.

![Receiver benennen und einem Bereich zuordnen](docs/images/receiver-zuordnen.png)

## Einrichtung

Über **Konfigurieren** legst du fest, welche Eingänge als eigene Schalter
erscheinen und welche zusätzlichen Funktionen du verwenden möchtest. Die
Einstellungen erläutern die einzelnen Optionen direkt im Config-Panel.
Beispielsweise kannst du bis zu fünf Eingänge als eigene Schalter anlegen,
den Listening-Mode-Schalter aktivieren und die zusätzliche Volume-Lampe
einschalten. Die Option **Zusätzliche Entitäten in Home Assistant anzeigen und
HomeKit Bridge verwenden** bestimmt, wo diese drei Arten von Zusatzgeräten
erscheinen. Sie ist standardmässig ausgeschaltet: Dann landen sie ausschliesslich
in der gemeinsamen Pioneer-HomeKit-Bridge. Aktivierst du sie, erscheinen sie
in Home Assistant und können über dessen HomeKit Bridge veröffentlicht werden.
Der Schalter **Telnet connection** hat eine eigene Checkbox und bleibt
standardmässig in Home Assistant. Deaktivierst du sie, erscheint dieser
Schalter stattdessen in der gemeinsamen Pioneer-Bridge.

![Beispiel der Receiver-Einstellungen](docs/images/einstellungen.png)

Die Bilder zeigen einen Teststand der Oberfläche. Einzelne Optionsnamen und
Beschreibungen können sich in neueren Versionen unterscheiden.

## Bedienung in Home Assistant

Am Receiver-Gerät findest du den Media-Player und Sensoren für Eingang,
Display, Listening Mode und Lautstärke. Zusätzliche Eingangs- und
Listening-Mode-Schalter sowie die einzelne Volume-Lampe sind hier nur sichtbar,
wenn du die entsprechende Checkbox aktivierst. Öffnest du den
Media-Player, kannst du die Lautstärke und den Eingang ändern; die Listening
Modes stehen ebenfalls in der Auswahl.

![Receiver mit Schaltern und Sensoren](docs/images/receiver-und-sensoren.png)

![Media-Player-Steuerung](docs/images/receiver-steuerung.png)

![Listening Modes im Media-Player auswählen](docs/images/listening-modes.png)

## Geräte in Apple Home einrichten

Die Integration stellt **eine gemeinsame Pioneer-HomeKit-Bridge** für alle
eingerichteten Receiver bereit. Sie enthält die aktivierten TVs und, solange
die Checkbox für zusätzliche HA-Entitäten ausgeschaltet ist, auch deren
Eingangsschalter, Listening-Mode-Schalter und einzelne Volume-Lampe.
Für Zusatzgeräte in Home Assistant kannst du ergänzend die **HomeKit Bridge
von Home Assistant** verwenden:

1. Öffne die Benachrichtigung **Pioneer HomeKit Bridge** in Home Assistant und
   kopple sie mit ihrem QR-Code in Apple Home. **Ein QR-Code genügt** für alle
   aktivierten Pioneer-TVs und direkten Zusatzgeräte, auch bei mehreren
   Receivern. Die Kanal-Auswahl der TVs bleibt in Apple Home verfügbar.
2. Wenn du **Zusätzliche Entitäten in Home Assistant anzeigen und HomeKit Bridge
   verwenden** aktiviert hast, füge die HA-Integration **HomeKit Bridge** hinzu,
   wähle dort die gewünschten Eingangsschalter, die einzelne Volume-Lampe und
   den Listening-Mode-Schalter aus und kopple auch diese HA-Bridge. Diese
   Zusatzgeräte erscheinen dann nicht nochmals in der Pioneer-Bridge.
   Den Telnet-connection-Schalter kannst du unabhängig davon in Home Assistant
   belassen oder direkt über die Pioneer-Bridge veröffentlichen.
3. Schliesse den normalen Receiver-Media-Player in der HA HomeKit Bridge aus,
   falls das Pioneer-Haupt-TV aktiviert ist. Sonst erscheint derselbe Receiver
   zusätzlich als zweites TV-Gerät.

**Umstieg von früheren Versionen:** Entferne die bisher einzeln gekoppelten
Pioneer-TVs aus Apple Home und kopple stattdessen die gemeinsame Pioneer-Bridge.
Ihre neuen HomeKit-Gerätekennungen lassen sich nicht aus den alten
Einzelkopplungen übernehmen. Die HA HomeKit Bridge bleibt bestehen, wenn du
sie für andere HA-Geräte verwendest.

Der Ein/Aus-Status des Listening-Mode-TVs und des MCACC-TVs folgt dem Receiver.
Der Power-Knopf dieser beiden TVs schaltet den Receiver nicht aus; die Anzeige
kehrt zu seinem tatsächlichen Status zurück. Wenn du den Listening-Mode-Schalter
beim Receiver anzeigen lässt, wird er zusätzlich mit dem Haupt-TV in der Pioneer-Bridge
verknüpft, sofern dieses aktiviert ist.

Für die Lautstärke-Lampe und die Eingangsschalter empfiehlt sich in Apple Home
ein eigener Raum für den Receiver. So werden sie bei Befehlen wie „Alle Lichter
im Wohnzimmer ausschalten“ nicht versehentlich mit den Raumlichtern geschaltet.

## Verbindung

Bei einem Verbindungsabbruch versucht die Integration selbstständig, den
Receiver wiederzufinden und die Verbindung erneut aufzubauen.

Quellcode und Fehlermeldungen: [GitHub – pioneer-telnet-avr](https://github.com/holuspokus/pioneer-telnet-avr).

## Lizenz

Dieses Projekt steht unter der [ISC-Lizenz](LICENSE).
