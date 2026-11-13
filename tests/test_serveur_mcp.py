"""Le serveur MCP, appelé comme un agent l'appellerait.

On ne teste pas les fonctions Python sous-jacentes — test_pipeline et
test_fit s'en chargent — mais le contrat exposé : noms d'outils,
annotations de lecture seule, et forme des réponses. C'est ce contrat
qu'un client MCP consomme, et c'est lui qui casse en silence.

`asyncio.run` plutôt que pytest-asyncio : une dépendance de test en moins
pour trois appels.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_4320 import PAGES  # noqa: E402
from mpbf import db  # noqa: E402
from mpbf.motifs import extraire_motifs  # noqa: E402
from mpbf.parse import parser  # noqa: E402
from mpbf.segment import SECTION_AVIS, segmenter  # noqa: E402
from mpbf.serveur_mcp import construire  # noqa: E402

REFERENCE = "2025-025/MESRI/SG/ENS/DG/SG/PRCP"

PROFIL = {
    "nom": "SOTECHDI",
    "rccm": {"numero": "BF-OUA-2019-B-00000",
             "domaines": ["materiel informatique", "hebergement"]},
    "agrements": [{"domaine": "materiel informatique", "categorie": "B2",
                   "expire_le": "2027-12-31"}],
    "attestations": {"fiscale": "2026-12-31", "cnss": "2026-12-31"},
    "chiffre_affaires": {"2024": 185000000},
    "references": [{"objet": "Acquisition de materiel informatique"}],
}


@pytest.fixture
def serveur(tmp_path):
    """Serveur branché sur une base construite depuis la fixture 4320."""
    base = tmp_path / "marches.db"
    conn = db.connecter(base)
    conn.execute(
        "INSERT INTO quotidien (numero, date_parution, url, sha256, nb_pages) "
        "VALUES (?,?,?,?,?)",
        ("4320", "Jeudi 22 janvier 2026", "fixture", "x", len(PAGES)),
    )
    for bloc in segmenter(PAGES):
        avis = parser(bloc, "fixture.pdf")
        db.enregistrer_avis(conn, avis.as_dict(), "4320", bloc.texte)
        if bloc.section != SECTION_AVIS:
            db.enregistrer_motifs(
                conn, extraire_motifs(bloc.texte, bloc.reference, bloc.page_debut),
                "4320")
    conn.commit()
    conn.close()

    chemin_profil = tmp_path / "profil.json"
    chemin_profil.write_text(json.dumps(PROFIL), encoding="utf-8")
    return construire(base=str(base), profil=str(chemin_profil))


def appeler(serveur, outil, arguments=None):
    res = asyncio.run(serveur.call_tool(outil, arguments or {}))
    assert not res.is_error, res.content
    return res.structured_content


def test_outils_exposes(serveur):
    outils = {o.name for o in asyncio.run(serveur.list_tools())}
    assert outils == {
        "rechercher_avis", "detail_avis", "verifier_eligibilite",
        "motifs_de_rejet", "catalogue_motifs", "profil_entreprise",
        "etat_base", "lister_quotidiens", "ingerer_quotidien",
    }


def test_seule_l_ingestion_ecrit(serveur):
    """Un client qui refuse les outils d'écriture doit garder la veille."""
    ecriture = {o.name for o in asyncio.run(serveur.list_tools())
                if not (o.annotations and o.annotations.read_only_hint)}
    assert ecriture == {"ingerer_quotidien"}


def test_rechercher_avis_trouve_l_avis_de_la_fixture(serveur):
    res = appeler(serveur, "rechercher_avis", {"ouverts_seulement": False})
    assert res["nombre"] == 1
    assert res["avis"][0]["reference"] == REFERENCE


def test_rechercher_avis_filtre_sur_le_texte(serveur):
    assert appeler(serveur, "rechercher_avis",
                   {"texte": "hotel", "ouverts_seulement": False})["nombre"] == 1
    assert appeler(serveur, "rechercher_avis",
                   {"texte": "zzzz", "ouverts_seulement": False})["nombre"] == 0


def test_detail_avis_rend_le_texte_source_et_l_historique(serveur):
    res = appeler(serveur, "detail_avis", {"reference": REFERENCE})
    assert res["trouve"] is True
    assert "Demande de prix" in res["texte"]
    assert res["revisions"][0]["revision"] == 1
    assert res["source"]["date_parution"] == "Jeudi 22 janvier 2026"


def test_detail_avis_inconnu_ne_leve_pas(serveur):
    """Une référence fausse doit guider l'agent, pas casser la conversation."""
    res = appeler(serveur, "detail_avis", {"reference": "inexistante"})
    assert res["trouve"] is False
    assert "rechercher_avis" in res["message"]


def test_verifier_eligibilite_croise_avis_et_profil(serveur):
    res = appeler(serveur, "verifier_eligibilite", {"reference": REFERENCE})
    assert res["verdict"] in ("favorable", "risque", "bloquant")
    assert res["couverture"] > 0
    assert res["profil"] == "SOTECHDI"
    # Le verdict doit être justifié contrôle par contrôle, jamais nu.
    assert all(c["constat"] for c in res["controles"])


def test_motifs_de_rejet_recalcule_le_taux_evitable(serveur):
    res = appeler(serveur, "motifs_de_rejet")
    assert res["total_rejets"] > 0
    assert 0 <= res["taux_evitable"] <= 1
    assert res["rejets_evitables_en_amont"] <= res["total_rejets"]


def test_etat_base_mesure_la_completude(serveur):
    res = appeler(serveur, "etat_base")
    assert res["quotidiens"] == 1
    assert res["dernier_quotidien"]["numero"] == "4320"
    assert set(res["completude_avis"]) >= {"objet", "date_limite"}


def test_ingestion_d_une_source_absente_echoue_proprement(serveur):
    """Pas d'exception nue : l'agent doit pouvoir lire l'échec."""
    res = appeler(serveur, "ingerer_quotidien", {"source": "data/inexistant.pdf"})
    assert res["statut"] == "echec"
    assert "Rien n'a été enregistré" in res["message"]


def test_ressource_profil_exposee(serveur):
    uris = [str(r.uri) for r in asyncio.run(serveur.list_resources())]
    assert "mpbf://profil" in uris
