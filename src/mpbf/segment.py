"""Découpage d'un Quotidien DGCMEF en avis individuels.

Le Quotidien contient deux grandes parties, annoncées par le sommaire :
  - RESULTATS PROVISOIRES : attributions et rejets (nos motifs d'échec)
  - AVIS                  : les appels d'offres ouverts (nos opportunités)

Chaque bloc conserve sa page source : sans traçabilité, pas de produit
fiable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .normalize import fold, normalize

# --- Types de procédure rencontrés dans le Quotidien -----------------
# Ordre important : les variantes longues d'abord.
PROCEDURES = [
    ("appel_offres_international", r"appel\s+d['’]?\s*offres?\s+ouvert\s+international(?:\s+accéléré)?"),
    ("appel_offres_anticipation", r"appel\s+d['’]?\s*offres?\s+ouvert\s+par\s+anticipation"),
    ("appel_offres", r"appel\s+d['’]?\s*offres?(?:\s+ouvert)?"),
    ("demande_propositions", r"demande\s+de\s+propositions?"),
    ("demande_prix_commande", r"demande\s+de\s+prix\s+à\s+commande"),
    ("demande_prix", r"demande\s+de\s+prix"),
    ("demande_cotation", r"demande\s+de\s+cotation"),
    ("manifestation_interet", r"(?:avis\s+a\s+)?manifestation\s+d['’]?\s*interet"),
]

# En-tête d'avis : <procédure> N°<ref> ...
RE_AVIS_HEAD = re.compile(
    r"(?P<proc>" + "|".join(p for _, p in PROCEDURES) + r")"
    r"\s*(?:n[°o]\s*)(?P<ref>[\w°/.\-]+(?:/[\w.\-]+)*)",
    re.IGNORECASE,
)

# Une ligne d'organisation : majuscules, pas de numéro d'ordre.
RE_ORG_LINE = re.compile(r"^[A-ZÉÈÀÇŒ][A-ZÉÈÀÇŒ'’\s,.\-()/]{12,}$")

# Toutes les lignes en majuscules ne nomment pas un acheteur. Deux familles
# de faux positifs, mesurées sur 212 avis réels (9 % et quelques pour cent) :
#
#   « AVIS DE DEMANDE DE PRIX »            -> intitulé de rubrique
#   « TRANSFORMATEURS AU PROFIT DU CHUR »  -> fragment d'objet en capitales
#
# Le piège est que `organisation` affichait 100 % de complétude : un champ
# toujours rempli, parfois faux. La complétude ne mesure pas la justesse.
# Troisième famille : une ligne qui commence par une préposition est la
# seconde moitié d'un nom coupé par la mise en page (« CENTRE HOSPITALIER
# UNIVERSITAIRE REGIONAL \n DE OUAHIGOUYA »). L'écarter fait retomber sur
# la première moitié — tronquée, mais identifiable, là où « DE OUAHIGOUYA »
# seul ne désigne personne.
RE_ORG_EXCLUE = re.compile(
    r"^\s*(?:AVIS|RECTIFICATIF|ADDITIF|COMMUNIQUE|RESULTATS?|SOMMAIRE"
    r"|ERRATUM)\b"
    r"|^\s*(?:DE|DU|DES|D'|ET|AU|AUX|LA|LE|LES)\s"
    r"|\bAU\s+PROFIT\b",
    re.IGNORECASE,
)

SECTION_RESULTATS = "resultats"
SECTION_AVIS = "avis"


@dataclass
class Bloc:
    """Un avis brut, non encore parsé."""

    section: str
    organisation: str | None
    procedure: str
    reference: str
    texte: str
    page_debut: int
    page_fin: int
    lignes: list[str] = field(default_factory=list)


def _proc_label(matched: str) -> str:
    low = matched.lower()
    for label, pattern in PROCEDURES:
        if re.fullmatch(pattern, low, re.IGNORECASE):
            return label
    for label, pattern in PROCEDURES:
        if re.match(pattern, low, re.IGNORECASE):
            return label
    return "inconnu"


# Renvoi de page du sommaire : « P. 49 », « P.49 ».
RE_RENVOI_PAGE = re.compile(r"\bP\.\s*(\d{1,3})\b")


def page_debut_avis(texte: str, offsets: list[tuple[int, int]]) -> int | None:
    """Première page de la section AVIS, lue dans le sommaire.

    Le corps du Quotidien ne porte **aucune** bannière de section : vérifié
    sur onze numéros consécutifs, le mot « AVIS » n'apparaît isolé que dans
    le sommaire de la page 2. Chercher un séparateur dans le corps classait
    donc la totalité du document en « resultats », et la moitié
    « opportunités » du produit restait vide.

    Le sommaire annonce la section puis sa page de début :

        RESULTATS PROVISOIRES
        - Ministères, institutions et maîtrises d'ouvrages déléguées P. 3
        AVIS
        • Marchés de fournitures et services courants P. 49

    On retient le premier renvoi de page qui suit « AVIS ». La mise en page
    sur deux colonnes colle parfois ce mot au pied de page — « Site web :
    www.dgcmef.gov.bf AVIS » — d'où la recherche sur le mot isolé plutôt
    que sur une ligne entière.
    """
    fin_sommaire = 0
    for start, page in offsets:
        if page <= 3:
            fin_sommaire = start
    sommaire = texte[:fin_sommaire] if fin_sommaire else texte

    marqueurs = list(re.finditer(r"(?<![\w'])AVIS(?![\w'])", sommaire))
    if not marqueurs:
        return None
    renvoi = RE_RENVOI_PAGE.search(sommaire, marqueurs[-1].end())
    return int(renvoi.group(1)) if renvoi else None


def segmenter(pages: list[str]) -> list[Bloc]:
    """pages : texte page par page (index 0 = page 1 du PDF).

    Retourne la liste des blocs d'avis détectés, avec leur page source.
    """
    # Table de correspondance offset -> numéro de page
    offsets: list[tuple[int, int]] = []
    morceaux = []
    curseur = 0
    for i, brut in enumerate(pages, start=1):
        page_txt = normalize(brut) + "\n"
        offsets.append((curseur, i))
        morceaux.append(page_txt)
        curseur += len(page_txt)
    texte = "".join(morceaux)
    replie = fold(texte)

    def page_de(pos: int) -> int:
        page = 1
        for start, num in offsets:
            if start <= pos:
                page = num
            else:
                break
        return page

    debut_avis = page_debut_avis(replie, offsets)

    def section_de(pos: int) -> str:
        # Sommaire illisible : tout reste en « resultats ». Classer à tort
        # un résultat en opportunité ferait monter un dossier sur un marché
        # déjà attribué.
        if debut_avis is None:
            return SECTION_RESULTATS
        return SECTION_AVIS if page_de(pos) >= debut_avis else SECTION_RESULTATS

    # Organisations : dernière ligne majuscule vue avant l'avis
    orgs: list[tuple[int, str]] = []
    for m in re.finditer(r"^(.+)$", replie, re.MULTILINE):
        ligne = texte[m.start():m.end()].strip()
        if (RE_ORG_LINE.match(ligne) and not RE_AVIS_HEAD.search(ligne)
                and not RE_ORG_EXCLUE.search(ligne)):
            orgs.append((m.start(), ligne))

    def org_de(pos: int) -> str | None:
        trouve = None
        for start, nom in orgs:
            if start < pos:
                trouve = nom
            else:
                break
        return trouve

    heads = list(RE_AVIS_HEAD.finditer(replie))
    blocs: list[Bloc] = []
    for idx, m in enumerate(heads):
        debut = m.start()
        fin = heads[idx + 1].start() if idx + 1 < len(heads) else len(texte)
        corps = texte[debut:fin].strip()
        blocs.append(
            Bloc(
                section=section_de(debut),
                organisation=org_de(debut),
                procedure=_proc_label(m.group("proc")),
                reference=m.group("ref").strip(" .;:"),
                texte=corps,
                page_debut=page_de(debut),
                page_fin=page_de(max(debut, fin - 1)),
                lignes=[l for l in corps.split("\n") if l.strip()],
            )
        )
    return blocs
