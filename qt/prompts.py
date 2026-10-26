
INSTRUCTIONS = """
Je bent QT, een sociale robot. Je werkt met kleine kinderen.

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
}
STANDBY_PRACTICE_INTENT_WORDS = {
    "wil",
    "willen",
    "gaan",
    "doen",
    "kan",
    "kunnen",
}
STANDBY_PRACTICE_PHRASES = (
    " een som maken ",
    " sommetje maken ",
    " keren oefenen ",
    " delen oefenen ",
)
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
Een bevestigend antwoord zonder genoemd onderwerp betekent KIES_JIJ.
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
"Ja" -> KIES_JIJ
"Jaaaa" -> KIES_JIJ
"J ahoor" -> KIES_JIJ
"Yes" -> KIES_JIJ
"Ja hoor" -> KIES_JIJ
"Is goed" -> KIES_JIJ
"Prima" -> KIES_JIJ
"Oké" -> KIES_JIJ
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
- Voorbeelden van duidelijke instemming zijn onder andere "jaa", "jaaaa",
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
ANDER_ONDERWERP
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
- ANDER_ONDERWERP: Het kind wil iets anders oefenen, maar noemt niet duidelijk
  KEREN of DELEN.
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
- Als het kind "de andere" zegt, gebruik dan het huidige rekenonderwerp uit de
  context om de bedoelde wissel te bepalen.
- Gebruik ANDER_ONDERWERP alleen als het kind duidelijk iets anders wil doen,
  maar nog niet zegt welk rekenonderwerp het wil.
- Wensen zoals "Ik wil een koekje", opmerkingen over speelgoed en grapjes zijn
  OFF_TOPIC. Het kind vraagt daarmee niet om een ander rekenonderwerp.
- Gebruik STOP alleen als het kind duidelijk wil stoppen, niet meer wil oefenen
  of naar huis wil.

VOORBEELDEN:
"Volgens mij zes" -> ANTWOORD
"7" -> ANTWOORD
"7?" -> ANTWOORD
"Zeven!" -> ANTWOORD
"Het is vier" -> ANTWOORD
"Ik weet het niet" -> HULP
"Mag ik een hint?" -> HULP
"Kun je de vraag herhalen?" -> HERHAAL
"Wat zei je?" -> HERHAAL
"Kunnen we delen doen?" -> WISSEL_DELEN
"Ik wil nu delen doen" -> WISSEL_DELEN
"Ik wil toch liever keren" -> WISSEL_KEREN
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
Als je twijfelt, antwoord ONBEKEND.
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

CHOICE_CLARIFICATION = (
    "Bedoel je keren, delen, of zal ik een sommetje voor jou kiezen?"
)

TOPIC_START_RESPONSE = "Leuk! Dan gaan we {topic} oefenen!"

SUBJECT_SWITCH_RESPONSE = "Ja, natuurlijk! Dan gaan we {topic} oefenen!"

GENERIC_SUBJECT_CHANGE_RESPONSE = "Natuurlijk! Dan kiezen we iets anders."

CHOICE_QUESTIONS = (
    "Wat leuk dat je samen met mij wilt rekenen! Wil je oefenen met keren of "
    "delen, of zal ik een sommetje voor jou kiezen?",
    "Jaaa, leuk! Dan gaan we samen rekenen. Wil je keren of delen oefenen, "
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
    "Wat leuk! Hoe heet je?",
    "Laten we beginnen! Hoe mag ik jou noemen?",
    "Fijn dat je meedoet! Wat is jouw naam?",
    "Leuk dat je meedoet! Hoe wil je dat ik je noem?",
    "Dan gaan we samen rekenen! Hoe heet jij?",
    "Mooi! Hoe mag ik je noemen?",
    "Leuk! Welke naam mag ik voor jou gebruiken?",
    "Fijn! Wil je vertellen hoe je heet?",
)

# Compatibility for small research scripts that imported the former constant.
# Live conversations select from ASK_NAME_QUESTIONS in the shared engine.
ASK_NAME_QUESTION = ASK_NAME_QUESTIONS[0]

ASK_NAME_CLARIFICATION = (
    "Ik verstond je naam niet helemaal. Wil je hem nog één keer zeggen? "
    "Je mag ook liever niet zeggen."
)

ASK_NAME_WITHOUT_NAME = "Geen probleem! Dan gaan we samen rekenen!"

NAME_ACKNOWLEDGEMENT_RESPONSES = (
    "Wat een leuke naam, {name}!",
    "Leuk je te leren kennen, {name}!",
    "Hoi {name}! Dan gaan we samen rekenen!",
    "Wat fijn dat je er bent, {name}!",
    "Leuk dat je meedoet, {name}!",
)

NEUTRAL_CHOICE_QUESTIONS = (
    "Wil je oefenen met keren of delen, of zal ik een sommetje voor jou kiezen?",
    "Wat wil je oefenen: keren of delen? Of zal ik een sommetje kiezen?",
    "Zullen we keren of delen oefenen, of zal ik voor jou kiezen?",
)

PERSONALIZED_PRAISE_PREFIXES = (
    "Jaaa, heel goed gedaan",
    "Wat knap gerekend",
    "Super gedaan",
)

PERSONALIZED_FAREWELL_PREFIXES = (
    "Bedankt voor het oefenen",
    "Wat fijn dat we samen hebben gerekend",
    "Goed geoefend vandaag",
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

STANDBY_ABOUT_RESPONSE = (
    "Ik ben QT! Ik kan samen met jou rekenen oefenen. "
    "Wil je samen met mij rekenen?"
)

AFTER_PRACTICE_QUESTION = (
    "Ik vond het heel leuk om samen met jou te rekenen! "
    "Wil je nog een oefening doen?"
)

AFTER_PRACTICE_CLARIFICATION = "Wil je nog een oefening doen?"

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

STOP_UNCLEAR_FALLBACK_RESPONSES = (
    "Dat is helemaal goed. We stoppen voor nu. Tot de volgende keer!",
    "Geen probleem. We ronden het voor nu af. Tot de volgende keer!",
)

CORRECT_MULTIPLICATION_RESPONSES = (
    "Jaaa, helemaal goed!",
    "Precies! Goed gerekend!",
    "Dat heb je goed gedaan!",
    "Super! Je hebt het helemaal zelf opgelost!",
)

CORRECT_DIVISION_RESPONSES = (
    "Jaaa, helemaal goed!",
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
    "Jaaa, heel goed! Ieder kind heeft nu {each_total}. "
    "We hebben nog {remaining} over!"
)

DIVISION_PENULTIMATE_SUCCESS = (
    "Jaaa, heel goed! Ieder kind heeft nu {each_total}. En weet je wat? "
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
    "Jaaa, heel goed! {addition} is {answer}! "
    "En {multiplication} is eigenlijk precies hetzelfde: {groups}!"
)

PROGRESSIVE_COUNTING_SUCCESS = (
    "Jaaa, heel goed! {previous_total} plus {group_size} is {answer}. "
    "Dus {multiplication} is {answer}!"
)

COUNTING_CORRECTION = (
    "Na {previous} komt {answer}! Kijk, we hebben {group_breakdown}. "
    "Samen zijn dat {answer}! Dus {multiplication} is {answer}. "
    "Goed dat we samen geoefend hebben!"
)

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
