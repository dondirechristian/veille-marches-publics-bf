"""Extraction des champs d'un avis.

Règle absolue : un champ absent du texte source reste None. On ne devine
jamais. La complétude se mesure, elle ne se déclare pas.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .normalize import fold, normalize_amount
from .segment import Bloc

MOIS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
}

RE_DATE_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
RE_DATE_TXT = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(sorted(set(fold(m) for m in MOIS)))
    + r")\s+(\d{4})\b", re.IGNORECASE
)

RE_FINANCEMENT = re.compile(
    r"financement\s*:\s*(?P<v>[^\n.;]{3,120})", re.IGNORECASE
)
# Par ordre de fiabilite decroissante : la premiere qui donne une date
# gagne. Une vraie date limite prime sur une date d'ouverture des plis,
# qui prime sur un depouillement.
#
# Les trois premieres formulations viennent de la section RESULTATS, qui
# recapitule en style telegraphique (« Date limite de depot : 08/01/2026 »).
# La section AVIS, elle, redige en prose :
#
#   « Les offres devront etre remises a l'adresse ci-dessous au plus tard
#     le 29/09/2026 a 9 heures 00 »
#   « ... devront parvenir ou etre remises au bureau de la PRCP, avant le
#     09/09/2026 a 09 heures 00 TU »
#
# Sans les trois dernieres, la date limite n'etait extraite que sur 1 % des
# avis reels — une veille incapable de dire quand une consultation ferme.
# Mesure sur 212 avis de 11 Quotidiens : 1 % -> 90 %.
#
# La fenetre de capture est courte (30 caracteres) : la date doit suivre le
# marqueur de pres, sinon « avant le » accroche une date sans rapport.
RE_DATES_LIMITE = [
    re.compile(r"date\s+limite\s+de\s+(?:depot|soumission|remise)"
               r"[^:]{0,30}:?\s*(?P<v>[^\n;]{4,60})", re.IGNORECASE),
    re.compile(r"au\s+plus\s+tard\s+le\s+(?P<v>[^\n;.]{4,30})", re.IGNORECASE),
    re.compile(r"\bavant\s+le\s+(?P<v>[^\n;.]{4,30})", re.IGNORECASE),
    re.compile(r"date\s+(?:de\s+)?d['’]?ouverture(?:\s+des\s+plis)?"
               r"[^:]{0,30}:?\s*(?P<v>[^\n;]{4,60})", re.IGNORECASE),
    re.compile(r"ouvertures?\s+des\s+plis[^.\n]{0,80}?\ble\s+"
               r"(?P<v>[^\n;.]{4,45})", re.IGNORECASE),
    re.compile(r"date\s+de\s+depouillement\s*:?\s*(?P<v>[^\n;]{4,60})",
               re.IGNORECASE),
]
# --- Montant prévisionnel --------------------------------------------
# La section AVIS écrit le montant en toutes lettres, puis en chiffres
# entre parenthèses :
#
#   Montant prévisionnel : onze millions quatre cent soixante-quatorze
#   mille cinq cent soixante-seize (11 474 576) FCFA HTVA soit treize
#   millions (13 540 000) FCFA TTC.
#
# Plus de 70 caractères séparent le libellé du premier chiffre : l'ancienne
# tolérance de 40 échouait sur tout ce gabarit. Mesure sur 212 avis réels :
# 20 % -> 55 %.
#
# Quand HTVA et TTC sont donnés, c'est le premier — le HTVA — qui est
# retenu. Les offres se comparent hors taxes.
RE_MONTANTS = [
    # Prose : le chiffre est entre parenthèses, après le montant en lettres.
    re.compile(r"(?:budget|montant|mnt)\s+previsionnel"
               r"[^\n]{0,170}?\(\s*(?P<v>\d[\d\s.]{4,18})\s*\)", re.IGNORECASE),
    # Télégraphique : « Mnt previsionnel : 37 760 000 ».
    re.compile(r"(?:budget|montant|mnt)\s+previsionnel"
               r"[^\d\n]{0,45}(?P<v>\d[\d\s.]{3,18})", re.IGNORECASE),
]

# Un marché public à moins de 10 000 FCFA n'existe pas. Sans ce plancher,
# une parenthèse de comptage — « en deux (02) lots », « (100) jours » —
# atterrirait dans le budget prévisionnel.
MONTANT_PLANCHER = 10_000
RE_AGREMENT = re.compile(r"agrement\s+(?:technique\s+)?(?:exige|requis)", re.IGNORECASE)
RE_LOT = re.compile(r"^lot\s*(?:n°\s*)?(\d+|unique)\s*:", re.IGNORECASE | re.MULTILINE)


@dataclass
class Avis:
    reference: str
    procedure: str
    section: str
    organisation: str | None
    objet: str | None
    financement: str | None
    date_limite: str | None
    montant_previsionnel: int | None
    agrement_exige: bool
    nb_lots: int
    page_debut: int
    page_fin: int
    source: str

    def as_dict(self) -> dict:
        return asdict(self)


def _premiere_date(fragment: str) -> str | None:
    m = RE_DATE_NUM.search(fragment)
    if m:
        j, mo, a = m.groups()
        return f"{a}-{int(mo):02d}-{int(j):02d}"
    m = RE_DATE_TXT.search(fragment)
    if m:
        j, mois, a = m.groups()
        return f"{a}-{MOIS[mois.lower()]:02d}-{int(j):02d}"
    return None


# --- Objet du marché --------------------------------------------------
# Dans la section AVIS, l'objet n'est pas dans l'en-tête : celui-ci enchaîne
# référence, financement et montant prévisionnel. L'objet vient plus loin,
# dans la description des lots :
#
#   demande de prix n°2026-021/MJ/SG/DMP du 26/08/2026 Financement : Budget
#   Etat [...] Montant prévisionnel : onze millions [...] 3. Les acquisitions
#   se décomposent en deux (02) lots répartis comme suit : - Lot 1 :
#   Fourniture de pauses-café et location de salles au profit de la DGPCP
#
# Chercher dans les quatre premières lignes ne trouvait donc rien : l'objet
# n'était extrait que sur 19 % des avis réels. Avec la cascade ci-dessous,
# mesuré sur 212 avis de 11 Quotidiens : 79 %.
MARQUEURS_OBJET = [
    re.compile(r"(?:ayant\s+pour\s+objet|a\s+pour\s+objet)\s*:?\s*"
               r"(?P<v>[^\n;]{12,220})", re.IGNORECASE),
    re.compile(r"\bobjet\s*:\s*(?P<v>[^\n;]{12,220})", re.IGNORECASE),
    re.compile(r"\blot\s+unique\s*:\s*(?P<v>[^\n;]{12,220})", re.IGNORECASE),
    re.compile(r"\blot\s*n?.?\s*0?1\s*:\s*(?P<v>[^\n;]{12,220})", re.IGNORECASE),
    # Gabarit de la section RESULTATS, plus télégraphique.
    re.compile(r"\bpour\s+(?:l['ea]|la|le|les|des?|du)\s+"
               r"(?P<v>[^\n;]{12,220})", re.IGNORECASE),
]

# L'objet s'arrête où commence le champ suivant.
RE_FIN_OBJET = re.compile(
    r"\s*(?:financement|montant\s+previsionnel|autorite\s+contractante"
    r"|publication|date\s+limite)\s*:", re.IGNORECASE)

# « Lot 1 : » en tête de capture est une étiquette, pas l'objet.
RE_ETIQUETTE_LOT = re.compile(r"^\W*lot\s*(?:unique|n?.?\s*\d+)\s*:\s*",
                              re.IGNORECASE)

# « Lot 1 : » introduit tantôt une description, tantôt le montant du lot.
# Une capture qui décrit un prix n'est pas un objet : on passe au marqueur
# suivant plutôt que d'enregistrer un montant comme intitulé de marché.
RE_CAPTURE_MONTANT = re.compile(
    r"montant|fcfa|francs?\s+cfa|htva|\bttc\b|\(\s*\d[\d\s.]{4,}\)"
    r"|^\W*\w+\s+millions?\b", re.IGNORECASE)


def _montant(replie: str) -> int | None:
    """Montant prévisionnel, ou None. Ne devine jamais.

    `replie` : texte sans accents (les chiffres y sont inchangés).
    """
    for motif in RE_MONTANTS:
        m = motif.search(replie)
        if m:
            valeur = normalize_amount(m.group("v"))
            if valeur and valeur >= MONTANT_PLANCHER:
                return valeur
    return None


def _objet(bloc: Bloc) -> str | None:
    """Intitulé du marché, ou None si aucun marqueur fiable ne le porte."""
    texte = bloc.texte
    replie = fold(texte)  # même longueur : les offsets restent valides
    for marqueur in MARQUEURS_OBJET:
        for m in marqueur.finditer(replie):
            valeur = RE_FIN_OBJET.split(texte[m.start("v"):m.end("v")])[0]
            valeur = RE_ETIQUETTE_LOT.sub("", " ".join(valeur.split()))
            valeur = valeur.strip(" .,;:-")
            if len(valeur) >= 12 and not RE_CAPTURE_MONTANT.search(valeur[:130]):
                return valeur[:250]
    return None


def parser(bloc: Bloc, source: str) -> Avis:
    txt = bloc.texte
    rep = fold(txt)  # meme longueur : les offsets restent valides

    fin = RE_FINANCEMENT.search(rep)
    montant = _montant(rep)

    # On s'arrete au premier motif qui donne une date *exploitable*, pas au
    # premier qui matche : un marqueur suivi d'un fragment sans date ne doit
    # pas masquer un marqueur moins prioritaire qui, lui, en porte une.
    date_limite = None
    for motif in RE_DATES_LIMITE:
        trouve = motif.search(rep)
        if trouve:
            date_limite = _premiere_date(trouve.group("v"))
            if date_limite:
                break

    return Avis(
        reference=bloc.reference,
        procedure=bloc.procedure,
        section=bloc.section,
        organisation=bloc.organisation,
        objet=_objet(bloc),
        financement=txt[fin.start("v"):fin.end("v")].strip(" .;:") if fin else None,
        date_limite=date_limite,
        montant_previsionnel=montant,
        agrement_exige=bool(RE_AGREMENT.search(txt)),
        nb_lots=len(set(RE_LOT.findall(txt))),
        page_debut=bloc.page_debut,
        page_fin=bloc.page_fin,
        source=source,
    )


def completude(avis: list[Avis]) -> dict[str, float]:
    """Taux de présence par champ. C'est ce qu'on affiche, pas des promesses."""
    if not avis:
        return {}
    champs = ["organisation", "objet", "financement", "date_limite",
              "montant_previsionnel"]
    total = len(avis)
    return {
        c: round(sum(1 for a in avis if getattr(a, c) not in (None, "")) / total, 3)
        for c in champs
    }
