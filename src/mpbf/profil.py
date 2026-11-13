"""Profil de l'entreprise candidate.

check_fit compare ce profil aux exigences lues dans l'avis. Le profil vit
dans un JSON versionné : il se relit à l'œil, se corrige à la main, et son
historique git dit quand un agrément a expiré.

Règle héritée du reste du projet : une information absente du profil reste
absente. Un champ `null` ne vaut pas zéro — il rend le contrôle
*indéterminé*, et check_fit le dit au lieu de conclure.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CHEMIN_DEFAUT = Path(os.environ.get("MPBF_PROFIL", "profil.json"))


@dataclass
class Agrement:
    domaine: str
    categorie: str | None = None
    expire_le: str | None = None  # AAAA-MM-JJ


@dataclass
class Profil:
    nom: str
    rccm_numero: str | None = None
    rccm_domaines: list[str] = field(default_factory=list)
    agrements: list[Agrement] = field(default_factory=list)
    # piece -> date de validité (AAAA-MM-JJ) ; None = non détenue
    attestations: dict[str, str | None] = field(default_factory=dict)
    # année -> montant FCFA
    chiffre_affaires: dict[str, int] = field(default_factory=dict)
    references: list[dict] = field(default_factory=list)

    @classmethod
    def depuis_dict(cls, d: dict) -> "Profil":
        rccm = d.get("rccm") or {}
        return cls(
            nom=d.get("nom") or "entreprise sans nom",
            rccm_numero=rccm.get("numero"),
            rccm_domaines=[m.lower() for m in (rccm.get("domaines") or [])],
            agrements=[
                Agrement(
                    domaine=(a.get("domaine") or "").lower(),
                    categorie=a.get("categorie"),
                    expire_le=a.get("expire_le"),
                )
                for a in (d.get("agrements") or [])
            ],
            attestations={k.lower(): v for k, v in (d.get("attestations") or {}).items()},
            chiffre_affaires={str(k): v for k, v in (d.get("chiffre_affaires") or {}).items()},
            references=list(d.get("references") or []),
        )

    def ca_moyen(self, annees: int = 3) -> int | None:
        """Moyenne des `annees` exercices les plus récents, ou None si vide.

        Les avis exigent en général un « chiffre d'affaires moyen annuel »
        sur les trois derniers exercices.
        """
        valeurs = [v for _, v in sorted(self.chiffre_affaires.items(), reverse=True)
                   if isinstance(v, int)][:annees]
        return sum(valeurs) // len(valeurs) if valeurs else None

    def agrement_pour(self, domaine_texte: str) -> Agrement | None:
        """Premier agrément dont le domaine apparaît dans le texte donné."""
        cible = domaine_texte.lower()
        for a in self.agrements:
            if a.domaine and a.domaine in cible:
                return a
        return self.agrements[0] if len(self.agrements) == 1 else None

    def attestation_valide(self, piece: str, au: str | None = None) -> bool | None:
        """True/False si on sait, None si le profil ne dit rien.

        `au` : date à laquelle la validité doit tenir (la date limite de
        dépôt), au format AAAA-MM-JJ.
        """
        if piece not in self.attestations:
            return None
        echeance = self.attestations[piece]
        if echeance is None:
            return False  # pièce explicitement non détenue
        reference = au or date.today().isoformat()
        return echeance >= reference


def charger(chemin: str | Path | None = None) -> Profil | None:
    """Charge le profil, ou None s'il n'existe pas.

    L'absence de profil n'est pas une erreur : la recherche d'avis marche
    sans. Seul check_fit en a besoin, et il le dit clairement.
    """
    chemin = Path(chemin) if chemin else CHEMIN_DEFAUT
    if not chemin.exists():
        return None
    return Profil.depuis_dict(json.loads(chemin.read_text(encoding="utf-8")))
