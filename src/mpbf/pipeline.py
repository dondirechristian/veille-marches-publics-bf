"""Ingestion complète d'un Quotidien : PDF -> avis + motifs en base.

Le CLI et le serveur MCP passent tous les deux par ici. Deux chemins de
code distincts pour la même opération auraient divergé, et c'est
l'ingestion — la seule étape irréversible — qu'on aurait laissée diverger.
"""

from __future__ import annotations

from pathlib import Path

from . import db, ingest
from .motifs import extraire_motifs
from .parse import completude, parser
from .segment import SECTION_AVIS, segmenter


def ingerer(conn, source: str) -> dict:
    """Ingère un Quotidien depuis une URL DGCMEF ou un fichier local.

    Retourne un compte rendu. `statut` vaut `alerte` quand le PDF n'a
    produit aucun avis : c'est une anomalie de parsing, pas un résultat, et
    l'appelant doit pouvoir la distinguer d'un succès.

    Lève `FileNotFoundError` si la source locale n'existe pas, et laisse
    remonter les erreurs réseau : un échec silencieux vaut moins que rien.
    """
    chemin = (ingest.telecharger(source) if source.startswith("http")
              else Path(source))
    if not chemin.exists():
        raise FileNotFoundError(f"Introuvable : {chemin}")

    pages = ingest.pages_texte(chemin)
    numero, date_par = ingest.metadonnees(pages)
    numero = numero or chemin.stem

    conn.execute(
        "INSERT OR REPLACE INTO quotidien (numero, date_parution, url, "
        "sha256, nb_pages) VALUES (?,?,?,?,?)",
        (numero, date_par, source, ingest.sha256(chemin), len(pages)),
    )
    conn.commit()

    blocs = segmenter(pages)
    if not blocs:
        message = f"{numero} : 0 avis détecté sur {len(pages)} pages"
        db.journaliser(conn, "ingestion", "ALERTE", message)
        return {
            "statut": "alerte",
            "quotidien": numero,
            "date_parution": date_par,
            "pages": len(pages),
            "avis_detectes": 0,
            "message": message + " — segmentation à reprendre avant de "
                                 "conclure que ce numéro est vide.",
        }

    avis_parses, compteurs, motifs_total = [], {"nouveau": 0, "revision": 0,
                                                "inchange": 0}, 0
    for bloc in blocs:
        avis = parser(bloc, chemin.name)
        avis_parses.append(avis)
        etat = db.enregistrer_avis(conn, avis.as_dict(), numero, bloc.texte)
        compteurs[etat] += 1
        if bloc.section != SECTION_AVIS:
            motifs = extraire_motifs(bloc.texte, bloc.reference, bloc.page_debut)
            motifs_total += db.enregistrer_motifs(conn, motifs, numero)

    detail = (f"{numero} : {len(blocs)} avis, {compteurs['nouveau']} nouveaux, "
              f"{compteurs['revision']} révisions, {motifs_total} motifs")
    db.journaliser(conn, "ingestion", "OK", detail)

    return {
        "statut": "ok",
        "quotidien": numero,
        "date_parution": date_par,
        "pages": len(pages),
        "avis_detectes": len(blocs),
        "nouveaux": compteurs["nouveau"],
        "revisions": compteurs["revision"],
        "inchanges": compteurs["inchange"],
        "motifs_rejet": motifs_total,
        # Mesurée sur ce numéro, pas déclarée : c'est ce qui permet de voir
        # tout de suite qu'une mise en page a cassé une extraction.
        "completude": completude(avis_parses),
        "source": str(chemin),
    }
