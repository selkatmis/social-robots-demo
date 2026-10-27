"""Authoritative reference data for ARI's ``Zoek de Verschillen`` game.

These fifteen references were supplied by the operator in ``klokwijzer.txt``.
They describe LEFT and RIGHT, not a verified A/B screen-label mapping.

The legacy field ``picture_a`` describes the first reference side (left), and
``picture_b`` the second (right).  Additional accepted descriptions
must preserve that same ordering.  ``synonyms`` contains alternative names
for the visual feature; it does not change the A/B descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionalDescription:
    """One accepted, directional description of a reference difference."""

    picture_a: str
    picture_b: str


@dataclass(frozen=True)
class DifferenceReference:
    """One verified difference between the two existing tablet pictures.

    ``difference_id`` must be stable and unique.  ``feature`` names the object
    or visual feature.  ``picture_a`` and ``picture_b`` contain the canonical
    state in reference-side order (left/right for the supplied dataset).
    ``accepted_descriptions`` can hold verified
    alternative pairs, while ``synonyms`` can hold alternative feature names.
    """

    difference_id: str
    feature: str
    picture_a: str
    picture_b: str
    accepted_descriptions: tuple[DirectionalDescription, ...] = ()
    synonyms: tuple[str, ...] = ()
    accepted_observations: tuple[str, ...] = ()
    hint: str = ""


REFERENCE_SIDE_LABELS = ("links", "rechts")
# Set to "links" or "rechts" only after checking the actual A/B tablet labels.
# Left/right descriptions already work without this optional mapping.
PICTURE_A_SIDE: str | None = None

REFERENCE_DIFFERENCES: tuple[DifferenceReference, ...] = (
    DifferenceReference(
        "klokwijzers", "klokwijzers", "wijzers in een andere stand dan rechts",
        "wijzers in een andere stand dan links",
        synonyms=("klok", "wijzer", "tijd", "uurwerk"),
        accepted_observations=("De klok geeft een andere tijd aan.", "De wijzers staan anders."),
        hint="Kijkt u eens naar de wijzers van de klok. Valt u daar iets op?",
    ),
    DifferenceReference(
        "berg_wandfoto", "berg op de wandfoto", "puntige berg", "andere vorm van de berg",
        synonyms=("berg", "bergpunt", "piek", "schilderij", "wandfoto", "wandplaat"),
        accepted_observations=("De berg op het schilderij heeft een andere vorm.",),
        hint="Kijkt u eens naar de berg op de afbeelding aan de wand. Ziet u iets anders?",
    ),
    DifferenceReference(
        "tong_meisje", "tong van het meisje", "meisje steekt haar tong uit",
        "meisje steekt haar tong niet uit",
        synonyms=("tong", "mond", "gezicht van het meisje", "meisje"),
        accepted_observations=("Links steekt het meisje haar tong uit.", "De tong is anders."),
        hint="Kijkt u eens naar het gezicht van het meisje. Ziet u daar een verschil?",
    ),
    DifferenceReference(
        "gordijn", "gordijn", "dunner gordijn", "dikker gordijn",
        synonyms=("gordijnen", "raamgordijn", "overgordijn"),
        accepted_observations=("Het gordijn is anders.", "Rechts is het gordijn breder of dikker."),
        hint="Kijkt u eens naar het gordijn. Is dat op beide plaatjes hetzelfde?",
    ),
    DifferenceReference(
        "kraag", "kraag", "andere kraag dan rechts", "andere kraag dan links",
        synonyms=("boord", "halsboord", "kraagje"),
        accepted_observations=("De kraag is anders.", "Het boordje ziet er anders uit."),
        hint="Kijkt u eens naar de kraag van de kleding. Valt u daar een verschil op?",
    ),
    DifferenceReference(
        "taartglazuur", "glazuur van de taart", "ander glazuur dan rechts",
        "ander glazuur dan links",
        synonyms=("glazuur", "taart", "taartlaag", "bovenkant van de taart", "versiering van de taart"),
        accepted_observations=("De laag op de taart is anders.", "De taart heeft ander glazuur."),
        hint="Kijkt u eens naar de bovenkant van de taart. Ziet u daar iets anders?",
    ),
    DifferenceReference(
        "bananen", "aantal bananen", "twee bananen", "drie bananen",
        accepted_descriptions=(DirectionalDescription("minder bananen", "meer bananen"),),
        synonyms=("bananen", "banaan", "tros bananen", "fruit"),
        accepted_observations=(
            "Rechts liggen meer bananen.", "Er zijn twee en drie bananen.",
            "Er mist een banaan.", "Er ontbreekt een banaan.",
            "Ik zie dat er een banaan mist.",
            "Ik zie dat er een banaan ontbreekt.",
            "Er is een banaan minder.", "Er is een banaan meer.",
            "Er is een banaan mist.", "Er is een banaan meest.",
            "Links mist een banaan.", "Links ontbreekt een banaan.",
            "Links zijn er twee bananen en rechts drie.",
            "Links zijn er 2 bananen en rechts 3.",
            "Links liggen twee bananen en rechts drie.",
            "Links liggen 2 bananen en rechts 3.",
            "Links heb je twee bananen en rechts heb je er drie.",
            "Links heb je 2 bananen en rechts heb je er 3.",
        ),
        hint="Kijkt u eens naar de bananen. Zijn het er op beide plaatjes evenveel?",
    ),
    DifferenceReference(
        "koekje", "afwijkend koekje", "ander koekje dan het gele koekje rechts",
        "een geel koekje",
        synonyms=("koek", "koekje", "koekjes", "gele koek"),
        accepted_observations=("Rechts ligt een geel koekje.", "Een van de koekjes is anders."),
        hint="Kijkt u eens naar de koekjes. Ziet u iets aan de kleur of vorm?",
    ),
    DifferenceReference(
        "kastknop", "knop van de kast", "ronde knop", "hartvormige knop",
        accepted_descriptions=(DirectionalDescription("cirkel", "hartje"),),
        synonyms=("kastknop", "deurknop", "kastdeurtje", "handvat", "knopje"),
        accepted_observations=("Rechts is de knop een hartje.", "De kast heeft een ander knopje."),
        hint="Kijkt u eens naar het knopje van de kast. Heeft het dezelfde vorm?",
    ),
    DifferenceReference(
        "voorwerpen_op_kast", "voorwerpen op de kast", "een kom of vaas",
        "poppetjes",
        synonyms=("kom", "vaas", "poppetjes", "beeldjes", "figuurtjes", "voorwerpen op de kast"),
        accepted_observations=("Op de kast staan rechts beeldjes in plaats van een vaas.",),
        hint="Kijkt u eens wat er boven op de kast staat. Ziet u daar een verschil?",
    ),
    DifferenceReference(
        "sokken_meisje", "sokken van het meisje", "geen sokken", "wel sokken",
        synonyms=("sokken", "sokjes", "kousen", "voeten van het meisje"),
        accepted_observations=("Rechts draagt het meisje sokken.", "Links draagt ze geen sokken."),
        hint="Kijkt u eens naar de voeten van het meisje. Ziet u daar iets anders?",
    ),
    DifferenceReference(
        "roze_bloem", "roze bloem", "een roze bloem ontbreekt", "een roze bloem meer",
        accepted_descriptions=(DirectionalDescription("minder bloemen", "meer bloemen"),),
        synonyms=("bloem", "bloemen", "roze bloemetje"),
        accepted_observations=("Er is rechts een bloem meer.", "Links mist een roze bloem."),
        hint="Kijkt u eens naar de bloemen. Zijn het er op beide plaatjes evenveel?",
    ),
    DifferenceReference(
        "voorwerp_poes", "voorwerp bij de poes", "lepel", "snoep of lolly",
        synonyms=("poes", "kat", "lepel", "snoep", "lolly", "lollipop"),
        accepted_observations=("De kat heeft rechts een lolly in plaats van een lepel.",),
        hint="Kijkt u eens naar wat de poes heeft. Is dat op beide plaatjes hetzelfde?",
    ),
    DifferenceReference(
        "gezicht_bord", "gezicht op het bord op de kast", "geen gezicht op het bord",
        "wel een gezicht op het bord",
        synonyms=("bord", "bordje", "smiley", "gezichtje", "lachend gezicht"),
        accepted_observations=("Rechts heeft het bord een gezichtje.", "De smiley ontbreekt links."),
        hint="Kijkt u eens naar het bord op de kast. Valt u daar iets op?",
    ),
    DifferenceReference(
        "haarspeld_oma", "haarspeld van oma", "rode haarspeld", "geen haarspeld",
        synonyms=("haarspeld", "speldje", "haarknip", "haarclip", "oma", "oudere vrouw"),
        accepted_observations=("Links heeft oma een rood speldje in haar haar.",),
        hint="Kijkt u eens naar het haar van de oudere vrouw. Ziet u een verschil?",
    ),
)


__all__ = [
    "DirectionalDescription",
    "DifferenceReference",
    "REFERENCE_DIFFERENCES",
    "REFERENCE_SIDE_LABELS",
    "PICTURE_A_SIDE",
]
