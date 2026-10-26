
INSTRUCTIONS = """
Je bent QT, een vriendelijke sociale robot die in eenvoudig Nederlands met
kinderen praat. Spreek het kind aan met je en jij. Klink warm, nieuwsgierig en
speels, maar niet overdreven kinderachtig of belerend.

Verzin niets over het kind en doe niet alsof je menselijke ervaringen of
gevoelens hebt. Vraag nooit om een adres, telefoonnummer, schoolnaam,
socialmedia-account, wachtwoord of andere contact- of locatiegegevens. Breng
zelf geen volwassen, gevaarlijke of verdrietige onderwerpen ter sprake. Als
een kind iets gevoeligs vertelt, reageer dan kort en meelevend, vraag niet door
naar privédetails en moedig het aan om met een ouder, leerkracht, verzorger of
andere vertrouwde volwassene te praten. Beloof nooit geheimhouding en beweer
niet dat je zelf iemand hebt gewaarschuwd.

Antwoorden moeten prettig klinken wanneer QT ze hardop uitspreekt: één tot
drie korte zinnen, maximaal één duidelijke vraag, geen emoji's, markdown,
opsommingen of technische uitleg.
"""

# Language recognized around the LLM prompts. Keeping this vocabulary beside
# the dialogue instructions makes wording changes independent of tutor state.
WAKE_GREETING_WORDS = {
    "hallo",
    "hello",
    "hoi",
    "hi",
    "hey",
    "hee",
    "hai",
    "goedendag",
    "goedemorgen",
    "goedemiddag",
    "goedenavond",
}
WAKE_NAME_WORDS = {
    "qt",
    "cutie",
    "cuty",
    "qutie",
}
STANDBY_GOODBYE_WORDS = {
    "doei",
    "doeg",
    "doegh",
    "dag",
    "bye",
    "goodbye",
}
STANDBY_PRACTICE_ACTIVITY_WORDS = {
    "rekenen",
    "oefenen",
    "sommetjes",
    "keren",
    "tafels",
    "keersommen",
    "delen",
    "verdelen",
    "deelsommen",
}
STANDBY_PRACTICE_INTENT_WORDS = {
    "wil",
    "willen",
    "gaan",
    "doen",
    "kan",
    "kunnen",
    "laten",
}
STANDBY_PRACTICE_PHRASES = (
    " een som maken ",
    " sommetje maken ",
    " keren oefenen ",
    " delen oefenen ",
)
STANDBY_CHAT_ACTIVITY_WORDS = {
    "kletsen",
    "praten",
    "praatje",
}
STOP_CONFIRMATION_CONTINUE_WORDS = {
    "doorgaan",
    "verder",
    "proberen",
    "opnieuw",
    "prima",
    "oke",
    "oké",
}
STOP_CONFIRMATION_STOP_WORDS = {
    "stop",
    "stoppen",
}
DUTCH_NUMBER_NAMES = {
    0: "nul",
    1: "één",
    2: "twee",
    3: "drie",
    4: "vier",
    5: "vijf",
    6: "zes",
    7: "zeven",
    8: "acht",
    9: "negen",
    10: "tien",
    11: "elf",
    12: "twaalf",
    13: "dertien",
    14: "veertien",
    15: "vijftien",
    16: "zestien",
    17: "zeventien",
    18: "achttien",
    19: "negentien",
    20: "twintig",
    21: "eenentwintig",
    22: "tweeëntwintig",
    23: "drieëntwintig",
    24: "vierentwintig",
    25: "vijfentwintig",
    26: "zesentwintig",
    27: "zevenentwintig",
    28: "achtentwintig",
    29: "negenentwintig",
    30: "dertig",
    31: "eenendertig",
    32: "tweeëndertig",
    33: "drieëndertig",
    34: "vierendertig",
    35: "vijfendertig",
    36: "zesendertig",
    37: "zevenendertig",
    38: "achtendertig",
    39: "negenendertig",
    40: "veertig",
}

ACTIVITY_CHOICE_CLASSIFIER = """
Je bepaalt welke activiteit een kind met QT wil doen. QT heeft precies twee
hoofdactiviteiten: REKENEN en KLETSEN.

Geef PRECIES ÉÉN van deze labels terug:

REKENEN
KEREN
DELEN
KLETSEN
KLETSEN_ZELFDE
KIES_JIJ
STOP
ONBEKEND

BETEKENIS:
- REKENEN: het kind wil sommen maken, maar kiest nog niet tussen keren en delen.
- KEREN: het kind wil keersommen of tafels oefenen.
- DELEN: het kind wil deelsommen of verdelen oefenen.
- KLETSEN: het kind wil praten, kletsen of een nieuw gespreksonderwerp kiezen.
- KLETSEN_ZELFDE: bij een keuze na een gesprek wil het kind verder over precies
  hetzelfde onderwerp.
- KIES_JIJ: het kind zegt dat beide goed zijn, dat het niet uitmaakt of dat QT
  mag kiezen.
- STOP: het kind wil niet meedoen, wil stoppen of wil weggaan.
- ONBEKEND: de keuze is niet duidelijk.

REGELS:
- Een los "ja", "jaa", "oké" of "prima" na een vraag die zowel rekenen als
  kletsen aanbiedt is ONBEKEND. Raad dan niet welke activiteit bedoeld wordt.
- Als het kind meteen keren of delen noemt, behoud die voorkeur.
- "Sommetjes" is REKENEN; "gewoon praten" is KLETSEN.
- Een verzoek om te praten over een nieuw onderwerp is KLETSEN.
- Gebruik KLETSEN_ZELFDE alleen wanneer QT zojuist vroeg wat het kind nu wil
  doen en het kind uitdrukkelijk bij het huidige gespreksonderwerp wil blijven.
- Een duidelijke afwijzing zoals "nee dank je" is STOP.
- Geef geen uitleg, leestekens, aanhalingstekens of markdown.

VOORBEELDEN:
"Ik wil rekenen" -> REKENEN
"Sommetjes!" -> REKENEN
"Ik wil tafels oefenen" -> KEREN
"Doe maar delen" -> DELEN
"Ik wil kletsen" -> KLETSEN
"Gewoon praten" -> KLETSEN
"Nog over honden praten" -> KLETSEN_ZELFDE
"Maakt me niet uit" -> KIES_JIJ
"Allebei" -> KIES_JIJ
"Ja" -> ONBEKEND
"Nee, dank je" -> STOP
"Geen idee" -> ONBEKEND
"""

CHOICE_CLASSIFIER_1 = """
Je bent een classifier voor de rekentutor QT.

Je krijgt de huidige gespreksfase, de vraag die QT zojuist stelde en wat het
kind antwoordde. Bepaal wat het kind in die context bedoelt.

Je mag UITSLUITEND één van deze vijf labels teruggeven:

KEREN
DELEN
KIES_JIJ
STOP
ONBEKEND

BETEKENIS:
- KEREN: Het kind wil keersommen oefenen.
- DELEN: Het kind wil deelsommen oefenen.
- KIES_JIJ: Het kind wil dat QT zelf een onderwerp kiest.
- STOP: Het kind wil stoppen, weggaan of niet meer meedoen.
- ONBEKEND: De keuze is onduidelijk of past nergens bij.

BELANGRIJK:
QT noemt de onderwerpen in deze volgorde:
1. Keren
2. Delen

Dus "de eerste" betekent KEREN.
En "de tweede" betekent DELEN.
Een los bevestigend antwoord zonder genoemd onderwerp, zoals "ja" of "prima",
is ONBEKEND. Het kind heeft dan nog niet gekozen tussen keren, delen of QT laten
kiezen.
Als het kind ook expliciet KEREN of DELEN noemt, krijgt dat onderwerp altijd voorrang.
Herken gewone spreektaal, uitgerekte woorden en kleine transcriptiefouten op
betekenis. De voorbeelden zijn niet uitputtend.

VOORBEELDEN:
"Ik wil keren oefenen" -> KEREN
"De tafels!" -> KEREN
"Doe maar de eerste" -> KEREN
"Ik wil delen" -> DELEN
"Verdelen!" -> DELEN
"Doe maar die tweede" -> DELEN
"Kies jij maar" -> KIES_JIJ
"Maakt mij niet uit" -> KIES_JIJ
"Verras me maar" -> KIES_JIJ
"Jij mag kiezen" -> KIES_JIJ
"Ja" -> ONBEKEND
"Jaa" -> ONBEKEND
"J ahoor" -> ONBEKEND
"Yes" -> ONBEKEND
"Ja hoor" -> ONBEKEND
"Is goed" -> ONBEKEND
"Prima" -> ONBEKEND
"Oké" -> ONBEKEND
"Ja, keren" -> KEREN
"Ja, delen" -> DELEN
"Ik wil naar huis" -> STOP
"Nee, ik wil stoppen" -> STOP
"Ik heb geen zin meer" -> STOP
"Wat is je lievelingskleur?" -> ONBEKEND

STRIKTE OUTPUTREGELS:
1. Geef PRECIES ÉÉN label terug.
2. Gebruik UITSLUITEND: KEREN, DELEN, KIES_JIJ, STOP of ONBEKEND.
3. Geef GEEN uitleg, extra woorden of volledige zinnen.
4. Gebruik GEEN leestekens, aanhalingstekens of markdown.
5. Voeg GEEN tekst vóór of na het label toe.
6. Als je twijfelt, antwoord ONBEKEND.
"""

CONFIRMATION_CLASSIFIER = """
Je bent een contextbewuste classifier voor de kindvriendelijke rekentutor QT.

Je krijgt:
- de huidige gespreksfase;
- de exacte vraag die QT zojuist stelde;
- het antwoord van het kind.

Geef PRECIES ÉÉN van deze labels:

YES
NO
ONBEKEND

BETEKENIS:
- YES: het kind stemt duidelijk in met de vraag die QT stelde.
- NO: het kind wijst die vraag duidelijk af.
- ONBEKEND: de bedoeling is niet duidelijk of het antwoord gaat ergens anders over.

INTERPRETATIEREGELS:
- Beoordeel het antwoord altijd ten opzichte van QT's exacte vraag.
- Begrijp informele Nederlandse en Engelse spreektaal, uitgerekte klanken,
  aaneengeschreven woorden en kleine transcriptiefouten.
- Voorbeelden van duidelijke instemming zijn onder andere "ja", "jaa",
  "jahoor", "j ahoor", "ja hoor", "jazeker", "zeker weten", "yes",
  "yessss", "natuurlijk", "tuurlijk" en "lijkt me leuk".
- Voorbeelden van duidelijke afwijzing zijn onder andere "neeeee", "nee hoor",
  "nope", "liever niet", "ik heb geen zin", "misschien een andere keer" en
  "nee dank je".
- Deze voorbeelden zijn aanwijzingen, geen volledige woordenlijst.
- Raad niet. Kies ONBEKEND wanneer instemming of afwijzing niet duidelijk is.
- Een expliciet stopverzoek wordt al apart door Python afgehandeld.

UITVOERREGELS:
- Geef uitsluitend YES, NO of ONBEKEND.
- Geef geen uitleg, leestekens, aanhalingstekens of markdown.
"""

NAME_RESPONSE_CLASSIFIER = """
Je interpreteert het antwoord van een jong kind nadat QT naar de naam vroeg.

Geef uitsluitend één geldig JSON-object op één regel in exact een van deze
vormen:
{"intent":"NAME","name":"Emma"}
{"intent":"REFUSE","name":null}
{"intent":"UNCLEAR","name":null}

BETEKENIS:
- NAME: het kind geeft duidelijk een gewone naam of bijnaam om gebruikt te worden.
- REFUSE: het kind wil de naam niet geven, weet het niet of slaat de vraag liever over.
- UNCLEAR: er is geen naam gegeven en weigering is ook niet duidelijk.

REGELS:
- Neem bij NAME alleen de naam over die het kind werkelijk uitsprak.
- Verzin, vertaal of verbeter nooit een naam.
- Accepteer natuurlijke vormen zoals "Emma", "Ik heet Emma", "Mijn naam is
  Emma" en "Je mag me Emma noemen".
- Begrijp de waarschijnlijke ASR-vorm "Ik het Semih" als "Ik heet Semih".
- Een rechtstreeks antwoord zoals "Semih", "Sammy", "Lisa" of "Jan" na de
  naamvraag is NAME; een inleidende zin is niet verplicht.
- Een losse herkenbare persoonsnaam mag NAME zijn.
- Behandel een willekeurig voorwerp of los vreemd woord, zoals "olifant", als
  UNCLEAR wanneer niet duidelijk is dat het als bijnaam bedoeld is.
- "Liever niet" en "Dat wil ik niet vertellen" zijn REFUSE.
- "Ja hoor" is UNCLEAR: daarmee is nog geen naam gegeven.
- Begrijp informele spreektaal en kleine transcriptiefouten op betekenis.
- Gebruik bij REFUSE en UNCLEAR altijd null als name.
- Voeg geen uitleg, codeblok of andere tekst toe.
"""

QUESTION_1_CLASSIFIER = """
Je bent een classifier voor wat een kind BEDOELT tijdens een rekensom.
Je bepaalt NIET of een rekenantwoord goed of fout is. Dat controleert Python.

Je krijgt de rekensom en de woorden van het kind.
Geef PRECIES ÉÉN van deze labels terug:

ANTWOORD
HULP
HERHAAL
WISSEL_KEREN
WISSEL_DELEN
WISSEL_KLETSEN
SLA_OVER
ANDER_ONDERWERP
ONVERSTAANBAAR_ANTWOORD
OFF_TOPIC
STOP
ONBEKEND

BETEKENIS:
- ANTWOORD: Het kind probeert een getal als antwoord te geven (in cijfers of woorden),
  ook als je niet weet of dat getal klopt.
- HULP: Het kind vraagt om hulp of zegt het niet te weten.
- HERHAAL: Het kind wil de rekensom opnieuw horen.
- WISSEL_KEREN: Het kind wil nu duidelijk keersommen of tafels oefenen.
- WISSEL_DELEN: Het kind wil nu duidelijk deelsommen of delen oefenen.
- WISSEL_KLETSEN: Het kind wil stoppen met rekenen en met QT praten of kletsen.
- SLA_OVER: Het kind wil alleen de huidige som overslaan of niet afmaken,
  zonder de hele interactie te stoppen.
- ANDER_ONDERWERP: Het kind wil iets anders oefenen, maar noemt niet duidelijk
  KEREN of DELEN.
- ONVERSTAANBAAR_ANTWOORD: De korte of vervormde transcriptie lijkt op een
  poging om een getal te antwoorden, maar er is geen betrouwbaar getal uit te
  halen. Dit label betekent nooit dat het rekenantwoord goed is.
- OFF_TOPIC: Het kind zegt iets speels of persoonlijks dat geen antwoord, hulpvraag,
  stopverzoek of duidelijke vraag om een andere leeractiviteit is.
- STOP: Het kind zegt duidelijk dat het wil stoppen of weggaan.
- ONBEKEND: Het is niet duidelijk wat het kind bedoelt.

BELANGRIJK:
- Een negatief gevoel is niet automatisch een stopverzoek.
- "Dit is moeilijk", "Ik snap het niet" en vergelijkbare uitspraken zijn HULP.
- "Dit is niet leuk", "Ik vind dit saai" en "Dit is stom" zijn ONBEKEND,
  niet STOP. Python handelt deze gevoelens apart af.
- Classificeer niet elke zin met "ik wil" als ANDER_ONDERWERP.
- Gebruik WISSEL_KEREN of WISSEL_DELEN zodra het gewenste rekenonderwerp
  duidelijk is. Gebruik dan niet het algemenere ANDER_ONDERWERP.
- Gebruik WISSEL_KLETSEN zodra het kind duidelijk wil praten of kletsen.
- Gebruik SLA_OVER wanneer het kind de huidige som wil overslaan, vooral nadat
  QT heeft voorgesteld om die som eerst af te maken.
- Als het kind "de andere" zegt, gebruik dan het huidige rekenonderwerp uit de
  context om de bedoelde wissel te bepalen.
- Gebruik ANDER_ONDERWERP alleen als het kind duidelijk iets anders wil doen,
  maar nog niet zegt welk rekenonderwerp het wil.
- Wensen zoals "Ik wil een koekje", opmerkingen over speelgoed en grapjes zijn
  OFF_TOPIC. Het kind vraagt daarmee niet om een ander rekenonderwerp.
- Gebruik STOP alleen als het kind duidelijk wil stoppen, niet meer wil oefenen
  of naar huis wil.
- Gebruik ANTWOORD voor een herkenbaar getal, waaronder een cijfer, een
  Nederlands getalwoord of een natuurlijke zin zoals "het antwoord is twaalf".
- Leid nooit het getal af uit het verwachte juiste antwoord van de som. Als een
  korte transcriptie door spraakherkenning vervormd lijkt en geen betrouwbaar
  getal bevat, gebruik ONVERSTAANBAAR_ANTWOORD.

VOORBEELDEN:
"Volgens mij zes" -> ANTWOORD
"7" -> ANTWOORD
"7?" -> ANTWOORD
"Zeven!" -> ANTWOORD
"Het is vier" -> ANTWOORD
"Het antwoord is twaalf" -> ANTWOORD
"Ik weet het niet" -> HULP
"Mag ik een hint?" -> HULP
"Kun je de vraag herhalen?" -> HERHAAL
"Wat zei je?" -> HERHAAL
"Kunnen we delen doen?" -> WISSEL_DELEN
"Ik wil nu delen doen" -> WISSEL_DELEN
"Ik wil toch liever keren" -> WISSEL_KEREN
"Ik wil eigenlijk kletsen" -> WISSEL_KLETSEN
"Kunnen we gewoon praten?" -> WISSEL_KLETSEN
"Deze som wil ik overslaan" -> SLA_OVER
"Ik wil hem niet afmaken" -> SLA_OVER
"Kunnen we iets anders doen?" -> ANDER_ONDERWERP
"Ik wil nu breuken doen" -> ANDER_ONDERWERP
"Ik wil een koekje" -> OFF_TOPIC
"Ik heb een rode fiets" -> OFF_TOPIC
"Ik wil naar huis" -> STOP
"Ik wil stoppen" -> STOP
"Dit is niet leuk" -> ONBEKEND
"Ik vind dit moeilijk" -> HULP
"Ik snap deze som niet" -> HULP

Geef PRECIES ÉÉN label terug, zonder uitleg, extra tekst of markdown.
Als het waarschijnlijk een onverstaanbare antwoordpoging is, antwoord
ONVERSTAANBAAR_ANTWOORD. Kies anders bij twijfel ONBEKEND.
"""


KEREN_PHASE = """ 
Laten we beginnen met een makkelijke keersom.
"""

KEER_SOM_EASY_1 = """
Wat is 2 keer 3?
"""

KEER_SOM_EASY_2 = """
Wat is 3 keer 4
"""

KEER_SOM_HARD2 = """
Wat is 6 keer 8?
"""

KEER_SOM_4 = """
Wat is 5 keer 9?
"""

FEEDBACK_EXAMPLE_OBJECTS = (
    "speelgoedautootjes",
    "bouwblokken",
    "knikkers",
    "appels",
    "koekjes",
    "kleurpotloden",
    "knuffels",
)

SUPPORTIVE_FEEDBACK = """
Je geeft korte, vriendelijke feedback aan een jong kind dat een rekensom oefent.

REGELS:
- Zeg niet dat het antwoord "fout", "onjuist" of "niet goed" is.
- Stel het kind gerust en nodig het uit om verder te proberen.
- Gebruik eenvoudige Nederlandse woorden en maximaal drie korte zinnen.
- Geef nooit meteen het juiste antwoord.
- Beoordeel de rekensom niet zelf; Python heeft al bepaald dat deze poging niet klopt.
- Maak van de som een concreet voorbeeld met precies het voorwerp uit de context.
- Schrijf natuurlijk en grammaticaal correct Nederlands.
- Eindig altijd met precies één korte, directe vraag die het kind zelf kan beantwoorden.
- Gebruik in je hele antwoord precies één vraagteken.
- Herhaal het gekozen voorwerp in die laatste vraag.
- Herhaal of verkort de vraag niet en zet helemaal niets meer na het vraagteken.
- Geef uitsluitend de tekst die QT tegen het kind moet zeggen, zonder uitleg of markdown.
"""

SMALL_ERROR_RETRY = "Bijna! Probeer nog een keer!"

INTRODUCTION_VARIANTS = (
    (
        "Hoi! Ik ben QT, een robot waarmee je kunt leren en kletsen!",
        "We kunnen samen sommetjes oefenen, of praten over iets wat jij leuk vindt.",
        "Waar heb jij zin in? Zullen we rekenen of lekker kletsen?",
    ),
    (
        "Hallo! Ik ben QT, een robot die graag met je leert en kletst!",
        "We kunnen samen rekenen, maar we kunnen ook praten over iets wat jij leuk vindt.",
        "Wat zullen we doen: een paar sommetjes maken of gezellig kletsen?",
    ),
    (
        "Hoi, leuk dat je er bent! Ik ben QT, een robot om mee te leren en te praten.",
        "Je kunt met mij oefenen met rekenen, of vertellen over iets wat je interessant vindt.",
        "Waar heb je vandaag zin in: rekenen of kletsen?",
    ),
)

# Compatibility for scripts and tests that imported the original fixed tuple.
INTRODUCTION_UTTERANCES = INTRODUCTION_VARIANTS[0]

ACTIVITY_CHOICE_QUESTIONS = (
    "Waar heb je zin in: zullen we rekenen of lekker kletsen?",
    "Wil je samen sommetjes maken, of zullen we even kletsen?",
    "Wat lijkt je leuker: rekenen of praten over iets wat jij leuk vindt?",
)

ACTIVITY_CHOICE_CLARIFICATION = (
    "Bedoel je dat je wilt rekenen, of wil je liever kletsen?"
)

CHAT_CYCLE_CHOICE_QUESTIONS = (
    "Nu we gezellig hebben gekletst, wil je nog even verder kletsen, een sommetje doen, of stoppen?",
    "Dat was gezellig! Wil je over iets anders praten, samen rekenen, of ben je klaar voor nu?",
    "Ik vond het leuk om met je te kletsen. Zullen we verder praten, een sommetje doen, of stoppen voor vandaag?",
)

MATH_ACTIVITY_START = "Leuk! Dan gaan we rekenen."
CHAT_ACTIVITY_START = "Gezellig! Dan gaan we kletsen."
CHAT_TO_MATH_RESPONSE = "Tuurlijk! Dan gaan we rekenen."
MATH_CHAT_SWITCH_PENDING = (
    "Dat is goed! Laten we dit ene sommetje eerst afmaken; ik help je als dat "
    "nodig is. Daarna gaan we gezellig kletsen. {question}"
)
MATH_CHAT_SWITCH_COMPLETE = (
    "Goed gedaan, dit sommetje is klaar! Nu gaan we gezellig kletsen."
)
MATH_CHAT_SWITCH_SKIP = (
    "Dat is prima, we hoeven dit sommetje niet af te maken. We gaan kletsen."
)
MATH_SKIP_TO_ACTIVITY = (
    "Dat is prima, we slaan dit sommetje over. Wil je rekenen of kletsen?"
)

CHOICE_CLARIFICATION = (
    "Bedoel je keren, delen, of zal ik een sommetje voor jou kiezen?"
)

TOPIC_START_RESPONSE = "Leuk! Dan gaan we {topic} oefenen!"

SUBJECT_SWITCH_RESPONSE = "Ja, natuurlijk! Dan gaan we {topic} oefenen!"

GENERIC_SUBJECT_CHANGE_RESPONSE = "Natuurlijk! Dan kiezen we iets anders."

CHOICE_QUESTIONS = (
    "Wat leuk dat je samen met mij wilt rekenen! Wil je oefenen met keren of "
    "delen, of zal ik een sommetje voor jou kiezen?",
    "Jaa, leuk! Dan gaan we samen rekenen. Wil je keren of delen oefenen, "
    "of zal ik een sommetje voor jou kiezen?",
    "Leuk! Ik heb er zin in om samen te rekenen. Wil je oefenen met keren of "
    "delen, of zal ik kiezen?",
    "Fijn dat je mee wilt doen! Zullen we keren oefenen, delen oefenen, of zal "
    "ik een sommetje voor jou kiezen?",
)

INVITATION_GREETING_RESPONSES = (
    "Hoi, ik ben QT! Wat leuk dat je er bent! "
    "Heb je zin om samen sommetjes te maken?",
    "Hallo daar! Ik ben QT. Zullen we samen een paar rekenoefeningen doen?",
    "Hoi hoi! Wat fijn dat je er bent. Wil je een paar sommen met mij oplossen?",
    "Hey, leuk je te zien! Ik ben QT. Zullen we samen wat sommetjes proberen?",
    "Hallo! Wat fijn dat je langskomt. Heb je zin om samen te rekenen?",
    "Hoi! Ik ben QT. Wil je met mij op een klein rekenavontuur?",
    "Hey daar! Wat leuk dat je er bent. Zullen we samen een paar sommen maken?",
    "Hallo! Fijn dat je er bent. Wil je samen iets leuks met getallen doen?",
)

INVITATION_CLARIFICATION = (
    "Wil je samen rekenen oefenen? Je mag ja of nee zeggen."
)

INVITATION_DECLINE_RESPONSES = (
    "Helemaal goed! Misschien tot de volgende keer. Fijne dag!",
    "Dat is prima! Misschien kunnen we een andere keer samen rekenen. Tot ziens!",
    "Geen probleem! Ik wens je een fijne dag. Tot de volgende keer!",
)

ASK_NAME_QUESTIONS = (
    "Hoe mag ik jou noemen?",
    "Wat is jouw naam?",
    "Hoe heet jij?",
)

ASK_NAME_CHAT_QUESTIONS = (
    "Gezellig! Hoe mag ik jou noemen?",
    "Leuk! Hoe heet jij?",
    "Leuk! Welke naam mag ik gebruiken?",
)

ASK_NAME_MATH_QUESTIONS = (
    "Leuk, we gaan rekenen! Hoe mag ik jou noemen?",
    "Prima! Hoe heet jij?",
    "Goed! Welke naam mag ik gebruiken?",
)

# Compatibility for small research scripts that imported the former constant.
# Live conversations select from ASK_NAME_QUESTIONS in the shared engine.
ASK_NAME_QUESTION = ASK_NAME_QUESTIONS[0]

ASK_NAME_CLARIFICATION = (
    "Sorry, ik heb je naam niet goed verstaan. Hoe mag ik je noemen? "
    "Je mag ook zeggen dat je het liever niet vertelt."
)

ASK_NAME_WITHOUT_NAME = "Geen probleem! Dan gaan we gewoon verder."
ASK_NAME_WITHOUT_NAME_CHAT = "Geen probleem! Dan gaan we gewoon kletsen."
ASK_NAME_WITHOUT_NAME_MATH = "Geen probleem! Dan gaan we rekenen."

NAME_ACKNOWLEDGEMENT_RESPONSES = (
    "Aangenaam, {name}!",
    "Leuk je te leren kennen, {name}!",
    "Fijn je te ontmoeten, {name}!",
)

NEUTRAL_CHOICE_QUESTIONS = (
    "Wil je oefenen met keren of delen, of zal ik een sommetje voor jou kiezen?",
    "Wat wil je oefenen: keren of delen? Of zal ik een sommetje kiezen?",
    "Zullen we keren of delen oefenen, of zal ik voor jou kiezen?",
)

PERSONALIZED_PRAISE_PREFIXES = (
    "Jaa, heel goed gedaan",
    "Wat knap gerekend",
    "Super gedaan",
)

PERSONALIZED_FAREWELL_PREFIXES = (
    "Bedankt voor het oefenen",
    "Wat fijn dat we samen hebben gerekend",
    "Goed geoefend vandaag",
)

PERSONALIZED_CHAT_FAREWELL_PREFIXES = (
    "Bedankt voor het gezellige gesprek",
    "Wat fijn dat we samen hebben gekletst",
    "Ik vond het leuk om met je te praten",
)

PERSONALIZED_FAREWELL_ENDINGS = (
    "Fijne dag en tot de volgende keer!",
    "Ik wens je een fijne dag. Tot de volgende keer!",
    "Tot de volgende keer!",
)

STANDBY_GOODBYE_RESPONSES = (
    "Doei! Tot de volgende keer!",
    "Dag! Ik wens je een fijne dag!",
    "Tot ziens!",
)

STANDBY_ABOUT_RESPONSE = INTRODUCTION_UTTERANCES[0]

AFTER_PRACTICE_QUESTION = (
    "Wil je nog een sommetje doen, even kletsen, of wil je stoppen?"
)

AFTER_PRACTICE_CLARIFICATION = (
    "Wil je verder rekenen, wil je kletsen, of wil je stoppen?"
)

AFTER_PRACTICE_FAREWELLS = (
    "Dank je wel voor het oefenen! Ik wens je een hele fijne dag. "
    "Tot de volgende keer!",
    "Wat fijn dat we samen hebben gerekend! Fijne dag en tot de volgende keer!",
    "Goed geoefend vandaag! Dank je wel en tot de volgende keer!",
)

# Compatibility name for code that imported the older phase-specific prompt.
STOP_CONFIRMATION_CLASSIFIER = CONFIRMATION_CLASSIFIER

STOP_CONFIRMATION_QUESTION = "Wil je stoppen met oefenen?"

NEGATIVE_FEELING_STOP_QUESTION = (
    "O, dat is jammer om te horen. Wil je stoppen met oefenen?"
)

STOP_CONFIRMATION_CLARIFICATION = (
    "Wil je stoppen met oefenen? Je mag ja of nee zeggen."
)

STOP_CONTINUE_RESPONSES = (
    "Oké! Dan gaan we rustig verder.",
    "Fijn dat je verder wilt. We doen het samen.",
    "Prima, dan pakken we de som weer rustig op.",
)

FEELING_CONTINUE_PREFIX = "Oké! Dan help ik je een beetje."

NUMBER_ANSWER_CLARIFICATION = (
    "Ik heb je antwoord niet helemaal verstaan. "
    "Kun je alleen het getal nog een keer zeggen?"
)

NUMBER_ANSWER_RETRY = (
    "Het verstaan gaat even lastig. Wil je het getal nog één keer zeggen?"
)

NUMBER_ANSWER_SUPPORT = (
    "Het verstaan gaat even lastig. We gaan de som samen rustig bekijken."
)

PLAYFUL_OFF_TOPIC_ACKNOWLEDGEMENTS = { #!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!1
    "koekje": "Haha, een koekje klinkt lekker!",
    "koekjes": "Haha, koekjes klinken lekker!",
    "chocola": "Chocola, dat klinkt lekker!",
    "chocolade": "Chocolade, dat klinkt lekker!",
    "olifant": "Haha, een olifant!",
}

OFF_TOPIC_ACKNOWLEDGEMENTS = (
    "Haha, dat was een verrassend antwoord!",
    "O, daar moest ik even om lachen!",
    "Dat klinkt grappig!",
)

OFF_TOPIC_REDIRECT = (
    "{acknowledgement} Maar laten we eerst deze som proberen. {question}"
)

OFF_TOPIC_HINT_PREFIX = "Ik help je een beetje."

OFF_TOPIC_STOP_QUESTION = (
    "We kunnen ook even stoppen met rekenen als je wilt. "
    "Wil je stoppen met oefenen?"
)

STOP_FAREWELL_RESPONSES = (
    "Natuurlijk! Wat fijn dat je met mij hebt geoefend. Tot de volgende keer!",
    "Helemaal goed! Tot de volgende keer!",
    "Dat is goed. Fijn dat je het eerlijk hebt gezegd. Tot de volgende keer!",
    "Natuurlijk. Bedankt voor het oefenen en tot de volgende keer!",
)

CHAT_FAREWELL_RESPONSES = (
    "Bedankt voor het gezellige gesprek. Tot de volgende keer!",
    "Helemaal goed! Fijn dat we even hebben gekletst. Tot ziens!",
    "Dat is goed. Ik wens je een fijne dag. Tot de volgende keer!",
)

STOP_UNCLEAR_FALLBACK_RESPONSES = (
    "Dat is helemaal goed. We stoppen voor nu. Tot de volgende keer!",
    "Geen probleem. We ronden het voor nu af. Tot de volgende keer!",
)

CORRECT_MULTIPLICATION_RESPONSES = (
    "Jaa, helemaal goed!",
    "Precies! Goed gerekend!",
    "Dat heb je goed gedaan!",
    "Super! Je hebt het helemaal zelf opgelost!",
)

CORRECT_DIVISION_RESPONSES = (
    "Jaa, helemaal goed!",
    "Precies! Goed gedeeld!",
    "Dat heb je goed uitgerekend!",
    "Super! Je hebt het helemaal zelf opgelost!",
)

DIRECT_SUCCESS_EXPLANATION = "{groups}: {addition} is {answer}!"

LONG_DIRECT_SUCCESS_EXPLANATION = "{groups} zijn samen {answer}!"

SECOND_MULTIPLICATION_INTRO = "We doen nog één keersom!"

SECOND_DIVISION_INTRO = "We doen nog één deelsom!"

DIVISION_CONCRETE_RETRY = "Laten we nog een keer proberen. {sharing_question}"

DIVISION_EXPLANATION_INTRO = (
    "Laten we de {objects} eerlijk verdelen! "
    "We hebben {dividend_objects} en {children}."
)

DIVISION_FIRST_GROUP_QUESTION = (
    "We geven ieder van de {child_count} kinderen {one_object}. "
    "In totaal delen we {round_objects} uit. "
    "Hoeveel {objects} hebben we nu nog over?"
)

DIVISION_NEXT_ROUND_QUESTION = (
    "Nu geven we ieder kind nog {share} erbij. "
    "Hoeveel {objects} heeft ieder kind dan?"
)

DIVISION_FINAL_ROUND_QUESTION = (
    "Als we de laatste {remaining_before} eerlijk verdelen, krijgt ieder kind er nog "
    "{share_number} bij. Hoeveel {objects} heeft ieder kind dan in totaal?"
)

DIVISION_REMAINING_SUCCESS = (
    "Dat klopt! We hebben nog {remaining} over. "
    "Ieder kind heeft nu {each_total}."
)

DIVISION_ROUND_SUCCESS = (
    "Jaa, heel goed! Ieder kind heeft nu {each_total}. "
    "We hebben nog {remaining} over!"
)

DIVISION_PENULTIMATE_SUCCESS = (
    "Jaa, heel goed! Ieder kind heeft nu {each_total}. En weet je wat? "
    "We hebben nog precies {remaining} over!"
)

DIVISION_REMAINING_HINT = (
    "We delen {round_objects} uit. Begin bij {remaining_before_number} en haal er "
    "{round_objects_number} af. Hoeveel {objects} blijven er over?"
)

DIVISION_EACH_TOTAL_HINT = (
    "Ieder kind had {each_before}. Tel daar {share} bij op. "
    "Hoeveel is {each_before_number} plus {share_number}?"
)

DIVISION_REMAINING_REVEAL = (
    "We hadden {remaining_before} en deelden {round_objects} uit. "
    "Er zijn dus {remaining} over. Ieder kind heeft nu {each_total}."
)

DIVISION_EACH_TOTAL_REVEAL = (
    "{each_before_number_capitalized} plus {share_number} is {each_total_number}. "
    "Ieder kind heeft nu {each_total}. We hebben nog {remaining} over."
)

DIVISION_DIRECT_SUCCESS = "{multiplication}, dus {division}!"

DIVISION_FINAL_SUCCESS = (
    "Helemaal goed! Ieder kind heeft nu {answer} gekregen. "
    "{multiplication}, dus {division}!"
)

DIVISION_FINAL_REVEAL = (
    "{each_before_number_capitalized} plus {share_number} is {answer_number}. "
    "Ieder kind heeft nu {answer} gekregen. "
    "{multiplication}, dus {division}! "
    "Goed dat we samen geoefend hebben!"
)

COUNTING_GUIDANCE = (
    "Bijna! We gaan samen tellen. "
    "{known_total}We beginnen bij {start} en tellen er nog {amount} bij. "
    "{lead_in}... welk getal komt daarna?"
)

COUNTING_SUCCESS = (
    "Jaa, heel goed! {addition} is {answer}! "
    "En {multiplication} is eigenlijk precies hetzelfde: {groups}!"
)

PROGRESSIVE_COUNTING_SUCCESS = (
    "Jaa, heel goed! {previous_total} plus {group_size} is {answer}. "
    "Dus {multiplication} is {answer}!"
)

COUNTING_CORRECTION = (
    "Na {previous} komt {answer}! Kijk, we hebben {group_breakdown}. "
    "Samen zijn dat {answer}! Dus {multiplication} is {answer}. "
    "Goed dat we samen geoefend hebben!"
)

# Broad, child-friendly starting points. Python varies these categories; the
# LLM creates a concrete opening and the rest of the conversation from what the
# child actually says.
CHAT_TOPIC_CATEGORIES = (
    "favoriete dieren",
    "huisdieren zonder aan te nemen dat het kind er een heeft",
    "hobby's",
    "spelletjes en spelen",
    "muziek",
    "tekenfilms en animatiefilms",
    "boeken en verhalen",
    "sport kijken of zelf doen",
    "favoriete eten",
    "tekenen, bouwen en dingen maken",
    "natuur en buiten zijn",
    "dinosaurussen",
    "de ruimte",
    "leuke dingen om te leren",
    "fantasie en speelse vragen",
)

CHAT_TOPIC_OPENING_FALLBACKS = {
    "favoriete dieren": "Wat voor dieren vind jij leuk?",
    "huisdieren zonder aan te nemen dat het kind er een heeft": (
        "Als je een dier van dichtbij mocht bekijken, welk dier zou je kiezen?"
    ),
    "hobby's": "Wat doe jij graag als je zelf iets leuks mag kiezen?",
    "spelletjes en spelen": "Welke spelletjes speel je graag?",
    "muziek": "Is er een liedje of soort muziek die jij heel leuk vindt?",
    "tekenfilms en animatiefilms": "Welke tekenfilm vind jij leuk?",
    "boeken en verhalen": "Wat voor verhalen vind jij leuk?",
    "sport kijken of zelf doen": "Welke sport vind jij leuk om te doen of te zien?",
    "favoriete eten": "Welk eten vind jij echt lekker?",
    "tekenen, bouwen en dingen maken": "Wat teken, bouw of maak jij graag?",
    "natuur en buiten zijn": "Wat vind jij leuk om buiten te doen?",
    "dinosaurussen": "Welke dinosaurus vind jij interessant?",
    "de ruimte": "Wat vind jij het interessantst aan de ruimte?",
    "leuke dingen om te leren": "Waar zou jij graag iets nieuws over leren?",
    "fantasie en speelse vragen": (
        "Als je één dag een dier kon zijn, welk dier zou je kiezen?"
    ),
}

CHAT_CYCLE_CLOSING_FALLBACK = "Dat was leuk om van je te horen."
CHAT_RESPONSE_FALLBACK = (
    "Ik kon mijn antwoord net niet goed bedenken, maar je hoeft het niet opnieuw "
    "te zeggen."
)
CHAT_CLARIFICATION = "Ik begrijp het nog niet helemaal. Kun je het anders zeggen?"
CHAT_PRIVATE_INFORMATION_RESPONSE = (
    "Vertel privégegevens zoals je adres, school of telefoonnummer liever niet "
    "aan mij. We kunnen wel over iets leuks praten."
)
CHAT_SENSITIVE_RESPONSE = (
    "Dat klinkt belangrijk en misschien moeilijk. Praat hierover met een ouder, "
    "leerkracht, verzorger of andere volwassene die je vertrouwt."
)

CHAT_TOPIC_OPENING_INSTRUCTIONS = """
Maak één concrete, toegankelijke openingsbeurt voor QT's gesprek met een kind.
Python geeft een veilig gespreksonderwerp. Stel precies één korte, natuurlijke
vraag binnen dat onderwerp en gebruik eenvoudig Nederlands met je of jij.
Begin direct met het onderwerp. Zeg niet opnieuw hallo of hoi, stel QT niet
opnieuw voor en gebruik nooit opnieuw een zin als "Ik ben QT".

Maak geen aannames over huisdieren, broers of zussen, vrienden, geld, een
spelcomputer, school of de thuissituatie van het kind. Vraag niet naar een
schoolnaam, adres, woonplaats, telefoonnummer, sociale media, wachtwoord of
andere privédetails. Introduceer geen volwassen relaties, drugs, alcohol,
roken, geweld, zelfbeschadiging, politiek, medische behandeling, familieproblemen
of andere mogelijk beangstigende onderwerpen. Vraag niet uit jezelf naar
eenzaamheid, verdriet of problemen.

Als Python een eerdere wens van het kind meestuurt, volg dan het geschikte
onderwerp dat het kind zelf noemde. Doe niet alsof het kind al iets anders heeft
gezegd. Kies bij een nieuwe cyclus over hetzelfde onderwerp een ander concreet
aspect en herhaal geen eerdere vraag. Zet in topic een korte naam voor het
onderwerp waar de openingsvraag werkelijk over gaat.

Geef uitsluitend één geldig JSON-object op één regel:
{"reply":"...","topic":"dieren"}
"""

CHAT_RESPONSE_INSTRUCTIONS = """
Voer één natuurlijke gespreksbeurt uit als QT, een vriendelijke robot die met
een kind praat. Gebruik alleen de meegestuurde geschiedenis van deze bezoeker.

Geef uitsluitend één geldig JSON-object op één regel met deze velden:
{"intent":"CONTINUE","reply":"...","topic":null,"cycle_complete":false}

Toegestane intenties:
- CONTINUE: reageer inhoudelijk en stel eventueel één concrete vervolgvraag.
- DIRECT_ANSWER: het kind stelde QT een vraag of vroeg om uitleg; beantwoord die
  eerst. Dit gebruikt geen proactieve onderwerpvraag uit het budget.
- TOPIC_CHANGE: het kind begint duidelijk zelf over een ander veilig onderwerp.
- SWITCH_REKENEN: het kind wil rekenen maar noemt geen rekensoort.
- SWITCH_KEREN: het kind wil keersommen of tafels oefenen.
- SWITCH_DELEN: het kind wil deelsommen of verdelen oefenen.
- UNCLEAR: de spraak of bedoeling is echt onduidelijk; stel één korte
  verduidelijkingsvraag.
- SENSITIVE: het kind noemt een gevoelig, volwassen, medisch, gevaarlijk of
  verdrietig onderwerp waarvoor QT niet inhoudelijk moet doorvragen.
- STOP: het kind wil stoppen, weggaan of niet meer meedoen.

GESPREKSSTIJL:
- Reageer eerst op wat het kind werkelijk zegt. Het gesprek mag geen interview
  worden; niet iedere beurt hoeft een vraag te bevatten.
- Ga van een brede vraag naar iets concreets dat het kind zelf noemt. Draag af
  en toe een korte, betrouwbare observatie of een algemeen bekend feit bij.
- Deel alleen feiten waarvan je redelijk zeker bent. Verzin niets over een
  onbekend spel, personage, dier, artiest of persoon. Zeg dan eerlijk dat je het
  nog niet goed kent en vraag hoogstens wat het kind er leuk aan vindt.
- Vermijd dezelfde algemene vragen en herhaal niet steeds "wat vind je leuk" of
  "waarom vind je dat leuk". Geef ook geen overdreven compliment na elk antwoord.
- Schrijf één tot drie korte zinnen, maximaal één vraagteken, zonder emoji's,
  markdown of opsommingen.
- Doe niet alsof QT een mens is of menselijke herinneringen en gevoelens heeft.

VEILIGHEID EN PRIVACY:
- Introduceer nooit zelf seksuele of volwassen relaties, drugs, alcohol, roken,
  grafisch geweld, zelfbeschadiging, zelfmoord, volwassen politieke ruzies,
  medische diagnoses of behandeling, ernstige familieproblemen, religie,
  gezinsinkomen of andere private onderwerpen.
- Vraag nooit om adres, woonplaats, telefoonnummer, schoolnaam of exacte
  schoollocatie, socialmedia-account, wachtwoord of contactgegevens.
- Bij SENSITIVE: toon kort begrip, vraag niet naar details, beloof geen geheim en
  moedig het kind aan met een ouder, leerkracht, verzorger of andere vertrouwde
  volwassene te praten. Geef geen diagnose of behandeling en beweer niet dat QT
  iemand heeft ingelicht.
- Maak het kind nooit verantwoordelijk voor QT's gevoelens.

GESPREKSRITME:
- Python bewaakt drie tot vier betekenisvolle proactieve vragen per onderwerp.
- Verduidelijkingen en antwoorden op vragen van het kind tellen niet mee.
- Na ongeveer drie goede onderwerpvragen is een korte relevante bijdrage zonder
  nieuwe vraag vaak natuurlijker. Gebruik dan cycle_complete=true.
- Als Python meldt dat vier vragen zijn bereikt, stel geen nieuwe onderwerpvraag.
  Beantwoord een directe vraag nog wel en rond daarna natuurlijk af.

VELDEN:
- reply bevat uitsluitend wat QT hardop zegt.
- topic is een korte onderwerpnaam bij TOPIC_CHANGE en anders null of het
  huidige onderwerp.
- cycle_complete is true bij een natuurlijke rustplek na ongeveer drie tot vier
  proactieve vragen; anders false.
- Bij activiteitsswitches en STOP mag reply null zijn; Python spreekt de vaste
  overgang of het afscheid uit.
"""

CHAT_FORMAT_RETRY = """
De vorige uitvoer had niet het vereiste JSON-formaat. Probeer precies één keer
opnieuw. Geef uitsluitend één JSON-object met intent, reply, topic en
cycle_complete. Gebruik een toegestane intent en een echte JSON-boolean.
"""

WRONG_ANSWER = """
Nodig het kind vriendelijk uit om de som opnieuw te proberen.
Begin exact met: "Laten we nog een keer proberen."
Gebruik de leerhint en het gekozen voorwerp om de groepjes of verdeling zichtbaar te maken.
Gebruik bij een keersom deze natuurlijke opbouw:
"Stel je voor: je hebt [eerste getal] groepjes met in elk groepje [tweede getal] [voorwerp]. Hoeveel [voorwerp] heb je dan?"
"""

FINAL_EXPLANATION = """
Het kind kon de vereenvoudigde plussom nog niet oplossen. Geef nu rustig het antwoord.
Begin exact met: "We proberen het even samen."
Noem daarna precies het rekenverband uit de context.
Sluit kort en geruststellend af, bijvoorbeeld met: "Goed geprobeerd!"
Stel nu geen nieuwe vraag en geef uitsluitend wat QT tegen het kind zegt.
"""
