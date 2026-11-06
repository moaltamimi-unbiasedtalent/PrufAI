"""Original AI-assisted civic-knowledge drafts. Independent review is still required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.lid_editorial.sources import (
    BERLIN,
    GII,
    S4_ENTRIES,
    editorial_replacement_source,
    editorial_source_snapshot,
    editorial_source_version,
    editorial_sources,
    register_editorial_source,
)

__all__ = [
    "ORIGINAL_DRAFTS",
    "S4_ENTRIES",
    "EditorialClaim",
    "EditorialDraft",
    "editorial_replacement_source",
    "editorial_source_snapshot",
    "editorial_source_version",
    "editorial_sources",
    "register_editorial_source",
]

GENERAL = "GENERAL_DE"
STATE = "BUNDESLAND_SPECIFIC"
POLITICS = "Politik in der Demokratie"
SOCIETY = "Mensch und Gesellschaft"
HISTORY = "Geschichte und Verantwortung"
BERLIN_TOPIC = "Landesspezifische Kenntnisse"
VERIFIED_ON = date(2026, 11, 6)


@dataclass(frozen=True)
class EditorialClaim:
    claim_code: str
    source_key: str
    reference_url: str
    locator: str
    fact: str
    last_verified: date
    recheck_after: date
    recheck_trigger: str
    applicability_as_of: date | None = None
    legal_effective_from: date | None = None
    effective_date_basis: str | None = None
    effective_date_reference: str | None = None
    historical_event_date: date | None = None

    def public(self) -> dict[str, Any]:
        return {
            "claim_code": self.claim_code,
            "source_key": self.source_key,
            "reference_url": self.reference_url,
            "locator": self.locator,
            "fact": self.fact,
            "last_verified": self.last_verified.isoformat(),
            "recheck_after": self.recheck_after.isoformat(),
            "recheck_trigger": self.recheck_trigger,
            "applicability_as_of": (
                self.applicability_as_of.isoformat() if self.applicability_as_of else None
            ),
            "legal_effective_from": (
                self.legal_effective_from.isoformat() if self.legal_effective_from else None
            ),
            "effective_date_basis": self.effective_date_basis,
            "effective_date_reference": self.effective_date_reference,
            "historical_event_date": (
                self.historical_event_date.isoformat() if self.historical_event_date else None
            ),
        }


@dataclass(frozen=True)
class EditorialDraft:
    code: str
    scope: str
    jurisdiction: str
    topic: str
    prompt: str
    options: tuple[str, str, str, str]
    correct_index: int
    rationale: str
    claim: EditorialClaim

    def question_content(self) -> dict[str, Any]:
        options = [
            {"option_id": f"choice-{index + 1}", "display_text": option, "order": index}
            for index, option in enumerate(self.options)
        ]
        return {
            "kind": "MULTIPLE_CHOICE",
            "prompt": self.prompt,
            "options": options,
            "correct_option_ids": [options[self.correct_index]["option_id"]],
        }


def _legal_draft(
    number: int,
    topic: str,
    prompt: str,
    options: tuple[str, str, str, str],
    correct_index: int,
    rationale: str,
    *,
    source: str,
    locator: str,
    fact: str,
    url: str,
    effective_from: date | None = None,
    effective_reference: str | None = None,
) -> EditorialDraft:
    berlin = source == "vbb"
    code = f"LID-S4-{'B' if berlin else 'G'}{number:02d}"
    entry = S4_ENTRIES[source]
    effective_url: str | None
    if effective_from is not None:
        basis = "RELEVANT_LEGAL_CHANGE"
        effective_url = effective_reference or entry.url
    elif entry.effective_from is not None:
        effective_from = entry.effective_from.date()
        basis = "CITED_EDITION_IN_FORCE"
        effective_url = entry.edition_url
    else:
        basis = "OBSERVED_IN_FORCE_AS_OF"
        effective_url = None
    return EditorialDraft(
        code,
        STATE if berlin else GENERAL,
        "DE-BE" if berlin else "DE",
        topic,
        prompt,
        options,
        correct_index,
        rationale,
        EditorialClaim(
            f"{code}-CLAIM",
            source,
            url,
            locator,
            fact,
            VERIFIED_ON,
            VERIFIED_ON + timedelta(days=90),
            "AMENDMENT_SOURCE_SUPERSESSION_WITHDRAWAL_OR_DATE",
            applicability_as_of=VERIFIED_ON,
            legal_effective_from=effective_from,
            effective_date_basis=basis,
            effective_date_reference=effective_url,
        ),
    )


def _constitutional_draft(
    number: int,
    article: int,
    clause: str,
    prompt: str,
    options: tuple[str, str, str, str],
    correct_index: int,
    rationale: str,
    fact: str,
    *,
    topic: str = POLITICS,
) -> EditorialDraft:
    return _legal_draft(
        number,
        topic,
        prompt,
        options,
        correct_index,
        rationale,
        source="gg",
        locator=f"Art. {article} {clause}",
        fact=fact,
        url=f"{GII}/gg/art_{article}.html",
    )


def _historical_draft(
    number: int,
    prompt: str,
    options: tuple[str, str, str, str],
    correct_index: int,
    rationale: str,
    *,
    source: str,
    locator: str,
    fact: str,
    event_date: date,
) -> EditorialDraft:
    code = f"LID-S4-G{number:02d}"
    return EditorialDraft(
        code,
        GENERAL,
        "DE",
        HISTORY,
        prompt,
        options,
        correct_index,
        rationale,
        EditorialClaim(
            f"{code}-CLAIM",
            source,
            S4_ENTRIES[source].url,
            locator,
            fact,
            VERIFIED_ON,
            VERIFIED_ON + timedelta(days=365),
            "SOURCE_CORRECTION_SUPERSESSION_WITHDRAWAL_OR_DATE",
            historical_event_date=event_date,
        ),
    )


ORIGINAL_DRAFTS: tuple[EditorialDraft, ...] = (
    _constitutional_draft(
        1,
        13,
        "Abs. 2",
        "Die Polizei plant eine Wohnungsdurchsuchung. Es ist genug Zeit, eine Anordnung "
        "einzuholen. Wer ist nach dem Grundgesetz grundsätzlich dafür zuständig?",
        (
            "Die Leitung der Polizeidienststelle",
            "Die Leitung der Stadtverwaltung",
            "Eine Richterin oder ein Richter",
            "Die Leitung der Meldebehörde",
        ),
        2,
        "Bei einer geplanten Durchsuchung ohne Eilfall liegt die Anordnung grundsätzlich "
        "beim Gericht.",
        "Art. 13 Abs. 2 sieht eine richterliche Anordnung vor. Für Gefahr im Verzug kann "
        "das Gesetz andere zuständige Stellen bestimmen.",
    ),
    _constitutional_draft(
        2,
        21,
        "Abs. 1 Satz 1",
        "Ein Schaubild erklärt die Rolle politischer Parteien im Bund. Welche Aufgabe gehört "
        "nach dem Grundgesetz zu dieser Rolle?",
        (
            "An der politischen Meinungs- und Willensbildung mitwirken",
            "Streitfälle zwischen Bürgerinnen und Bürgern entscheiden",
            "Das Abstimmungsergebnis nach einer Wahl amtlich feststellen",
            "Beschlossene Bundesgesetze selbst amtlich verkünden",
        ),
        0,
        "Parteien bringen politische Ideen und Interessen in die öffentliche "
        "Auseinandersetzung ein. So wirken sie daran mit, wie das Volk politisch Position bezieht.",
        "Art. 21 Abs. 1 Satz 1 weist den Parteien eine mitwirkende Rolle bei der politischen "
        "Willensbildung des Volkes zu.",
    ),
    _constitutional_draft(
        3,
        30,
        "einziger Satz",
        "Eine staatliche Aufgabe ist zu verteilen. Das Grundgesetz sieht für sie keine andere "
        "Zuständigkeit vor. Bei wem liegt die Aufgabe nach der allgemeinen Regel?",
        (
            "Bei der Bundesverwaltung",
            "Bei der Europäischen Union",
            "Bei den Bundesgerichten",
            "Bei den Ländern",
        ),
        3,
        "Staatliche Aufgaben liegen grundsätzlich bei den Ländern. Eine andere Verteilung "
        "muss das Grundgesetz vorsehen oder zulassen.",
        "Art. 30 legt die Zuständigkeit für staatliche Befugnisse und Aufgaben grundsätzlich "
        "bei den Ländern fest.",
    ),
    _constitutional_draft(
        4,
        42,
        "Abs. 1",
        "Eine Bürgerin will eine Debatte im Bundestag verfolgen. Wie sind die Verhandlungen "
        "grundsätzlich organisiert, wenn keine Ausnahme beschlossen wurde?",
        (
            "Sie sind nur für die Landesregierungen zugänglich.",
            "Sie finden öffentlich statt.",
            "Sie sind nur für Mitglieder einer Partei zugänglich.",
            "Sie finden unter Ausschluss der Öffentlichkeit statt.",
        ),
        1,
        "Der Bundestag berät grundsätzlich öffentlich. Für den Ausschluss der Öffentlichkeit "
        "gelten besondere Voraussetzungen.",
        "Art. 42 Abs. 1 bestimmt öffentliche Verhandlungen als Regelfall. Für eine Ausnahme "
        "ist ein besonderer Beschluss nötig.",
    ),
    _constitutional_draft(
        5,
        44,
        "Abs. 1 Satz 1",
        "Ein Viertel der Mitglieder des Bundestages beantragt die Einsetzung eines "
        "Untersuchungsausschusses. Was verlangt das Grundgesetz vom Bundestag?",
        (
            "Er muss den Ausschuss einsetzen.",
            "Er muss den Antrag an die Länder abgeben.",
            "Er muss zuerst eine Volksabstimmung abhalten.",
            "Er muss auf einen Antrag der Bundesregierung warten.",
        ),
        0,
        "Ein Antrag dieses Anteils der Mitglieder reicht aus. Auch ohne Antrag der "
        "Regierung muss der Bundestag dann einen Untersuchungsausschuss einsetzen.",
        "Art. 44 Abs. 1 begründet bei einem Antrag von einem Viertel der Bundestagsmitglieder "
        "die Pflicht zur Einsetzung eines Untersuchungsausschusses.",
    ),
    _constitutional_draft(
        6,
        51,
        "Abs. 1",
        "Ein Bundesland entsendet Mitglieder in den Bundesrat. Wer bestellt die Personen, "
        "die das Land dort vertreten?",
        (
            "Die gewählten Mitglieder des Bundestages aus dem Land",
            "Die Wahlberechtigten des Landes in einer Direktwahl",
            "Die Regierung des Landes aus ihren eigenen Reihen",
            "Die Bundesregierung auf Vorschlag der Gemeinden",
        ),
        2,
        "Die Landesregierungen bestimmen die Mitglieder des Bundesrates aus dem eigenen "
        "Kreis und können sie auch abberufen.",
        "Art. 51 Abs. 1 regelt die Zusammensetzung des Bundesrates aus Mitgliedern der "
        "Landesregierungen und deren Bestellung durch die jeweiligen Landesregierungen.",
    ),
    _constitutional_draft(
        7,
        54,
        "Abs. 1 Satz 1",
        "Die Amtszeit der Bundespräsidentin oder des Bundespräsidenten endet. Welches Organ "
        "wählt das Staatsoberhaupt für die nächste Amtszeit?",
        (
            "Der Bundesrat",
            "Die Bundesversammlung",
            "Der Bundestag allein",
            "Das Bundeskabinett",
        ),
        1,
        "Die Bundesversammlung wird für die Wahl der Bundespräsidentin oder des "
        "Bundespräsidenten einberufen.",
        "Art. 54 Abs. 1 weist die Wahl des Bundespräsidenten der Bundesversammlung zu.",
    ),
    _constitutional_draft(
        8,
        65,
        "Satz 2",
        "In einem Bundesministerium steht eine Entscheidung an. Sie liegt im eigenen "
        "Aufgabenbereich und entspricht den politischen Richtlinien. Wer leitet dieses "
        "Ministerium in eigener Verantwortung?",
        (
            "Die Präsidentin oder der Präsident des Bundestages",
            "Die Ministerpräsidentin oder der Ministerpräsident des Sitzlandes",
            "Der zuständige Ausschuss im Bundesrat",
            "Die zuständige Bundesministerin oder der zuständige Bundesminister",
        ),
        3,
        "Innerhalb der politischen Richtlinien leiten die Bundesministerinnen und "
        "Bundesminister ihr jeweiliges Ressort eigenverantwortlich.",
        "Art. 65 Satz 2 weist die selbstständige und eigenverantwortliche Leitung "
        "jedes Ressorts innerhalb der Richtlinien dem zuständigen Bundesminister zu.",
    ),
    _constitutional_draft(
        9,
        76,
        "Abs. 1",
        "Gesetzentwürfe können von der Bundesregierung und aus der Mitte des Bundestages "
        "eingebracht werden. Welches weitere Organ ist dazu berechtigt?",
        (
            "Die Bundesversammlung",
            "Das Bundesverfassungsgericht",
            "Der Bundesrat",
            "Der Bundesrechnungshof",
        ),
        2,
        "Der Bundesrat kann eigene Gesetzentwürfe in das parlamentarische Verfahren einbringen.",
        "Art. 76 Abs. 1 nennt den Bundesrat als einen der drei möglichen Initiatoren "
        "einer Gesetzesvorlage.",
    ),
    _constitutional_draft(
        10,
        77,
        "Abs. 2 Satz 1",
        "Bei einem Bundesgesetz suchen Bundestag und Bundesrat einen Kompromiss. Welches "
        "gemeinsame Gremium ist dafür vorgesehen?",
        (
            "Der Vermittlungsausschuss",
            "Der Petitionsausschuss",
            "Der Gemeinsame Ausschuss für den Verteidigungsfall",
            "Der Haushaltsausschuss",
        ),
        0,
        "Im Vermittlungsausschuss beraten Mitglieder beider Häuser über eine Lösung "
        "für das Gesetzgebungsverfahren.",
        "Art. 77 Abs. 2 sieht einen aus Bundestag und Bundesrat gebildeten Ausschuss zur "
        "gemeinsamen Beratung von Vorlagen vor.",
    ),
    _constitutional_draft(
        11,
        82,
        "Abs. 1",
        "Eine Redaktion sucht die amtliche Verkündung eines neuen Bundesgesetzes. Wo findet "
        "sie diese Verkündung nach dem Grundgesetz?",
        (
            "Im Bundesanzeiger",
            "Im Plenarprotokoll des Bundestages",
            "Im Gemeinsamen Ministerialblatt",
            "Im Bundesgesetzblatt",
        ),
        3,
        "Bundesgesetze werden im Bundesgesetzblatt verkündet. Ein Bericht über die "
        "Abstimmung im Bundestag ist ein anderes Dokument.",
        "Art. 82 Abs. 1 nennt das Bundesgesetzblatt als Ort der Verkündung von Bundesgesetzen.",
    ),
    _constitutional_draft(
        12,
        103,
        "Abs. 1",
        "Eine Person möchte einem Gericht erklären, warum sie eine wichtige Behauptung der "
        "Gegenseite bestreitet. Welches Recht schützt die Möglichkeit, Stellung zu nehmen?",
        (
            "Das Recht auf Zugang zu öffentlichen Ämtern",
            "Das Recht auf rechtliches Gehör",
            "Das Recht auf freie Berufswahl",
            "Das Recht auf eine Petition an den Bundestag",
        ),
        1,
        "Rechtliches Gehör gibt Beteiligten die Möglichkeit, ihre Sicht in das Verfahren "
        "einzubringen. Das Gericht muss dieses Vorbringen berücksichtigen.",
        "Art. 103 Abs. 1 garantiert den Anspruch auf rechtliches Gehör vor Gericht.",
    ),
    _legal_draft(
        13,
        SOCIETY,
        "Eine Beratungsstelle erstellt eine Zeitleiste zum Erwachsenwerden. Mit welchem "
        "Geburtstag beginnt nach dem Bürgerlichen Gesetzbuch die Volljährigkeit?",
        (
            "Mit dem 16. Geburtstag",
            "Mit dem 17. Geburtstag",
            "Mit dem 21. Geburtstag",
            "Mit dem 18. Geburtstag",
        ),
        3,
        "Wer 18 Jahre alt wird, ist nach dem Bürgerlichen Gesetzbuch volljährig.",
        source="bgb",
        locator="§ 2",
        fact="Nach § 2 beginnt die Volljährigkeit mit dem Abschluss des 18. Lebensjahres.",
        url=f"{GII}/bgb/__2.html",
        effective_from=date(1975, 1, 1),
        effective_reference="https://www.bundestag.de/resource/blob/683092/WD-7-002-20-pdf.pdf",
    ),
    _legal_draft(
        14,
        SOCIETY,
        "Ein Standesamt erklärt in einem Infoblatt, wer heiraten kann. Welche Aussage zum "
        "Geschlecht der beiden Personen entspricht dem Bürgerlichen Gesetzbuch?",
        (
            "Die Ehe steht nur Personen unterschiedlichen Geschlechts offen.",
            "Die Ehe steht Personen gleichen oder unterschiedlichen Geschlechts offen.",
            "Paare gleichen Geschlechts können ausschließlich eine neue Lebenspartnerschaft "
            "eintragen lassen.",
            "Paare gleichen Geschlechts dürfen ausschließlich eine Ehe anerkennen lassen, die "
            "sie im Ausland geschlossen haben.",
        ),
        1,
        "Das Geschlecht der beiden Personen beschränkt den Zugang zur Ehe in dieser "
        "Hinsicht nicht. Die übrigen Voraussetzungen für eine Eheschließung gelten weiter.",
        source="bgb",
        locator="§ 1353 Abs. 1 Satz 1",
        fact="§ 1353 Abs. 1 lässt eine Ehe zwischen zwei Personen gleichen oder "
        "unterschiedlichen Geschlechts zu.",
        url=f"{GII}/bgb/__1353.html",
        effective_from=date(2017, 10, 1),
        effective_reference="https://dserver.bundestag.de/btd/19/046/1904670.pdf#page=19",
    ),
    _legal_draft(
        15,
        SOCIETY,
        "Beschäftigte wollen die Einarbeitung neuer Kolleginnen und Kollegen verbessern. "
        "Welche Vertretung im Betrieb nimmt solche Anregungen entgegen und spricht bei "
        "berechtigten Anliegen mit dem Arbeitgeber?",
        (
            "Der Aufsichtsrat",
            "Die Geschäftsführung",
            "Der Betriebsrat",
            "Die Berufsgenossenschaft",
        ),
        2,
        "Der Betriebsrat nimmt Anregungen der Beschäftigten auf. Sind sie berechtigt, "
        "verhandelt er mit dem Arbeitgeber darüber.",
        source="betrvg",
        locator="§ 80 Abs. 1 Nr. 3",
        fact="Die allgemeinen Aufgaben des Betriebsrats umfassen die Annahme von Anregungen "
        "aus der Belegschaft und Verhandlungen über berechtigte Anregungen.",
        url=f"{GII}/betrvg/__80.html",
    ),
    _legal_draft(
        16,
        SOCIETY,
        "Eine Person beginnt eine duale Ausbildung. Wer muss dafür sorgen, dass die "
        "praktischen Ausbildungsinhalte im Betrieb planmäßig vermittelt werden?",
        (
            "Der Ausbildungsbetrieb",
            "Die zuständige Krankenkasse",
            "Die Berufsschule",
            "Die Agentur für Arbeit",
        ),
        0,
        "Der Ausbildungsbetrieb muss die beruflichen Fertigkeiten planmäßig vermitteln. "
        "Er bildet selbst aus oder beauftragt ausdrücklich eine geeignete Person.",
        source="bbig",
        locator="§ 14 Abs. 1 Nr. 1 und 2",
        fact="§ 14 Abs. 1 verpflichtet den Ausbildenden, die betriebliche Ausbildung "
        "planmäßig durchzuführen und eine verantwortliche Ausbildungsperson zu benennen, "
        "wenn er nicht selbst ausbildet.",
        url=f"{GII}/bbig_2005/__14.html",
        effective_from=date(2005, 4, 1),
        effective_reference="https://www.bibb.de/de/120993.php",
    ),
    _legal_draft(
        17,
        SOCIETY,
        "Eine beschäftigte Person fühlt sich im Betrieb wegen ihres Alters benachteiligt. "
        "Welche Möglichkeit innerhalb des Betriebs sieht das Allgemeine "
        "Gleichbehandlungsgesetz vor?",
        (
            "Sie kann nur über eine Betriebsversammlung Beschwerde einlegen.",
            "Sie kann nur dann Beschwerde einlegen, wenn die Leitung vorab zustimmt.",
            "Sie kann sich bei der zuständigen Beschwerdestelle des Betriebs beschweren.",
            "Sie kann eine Beschwerde erst nach Ende des Arbeitsverhältnisses einreichen.",
        ),
        2,
        "Beschäftigte können sich bei der zuständigen Stelle beschweren. Die Stelle muss "
        "die Beschwerde prüfen und das Ergebnis mitteilen.",
        source="agg",
        locator="§ 13 Abs. 1 in Verbindung mit § 1",
        fact="§ 13 Abs. 1 gibt Beschäftigten bei einer empfundenen Benachteiligung aus "
        "einem in § 1 genannten Grund ein Beschwerderecht. § 1 nennt das Alter.",
        url=f"{GII}/agg/__13.html",
        effective_from=date(2006, 8, 18),
    ),
    _legal_draft(
        18,
        SOCIETY,
        "Eine eingeteilte Schicht fällt allein wegen eines gesetzlichen Feiertags aus. "
        "Die beschäftigte Person hat vor und nach dem Feiertag wie geplant gearbeitet. "
        "Welches Entgelt steht ihr für die ausgefallene Schicht zu?",
        (
            "Kein Entgelt, weil sie an dem Tag nicht gearbeitet hat",
            "Das Entgelt, das sie für die Schicht ohne Feiertag erhalten hätte",
            "Das Entgelt erst, nachdem sie die Schicht nachgeholt hat",
            "Immer das Doppelte des üblichen Entgelts für die Schicht",
        ),
        1,
        "Fällt geplante Arbeitszeit wegen des Feiertags aus, gilt der Verdienst, den die "
        "Person ohne den Feiertag erhalten hätte.",
        source="entgfg",
        locator="§ 2 Abs. 1 und 3",
        fact="§ 2 Abs. 1 sichert das wegen eines gesetzlichen Feiertags ausgefallene "
        "Arbeitsentgelt. Unentschuldigtes Fehlen unmittelbar davor oder danach ist ein "
        "Ausschlussgrund nach Abs. 3.",
        url=f"{GII}/entgfg/BJNR106500994.html",
        effective_from=date(1994, 6, 1),
    ),
    _legal_draft(
        19,
        SOCIETY,
        "In einem Kurs erklärt eine Gruppe die Sozialversicherung. Welche Aufgabe gehört "
        "nach dem Sozialgesetzbuch zur gesetzlichen Krankenversicherung?",
        (
            "Einkommensverluste bei Arbeitslosigkeit ersetzen",
            "Altersrenten für alle Beschäftigten auszahlen",
            "Familien beim Bau von Eigenheimen unterstützen",
            "Die Gesundheit der Versicherten erhalten und wiederherstellen helfen",
        ),
        3,
        "Die gesetzliche Krankenversicherung hat den Auftrag, die Gesundheit der "
        "Versicherten zu erhalten, wiederherzustellen oder zu verbessern.",
        source="sgb_v",
        locator="§ 1 Satz 1",
        fact="§ 1 Satz 1 nennt Erhaltung, Wiederherstellung und Besserung der Gesundheit "
        "der Versicherten als Aufgabe der gesetzlichen Krankenversicherung.",
        url=f"{GII}/sgb_5/__1.html",
        effective_from=date(1989, 1, 1),
        effective_reference=f"{GII}/grg/art_79.html",
    ),
    _legal_draft(
        20,
        SOCIETY,
        "Eine Person zieht dauerhaft in eine neue Wohnung. Für sie gilt keine Ausnahme "
        "von der Meldepflicht. Bis wann muss sie sich nach dem Einzug bei der "
        "Meldebehörde anmelden?",
        (
            "Innerhalb von zwei Wochen",
            "Innerhalb von drei Monaten",
            "Spätestens am Ende des Kalenderjahrs",
            "Erst nach einer schriftlichen Aufforderung",
        ),
        0,
        "Die allgemeine Frist für die Anmeldung einer Wohnung beträgt zwei Wochen ab dem Einzug.",
        source="bmg",
        locator="§ 17 Abs. 1",
        fact="§ 17 Abs. 1 legt für die allgemeine Anmeldung einer bezogenen Wohnung "
        "eine Frist von zwei Wochen nach dem Einzug fest.",
        url=f"{GII}/bmg/__17.html",
        effective_from=date(2015, 11, 1),
    ),
    _legal_draft(
        21,
        SOCIETY,
        "Eine Gemeinde erklärt die gesetzliche Rangfolge im Umgang mit Abfällen. "
        "Welche Maßnahme steht im Kreislaufwirtschaftsgesetz an erster Stelle?",
        (
            "Abfälle recyceln",
            "Abfälle vermeiden",
            "Abfälle zur Energiegewinnung nutzen",
            "Abfälle beseitigen",
        ),
        1,
        "In der gesetzlichen Reihenfolge kommt die Vermeidung vor der Verwertung "
        "oder Beseitigung von Abfällen.",
        source="krwg",
        locator="§ 6 Abs. 1 Nr. 1",
        fact="§ 6 Abs. 1 führt die Vermeidung an erster Stelle der Abfallhierarchie auf. "
        "Abs. 2 bestimmt, wie die Rangfolge im Einzelfall anzuwenden ist.",
        url=f"{GII}/krwg/__6.html",
        effective_from=date(2012, 6, 1),
    ),
    _constitutional_draft(
        22,
        7,
        "Abs. 1",
        "Eine Schule hat einen privaten Träger. Welche Aussage zur Aufsicht über "
        "Schulen gilt nach dem Grundgesetz auch in diesem Fall?",
        (
            "Nur die Eltern üben die Aufsicht aus.",
            "Die Aufsicht liegt allein bei der Schulleitung.",
            "Eine privat getragene Schule unterliegt keiner staatlichen Aufsicht.",
            "Auch eine privat getragene Schule steht unter staatlicher Aufsicht.",
        ),
        3,
        "Das Grundgesetz stellt das gesamte Schulwesen unter staatliche Aufsicht. "
        "Die private Trägerschaft ändert daran nichts.",
        "Art. 7 Abs. 1 erstreckt die staatliche Aufsicht auf das gesamte Schulwesen.",
        topic=SOCIETY,
    ),
    _historical_draft(
        23,
        "Ein Foto von 1948 zeigt, wie in West-Berlin Mehlsäcke aus einem Flugzeug "
        "ausgeladen werden. Warum kamen damals so viele Versorgungsflüge in die Stadt?",
        (
            "Die Flughäfen sollten den gesamten Reiseverkehr Deutschlands übernehmen.",
            "Eine Überschwemmung hatte die Verkehrswege nach Berlin zerstört.",
            "Die sowjetische Blockade sperrte die Zugänge über Straßen, Schienen und Wasser.",
            "Die Flüge dienten der Versorgung einer internationalen Ausstellung.",
        ),
        2,
        "Während der Berlin-Blockade brachten die Westalliierten Lebensmittel und andere "
        "Güter auf dem Luftweg nach West-Berlin.",
        source="berlin-airlift",
        locator="Beginn der Blockade und der Luftbrücke im Juni 1948",
        fact="Nach der Sperrung der Zugänge versorgten die Westalliierten West-Berlin "
        "ab Juni 1948 über die Luftbrücke.",
        event_date=date(1948, 6, 26),
    ),
    _historical_draft(
        24,
        "Ein Archiv zeigt Fotos von streikenden Bauarbeitern in Ost-Berlin kurz vor "
        "dem 17. Juni 1953. Wogegen richtete sich ihr Protest zu Beginn?",
        (
            "Gegen höhere Vorgaben für die Arbeitsleistung",
            "Gegen den geplanten Wechsel zu kürzeren Arbeitswochen",
            "Gegen die Einführung des freien Wochenendes",
            "Gegen einen gesetzlichen Mindestlohn",
        ),
        0,
        "Erhöhte Arbeitsnormen waren ein Auslöser der Streiks. Daraus entwickelte sich "
        "ein breiter Protest, der auch politische Forderungen umfasste.",
        source="gdr-1953",
        locator="Normerhöhungen führen zu Unruhe und Beginn der Demonstrationen auf den Baustellen",
        fact="Die Erhöhung der Arbeitsnormen löste im Juni 1953 Proteste von Bauarbeitern "
        "aus, die dem landesweiten Aufstand vorausgingen.",
        event_date=date(1953, 6, 16),
    ),
    _historical_draft(
        25,
        "Am 27. Januar gedenkt der Bundestag der Opfer des Nationalsozialismus. "
        "Welches Ereignis aus dem Jahr 1945 erklärt die Wahl des Datums?",
        (
            "Die Befreiung des Lagers Buchenwald",
            "Die Befreiung des Lagers Dachau",
            "Die Befreiung des Lagers Bergen-Belsen",
            "Die Befreiung des Lagers Auschwitz",
        ),
        3,
        "Am 27. Januar 1945 befreiten sowjetische Soldaten den Lagerkomplex Auschwitz. "
        "Das Datum ist heute ein Gedenktag für die Opfer des Nationalsozialismus.",
        source="auschwitz-liberation",
        locator="Einleitung zur Befreiung des Lagers am 27. Januar 1945",
        fact="Auschwitz wurde am 27. Januar 1945 von der Roten Armee befreit.",
        event_date=date(1945, 1, 27),
    ),
    _historical_draft(
        26,
        "Ein Archivordner enthält Flugblätter der Weißen Rose aus der Zeit der "
        "NS-Diktatur. Welches Ziel verfolgte die Gruppe mit solchen Texten?",
        (
            "Sie wollte neue Mitglieder für die NSDAP gewinnen.",
            "Sie wollte Menschen zum Widerstand gegen die Diktatur bewegen.",
            "Sie wollte den staatlichen Luftschutz bekannt machen.",
            "Sie wollte Lebensmittelspenden für die Wehrmacht organisieren.",
        ),
        1,
        "Die Weiße Rose verbreitete Flugblätter gegen die NS-Herrschaft. Damit rief "
        "die Gruppe zum Widerstand auf.",
        source="white-rose",
        locator="Einleitung und Erläuterungen zur Verbreitung der Flugblätter",
        fact="Die Weiße Rose setzte Flugblätter als Mittel des Widerstands gegen "
        "den Nationalsozialismus ein.",
        event_date=date(1943, 2, 18),
    ),
    _historical_draft(
        27,
        "Ein Text über das Verhältnis zwischen der Bundesrepublik und der DDR "
        "beschreibt neue Vertretungen an den Regierungssitzen. Welche Bezeichnung "
        "sah der Grundlagenvertrag von 1972 für diese Vertretungen vor?",
        (
            "Ständige Vertretungen",
            "Gemeinsame Ministerien",
            "Parlamentarische Wahlbüros",
            "Ordentliche Botschaften",
        ),
        0,
        "Beide deutsche Staaten vereinbarten Ständige Vertretungen am jeweiligen "
        "Regierungssitz. Der Vertrag bezeichnete sie als Ständige Vertretungen.",
        source="basic-treaty",
        locator=(
            "Einleitung zur Unterzeichnung und Abschnitt Keine Einigung in grundsätzlichen Fragen"
        ),
        fact="Der am 21. Dezember 1972 unterzeichnete Grundlagenvertrag sah vor, "
        "Ständige Vertreter statt Botschafter zu entsenden.",
        event_date=date(1972, 12, 21),
    ),
    _historical_draft(
        28,
        "Ein Zeitungsfoto zeigt Bundeskanzler Willy Brandt im Dezember 1970 "
        "bei einem Gedenken in Warschau. Woran erinnerte das Denkmal, vor dem "
        "er niederkniete?",
        (
            "An den Warschauer Aufstand von 1944",
            "An die Gründung der Stadt Warschau",
            "An den Aufstand im Warschauer Ghetto von 1943",
            "An die polnische Verfassung von 1791",
        ),
        2,
        "Brandt kniete vor dem Denkmal für den Aufstand im Warschauer Ghetto. "
        "Das Gedenken galt dem jüdischen Widerstand und den Opfern.",
        source="warsaw-1970",
        locator="Rede vom 7. Dezember 2020, Rückblick auf das Gedenken am Ghetto-Denkmal",
        fact="Am 7. Dezember 1970 kniete Willy Brandt vor dem Denkmal für den "
        "Aufstand im Warschauer Ghetto.",
        event_date=date(1970, 12, 7),
    ),
    _historical_draft(
        29,
        "Ein Ausstellungstext erzählt von sechs Ländern, die 1957 in Rom Verträge "
        "unterzeichneten. Welche Gemeinschaft zur wirtschaftlichen Zusammenarbeit "
        "wurde mit diesen Verträgen gegründet?",
        (
            "Der Europarat",
            "Die Europäische Wirtschaftsgemeinschaft",
            "Die Europäische Freihandelsassoziation",
            "Die Europäische Gemeinschaft für Kohle und Stahl",
        ),
        1,
        "Die sechs Staaten schlossen in Rom den Gründungsvertrag der Europäischen "
        "Wirtschaftsgemeinschaft. Ein weiterer Vertrag gründete Euratom.",
        source="rome-treaties",
        locator="Glossareintrag Römische Verträge, Unterzeichnung am 25. März 1957",
        fact="Die am 25. März 1957 unterzeichneten Römischen Verträge gründeten "
        "die Europäische Wirtschaftsgemeinschaft und Euratom.",
        event_date=date(1957, 3, 25),
    ),
    _historical_draft(
        30,
        "Für eine historische Zeitleiste soll der 8. Mai 1945 eingeordnet werden. "
        "Welches Ereignis wird in Deutschland mit diesem Datum verbunden?",
        (
            "Der Beginn der alliierten Landung in der Normandie",
            "Die Gründung der Bundesrepublik Deutschland",
            "Das Ende des Zweiten Weltkriegs auch im Pazifik",
            "Das Ende des Zweiten Weltkriegs in Europa",
        ),
        3,
        "Mit der bedingungslosen Kapitulation der Wehrmacht endete der Krieg in "
        "Europa. Im Pazifik ging der Krieg über den Mai hinaus weiter.",
        source="war-end-europe",
        locator="Einleitung der Gedenkstunde zum 80. Jahrestag des Kriegsendes",
        fact="Der 8. Mai 1945 steht für die bedingungslose Kapitulation der Wehrmacht "
        "und das Ende des Zweiten Weltkriegs in Europa.",
        event_date=date(1945, 5, 8),
    ),
    _legal_draft(
        1,
        BERLIN_TOPIC,
        "In einem Verzeichnis steht Berlin sowohl unter den deutschen Ländern als "
        "auch unter den Städten. Welche Erklärung passt zur Verfassung von Berlin?",
        (
            "Berlin ist gleichzeitig eine Stadt und ein deutsches Land.",
            "Berlin ist eine Stadt im Land Brandenburg.",
            "Berlin ist ein deutsches Land, aber keine Stadt.",
            "Berlin ist ein Bundesgebiet ohne den Status eines Landes.",
        ),
        0,
        "Die Berliner Verfassung beschreibt Berlin als Stadt und zugleich als Land "
        "der Bundesrepublik.",
        source="vbb",
        locator="Art. 1 Abs. 1",
        fact="Art. 1 Abs. 1 bestimmt Berlins Doppelstellung als deutsches Land und Stadt.",
        url=f"{BERLIN}/verfassung-von-berlin-abschnitt-i-die-grundlagen-41549.php",
    ),
    _legal_draft(
        2,
        BERLIN_TOPIC,
        "In einem Berliner Bezirk geht es um die Kontrolle der Bezirksverwaltung. "
        "Welchem Organ weist die Verfassung von Berlin diese Aufgabe auf Bezirksebene zu?",
        (
            "Dem Rat der Bürgermeister",
            "Dem Senat von Berlin",
            "Dem Bezirksamt eines benachbarten Bezirks",
            "Der Bezirksverordnetenversammlung des Bezirks",
        ),
        3,
        "Die Bezirksverordnetenversammlung ist das Organ der bezirklichen "
        "Selbstverwaltung, das die Verwaltung des Bezirks kontrolliert.",
        source="vbb",
        locator="Art. 72 Abs. 1",
        fact="Art. 72 Abs. 1 weist der Bezirksverordnetenversammlung die Kontrolle "
        "über die Bezirksverwaltung zu.",
        url=f"{BERLIN}/artikel.41514.php",
    ),
    _legal_draft(
        3,
        BERLIN_TOPIC,
        "Eine Berliner Initiative will mit einem Volksbegehren ein Gesetz anstoßen. "
        "Welche Voraussetzung zum Thema des Gesetzes nennt die Verfassung von Berlin?",
        (
            "Für das Gesetz muss allein der Bund zuständig sein.",
            "Das Gesetz muss für alle deutschen Länder gelten.",
            "Das Land Berlin muss für das Gesetz zuständig sein.",
            "Der Senat muss das Gesetz bereits beschlossen haben.",
        ),
        2,
        "Bei diesem Weg der direkten Demokratie muss das Land Berlin für den "
        "Gegenstand des Gesetzes zuständig sein.",
        source="vbb",
        locator="Art. 62 Abs. 1 Satz 1",
        fact="Ein Volksbegehren über ein Gesetz setzt nach Art. 62 Abs. 1 voraus, "
        "dass Berlin die Gesetzgebungskompetenz für den Gegenstand hat.",
        url=f"{BERLIN}/artikel.41525.php",
    ),
)
