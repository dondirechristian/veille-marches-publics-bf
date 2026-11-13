"""Stockage SQLite avec historisation.

Deux exigences non négociables :
  - traçabilité : chaque avis pointe vers son PDF source et sa page ;
  - détection de changement : un avis modifié (date reportée, montant
    rectifié) crée une nouvelle révision au lieu d'écraser l'ancienne.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotidien (
    numero        TEXT PRIMARY KEY,
    date_parution TEXT,
    url           TEXT,
    sha256        TEXT,
    nb_pages      INTEGER,
    ingere_le     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS avis (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    reference            TEXT NOT NULL,
    quotidien            TEXT REFERENCES quotidien(numero),
    revision             INTEGER NOT NULL DEFAULT 1,
    empreinte            TEXT NOT NULL,
    procedure            TEXT,
    section              TEXT,
    organisation         TEXT,
    objet                TEXT,
    financement          TEXT,
    date_limite          TEXT,
    montant_previsionnel INTEGER,
    agrement_exige       INTEGER,
    nb_lots              INTEGER,
    page_debut           INTEGER,
    page_fin             INTEGER,
    -- Texte source du bloc. Conservé pour relire les exigences chiffrées
    -- (catégorie d'agrément, chiffre d'affaires minimum) sans rouvrir le
    -- PDF, et pour re-parser un avis après correction d'une regex.
    texte                TEXT,
    secteur              TEXT,
    secteur_confiance    REAL,
    vu_le                TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(reference, quotidien, revision)
);

CREATE INDEX IF NOT EXISTS idx_avis_ref ON avis(reference);
CREATE INDEX IF NOT EXISTS idx_avis_limite ON avis(date_limite);
CREATE INDEX IF NOT EXISTS idx_avis_secteur ON avis(secteur);

CREATE TABLE IF NOT EXISTS motif_rejet (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    reference_avis      TEXT,
    quotidien           TEXT REFERENCES quotidien(numero),
    categorie           TEXT,
    libelle             TEXT,
    verifiable_en_amont INTEGER,
    extrait             TEXT,
    page                INTEGER
);

CREATE INDEX IF NOT EXISTS idx_motif_cat ON motif_rejet(categorie);

-- Journal d'ingestion : une ingestion silencieusement ratée est pire
-- qu'une ingestion absente.
CREATE TABLE IF NOT EXISTS journal (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    horodate  TEXT DEFAULT CURRENT_TIMESTAMP,
    etape     TEXT,
    statut    TEXT,
    detail    TEXT
);
"""


def empreinte(donnees: dict) -> str:
    """Hash stable des champs de fond, hors métadonnées de collecte."""
    pertinents = {
        k: v for k, v in donnees.items()
        if k not in ("page_debut", "page_fin", "source", "vu_le")
    }
    brut = json.dumps(pertinents, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:16]


def connecter(chemin: str | Path = "data/marches.db") -> sqlite3.Connection:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrer(conn)
    return conn


def _migrer(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes apparues après coup.

    CREATE TABLE IF NOT EXISTS ne touche pas une table déjà créée : sans
    ça, une base ingérée avant l'ajout de `texte` casserait à la lecture.
    """
    existantes = {c["name"] for c in conn.execute("PRAGMA table_info(avis)")}
    for nom, decl in (("texte", "TEXT"),):
        if nom not in existantes:
            conn.execute(f"ALTER TABLE avis ADD COLUMN {nom} {decl}")
    conn.commit()


def journaliser(conn, etape: str, statut: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO journal (etape, statut, detail) VALUES (?, ?, ?)",
        (etape, statut, detail),
    )
    conn.commit()


def enregistrer_avis(conn, avis_dict: dict, quotidien: str,
                     texte: str | None = None) -> str:
    """Insère l'avis, ou une nouvelle révision s'il a changé.

    Retourne 'nouveau', 'revision' ou 'inchange'.
    """
    emp = empreinte(avis_dict)
    cur = conn.execute(
        "SELECT revision, empreinte FROM avis "
        "WHERE reference = ? AND quotidien = ? "
        "ORDER BY revision DESC LIMIT 1",
        (avis_dict["reference"], quotidien),
    )
    ligne = cur.fetchone()
    if ligne and ligne["empreinte"] == emp:
        return "inchange"

    revision = (ligne["revision"] + 1) if ligne else 1
    conn.execute(
        """INSERT INTO avis (reference, quotidien, revision, empreinte,
               procedure, section, organisation, objet, financement,
               date_limite, montant_previsionnel, agrement_exige, nb_lots,
               page_debut, page_fin, texte)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            avis_dict["reference"], quotidien, revision, emp,
            avis_dict["procedure"], avis_dict["section"],
            avis_dict["organisation"], avis_dict["objet"],
            avis_dict["financement"], avis_dict["date_limite"],
            avis_dict["montant_previsionnel"],
            int(avis_dict["agrement_exige"]), avis_dict["nb_lots"],
            avis_dict["page_debut"], avis_dict["page_fin"], texte,
        ),
    )
    conn.commit()
    return "revision" if ligne else "nouveau"


def enregistrer_motifs(conn, motifs: list, quotidien: str) -> int:
    conn.executemany(
        """INSERT INTO motif_rejet (reference_avis, quotidien, categorie,
               libelle, verifiable_en_amont, extrait, page)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (m.reference_avis, quotidien, m.categorie, m.libelle,
             int(m.verifiable_en_amont), m.extrait, m.page)
            for m in motifs
        ],
    )
    conn.commit()
    return len(motifs)
