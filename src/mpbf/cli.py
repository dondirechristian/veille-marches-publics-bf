"""Ligne de commande.

    python -m mpbf ingerer <url_ou_chemin.pdf>
    python -m mpbf stats
    python -m mpbf annoter --sortie data/annotation.csv
    python -m mpbf mcp            # serveur MCP sur stdio
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import db, pipeline
from .segment import SECTION_AVIS


def cmd_ingerer(args) -> int:
    conn = db.connecter(args.base)
    try:
        res = pipeline.ingerer(conn, args.source)
    except Exception as exc:  # noqa: BLE001
        db.journaliser(conn, "ingestion", "ECHEC", f"{args.source}: {exc}")
        print(f"Échec : {exc}", file=sys.stderr)
        return 1

    if res["statut"] == "alerte":
        # Zéro avis détecté n'est pas un succès : code de sortie non nul,
        # pour qu'un cron le remonte au lieu de l'avaler.
        print(f"ALERTE : {res['message']}", file=sys.stderr)
        return 2

    print(f"Quotidien {res['quotidien']} "
          f"({res['date_parution'] or 'date inconnue'}) — {res['pages']} pages")
    print(f"  {res['avis_detectes']} avis : {res['nouveaux']} nouveaux, "
          f"{res['revisions']} révisions, {res['inchanges']} inchangés")
    print(f"  {res['motifs_rejet']} motifs de rejet")
    print("\n  Complétude des champs :")
    for champ, taux in res["completude"].items():
        print(f"    {champ:22} {taux:.0%}")
    return 0


def cmd_stats(args) -> int:
    conn = db.connecter(args.base)
    n_avis = conn.execute("SELECT COUNT(*) c FROM avis").fetchone()["c"]
    n_quot = conn.execute("SELECT COUNT(*) c FROM quotidien").fetchone()["c"]
    print(f"{n_quot} quotidiens, {n_avis} avis\n")

    print("Motifs de rejet les plus fréquents :")
    lignes = conn.execute(
        "SELECT libelle, verifiable_en_amont, COUNT(*) n FROM motif_rejet "
        "GROUP BY libelle ORDER BY n DESC LIMIT 15"
    ).fetchall()
    for l in lignes:
        marque = " [évitable en amont]" if l["verifiable_en_amont"] else ""
        print(f"  {l['n']:4}x {l['libelle']}{marque}")

    tot = conn.execute("SELECT COUNT(*) c FROM motif_rejet").fetchone()["c"]
    evit = conn.execute(
        "SELECT COUNT(*) c FROM motif_rejet WHERE verifiable_en_amont = 1"
    ).fetchone()["c"]
    if tot:
        print(f"\n{evit}/{tot} rejets ({evit / tot:.0%}) auraient pu être "
              f"évités avant de soumissionner.")

    alertes = conn.execute(
        "SELECT COUNT(*) c FROM journal WHERE statut != 'OK'"
    ).fetchone()["c"]
    if alertes:
        print(f"\n{alertes} anomalie(s) d'ingestion — voir la table journal.")
    return 0


def _ouvrir_csv(chemin: Path, standard: bool):
    """Ouvre le fichier d'annotation et renvoie (fichier, writer).

    Par défaut, le format est celui qu'Excel francophone ouvre d'un
    double-clic : séparateur point-virgule et UTF-8 avec BOM. Sans ça,
    Excel empile tout dans la colonne A et casse les accents — et un jeu
    qu'on n'arrive pas à ouvrir ne sera jamais annoté.

    `--csv-standard` rend la virgule et l'UTF-8 nu, pour pandas ou R.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    encodage = "utf-8" if standard else "utf-8-sig"
    f = chemin.open("w", newline="", encoding=encodage)
    return f, csv.writer(f, delimiter="," if standard else ";")


def cmd_annoter(args) -> int:
    """Exporte les avis à annoter à la main : c'est le jeu de référence.

    Sans lui, aucune mesure de qualité n'est possible.
    """
    conn = db.connecter(args.base)
    # Uniquement la dernière révision de chaque avis : annoter deux fois
    # le même marché fausserait la mesure de qualité.
    lignes = conn.execute(
        "SELECT a.reference, a.organisation, a.objet, a.date_limite, "
        "       a.montant_previsionnel, a.agrement_exige, a.page_debut, "
        "       a.quotidien "
        "FROM avis a "
        "JOIN (SELECT reference, quotidien, MAX(revision) AS r FROM avis "
        "      GROUP BY reference, quotidien) d "
        "  ON a.reference = d.reference AND a.quotidien = d.quotidien "
        " AND a.revision = d.r "
        "WHERE a.section = ? ORDER BY a.id LIMIT ?",
        (SECTION_AVIS, args.nombre),
    ).fetchall()

    sortie = Path(args.sortie)
    fichier, w = _ouvrir_csv(sortie, args.csv_standard)
    with fichier:
        w.writerow([
            "reference", "organisation", "objet", "date_limite",
            "montant_previsionnel", "agrement_exige", "page", "quotidien",
            # colonnes à remplir à la main
            "secteur_correct", "sotechdi_eligible", "pieces_manquantes",
            "commentaire",
        ])
        for l in lignes:
            w.writerow([*[l[k] for k in l.keys()], "", "", "", ""])

    print(f"{len(lignes)} avis exportés vers {sortie}")
    print("Remplis les 4 dernières colonnes à la main : c'est le seul actif")
    print("que la concurrence ne peut pas copier.")
    return 0


def cmd_annoter_motifs(args) -> int:
    """Exporte les segments de rejet à annoter : le catalogue se mesure là.

    Le taux de rejets évitables en amont est l'argument commercial du
    produit. Tant que le catalogue est calibré sur des extraits écrits à la
    main, ce taux mesure la cohérence du code avec lui-même, pas la
    réalité. Ce fichier est ce qui permet de trancher.
    """
    from .motifs import est_illisible, extraire_motifs, segments_de_rejet

    conn = db.connecter(args.base)
    lignes = conn.execute(
        "SELECT reference, quotidien, page_debut, texte FROM avis "
        "WHERE section != ? AND texte IS NOT NULL ORDER BY id",
        (SECTION_AVIS,),
    ).fetchall()

    sortie = Path(args.sortie)
    exportes = reconnus = ecartes = doublons = 0
    # Un même passage réimprimé par la mise en page ne doit pas être annoté
    # deux fois : il pèserait double dans le taux calculé ensuite.
    deja_vus: set[str] = set()
    fichier, w = _ouvrir_csv(sortie, args.csv_standard)
    with fichier:
        w.writerow([
            "quotidien", "page", "reference_avis", "extrait",
            "motifs_detectes",
            # colonnes à remplir à la main
            "motif_reel", "verifiable_en_amont", "commentaire",
        ])
        for ligne in lignes:
            for extrait in segments_de_rejet(ligne["texte"]):
                if exportes >= args.nombre:
                    break
                if est_illisible(extrait):
                    # Colonnes entrelacées par l'extraction PDF : illisible
                    # pour un annotateur, donc inutile dans le jeu.
                    ecartes += 1
                    continue
                if extrait in deja_vus:
                    doublons += 1
                    continue
                deja_vus.add(extrait)
                # Le catalogue est évalué sur le segment lui-même, pas sur
                # le bloc : l'annotateur voit alors exactement où il est
                # muet, extrait par extrait.
                detectes = sorted({
                    m.categorie for m in
                    extraire_motifs(extrait, ligne["reference"],
                                    ligne["page_debut"])})
                reconnus += bool(detectes)
                w.writerow([ligne["quotidien"], ligne["page_debut"],
                            ligne["reference"], extrait,
                            "|".join(detectes), "", "", ""])
                exportes += 1
            if exportes >= args.nombre:
                break

    print(f"{exportes} segments de rejet exportés vers {sortie}")
    if exportes:
        muets = exportes - reconnus
        print(f"Le catalogue actuel est muet sur {muets}/{exportes} "
              f"({muets / exportes:.0%}) d'entre eux.")
    if ecartes:
        print(f"{ecartes} segment(s) écarté(s) : colonnes entrelacées par "
              "l'extraction PDF.")
    if doublons:
        print(f"{doublons} doublon(s) écarté(s) : passage réimprimé.")
    print("Colonne 'motif_reel' : la vraie cause, avec tes mots.")
    print("Colonne 'verifiable_en_amont' : o/n — le candidat pouvait-il le")
    print("constater avant de déposer son offre ?")
    print("\nC'est ce fichier qui dira si le taux annoncé tient.")
    return 0


def cmd_lister(args) -> int:
    """Les Quotidiens publiés, et ceux qui manquent en base."""
    from . import ingest

    conn = db.connecter(args.base)
    deja = {l["numero"] for l in conn.execute("SELECT numero FROM quotidien")}
    try:
        quotidiens = ingest.lister_recents(args.nombre)
    except Exception as exc:  # noqa: BLE001
        print(f"Site DGCMEF injoignable : {exc}", file=sys.stderr)
        return 1

    manquants = 0
    for q in quotidiens:
        present = q["numero"].split()[0] in deja
        manquants += not present
        print(f"  {'[en base]' if present else '[ à faire]'} "
              f"N°{q['numero']:10} {q['url']}")
    print(f"\n{len(quotidiens)} publiés, {manquants} pas encore ingérés.")
    return 0


def cmd_mcp(args) -> int:
    """Démarre le serveur MCP sur stdio.

    Import local : le serveur tire `mcp`, dont l'ingestion en cron n'a
    aucun besoin. Une dépendance absente ne doit pas casser `ingerer`.
    """
    from .serveur_mcp import construire

    construire(base=args.base, profil=args.profil).run("stdio")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mpbf",
                                 description="Veille marchés publics Burkina Faso")
    ap.add_argument("--base", default="data/marches.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingerer", help="ingérer un Quotidien (URL ou fichier)")
    p.add_argument("source")
    p.set_defaults(func=cmd_ingerer)

    p = sub.add_parser("stats", help="statistiques et motifs de rejet")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("annoter", help="exporter le jeu à annoter")
    p.add_argument("--sortie", default="data/annotation.csv")
    p.add_argument("--nombre", type=int, default=100)
    p.add_argument("--csv-standard", action="store_true",
                   help="virgule et UTF-8 nu, au lieu du format Excel")
    p.set_defaults(func=cmd_annoter)

    p = sub.add_parser("annoter-motifs",
                       help="exporter les segments de rejet à annoter")
    p.add_argument("--sortie", default="data/annotation_motifs.csv")
    p.add_argument("--nombre", type=int, default=100)
    p.add_argument("--csv-standard", action="store_true",
                   help="virgule et UTF-8 nu, au lieu du format Excel")
    p.set_defaults(func=cmd_annoter_motifs)

    p = sub.add_parser("lister", help="Quotidiens publiés par la DGCMEF")
    p.add_argument("--nombre", type=int, default=10)
    p.set_defaults(func=cmd_lister)

    p = sub.add_parser("mcp", help="serveur MCP sur stdio")
    p.add_argument("--profil", default="profil.json",
                   help="profil entreprise utilisé par verifier_eligibilite")
    p.set_defaults(func=cmd_mcp)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
