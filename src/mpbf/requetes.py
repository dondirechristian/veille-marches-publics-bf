"""Lectures de la base, partagées par le CLI et le serveur MCP.

Un seul endroit où s'exprime la règle « dernière révision seulement » :
un avis reporté existe en plusieurs exemplaires, et le compter deux fois
fausserait aussi bien une recherche qu'une mesure de qualité.
"""

from __future__ import annotations

from datetime import date

from .segment import SECTION_AVIS, SECTION_RESULTATS

# Ne garde que la révision la plus récente de chaque (référence, quotidien).
JOINTURE_DERNIERE_REVISION = """
JOIN (SELECT reference, quotidien, MAX(revision) AS r FROM avis
      GROUP BY reference, quotidien) d
  ON a.reference = d.reference AND a.quotidien = d.quotidien
 AND a.revision = d.r
"""

CHAMPS_AVIS = """a.reference, a.quotidien, a.revision, a.procedure, a.section,
    a.organisation, a.objet, a.financement, a.date_limite,
    a.montant_previsionnel, a.agrement_exige, a.nb_lots, a.page_debut,
    a.page_fin, a.vu_le"""


def _lignes(curseur) -> list[dict]:
    return [dict(l) for l in curseur.fetchall()]


def rechercher_avis(conn, texte: str | None = None, organisation: str | None = None,
                    procedure: str | None = None, section: str = SECTION_AVIS,
                    ouverts_seulement: bool = True, agrement_exige: bool | None = None,
                    montant_min: int | None = None, limite: int = 20) -> list[dict]:
    """Avis correspondant aux filtres, échéance la plus proche d'abord.

    `ouverts_seulement` écarte les dates limites passées. Un avis dont la
    date limite n'a pas pu être extraite est **conservé** : le taire
    reviendrait à faire manquer une opportunité à cause d'un défaut de
    parsing de notre côté.
    """
    clauses, params = ["a.section = ?"], [section]

    if texte:
        clauses.append("(a.objet LIKE ? OR a.organisation LIKE ? "
                       "OR a.reference LIKE ?)")
        params += [f"%{texte}%"] * 3
    if organisation:
        clauses.append("a.organisation LIKE ?")
        params.append(f"%{organisation}%")
    if procedure:
        clauses.append("a.procedure = ?")
        params.append(procedure)
    if agrement_exige is not None:
        clauses.append("a.agrement_exige = ?")
        params.append(int(agrement_exige))
    if montant_min is not None:
        clauses.append("a.montant_previsionnel >= ?")
        params.append(montant_min)
    if ouverts_seulement:
        clauses.append("(a.date_limite IS NULL OR a.date_limite >= ?)")
        params.append(date.today().isoformat())

    params.append(limite)
    return _lignes(conn.execute(
        f"SELECT {CHAMPS_AVIS} FROM avis a {JOINTURE_DERNIERE_REVISION} "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY a.date_limite IS NULL, a.date_limite ASC LIMIT ?",
        params,
    ))


def avis_par_reference(conn, reference: str) -> dict | None:
    """Dernière révision d'un avis, avec son texte source et son historique."""
    ligne = conn.execute(
        f"SELECT {CHAMPS_AVIS}, a.texte FROM avis a "
        "WHERE a.reference = ? ORDER BY a.revision DESC, a.vu_le DESC LIMIT 1",
        (reference,),
    ).fetchone()
    if ligne is None:
        return None

    avis = dict(ligne)
    avis["revisions"] = _lignes(conn.execute(
        "SELECT revision, date_limite, montant_previsionnel, vu_le FROM avis "
        "WHERE reference = ? ORDER BY revision",
        (reference,),
    ))
    avis["motifs_lies"] = _lignes(conn.execute(
        "SELECT categorie, libelle, verifiable_en_amont, extrait, page "
        "FROM motif_rejet WHERE reference_avis = ?",
        (reference,),
    ))
    source = conn.execute(
        "SELECT url, date_parution FROM quotidien WHERE numero = ?",
        (avis["quotidien"],),
    ).fetchone()
    avis["source"] = dict(source) if source else None
    return avis


def motifs_frequents(conn, categorie: str | None = None,
                     organisation: str | None = None,
                     verifiables_seulement: bool = False,
                     limite: int = 15) -> dict:
    """Classement des causes de rejet, avec le taux évitable en amont.

    C'est le chiffre qui fonde le produit : il se recalcule sur les données
    présentes, il ne se recopie pas d'un argumentaire.
    """
    clauses, params = ["1=1"], []
    if categorie:
        clauses.append("m.categorie = ?")
        params.append(categorie)
    if verifiables_seulement:
        clauses.append("m.verifiable_en_amont = 1")
    if organisation:
        # On remonte à l'avis pour filtrer sur l'acheteur : « qu'est-ce qui
        # fait échouer les offres chez CET acheteur » est la question utile.
        clauses.append(
            "m.reference_avis IN (SELECT reference FROM avis "
            "WHERE organisation LIKE ?)")
        params.append(f"%{organisation}%")

    ou = " AND ".join(clauses)
    classement = _lignes(conn.execute(
        f"SELECT m.categorie, m.libelle, m.verifiable_en_amont, COUNT(*) n "
        f"FROM motif_rejet m WHERE {ou} "
        "GROUP BY m.categorie ORDER BY n DESC LIMIT ?",
        params + [limite],
    ))
    total = conn.execute(
        f"SELECT COUNT(*) c FROM motif_rejet m WHERE {ou}", params
    ).fetchone()["c"]
    evitables = conn.execute(
        f"SELECT COUNT(*) c FROM motif_rejet m WHERE {ou} "
        "AND m.verifiable_en_amont = 1", params
    ).fetchone()["c"]

    return {
        "total_rejets": total,
        "rejets_evitables_en_amont": evitables,
        "taux_evitable": round(evitables / total, 3) if total else None,
        "classement": classement,
    }


def etat_base(conn) -> dict:
    """Volumétrie, complétude mesurée et anomalies d'ingestion.

    Sert à répondre honnêtement à « la base est-elle à jour ». Un agrégateur
    qui rate trois jours de publication en silence est pire qu'inutile.
    """
    un = lambda sql, p=(): conn.execute(sql, p).fetchone()  # noqa: E731

    champs = ["organisation", "objet", "financement", "date_limite",
              "montant_previsionnel"]
    total_avis = un("SELECT COUNT(*) c FROM avis WHERE section = ?",
                    (SECTION_AVIS,))["c"]
    completude = {}
    for champ in champs:
        n = un(f"SELECT COUNT(*) c FROM avis WHERE section = ? "
               f"AND {champ} IS NOT NULL AND {champ} != ''",
               (SECTION_AVIS,))["c"]
        completude[champ] = round(n / total_avis, 3) if total_avis else None

    return {
        "quotidiens": un("SELECT COUNT(*) c FROM quotidien")["c"],
        "dernier_quotidien": dict(un(
            "SELECT numero, date_parution, ingere_le FROM quotidien "
            "ORDER BY ingere_le DESC LIMIT 1") or {}) or None,
        "avis_ouverts": total_avis,
        "blocs_resultats": un("SELECT COUNT(*) c FROM avis WHERE section = ?",
                              (SECTION_RESULTATS,))["c"],
        "motifs_rejet": un("SELECT COUNT(*) c FROM motif_rejet")["c"],
        "completude_avis": completude,
        "anomalies": _lignes(conn.execute(
            "SELECT horodate, etape, statut, detail FROM journal "
            "WHERE statut != 'OK' ORDER BY horodate DESC LIMIT 10")),
    }
