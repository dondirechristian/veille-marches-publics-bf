"""check_fit : « puis-je gagner ce marché » plutôt que « ce marché existe ».

Deux sources d'exigence, jamais confondues dans le rendu :

  1. Ce que l'avis **dit** : catégorie d'agrément, chiffre d'affaires
     minimum, références exigées. Cité mot pour mot depuis le texte source.
  2. Ce qui a **réellement** écarté des concurrents : la table
     `motif_rejet`, alimentée par la section RESULTATS PROVISOIRES. Un
     contrôle adossé à 40 rejets observés ne pèse pas comme un contrôle
     adossé à 1.

La seconde source est l'actif du projet. Les agrégateurs concurrents n'ont
que la première, et encore : sans la citer.

Aucun contrôle n'invente. Quand le profil ou l'avis est muet, le statut est
`indetermine` et il est compté comme tel dans la couverture.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

from .motifs import CATALOGUE
from .normalize import fold, normalize_amount
from .profil import Profil

# --- Statuts, du plus grave au plus neutre ---------------------------
BLOQUANT = "bloquant"
RISQUE = "risque"
CONFORME = "conforme"
INDETERMINE = "indetermine"
SANS_OBJET = "sans_objet"

_GRAVITE = {BLOQUANT: 0, RISQUE: 1, INDETERMINE: 2, CONFORME: 3, SANS_OBJET: 4}

# --- Exigences lisibles dans le texte de l'avis -----------------------
# Elles tournent sur le texte replié (sans accents), comme tout le reste
# du parsing : voir normalize.fold.
RE_CATEGORIE = re.compile(
    r"agrement\s+(?:technique\s+)?(?:de\s+)?categorie\s+(?P<cat>[A-Z]\s?\d+)",
    re.IGNORECASE,
)
RE_CA_MINIMUM = re.compile(
    r"chiffre\s+d[']?affaires\s+(?:moyen\s+)?(?:annuel\s+)?[^.\n]{0,60}?"
    r"(?:minimum|au\s+moins|superieur\s+a|egal\s+a)[^\d]{0,20}(?P<v>[\d\s]{7,20})",
    re.IGNORECASE,
)
RE_REFERENCES_EXIGEES = re.compile(
    r"references?\s+(?:techniques?|similaires?)", re.IGNORECASE
)

# Seule pièce administrative que le corpus nomme explicitement comme cause
# de rejet (catégorie `fiscal` du catalogue). Les autres attestations sont
# contrôlées telles que le profil les déclare : inventer ici une liste de
# pièces « obligatoires » non observée dans les résultats reviendrait à
# bloquer un candidat sur une exigence qu'on aurait supposée.
PIECE_NOMMEE = "fiscale"

_LIBELLES = {c: lib for c, lib, _, _ in CATALOGUE}


@dataclass
class Controle:
    """Un point de conformité tranché, avec sa justification."""

    categorie: str          # vocabulaire commun avec motifs.CATALOGUE
    libelle: str
    statut: str
    constat: str            # pourquoi ce statut, en français
    exigence: str | None    # citation de l'avis, None si l'avis est muet
    rejets_observes: int    # occurrences de cette cause dans le corpus

    def as_dict(self) -> dict:
        return asdict(self)


def _ctrl(categorie, statut, constat, exigence=None, freq=None) -> Controle:
    return Controle(
        categorie=categorie,
        libelle=_LIBELLES.get(categorie, categorie),
        statut=statut,
        constat=constat,
        exigence=exigence,
        rejets_observes=(freq or {}).get(categorie, 0),
    )


def _fcfa(montant: int) -> str:
    return f"{montant:,}".replace(",", " ") + " FCFA"


def frequences_motifs(conn) -> dict[str, int]:
    """Nombre de rejets observés par catégorie, sur tout le corpus."""
    lignes = conn.execute(
        "SELECT categorie, COUNT(*) n FROM motif_rejet GROUP BY categorie"
    ).fetchall()
    return {l["categorie"]: l["n"] for l in lignes}


def _mots(texte: str) -> set[str]:
    """Mots significatifs, repliés au singulier.

    Sans le repli, « materiel informatique » au RCCM ne recoupait pas
    « materiels peri-informatiques » dans l'objet : le candidat était
    signalé hors de son propre domaine. Le découpage casse aussi les
    composés (« peri-informatiques » -> « peri », « informatique »).
    """
    return {re.sub(r"[sx]$", "", m)
            for m in re.findall(r"[a-z]{4,}", fold(texte or "").lower())}


def _domaine_couvert(profil: Profil, objet: str | None) -> bool | None:
    """True/False si on peut trancher, None si la comparaison est impossible.

    On compare chaque domaine RCCM au texte de l'objet : d'abord en
    sous-chaîne (« materiel informatique »), sinon par mots communs.
    """
    if not objet or not profil.rccm_domaines:
        return None
    cible = fold(objet).lower()
    mots_objet = _mots(objet)
    for domaine in profil.rccm_domaines:
        if fold(domaine).lower() in cible or (_mots(domaine) & mots_objet):
            return True
    return False


def _controle_rccm(profil: Profil, freq: dict) -> Controle:
    if profil.rccm_numero:
        return _ctrl("rccm_absent", CONFORME,
                     f"RCCM {profil.rccm_numero} au dossier.", None, freq)
    return _ctrl("rccm_absent", BLOQUANT,
                 "Aucun numéro RCCM dans le profil : c'est la cause de rejet "
                 "la plus mécanique du corpus.", None, freq)


def _controle_domaine(profil: Profil, objet: str | None, freq: dict) -> Controle:
    couvert = _domaine_couvert(profil, objet)
    if couvert is True:
        return _ctrl("rccm_domaine", CONFORME,
                     "L'objet du marché recoupe un domaine déclaré au RCCM.",
                     objet, freq)
    if couvert is False:
        return _ctrl("rccm_domaine", RISQUE,
                     "Aucun domaine RCCM du profil ne recoupe l'objet. À "
                     "confronter au libellé exact du registre avant de "
                     "soumissionner.", objet, freq)
    return _ctrl("rccm_domaine", INDETERMINE,
                 "Objet du marché ou domaines RCCM absents : comparaison "
                 "impossible.", objet, freq)


def _controles_agrement(avis: dict, profil: Profil, texte: str, rep: str,
                        echeance: str, freq: dict) -> list[Controle]:
    if not avis.get("agrement_exige"):
        return [_ctrl("agrement_absent", SANS_OBJET,
                      "Aucun agrément technique exigé dans cet avis.",
                      None, freq)]

    objet = avis.get("objet") or ""
    agrement = profil.agrement_pour(objet)
    sortie: list[Controle] = []

    if agrement is None:
        sortie.append(_ctrl(
            "agrement_absent", BLOQUANT,
            "L'avis exige un agrément technique ; le profil n'en déclare "
            "aucun pour ce domaine.", None, freq))
    elif agrement.expire_le and agrement.expire_le < echeance:
        sortie.append(_ctrl(
            "agrement_absent", BLOQUANT,
            f"Agrément « {agrement.domaine} » expiré le {agrement.expire_le}, "
            f"avant la date limite {echeance}.", None, freq))
    else:
        sortie.append(_ctrl(
            "agrement_absent", CONFORME,
            f"Agrément « {agrement.domaine} » valide à la date limite "
            f"{echeance}.", None, freq))

    m = RE_CATEGORIE.search(rep)
    if m and agrement is not None:
        cite = texte[m.start():m.end()] if texte else None
        exigee = re.sub(r"\s+", "", m.group("cat")).upper()
        detenue = re.sub(r"\s+", "", agrement.categorie or "").upper()
        if not detenue:
            sortie.append(_ctrl(
                "agrement_categorie", INDETERMINE,
                f"L'avis exige la catégorie {exigee} ; le profil ne précise "
                "pas la catégorie détenue.", cite, freq))
        elif detenue == exigee:
            sortie.append(_ctrl(
                "agrement_categorie", CONFORME,
                f"Catégorie {detenue} détenue, {exigee} exigée.", cite, freq))
        else:
            # Volontairement « risque » et non « bloquant » : l'ordre des
            # catégories d'agrément n'est pas codé ici, donc pas tranché ici.
            sortie.append(_ctrl(
                "agrement_categorie", RISQUE,
                f"Catégorie {detenue} détenue, {exigee} exigée. À confronter "
                "au barème d'équivalence.", cite, freq))
    return sortie


def _controles_attestations(profil: Profil, echeance: str,
                            freq: dict) -> list[Controle]:
    """Deux contrôles distincts, parce qu'ils ne pèsent pas pareil.

    L'attestation fiscale est nommée par le corpus : son absence a
    réellement écarté des offres, donc elle bloque. Les autres pièces
    déclarées au profil ne sont que signalées : rien ne prouve que CET
    avis les exige.
    """
    def etat_de(piece: str) -> str:
        """'valide', 'perimee', 'non_detenue' ou 'non_renseignee'."""
        valide = profil.attestation_valide(piece, echeance)
        if valide is None:
            return "non_renseignee"
        if valide:
            return "valide"
        return "perimee" if profil.attestations.get(piece) else "non_detenue"

    fiscale = etat_de(PIECE_NOMMEE)
    if fiscale == "valide":
        ctrl_fiscal = _ctrl(
            "fiscal", CONFORME,
            f"Attestation de situation fiscale valide jusqu'au "
            f"{profil.attestations[PIECE_NOMMEE]}, au-delà de la date limite "
            f"{echeance}.", None, freq)
    elif fiscale == "perimee":
        ctrl_fiscal = _ctrl(
            "fiscal", BLOQUANT,
            f"Attestation de situation fiscale expirée le "
            f"{profil.attestations[PIECE_NOMMEE]}, avant la date limite "
            f"{echeance}.", None, freq)
    elif fiscale == "non_detenue":
        ctrl_fiscal = _ctrl(
            "fiscal", BLOQUANT,
            "Attestation de situation fiscale non détenue.", None, freq)
    else:
        ctrl_fiscal = _ctrl(
            "fiscal", INDETERMINE,
            "Le profil ne renseigne pas l'attestation de situation fiscale.",
            None, freq)

    autres = [p for p in profil.attestations if p != PIECE_NOMMEE]
    if not autres:
        return [ctrl_fiscal]

    problemes = [p for p in autres if etat_de(p) in ("perimee", "non_detenue")]
    if problemes:
        ctrl_pieces = _ctrl(
            "piece_admin", RISQUE,
            "Pièce(s) périmée(s) ou non détenue(s) au " + echeance + " : "
            + ", ".join(problemes) + ". Vérifier si le dossier de "
            "consultation les exige.", None, freq)
    else:
        ctrl_pieces = _ctrl(
            "piece_admin", CONFORME,
            "Autres pièces déclarées à jour au " + echeance + " : "
            + ", ".join(autres) + ".", None, freq)
    return [ctrl_fiscal, ctrl_pieces]


def _controle_ca(profil: Profil, texte: str, rep: str, freq: dict) -> Controle:
    m = RE_CA_MINIMUM.search(rep)
    exige = normalize_amount(m.group("v")) if m else None
    cite = texte[m.start():m.end()].strip() if (m and texte) else None
    ca = profil.ca_moyen()

    if exige is None:
        constat = "Aucun chiffre d'affaires minimum lu dans l'avis."
        if ca is not None:
            constat += f" CA moyen déclaré : {_fcfa(ca)}."
        return _ctrl("ca_insuffisant", SANS_OBJET, constat, None, freq)
    if ca is None:
        return _ctrl("ca_insuffisant", INDETERMINE,
                     f"L'avis exige {_fcfa(exige)} ; le profil ne déclare "
                     "aucun chiffre d'affaires.", cite, freq)
    if ca < exige:
        return _ctrl("ca_insuffisant", BLOQUANT,
                     f"CA moyen {_fcfa(ca)} contre {_fcfa(exige)} exigés.",
                     cite, freq)
    return _ctrl("ca_insuffisant", CONFORME,
                 f"CA moyen {_fcfa(ca)} pour {_fcfa(exige)} exigés.",
                 cite, freq)


def _controle_references(profil: Profil, rep: str, freq: dict) -> Controle:
    if not RE_REFERENCES_EXIGEES.search(rep):
        return _ctrl("references", SANS_OBJET,
                     "Aucune référence similaire exigée dans le texte lu.",
                     None, freq)
    if profil.references:
        return _ctrl("references", CONFORME,
                     f"{len(profil.references)} référence(s) au profil. "
                     "Vérifier la similarité d'objet et les attestations de "
                     "bonne fin.", None, freq)
    return _ctrl("references", BLOQUANT,
                 "L'avis demande des références similaires ; le profil n'en "
                 "déclare aucune.", None, freq)


def evaluer(avis: dict, profil: Profil, texte_avis: str = "",
            frequences: dict[str, int] | None = None) -> dict:
    """Confronte un avis au profil. Retourne un verdict motivé.

    `avis`       : ligne de la table `avis`, ou dict équivalent.
    `texte_avis` : texte source du bloc, pour lire et citer les exigences
                   chiffrées. Vide, les contrôles textuels passent en
                   `sans_objet` plutôt que de conclure à tort — et
                   l'avertissement le signale.
    """
    freq = frequences or {}
    rep = fold(texte_avis or "")
    echeance = avis.get("date_limite") or date.today().isoformat()
    objet = avis.get("objet")

    controles = [
        _controle_rccm(profil, freq),
        _controle_domaine(profil, objet, freq),
        *_controles_agrement(avis, profil, texte_avis, rep, echeance, freq),
        *_controles_attestations(profil, echeance, freq),
        _controle_ca(profil, texte_avis, rep, freq),
        _controle_references(profil, rep, freq),
    ]

    # Rappels : causes réelles qu'un profil ne peut pas trancher (lettre non
    # signée, garantie, spécifications techniques). Elles ne pèsent pas dans
    # le verdict, mais elles ont écarté des concurrents : c'est la check-list
    # de dernière minute, ordonnée par dégâts constatés.
    evaluees = {c.categorie for c in controles}
    rappels = sorted(
        (
            {"categorie": cat, "libelle": lib, "rejets_observes": freq.get(cat, 0)}
            for cat, lib, _, verifiable in CATALOGUE
            if verifiable and cat not in evaluees and freq.get(cat, 0) > 0
        ),
        key=lambda r: -r["rejets_observes"],
    )

    controles.sort(key=lambda c: (_GRAVITE[c.statut], -c.rejets_observes))
    statuts = {c.statut for c in controles}
    verdict = (BLOQUANT if BLOQUANT in statuts
               else RISQUE if RISQUE in statuts
               else "favorable")

    tranchables = [c for c in controles if c.statut != SANS_OBJET]
    decides = [c for c in tranchables if c.statut != INDETERMINE]
    couverture = round(len(decides) / len(tranchables), 3) if tranchables else 0.0

    return {
        "reference": avis.get("reference"),
        "objet": objet,
        "organisation": avis.get("organisation"),
        "date_limite": avis.get("date_limite"),
        "profil": profil.nom,
        "verdict": verdict,
        # Part des contrôles réellement tranchés. Mesurée, pas déclarée : un
        # verdict « favorable » à 40 % de couverture ne vaut rien, et le
        # lecteur doit pouvoir le voir.
        "couverture": couverture,
        "bloquants": [c.libelle for c in controles if c.statut == BLOQUANT],
        "controles": [c.as_dict() for c in controles],
        "rappels": rappels,
        "avertissement": (
            None if texte_avis else
            "Texte source de l'avis non fourni : les exigences chiffrées "
            "(catégorie d'agrément, chiffre d'affaires, références) n'ont "
            "pas pu être lues."
        ),
    }
