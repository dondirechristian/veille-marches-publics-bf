"""Collecte et ingestion des Quotidiens DGCMEF."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

import pdfplumber
import requests

BASE = "https://www.dgcmef.gov.bf"
UA = "SOTECHDI-veille/0.1 (+contact: christian.dondire@sotechdi.bf)"
CACHE = Path("data/pdf")

# Page de publication des Quotidiens.
URL_LISTE = f"{BASE}/fr/taxonomy/term/17"

# Le numéro dans le nom de fichier, quelle que soit la fantaisie du jour :
#   Quotidien n°4486_0.pdf   Quotidien N°4481 .pdf   Quotidien N°4480 (bis).pdf
RE_NOM_QUOTIDIEN = re.compile(
    r"Quotidien\s*[Nn]\s*[°o]\s*(?P<numero>\d{3,5})(?P<suite>[^/]*)\.pdf",
    re.IGNORECASE,
)
RE_LIEN_PDF = re.compile(r'href="([^"]*\.pdf[^"]*)"', re.IGNORECASE)


def extraire_liens(html: str) -> list[dict]:
    """Quotidiens référencés dans une page de publication.

    Séparé du réseau pour être testable : c'est la partie qui casse quand
    le site change de gabarit, pas le téléchargement.

    Les noms de fichiers sont irréguliers — « N° » ou « n° », suffixe
    `_0` après un remplacement, espace avant `.pdf`, mention `(bis)` pour
    un numéro supplémentaire. Les deviner à partir du numéro échoue une
    fois sur deux : on lit les liens réellement publiés.
    """
    vus, sortie = set(), []
    for href in RE_LIEN_PDF.findall(html):
        nom = unquote(href.rsplit("/", 1)[-1])
        m = RE_NOM_QUOTIDIEN.search(nom)
        if not m:
            continue
        numero = m.group("numero")
        # « (bis) » est un second numéro paru le même jour : c'est un
        # document distinct, pas un doublon.
        bis = "bis" in m.group("suite").lower()
        cle = (numero, bis)
        if cle in vus:
            continue
        vus.add(cle)
        sortie.append({
            "numero": numero + (" (bis)" if bis else ""),
            # Le site sert ses liens en http ; on ne rétrograde pas.
            "url": href.replace("http://", "https://", 1),
        })
    return sorted(sortie, key=lambda q: q["numero"], reverse=True)


def lister_recents(limite: int = 10) -> list[dict]:
    """Les Quotidiens les plus récents publiés par la DGCMEF."""
    rep = requests.get(URL_LISTE, headers={"User-Agent": UA}, timeout=60)
    rep.raise_for_status()
    return extraire_liens(rep.text)[:limite]


def telecharger(url: str, forcer: bool = False) -> Path:
    """Récupère un Quotidien, met en cache. Le cache évite de retélécharger
    à chaque test de parsing."""
    CACHE.mkdir(parents=True, exist_ok=True)
    # Décoder avant d'assainir : sans ça « Quotidien%20N%C2%B04481 .pdf »
    # devient « Quotidien_20N_C2_B04481_20.pdf », illisible dans le cache
    # et inutilisable comme numéro de repli.
    nom = re.sub(r"[^\w.\-]", "_", unquote(url.rsplit("/", 1)[-1]))
    if not nom.lower().endswith(".pdf"):
        nom += ".pdf"
    cible = CACHE / nom
    if cible.exists() and not forcer:
        return cible
    rep = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    rep.raise_for_status()
    if not rep.content.startswith(b"%PDF"):
        raise ValueError(f"Pas un PDF : {url}")
    cible.write_bytes(rep.content)
    return cible


def sha256(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def pages_texte(chemin: Path) -> list[str]:
    """Texte page par page. La page est notre unité de traçabilité."""
    pages = []
    with pdfplumber.open(chemin) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


# « (bis) » s'intercale entre le numéro et le tiret :
#   N° 4480 (bis) – Jeudi 03 septembre 2026
# Sans ce groupe optionnel, l'en-tête d'un numéro bis ne matche pas du tout
# et l'ingestion se rabat sur le numéro figé du gabarit.
RE_NUMERO = re.compile(
    r"N°\s*(\d{3,5})\s*(\(\s*bis\s*\))?\s*[-–]\s*(\w+\s+\d{1,2}\s+\w+\s+\d{4})"
)


def metadonnees(pages: list[str]) -> tuple[str | None, str | None]:
    """Numéro et date de parution, lus dans l'en-tête du Quotidien.

    Le gabarit du PDF traîne un en-tête figé depuis des années — tout
    numéro de 2026 porte encore « N°3827 - lundi 04 Mars 2024 » en tête de
    page, avant son vrai numéro. Prendre la première correspondance
    rangeait donc *chaque* Quotidien sous 3827, et les avis de jours
    différents se télescopaient sur la clé (reference, quotidien).

    On retient le plus grand numéro vu : la numérotation est croissante,
    et le gabarit est plus ancien que n'importe quelle parution en cours.
    """
    trouves: list[tuple[int, bool, str]] = []
    for page in pages[:3]:
        for m in RE_NUMERO.finditer(page or ""):
            trouves.append((int(m.group(1)), bool(m.group(2)), m.group(3).strip()))
    if not trouves:
        return None, None
    numero, bis, date_txt = max(trouves, key=lambda t: t[0])
    # Un « bis » paraît le même jour que son numéro principal mais porte
    # d'autres avis : il doit rester une entrée distincte en base.
    return f"{numero} (bis)" if bis else str(numero), date_txt
