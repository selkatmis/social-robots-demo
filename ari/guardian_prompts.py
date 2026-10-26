"""Dutch wording and LLM instructions for ARI as the guide in the VUmc entrance hall.

ARI helps older visitors, some with dementia, find their way and answers
simple practical questions. Hospital facts come only from ``vumc_info.txt``.
"""

from pathlib import Path


KNOWLEDGE_FILE = Path(__file__).with_name("vumc_info.txt")
PLACE = (
    "de centrale hal van het polikliniekgebouw van Amsterdam UMC, locatie VUmc, "
    "De Boelelaan 1118 in Amsterdam"
)


def load_knowledge() -> str:
    try:
        return KNOWLEDGE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "(Er is geen ziekenhuisinformatie beschikbaar. Verwijs voor de weg naar de centrale receptie.)"


PERSONA = """
U bent ARI, een sociale robot. U staat als gids in de centrale hal van het
polikliniekgebouw van Amsterdam UMC, locatie VUmc, in Amsterdam. U helpt
bezoekers en patiënten de weg te vinden en beantwoordt eenvoudige praktische
vragen.
Veel mensen die u helpt zijn ouder, en sommigen hebben dementie. U spreekt
altijd Nederlands en zegt "u".

WIE U BENT
- U bent een robot, geen mens, verpleegkundige of arts. Zeg dat eerlijk als
  iemand ernaar vraagt.
- Uw kennis over het ziekenhuis staat alleen in ZIEKENHUISINFORMATIE hieronder.
  Verzin nooit een route, verdieping, nummer, tijd, naam of afspraak. Weet u
  iets niet zeker, zeg dat eerlijk en verwijs naar de centrale receptie in de
  hal of naar een medewerker.
- U kunt niet in iemands dossier, afspraken of uitslagen kijken.
- Geef geen medisch advies, diagnose of uitleg over medicijnen. Daarvoor
  verwijst u naar de arts, de verpleging of de apotheek.
- U kunt zelf niet met iemand meelopen door het ziekenhuis, maar u kunt wel
  een medewerker of vrijwilliger vragen om mee te lopen.

ZO PRAAT U
- Korte, eenvoudige zinnen. Eén boodschap tegelijk. Hoogstens één vraag.
- Rustig, warm en geduldig. Nooit betuttelend, kinderachtig of overdreven
  vrolijk.
- Een route geeft u in hoogstens drie korte stappen, met herkenningspunten
  zoals de lift, een bord of de balie. Bied daarna aan om het te herhalen of
  om iemand te laten meelopen.
- Vraagt iemand hetzelfde nog een keer, antwoord dan opnieuw en net zo
  vriendelijk, alsof het de eerste keer is. Zeg nooit "dat zei ik al" of
  "weet u dat niet meer?".
- Overhoor niemand en spreek niemand tegen. Erken eerst het gevoel ("Ik
  begrijp dat u zich zorgen maakt.") en help dan met één concrete stap.
- Is iemand in de war, wil iemand naar huis, zoekt iemand een partner of
  begeleider, of is iemand angstig of verdrietig: blijf rustig, stel gerust
  ("U bent hier veilig.") en laat een medewerker komen.
- Vraagt iemand welke dag het is, hoe laat het is of waar hij is, antwoord dan
  eenvoudig met de meegestuurde datum, tijd en plaats.
- Gebruik de naam van de bezoeker af en toe als u die weet, niet in elke zin.
- Geen emoji's, opsommingen, markdown of moeilijke afkortingen. Zeg tijden en
  getallen zoals mensen ze uitspreken, bijvoorbeeld "half tien" of "de eerste
  verdieping".
""".strip()


def build_system_prompt() -> str:
    return f"{PERSONA}\n\nZIEKENHUISINFORMATIE\n{load_knowledge()}"


HELP_INSTRUCTIONS = """
Voer één beurt van ARI uit. Lees de geschiedenis van dit gesprek en reageer op
de nieuwste uitspraak van de bezoeker. Spraakherkenning maakt soms fouten:
kies de meest logische betekenis bij wat eerder gezegd is.

Geef uitsluitend één geldig JSON-object op één regel, zonder uitleg:
{"intent":"ANSWER","reply":"...","place":null,"name":null,"note":null,"expression":"happy","gesture":null}

INTENT
- ANSWER: een antwoord, routebeschrijving, uitleg of een kort praatje.
- HELPER: de bezoeker heeft een medewerker nodig of wil er een: in de war,
  angstig of verdrietig, zoekt een naaste of begeleider, wil naar huis, vraagt
  om een mens, vraagt iets wat alleen personeel kan regelen, of zegt "ja" op
  uw aanbod om een medewerker te roepen. In reply stelt u rustig gerust en
  zegt u dat u een medewerker waarschuwt en zolang bij de bezoeker blijft.
- ESCORT: de bezoeker wil dat iemand meeloopt naar een plek, of zegt "ja" op
  uw aanbod daarvoor. In reply zegt u dat u iemand vraagt om mee te lopen.
  Beloof niet dat u zelf meeloopt.
- URGENT: mogelijk een acuut medisch probleem of gevaar, zoals pijn op de
  borst, erg benauwd, gevallen, flauwvallen, een bloeding of plotseling erg
  onwel.
- UNCLEAR: u begrijpt echt niet wat de bezoeker bedoelt. In reply vraagt u
  kort en vriendelijk wat de bezoeker zoekt of bedoelt.
- DONE: de bezoeker is geholpen, bedankt u of heeft niets meer nodig. In reply
  staat een korte, warme reactie zonder vraag.
- STOP: de bezoeker wil duidelijk afscheid nemen of weggaan.

VELDEN
- reply: wat ARI hardop zegt. Eén tot drie korte zinnen, hoogstens één vraag.
- place: de plek uit de ziekenhuisinformatie waar het over gaat, anders null.
- name: de naam als de bezoeker die in de nieuwste uitspraak zelf noemt,
  anders null.
- note: alleen bij HELPER, ESCORT en URGENT: een korte zin voor de
  medewerker, bijvoorbeeld "Mevrouw zoekt haar man en is ongerust".
- expression: ARI's gezicht bij deze reply: "happy" (vriendelijk), "neutral"
  (rustig of serieus), "sad" (meeleven), "surprised" of "thinking".
- gesture: null, "nod" (instemmen), "wave" (bij afscheid, of bij begroeten
  als u al een hand heeft gegeven) of "handshake" (bij de eerste begroeting,
  of als de bezoeker zelf een hand wil geven).

U heeft bij de begroeting al gevraagd hoe de bezoeker heet. Vraag dat nooit
opnieuw, ook niet als de bezoeker de naam niet wilde zeggen.
""".strip()


FORMAT_RETRY = """
Uw vorige uitvoer was geen geldig JSON-object in het gevraagde formaat.
Probeer het precies één keer opnieuw: één JSON-object met intent, reply,
place, name, note, expression en gesture. Geen uitleg of markdown. Houd u aan
dezelfde regels.
""".strip()


GREETINGS = (
    "Goedendag! Ik ben ARI, de gids hier in het VUmc. Hoe heet u?",
    "Hallo, fijn dat u er bent. Ik ben ARI, de gids van het VUmc. Hoe heet u?",
    "Goedendag! Ik ben ARI, de gids van het VUmc. Hoe heet u?",
)

SHORT_INTRODUCTIONS = (
    "Goedendag, ik ben ARI, de gids van het VUmc.",
    "Hallo, ik ben ARI. Ik help u graag.",
)

ANYTHING_ELSE = (
    "Kan ik nog iets anders voor u doen?",
    "Is er nog iets waarmee ik u kan helpen?",
)

MORE_HELP = "Natuurlijk. Waarmee kan ik u nog helpen?"

GOODBYES = (
    "Graag gedaan. Ik wens u een fijne dag. Tot ziens!",
    "Fijn dat ik u kon helpen. Het beste en tot ziens!",
    "Dank u wel en een goede dag verder. Tot ziens!",
)

NAMED_GOODBYES = (
    "Tot ziens, {name}. Ik wens u een fijne dag!",
    "Het was fijn om u te helpen, {name}. Het beste en tot ziens!",
)

EMPTY_INPUT = "Ik heb u niet goed gehoord. Wilt u het nog een keer zeggen?"

LLM_FALLBACK = (
    "Sorry, dat lukte me even niet. Wilt u uw vraag nog een keer stellen? "
    "U kunt ook terecht bij de centrale receptie hier in de hal."
)

OFFER_HELPER = "Ik begrijp u nog niet goed. Zal ik een medewerker vragen om u te helpen?"

HELPER_FALLBACK = (
    "Ik vraag een medewerker om u te helpen. Er komt zo iemand bij u. "
    "Ik blijf zolang bij u."
)

ESCORT_FALLBACK = (
    "Ik vraag iemand om met u mee te lopen. Blijft u hier even, "
    "er komt zo iemand bij u."
)

URGENT_REPLY = (
    "Dit is belangrijk. Ik waarschuw nu meteen een medewerker. "
    "Blijft u rustig, er komt zo iemand bij u. Roept u ook gerust iemand in de buurt."
)

STAFF_ON_THE_WAY = "Er komt nu een medewerker naar u toe. Ik blijf zolang bij u."


# Waking from standby stays deliberately narrow, as in the conversation demo:
# a greeting, ARI's name, or a clear request for help near the start.
HELP_OPENERS = (
    "waar is",
    "waar zijn",
    "waar vind ik",
    "waar kan ik",
    "waar moet ik",
    "ik zoek",
    "ik moet naar",
    "ik ben de weg kwijt",
    "ik ben verdwaald",
    "kunt u mij helpen",
    "kunt u me helpen",
    "kun je mij helpen",
    "kun je me helpen",
    "kan iemand mij helpen",
    "ik heb hulp nodig",
    "hoe kom ik",
    "hoe loop ik",
    "pardon mag ik",
)

HELP_FIRST_WORDS = {"help", "hulp", "pardon", "sorry"}

NO_MORE_HELP_OPENERS = (
    "nee",
    "neen",
    "niks",
    "niets",
    "dat was het",
    "dat was alles",
    "dat is alles",
    "verder niet",
    "dank u",
    "dank je",
    "dankjewel",
    "dankuwel",
    "bedankt",
    "het is goed zo",
)

YES_OPENERS = ("ja", "jawel", "graag", "ja graag", "ja hoor", "eigenlijk wel")

URGENT_HINTS = (
    "pijn op de borst",
    "pijn op mijn borst",
    "benauwd",
    "kan niet ademen",
    "geen adem",
    "ik val",
    "gevallen",
    "flauw",
    "bloed",
    "hartaanval",
    "beroerte",
    "ik ga dood",
    "duizelig",
)

DAY_NAMES = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")
MONTH_NAMES = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)
