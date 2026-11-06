"""Original synthetic test scenarios. No BAMF catalogue content or question identifiers."""

from dataclasses import dataclass

GG = "https://www.gesetze-im-internet.de/gg"
POLITICS = "Politik in der Demokratie"
HISTORY = "Geschichte und Verantwortung"
SOCIETY = "Mensch und Gesellschaft"
STATE = "Landesspezifische Kenntnisse"
BERLIN = (
    "https://www.berlin.de/rbmskzl/politik/senat/verfassung/"
    "verfassung-von-berlin-abschnitt-i-die-grundlagen-41549.php"
)
BERLIN_POLITICS = "https://www.berlin.de/rbmskzl/politik/senat/landespolitik/"
CONSTITUTION_HISTORY = (
    "https://www.bundestag.de/parlament/grundgesetz/aktueller-begriff-grundgesetz-643586"
)
UNITY = (
    "https://www.bundesregierung.de/breg-de/schwerpunkte/deutsche-einheit/"
    "die-einheit-ist-wirklichkeit-432814"
)
DICTATORSHIP = "https://www.bundestag.de/parlament/geschichte/parlamentarismus/drittes_reich"
SOCIAL_INSURANCE = "https://www.bmas.de/DE/Soziales/Sozialversicherung/sozialversicherung-art.html"


@dataclass(frozen=True)
class Scenario:
    topic: str
    reference: str
    prompt: str
    answer: str
    distractors: tuple[str, str, str]


GENERAL_SCENARIOS = (
    Scenario(
        POLITICS,
        f"{GG}/__1.html",
        "Im Jugendrat gestaltet Leila ein Plakat über den Umgang mit allen Menschen. "
        "Welchen Grundsatz kann sie als Ausgangspunkt nehmen?",
        "Die Würde jedes Menschen ist zu achten.",
        (
            "Die Würde hängt vom Beruf ab.",
            "Die Würde gilt nur bei Wahlen.",
            "Die Verwaltung darf die Würde nach Wohnort zuteilen.",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__3.html",
        "In einer Behörde werden zwei gleiche Anträge ohne sachlichen Grund unterschiedlich "
        "behandelt. Welcher Grundsatz ist für die Prüfung wichtig?",
        "Gleichheit vor dem Gesetz",
        ("Geheimhaltung der Wahl", "Weisungsfreiheit der Gerichte", "Haushaltsrecht des Bundes"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__5.html",
        "Ein Schülerblog kritisiert sachlich den Busfahrplan. Auf welches Grundrecht "
        "kann sich die Redaktion dafür berufen?",
        "Meinungsfreiheit",
        ("Eigentumsgarantie", "Briefgeheimnis", "Freizügigkeit"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__8.html",
        "Ein Verein plant einen friedlichen, unbewaffneten Protest. In der Planung geht es um "
        "das Recht, gemeinsam öffentlich ein Anliegen zu zeigen. Welches Recht ist gemeint?",
        "Versammlungsfreiheit",
        ("Unverletzlichkeit der Wohnung", "Recht auf ein Erbe", "Schutz des Briefgeheimnisses"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__17.html",
        "Mara schreibt mit Nachbarn eine Bitte an die zuständige Volksvertretung. "
        "Welcher Begriff passt zu dieser Möglichkeit?",
        "Petition",
        ("Einbürgerungsurkunde", "Gerichtsurteil", "Wahlbenachrichtigung"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__20.html",
        "Ein Schaubild für eine Ausstellung beginnt mit der Frage, von wem die staatliche "
        "Gewalt ausgeht. Welcher Eintrag passt zu Deutschland?",
        "Vom Volk",
        ("Von der größten Bank", "Von den Gerichten allein", "Von einer Erbmonarchie"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__28.html",
        "Der Rat einer Gemeinde berät darüber, wie er eine örtliche Bibliothek organisiert. "
        "Welches Prinzip beschreibt den eigenen Handlungsspielraum der Gemeinde?",
        "Kommunale Selbstverwaltung im Rahmen der Gesetze",
        (
            "Alleinige Zuständigkeit des Bundes für Gemeindeaufgaben",
            "Befehlsgewalt einer privaten Stiftung",
            "Unmittelbare Verwaltung durch die EU",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__38.html",
        "Am Wahltag sieht ein Wahlhelfer, dass ein Handy auf die Wahlkabine gerichtet "
        "ist. Welches Wahlprinzip soll er besonders schützen?",
        "Die Wahl ist geheim.",
        (
            "Die Regierung bestimmt jede Stimme.",
            "Jede Stimme muss veröffentlicht werden.",
            "Nur Parteien dürfen eine Wahlkabine nutzen.",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__50.html",
        "In einem Planspiel wollen die Landesregierungen an der Gesetzgebung des Bundes "
        "mitwirken. Welche Institution vertreten die Teilnehmenden?",
        "Den Bundesrat",
        ("Den Deutschen Städtetag", "Das Europäische Parlament", "Die Bundesbank"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__63.html",
        "Eine Redakteurin prüft die Beschriftung eines Nachrichtenbilds zur Wahl des "
        "Bundeskanzlers. Welche Institution muss sie nennen?",
        "Den Deutschen Bundestag",
        ("Den Bundesgerichtshof", "Die Bundesversammlung", "Die Europäische Kommission"),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__67.html",
        "Eine Fraktion bereitet ein konstruktives Misstrauensvotum vor. "
        "Welcher parlamentarische Schritt gehört dazu?",
        "Einen Nachfolger für das Kanzleramt wählen",
        (
            "Die Bundesländer neu zuschneiden",
            "Die Wahlperiode ohne Abstimmung verlängern",
            "Die Richter des Bundesgerichtshofs abwählen",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__79.html",
        "Eine Arbeitsgruppe erläutert, warum sich das Grundgesetz schwerer ändern lässt "
        "als ein gewöhnliches Gesetz. Welche Regel gehört auf ihr Schaubild?",
        "Dafür braucht es Zweidrittelmehrheiten in Bundestag und Bundesrat.",
        (
            "Ein Beschluss des Kabinetts genügt.",
            "Eine einzelne Gemeinde kann es ändern.",
            "Die Zustimmung einer Partei ersetzt alle Abstimmungen.",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__97.html",
        "Ein Bürgermeister möchte einer Richterin sagen, wie sie einen Streit entscheiden "
        "soll. Welche Regel spricht dagegen?",
        "Richterinnen und Richter entscheiden unabhängig nach dem Gesetz.",
        (
            "Gerichte müssen Bürgermeisterbeschlüssen immer folgen.",
            "Gerichte werden durch den Gemeinderat geleitet.",
            "Gerichte dürfen nur Entscheidungen von Vereinen übernehmen.",
        ),
    ),
    Scenario(
        POLITICS,
        f"{GG}/__19.html",
        "Eine Lerngruppe untersucht, wie jemand eine mögliche Rechtsverletzung durch eine "
        "Behörde überprüfen lassen kann. Welcher Weg ist grundsätzlich eröffnet?",
        "Der Rechtsweg",
        (
            "Die automatische Zuteilung eines Mandats",
            "Der Wechsel des Bundeslands",
            "Ein Brief an eine beliebige Partei als einzige Möglichkeit",
        ),
    ),
    Scenario(
        HISTORY,
        CONSTITUTION_HISTORY,
        "Ein Museum beschriftet ein Foto von der Verkündung des Grundgesetzes in Bonn. "
        "Welches Jahr gehört auf die Beschriftung?",
        "1949",
        ("1919", "1961", "1990"),
    ),
    Scenario(
        HISTORY,
        "https://www.bundestag.de/webarchiv/textarchiv/2019/kw47-frauenwahlrecht-669048",
        "Eine Ausstellung zeigt einen Wahlaufruf zur Nationalversammlung von 1919. "
        "Welche Veränderung für Frauen erklärt die Station?",
        "Sie konnten deutschlandweit an der Wahl teilnehmen und kandidieren.",
        (
            "Sie konnten nur in Vereinen abstimmen.",
            "Sie durften nur kommunal kandidieren.",
            "Sie konnten ausschließlich an Landtagswahlen teilnehmen.",
        ),
    ),
    Scenario(
        HISTORY,
        DICTATORSHIP,
        "Ein Schulprojekt vergleicht das Parlament vor und nach der nationalsozialistischen "
        "Machtübernahme. Welche Entwicklung beschreibt den Einschnitt?",
        "Die parlamentarische Demokratie wurde zerstört.",
        (
            "Der freie Parteienwettbewerb wurde ausgebaut.",
            "Die Weimarer Grundrechte wurden dauerhaft gestärkt.",
            "Parlamentarische Kontrolle der Regierung wurde zum wichtigsten Prinzip.",
        ),
    ),
    Scenario(
        HISTORY,
        "https://www.bundesregierung.de/breg-de/schwerpunkte/deutsche-einheit/30-jahre-deutsche-einheit-1817176",
        "Nora ordnet ein Familienfoto von einer großen Leipziger Demonstration im Herbst "
        "1989 ein. Zu welcher historischen Entwicklung passt es?",
        "Zur Friedlichen Revolution in der DDR",
        (
            "Zur Einführung des Frauenwahlrechts",
            "Zur Gründung des Deutschen Kaiserreichs",
            "Zur Verkündung des Grundgesetzes in Bonn",
        ),
    ),
    Scenario(
        HISTORY,
        "https://www.bundesregierung.de/breg-de/service/newsletter-und-abos/bulletin/"
        "sonderausgabe-zum-10-jahrestag-des-mauerfalls-fernsehansprache-von-bundespraesident-johannes-rau-808462",
        "Ein Familienalbum nennt den Abend, an dem sich am 9. November 1989 die Grenze "
        "in Berlin öffnete. Wie wird dieses Ereignis oft bezeichnet?",
        "Mauerfall",
        ("Währungsreform", "Gründung der Europäischen Union", "Berliner Luftbrücke"),
    ),
    Scenario(
        HISTORY,
        UNITY,
        "Ein Archiv sortiert Fotos vom 3. Oktober 1990. Welches Ereignis verbindet die "
        "Bilder von Feiern vor dem Reichstag?",
        "Die deutsche Wiedervereinigung",
        (
            "Der Bau der Berliner Mauer",
            "Die erste Sitzung der Weimarer Nationalversammlung",
            "Die erste Bundestagswahl",
        ),
    ),
    Scenario(
        HISTORY,
        "https://www.bundesregierung.de/breg-de/schwerpunkte/deutsche-einheit/"
        "frei-gewaehlte-volkskammer-konstituiert-sich-441942",
        "Ein Zeitungsarchiv erfasst die erste frei gewählte Volkskammer im Frühjahr "
        "1990. Zu welchem Staat gehörte dieses Parlament?",
        "Zur DDR",
        ("Zur Republik Österreich", "Zur Bundesrepublik Deutschland", "Zur Schweiz"),
    ),
    Scenario(
        HISTORY,
        "https://www.bundestag.de/parlament/geschichte/75jahre/wie-alles-anfing-933544",
        "Eine Führung verbindet die Erfahrungen der NS-Diktatur mit der Arbeit des "
        "Parlamentarischen Rats. Woran arbeitete der Rat?",
        "Am Grundgesetz für die Bundesrepublik Deutschland",
        (
            "Am Einigungsvertrag von 1990",
            "Am Wahlgesetz der Weimarer Nationalversammlung",
            "Am Vertrag über die Einführung des Euro",
        ),
    ),
    Scenario(
        SOCIETY,
        f"{GG}/__4.html",
        "Ein Nachbarschaftsprojekt lädt Gruppen unterschiedlicher Religionen ein. "
        "Welcher Grundsatz schützt ihre religiöse Überzeugung und Praxis?",
        "Glaubensfreiheit",
        (
            "Bindung an eine Einheitsreligion",
            "Pflicht zur Vereinsmitgliedschaft",
            "Anspruch auf eine bestimmte Lehrstelle",
        ),
    ),
    Scenario(
        SOCIETY,
        f"{GG}/__9.html",
        "Vier Nachbarinnen gründen einen Verein für einen Gemeinschaftsgarten. "
        "Welches Grundrecht betrifft die Bildung eines Vereins unmittelbar?",
        "Vereinigungsfreiheit",
        ("Eigentumsgarantie", "Berufsfreiheit", "Pressefreiheit"),
    ),
    Scenario(
        SOCIETY,
        f"{GG}/__12.html",
        "In einem Workshop über Ausbildung wird erklärt, dass Menschen ihren Beruf "
        "grundsätzlich selbst wählen können. Welches Grundrecht ist damit gemeint?",
        "Berufsfreiheit",
        ("Wahlgeheimnis", "Versammlungsfreiheit", "Petitionsrecht"),
    ),
    Scenario(
        SOCIETY,
        "https://www.gesetze-im-internet.de/agg/__1.html",
        "Eine Personalstelle prüft ihre Stellenanzeigen auf Benachteiligung. Welches "
        "Merkmal nennt das Allgemeine Gleichbehandlungsgesetz ausdrücklich?",
        "Eine Behinderung",
        ("Die Lieblingsfarbe", "Die Art des Fahrrads", "Die bevorzugte Musikrichtung"),
    ),
    Scenario(
        SOCIETY,
        SOCIAL_INSURANCE,
        "Im Betrieb erklärt ein Infoblatt die gesetzlichen Versicherungszweige. "
        "Welcher Zweig ist bei einem versicherten Arbeitsunfall zuständig?",
        "Die gesetzliche Unfallversicherung",
        (
            "Die private Reisegepäckversicherung",
            "Die Kfz-Haftpflicht der Gemeinde",
            "Eine freiwillige Konzertversicherung",
        ),
    ),
    Scenario(
        SOCIETY,
        SOCIAL_INSURANCE,
        "Eine Familie sucht Informationen zu dem Zweig der Sozialversicherung, "
        "der das Risiko der Pflegebedürftigkeit absichert. Welcher ist es?",
        "Die soziale Pflegeversicherung",
        ("Die Hausratversicherung", "Die Rechtsschutzversicherung", "Die Rentenberatung"),
    ),
    Scenario(
        SOCIETY,
        f"{GG}/__10.html",
        "Für einen Medienworkshop erklärt Deniz, warum vertrauliche Briefe besonders "
        "geschützt sind. Welcher Begriff gehört auf seine Folie?",
        "Briefgeheimnis",
        (
            "Grundsatz der Öffentlichkeit von Wahlen",
            "Recht auf eine Petition",
            "Kommunale Selbstverwaltung",
        ),
    ),
    Scenario(
        SOCIETY,
        f"{GG}/__6.html",
        "Eine Beratungsstelle erklärt, wer nach dem Grundgesetz das natürliche Recht und "
        "die vorrangige Pflicht zur Erziehung von Kindern hat. Wer ist gemeint?",
        "Die Eltern",
        ("Die Bundesbank", "Der Bundestag", "Die Gemeinde unabhängig vom Einzelfall"),
    ),
)

BERLIN_SCENARIOS = (
    Scenario(
        STATE,
        BERLIN,
        "Für eine Berliner Stadtführung zeichnet Nuri je ein Feld für jeden Bezirk "
        "auf einen Stadtplan. Wie viele Felder braucht er?",
        "Zwölf",
        ("Acht", "Sechzehn", "Zwanzig"),
    ),
    Scenario(
        STATE,
        BERLIN_POLITICS,
        "Eine Berliner Lokalzeitung soll ein Bild der Landesregierung beschriften. "
        "Welcher Name passt?",
        "Senat von Berlin",
        ("Bundestag", "Bezirksamt Mitte", "Landtag Brandenburg"),
    ),
    Scenario(
        STATE,
        BERLIN_POLITICS,
        "In einem Berliner Planspiel stimmen die Mitglieder der Landesvertretung "
        "über einen Entwurf für ein Landesgesetz ab. Welche Institution spielen sie nach?",
        "Das Abgeordnetenhaus von Berlin",
        ("Die Berliner Feuerwehr", "Den Bundesrat", "Das Bezirksamt Pankow"),
    ),
)
