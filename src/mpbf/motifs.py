"""Motifs de rejet des soumissionnaires.

C'est l'actif différenciant du projet. La section RESULTATS PROVISOIRES du
Quotidien dit, entreprise par entreprise, pourquoi une offre a été écartée.
Les agrégateurs concurrents traitent ces pages comme du bruit. Elles sont
en réalité le corpus qui permet de répondre à « puis-je gagner ce marché »
plutôt qu'à « ce marché existe ».

Chaque motif porte sur une cause vérifiable en amont par le candidat.
"""

from __future__ import annotations

import re
from collections import Counter

from .normalize import fold
from dataclasses import dataclass

# categorie -> (libellé lisible, motif regex, vérifiable avant de soumissionner)
CATALOGUE = [
    ("rccm_absent", "RCCM non fourni",
     r"absence\s+de\s+RCCM|RCCM\s+non\s+fourni", True),
    ("rccm_domaine", "RCCM incompatible avec le domaine du lot",
     r"RCCM\s+incompatible\s+avec\s+le\s+domaine", True),
    ("agrement_absent", "Agrément technique non fourni",
     r"absence\s+d['’]?agrement|agrement\s+.{0,30}non\s+fourni", True),
    ("agrement_categorie", "Catégorie d'agrément insuffisante",
     r"agrement\s+de\s+categorie\s+\w+\s+au\s+lieu", True),
    ("fiscal", "Attestation de situation fiscale non fournie",
     r"attestation\s+de\s+situation\s+fiscale\s+non\s+fournie", True),
    ("piece_admin", "Pièces administratives non fournies",
     r"pieces\s+administratives\s+n['’]?ont\s+pas\s+ete\s+fournies", True),
    ("ca_insuffisant", "Chiffre d'affaires insuffisant",
     r"chiffre\s+d['’]?affaires\s+(?:insuffisant|moyen)", True),
    ("references", "Références similaires insuffisantes ou non pertinentes",
     r"references?\s+(?:similaires?\s+)?(?:non\s+pertinentes?|insuffisantes?)"
     r"|aucune\s+reference\s+fournie", True),
    ("lettre_non_signee", "Lettre de soumission non signée",
     r"lettre\s+de\s+soumission\s+non\s+signee", True),
    ("garantie", "Garantie d'offre non conforme",
     r"garantie\s+d['’]?offre\s+.{0,60}(?:n['’]est\s+pas|non\s+conforme)", True),
    ("diplome", "Diplôme ou qualification du personnel non conforme",
     r"diplome\s+.{0,60}(?:non\s+conforme|au\s+lieu\s+de)", True),
    ("langue", "Document non traduit en français",
     r"non\s+traduits?\s+en\s+francais", True),
    ("specs_techniques", "Spécifications techniques non fournies ou non conformes",
     r"tableau\s+des\s+specifications\s+techniques\s+.{0,40}non\s+fourni"
     r"|fiches?\s+techniques?\s+n['’]?(?:a|ont)\s+pas\s+ete\s+fournie", True),
    # Motifs liés au prix : non vérifiables en amont, mais utiles à la stratégie
    ("offre_basse", "Offre anormalement basse",
     r"offre\s+anormalement\s+basse", False),
    ("hors_enveloppe", "Offre hors enveloppe budgétaire",
     r"hors\s+enveloppe", False),
]

RE_NON_RETENU = re.compile(
    r"non\s+(?:retenu|conforme|classe)", re.IGNORECASE
)


@dataclass
class Motif:
    categorie: str
    libelle: str
    verifiable_en_amont: bool
    extrait: str
    reference_avis: str
    page: int


def extraire_motifs(texte: str, reference_avis: str, page: int) -> list[Motif]:
    """Retourne les motifs de rejet présents dans un bloc de résultats."""
    trouves: list[Motif] = []
    rep = fold(texte)
    for categorie, libelle, pattern, verifiable in CATALOGUE:
        for m in re.finditer(pattern, rep, re.IGNORECASE):
            debut = max(0, m.start() - 60)
            extrait = texte[debut:m.end() + 60].replace("\n", " ").strip()
            trouves.append(
                Motif(categorie, libelle, verifiable, extrait, reference_avis, page)
            )
            break  # un motif compte une fois par avis
    return trouves


RE_DEBUT_REJET = re.compile(r"[Nn]on\s+(?:conforme|retenu|classe)",
                            re.IGNORECASE)
LONGUEUR_SEGMENT = 250

# Sur quelques pages en tableau, l'extraction PDF entrelace les colonnes
# caractère par caractère : « M M a i x n i i : : » est « Min : » et
# « Max : » superposés. Cela touche 1 % des blocs — assez rare pour ne pas
# justifier un autre moteur d'extraction, assez illisible pour ne pas
# encombrer un jeu qu'un humain doit annoter à la main.
RE_TEXTE_ENTRELACE = re.compile(r"(?:(?<!\S)\S(?=\s)\s){8,}")


def est_illisible(segment: str) -> bool:
    """Vrai si l'extraction a entrelacé les colonnes de ce passage."""
    return bool(RE_TEXTE_ENTRELACE.search(segment))


LONGUEUR_MINIMALE = 25


def segments_de_rejet(texte: str) -> list[str]:
    """Les passages qui énoncent pourquoi une offre a été écartée.

    Le motif suit immédiatement la mention « Non conforme » / « Non
    retenu » / « Non classé ». On découpe une fenêtre après chaque mention
    plutôt que de raisonner sur le bloc entier : un bloc de résultats
    couvre des dizaines de soumissionnaires, et mélanger leurs motifs
    interdirait de mesurer quoi que ce soit.

    Les fenêtres sont **disjointes** : chacune s'arrête à la mention
    suivante, sans jamais dépasser LONGUEUR_SEGMENT. Une fenêtre de
    longueur fixe empiétait sur la suivante dès que deux rejets étaient
    proches, et le même motif ressortait trois ou quatre fois — de quoi
    gonfler artificiellement un taux calculé là-dessus.

    Sert à l'export d'annotation : c'est contre ces segments, annotés à la
    main, que le CATALOGUE ci-dessus doit être calibré. Le calibrer sur des
    extraits écrits à la main revient à mesurer le code contre lui-même.
    """
    marques = [m.start() for m in RE_DEBUT_REJET.finditer(texte)]
    segments = []
    for i, debut in enumerate(marques):
        borne = marques[i + 1] if i + 1 < len(marques) else len(texte)
        fin = min(borne, debut + LONGUEUR_SEGMENT)
        extrait = " ".join(texte[debut:fin].split())
        # Deux mentions collées ne laissent pas de place à un motif.
        if len(extrait) >= LONGUEUR_MINIMALE:
            segments.append(extrait)
    return segments


def statistiques(motifs: list[Motif]) -> list[tuple[str, int, bool]]:
    """Classement des causes d'échec. Alimente directement check_fit."""
    compte = Counter(m.categorie for m in motifs)
    index = {c: (lib, verif) for c, lib, _, verif in CATALOGUE}
    return sorted(
        ((index[c][0], n, index[c][1]) for c, n in compte.items()),
        key=lambda t: t[1],
        reverse=True,
    )


def taux_evitable(motifs: list[Motif]) -> float | None:
    """Part des rejets qui auraient pu être évités avant de soumissionner.

    C'est l'argument commercial du produit, chiffré sur données réelles.
    """
    if not motifs:
        return None
    evitables = sum(1 for m in motifs if m.verifiable_en_amont)
    return round(evitables / len(motifs), 3)
