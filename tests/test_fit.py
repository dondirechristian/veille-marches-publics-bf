"""check_fit : ce qui doit bloquer, et ce qui ne doit surtout pas conclure.

Le piège de ce module serait de rendre un verdict rassurant sur un profil
vide. Ces tests vérifient l'inverse : tant qu'une information manque, le
contrôle reste `indetermine` et la couverture baisse.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mpbf import fit  # noqa: E402
from mpbf.profil import Profil  # noqa: E402

PROFIL_COMPLET = Profil.depuis_dict({
    "nom": "SOTECHDI",
    "rccm": {"numero": "BF-OUA-2019-B-00000",
             "domaines": ["materiel informatique", "fournitures de bureau"]},
    "agrements": [{"domaine": "materiel informatique", "categorie": "B2",
                   "expire_le": "2027-12-31"}],
    "attestations": {"fiscale": "2026-12-31", "cnss": "2026-12-31"},
    "chiffre_affaires": {"2023": 120_000_000, "2024": 185_000_000},
    "references": [{"objet": "Acquisition de materiel informatique"}],
})

AVIS_INFORMATIQUE = {
    "reference": "2026-004/TEST",
    "objet": "Acquisition de materiel informatique au profit de la DPESFPT",
    "organisation": "REGION DE OUBRI",
    "date_limite": "2026-03-15",
    "agrement_exige": 1,
}

TEXTE_AVIS = (
    "Acquisition de materiel informatique. Agrement technique de categorie "
    "B2 exige. Le soumissionnaire doit justifier d'un chiffre d'affaires "
    "moyen annuel minimum de 90 000 000 FCFA sur les trois derniers "
    "exercices, et fournir des references similaires."
)


def _statuts(resultat):
    return {c["categorie"]: c["statut"] for c in resultat["controles"]}


def test_profil_adapte_donne_un_verdict_favorable():
    r = fit.evaluer(AVIS_INFORMATIQUE, PROFIL_COMPLET, TEXTE_AVIS)
    assert r["verdict"] == "favorable"
    assert r["bloquants"] == []


def test_couverture_totale_quand_rien_ne_manque():
    r = fit.evaluer(AVIS_INFORMATIQUE, PROFIL_COMPLET, TEXTE_AVIS)
    assert r["couverture"] == 1.0


def test_chiffre_affaires_insuffisant_bloque():
    maigre = Profil.depuis_dict({
        "nom": "Petite structure",
        "rccm": {"numero": "X", "domaines": ["materiel informatique"]},
        "agrements": [{"domaine": "materiel informatique", "categorie": "B2"}],
        "attestations": {"fiscale": "2026-12-31"},
        "chiffre_affaires": {"2024": 10_000_000},
        "references": [{"objet": "x"}],
    })
    r = fit.evaluer(AVIS_INFORMATIQUE, maigre, TEXTE_AVIS)
    assert r["verdict"] == "bloquant"
    assert _statuts(r)["ca_insuffisant"] == "bloquant"


def test_attestation_perimee_avant_la_date_limite_bloque():
    perime = Profil.depuis_dict({
        "nom": "Retardataire",
        "rccm": {"numero": "X", "domaines": ["materiel informatique"]},
        "agrements": [{"domaine": "materiel informatique", "categorie": "B2"}],
        # Valide aujourd'hui, périmée le jour du dépôt : c'est exactement
        # le cas que le candidat ne voit pas venir.
        "attestations": {"fiscale": "2026-01-31"},
        "chiffre_affaires": {"2024": 185_000_000},
        "references": [{"objet": "x"}],
    })
    r = fit.evaluer(AVIS_INFORMATIQUE, perime, TEXTE_AVIS)
    assert _statuts(r)["fiscal"] == "bloquant"


def test_agrement_expire_bloque():
    expire = Profil.depuis_dict({
        "nom": "Agrement perime",
        "rccm": {"numero": "X", "domaines": ["materiel informatique"]},
        "agrements": [{"domaine": "materiel informatique", "categorie": "B2",
                       "expire_le": "2026-01-01"}],
        "attestations": {"fiscale": "2026-12-31"},
        "chiffre_affaires": {"2024": 185_000_000},
        "references": [{"objet": "x"}],
    })
    r = fit.evaluer(AVIS_INFORMATIQUE, expire, TEXTE_AVIS)
    assert _statuts(r)["agrement_absent"] == "bloquant"


def test_domaine_hors_rccm_est_un_risque_pas_un_blocage():
    """L'extraction de l'objet est imparfaite : elle ne doit pas trancher.

    Un faux négatif ici ferait rater un marché réel.
    """
    avis = dict(AVIS_INFORMATIQUE,
                objet="Travaux de construction d'un batiment administratif",
                agrement_exige=0)
    r = fit.evaluer(avis, PROFIL_COMPLET, "Travaux de construction.")
    assert _statuts(r)["rccm_domaine"] == "risque"
    assert r["verdict"] == "risque"


def test_profil_vide_ne_conclut_pas_favorable():
    vide = Profil.depuis_dict({"nom": "Inconnue"})
    r = fit.evaluer(AVIS_INFORMATIQUE, vide, TEXTE_AVIS)
    assert r["verdict"] == "bloquant"
    assert _statuts(r)["rccm_domaine"] == "indetermine"
    assert r["couverture"] < 1.0


def test_sans_texte_source_les_exigences_chiffrees_ne_sont_pas_inventees():
    r = fit.evaluer(AVIS_INFORMATIQUE, PROFIL_COMPLET, "")
    assert r["avertissement"] is not None
    # Aucun CA exigé n'a pu être lu : le contrôle est sans objet, il ne
    # bascule pas en « conforme » par défaut.
    assert _statuts(r)["ca_insuffisant"] == "sans_objet"


def test_categorie_agrement_differente_reste_un_risque():
    """L'ordre des catégories n'est pas codé : on signale, on ne tranche pas."""
    autre = Profil.depuis_dict({
        "nom": "Categorie B1",
        "rccm": {"numero": "X", "domaines": ["materiel informatique"]},
        "agrements": [{"domaine": "materiel informatique", "categorie": "B1"}],
        "attestations": {"fiscale": "2026-12-31"},
        "chiffre_affaires": {"2024": 185_000_000},
        "references": [{"objet": "x"}],
    })
    r = fit.evaluer(AVIS_INFORMATIQUE, autre, TEXTE_AVIS)
    assert _statuts(r)["agrement_categorie"] == "risque"


def test_rejets_observes_remontent_du_corpus():
    r = fit.evaluer(AVIS_INFORMATIQUE, PROFIL_COMPLET, TEXTE_AVIS,
                    frequences={"rccm_absent": 42})
    rccm = next(c for c in r["controles"] if c["categorie"] == "rccm_absent")
    assert rccm["rejets_observes"] == 42


def test_domaine_reconnu_malgre_le_pluriel_et_les_composes():
    """Cas réel : « materiel informatique » au RCCM contre « matériels
    péri-informatiques » dans l'objet. Sans repli au singulier, le candidat
    était signalé hors de son propre domaine."""
    avis = dict(AVIS_INFORMATIQUE, agrement_exige=0,
                objet="acquisition de matériels péri-informatiques au "
                      "profit du CHUR de Ouahigouya")
    r = fit.evaluer(avis, PROFIL_COMPLET, "")
    assert _statuts(r)["rccm_domaine"] == "conforme"
