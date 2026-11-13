"""Serveur MCP : la veille marchés publics exposée à un agent.

Le CLI sert l'opérateur ; ce serveur sert l'agent qui répond à
« ce marché est-il pour nous ». Les deux tapent dans la même base et
appellent le même pipeline.

Trois règles tenues côté serveur, parce qu'un agent est un lecteur naïf :

  - `null` veut dire « absent du source », jamais « zéro ». Les
    instructions du serveur le disent, et chaque outil renvoie sa
    complétude quand elle est mesurable.
  - Tout ce qui est affirmé est traçable : numéro de Quotidien, page,
    extrait cité.
  - Un verdict d'éligibilité arrive toujours avec sa couverture. Un
    « favorable » adossé à trois contrôles indéterminés n'en est pas un.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import db, fit, pipeline, profil as profil_mod, requetes
from .motifs import CATALOGUE
from .segment import SECTION_AVIS

INSTRUCTIONS = """\
Veille des marchés publics du Burkina Faso, à partir des Quotidiens de la
DGCMEF (Direction générale du contrôle des marchés publics et des
engagements financiers).

Le Quotidien comporte deux parties, et les deux sont exploitées ici :
  - RESULTATS PROVISOIRES : qui a gagné, et surtout POURQUOI chaque offre
    écartée l'a été (RCCM absent, agrément manquant, attestation fiscale
    non fournie...). C'est la matière de `motifs_de_rejet` et le socle de
    `verifier_eligibilite`.
  - AVIS : les consultations ouvertes. C'est la matière de
    `rechercher_avis`.

Règles de lecture, à respecter dans toute réponse :
  - Un champ `null` signifie « absent du document source ». Ne le remplace
    jamais par 0, par une estimation ou par une valeur plausible. Dis
    « non précisé dans l'avis ».
  - Les montants sont en FCFA.
  - Cite toujours la référence de l'avis et le numéro de Quotidien : ils
    permettent à l'utilisateur de retrouver la page d'origine.
  - `verifier_eligibilite` renvoie un champ `couverture` : la part des
    contrôles réellement tranchés. Mentionne-la avec le verdict. Un
    verdict favorable à faible couverture doit être présenté comme
    provisoire, pas comme un feu vert.
"""

LECTURE = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ECRITURE = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                           idempotent_hint=True, open_world_hint=True)


def construire(base: str = "data/marches.db",
               profil: str = "profil.json") -> MCPServer:
    """Assemble le serveur. `base` et `profil` sont capturés par les outils.

    Une connexion SQLite est ouverte par appel plutôt que partagée :
    sqlite3 n'aime pas le partage entre threads, et un outil MCP peut être
    appelé depuis n'importe lequel.
    """
    base = os.environ.get("MPBF_BASE", base)
    profil = os.environ.get("MPBF_PROFIL", profil)

    serveur = MCPServer(
        name="mpbf",
        title="Marchés publics — Burkina Faso",
        instructions=INSTRUCTIONS,
        version="0.2.0",
    )

    @contextmanager
    def connexion():
        # `with sqlite3.connect(...)` gère la transaction, pas la fermeture :
        # sans ce finally, chaque appel d'outil fuirait un descripteur.
        conn = db.connecter(base)
        try:
            yield conn
        finally:
            conn.close()

    # ---------------------------------------------------------------- #
    # Recherche d'opportunités
    # ---------------------------------------------------------------- #

    @serveur.tool(
        title="Rechercher des avis",
        description=(
            "Cherche des consultations publiées (section AVIS du Quotidien). "
            "Trie par date limite la plus proche. Les avis dont la date "
            "limite n'a pas pu être extraite sont conservés et remontés avec "
            "date_limite null : ne les écarte pas, signale l'incertitude."
        ),
        annotations=LECTURE,
    )
    def rechercher_avis(
        texte: Annotated[str | None, Field(
            description="Mots recherchés dans l'objet, l'organisation ou la "
                        "référence. Ex : 'informatique', 'nettoyage'.")] = None,
        organisation: Annotated[str | None, Field(
            description="Filtre sur l'acheteur public.")] = None,
        procedure: Annotated[str | None, Field(
            description="Type de procédure : appel_offres, demande_prix, "
                        "demande_cotation, manifestation_interet, "
                        "demande_propositions, appel_offres_international, "
                        "appel_offres_anticipation, demande_prix_commande.")] = None,
        ouverts_seulement: Annotated[bool, Field(
            description="Écarte les avis dont la date limite est passée.")] = True,
        agrement_exige: Annotated[bool | None, Field(
            description="True pour ne garder que les avis exigeant un "
                        "agrément technique.")] = None,
        montant_min: Annotated[int | None, Field(
            description="Montant prévisionnel minimum en FCFA. Écarte les "
                        "avis sans montant publié.")] = None,
        limite: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        with connexion() as conn:
            resultats = requetes.rechercher_avis(
                conn, texte=texte, organisation=organisation,
                procedure=procedure, section=SECTION_AVIS,
                ouverts_seulement=ouverts_seulement,
                agrement_exige=agrement_exige, montant_min=montant_min,
                limite=limite)
        sans_date = sum(1 for a in resultats if not a["date_limite"])
        return {
            "nombre": len(resultats),
            "avis": resultats,
            "sans_date_limite": sans_date,
            "note": (f"{sans_date} avis sans date limite extraite : à "
                     "vérifier sur le PDF source avant de conclure."
                     if sans_date else None),
        }

    @serveur.tool(
        title="Détail d'un avis",
        description=(
            "Fiche complète d'un avis : champs extraits, texte source, "
            "historique des révisions (report de date, montant rectifié), "
            "motifs de rejet déjà observés sur cette référence, et le "
            "Quotidien d'origine."
        ),
        annotations=LECTURE,
    )
    def detail_avis(
        reference: Annotated[str, Field(
            description="Référence de l'avis, ex : "
                        "'2025-025/MESRI/SG/ENS/DG/SG/PRCP'.")],
    ) -> dict[str, Any]:
        with connexion() as conn:
            avis = requetes.avis_par_reference(conn, reference)
        if avis is None:
            return {"trouve": False, "reference": reference,
                    "message": "Aucun avis sous cette référence. Utilise "
                               "rechercher_avis pour retrouver la bonne."}
        avis["trouve"] = True
        return avis

    # ---------------------------------------------------------------- #
    # Qualification — le cœur du produit
    # ---------------------------------------------------------------- #

    @serveur.tool(
        title="Vérifier l'éligibilité",
        description=(
            "Confronte un avis au profil de l'entreprise et dit ce qui "
            "bloque AVANT de monter un dossier. Chaque contrôle porte son "
            "statut (bloquant / risque / conforme / indetermine / "
            "sans_objet), sa justification, l'exigence citée depuis l'avis "
            "quand elle y figure, et le nombre de fois où cette cause a "
            "réellement écarté une offre dans le corpus. Renvoie aussi "
            "`couverture` : la part des contrôles tranchés — à citer avec "
            "le verdict."
        ),
        annotations=LECTURE,
    )
    def verifier_eligibilite(
        reference: Annotated[str, Field(
            description="Référence de l'avis à qualifier.")],
    ) -> dict[str, Any]:
        p = profil_mod.charger(profil)
        if p is None:
            return {
                "erreur": "profil_absent",
                "message": f"Aucun profil entreprise en {profil}. Crée ce "
                           "fichier (RCCM, agréments, attestations, chiffre "
                           "d'affaires, références) : sans lui, aucun "
                           "contrôle d'éligibilité n'est possible.",
            }
        with connexion() as conn:
            avis = requetes.avis_par_reference(conn, reference)
            if avis is None:
                return {"erreur": "avis_introuvable", "reference": reference}
            frequences = fit.frequences_motifs(conn)
        return fit.evaluer(avis, p, avis.get("texte") or "", frequences)

    @serveur.tool(
        title="Motifs de rejet",
        description=(
            "Classement des causes qui ont fait écarter des offres, avec la "
            "part vérifiable avant de soumissionner. Filtrable par acheteur "
            "pour répondre à « qu'est-ce qui fait échouer les dossiers chez "
            "cet acheteur ». Chiffres recalculés sur les données présentes."
        ),
        annotations=LECTURE,
    )
    def motifs_de_rejet(
        categorie: Annotated[str | None, Field(
            description="Catégorie précise, ex : 'rccm_absent', 'fiscal', "
                        "'agrement_absent'. Voir catalogue_motifs.")] = None,
        organisation: Annotated[str | None, Field(
            description="Restreint aux avis d'un acheteur donné.")] = None,
        verifiables_seulement: Annotated[bool, Field(
            description="Ne garde que les causes évitables en amont.")] = False,
        limite: Annotated[int, Field(ge=1, le=50)] = 15,
    ) -> dict[str, Any]:
        with connexion() as conn:
            return requetes.motifs_frequents(
                conn, categorie=categorie, organisation=organisation,
                verifiables_seulement=verifiables_seulement, limite=limite)

    @serveur.tool(
        title="Catalogue des motifs",
        description=(
            "Les causes de rejet reconnues par l'extraction, chacune "
            "marquée vérifiable ou non avant de soumissionner. Sert à "
            "interpréter les sorties de motifs_de_rejet et de "
            "verifier_eligibilite. Ne dépend pas de la base."
        ),
        annotations=LECTURE,
    )
    def catalogue_motifs() -> dict[str, Any]:
        return {
            "motifs": [
                {"categorie": cat, "libelle": lib,
                 "verifiable_en_amont": verifiable}
                for cat, lib, _, verifiable in CATALOGUE
            ],
            "note": "verifiable_en_amont = le candidat pouvait constater la "
                    "cause avant de déposer son offre.",
        }

    # ---------------------------------------------------------------- #
    # Profil et état du système
    # ---------------------------------------------------------------- #

    @serveur.tool(
        title="Profil entreprise",
        description=(
            "Le profil utilisé par verifier_eligibilite : RCCM et domaines "
            "couverts, agréments et leurs échéances, attestations, chiffre "
            "d'affaires, références. À consulter pour expliquer POURQUOI un "
            "contrôle a été tranché dans un sens."
        ),
        annotations=LECTURE,
    )
    def profil_entreprise() -> dict[str, Any]:
        p = profil_mod.charger(profil)
        if p is None:
            return {"present": False, "chemin": profil,
                    "message": "Aucun profil enregistré."}
        return {
            "present": True,
            "chemin": profil,
            "nom": p.nom,
            "rccm": {"numero": p.rccm_numero, "domaines": p.rccm_domaines},
            "agrements": [vars(a) for a in p.agrements],
            "attestations": p.attestations,
            "chiffre_affaires": p.chiffre_affaires,
            "ca_moyen_3_exercices": p.ca_moyen(),
            "nb_references": len(p.references),
        }

    @serveur.tool(
        title="État de la base",
        description=(
            "Volumétrie, dernier Quotidien ingéré, complétude mesurée par "
            "champ, et anomalies d'ingestion. À consulter avant d'affirmer "
            "qu'un marché n'existe pas : la base peut simplement être en "
            "retard."
        ),
        annotations=LECTURE,
    )
    def etat_base() -> dict[str, Any]:
        with connexion() as conn:
            etat = requetes.etat_base(conn)
        if etat["anomalies"]:
            etat["note"] = ("Des ingestions ont échoué ou n'ont produit aucun "
                            "avis. La couverture n'est pas garantie sur la "
                            "période concernée.")
        return etat

    @serveur.tool(
        title="Lister les Quotidiens publiés",
        description=(
            "Les derniers Quotidiens parus sur le site de la DGCMEF, du plus "
            "récent au plus ancien, avec leur URL. À appeler avant "
            "ingerer_quotidien : les noms de fichiers publiés sont "
            "irréguliers et ne se devinent pas à partir du numéro. Indique "
            "aussi lesquels sont déjà en base."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    def lister_quotidiens(
        limite: Annotated[int, Field(ge=1, le=30)] = 10,
    ) -> dict[str, Any]:
        from . import ingest
        try:
            disponibles = ingest.lister_recents(limite)
        except Exception as exc:  # noqa: BLE001
            return {"erreur": "site_injoignable", "detail": str(exc),
                    "message": "Le site DGCMEF n'a pas répondu. Les avis "
                               "déjà en base restent consultables."}
        with connexion() as conn:
            deja = {l["numero"] for l in conn.execute(
                "SELECT numero FROM quotidien")}
        for q in disponibles:
            q["deja_ingere"] = q["numero"].split()[0] in deja
        return {
            "nombre": len(disponibles),
            "quotidiens": disponibles,
            "a_ingerer": [q["url"] for q in disponibles if not q["deja_ingere"]],
        }

    @serveur.tool(
        title="Ingérer un Quotidien",
        description=(
            "Télécharge et dépouille un Quotidien DGCMEF (URL ou chemin "
            "local), puis enregistre avis et motifs. Réexécutable sans "
            "risque : un avis inchangé n'est pas redupliqué, un avis modifié "
            "crée une révision. Un numéro qui ne produit aucun avis remonte "
            "statut='alerte' — c'est un défaut de parsing à signaler, pas un "
            "numéro vide."
        ),
        annotations=ECRITURE,
    )
    def ingerer_quotidien(
        source: Annotated[str, Field(
            description="URL du PDF sur dgcmef.gov.bf, ou chemin d'un PDF "
                        "déjà téléchargé.")],
    ) -> dict[str, Any]:
        with connexion() as conn:
            try:
                return pipeline.ingerer(conn, source)
            except Exception as exc:  # noqa: BLE001
                db.journaliser(conn, "ingestion", "ECHEC", f"{source}: {exc}")
                return {"statut": "echec", "source": source,
                        "erreur": str(exc),
                        "message": "Rien n'a été enregistré. L'échec est "
                                   "tracé dans la table journal."}

    # ---------------------------------------------------------------- #
    # Ressources : contexte que l'agent peut lire sans appeler d'outil
    # ---------------------------------------------------------------- #

    @serveur.resource(
        "mpbf://profil",
        title="Profil entreprise",
        description="Le profil confronté aux avis par verifier_eligibilite.",
        mime_type="application/json",
    )
    def ressource_profil() -> str:
        chemin = Path(profil)
        return (chemin.read_text(encoding="utf-8") if chemin.exists()
                else '{"present": false}')

    return serveur


def main() -> None:
    construire().run("stdio")


if __name__ == "__main__":
    main()
