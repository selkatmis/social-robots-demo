
INSTRUCTIONS = """
Je bent QT, een sociale robot. Je werkt met kleine kinderen.

"""

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

QT heeft zojuist gevraagd:
"Waarmee wil je beginnen, met keren of delen?"

Bepaal wat het kind bedoelt.

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
QT noemde de onderwerpen in deze volgorde:
1. Keren
2. Delen

Dus "de eerste" betekent KEREN.
En "de tweede" betekent DELEN.
Een bevestigend antwoord zonder onderwerp betekent KIES_JIJ.
Als het kind ook expliciet KEREN of DELEN noemt, krijgt dat onderwerp altijd voorrang.

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

QUESTION_1_CLASSIFIER = """
Je bent een classifier voor wat een kind BEDOELT tijdens een rekensom.
Je bepaalt NIET of een rekenantwoord goed of fout is. Dat controleert Python.

Je krijgt de rekensom en de woorden van het kind.
Geef PRECIES ÉÉN van deze labels terug:

ANTWOORD
HULP
HERHAAL
ANDER_ONDERWERP
STOP
ONBEKEND

BETEKENIS:
- ANTWOORD: Het kind probeert een getal als antwoord te geven (in cijfers of woorden),
  ook als je niet weet of dat getal klopt.
- HULP: Het kind vraagt om hulp of zegt het niet te weten.
- HERHAAL: Het kind wil de rekensom opnieuw horen.
- ANDER_ONDERWERP: Het kind wil wisselen naar een ander onderwerp of een andere activiteit.
- STOP: Het kind wil stoppen of weggaan.
- ONBEKEND: Het is niet duidelijk wat het kind bedoelt.

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
"Ik wil nu delen doen" -> ANDER_ONDERWERP
"Ik wil nu breuken doen" -> ANDER_ONDERWERP
"Ik wil naar huis" -> STOP

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
    "stickers",
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
