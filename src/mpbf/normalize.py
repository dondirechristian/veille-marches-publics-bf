"""Nettoyage du texte brut extrait des Quotidiens DGCMEF.

Le PDF est mis en page sur deux colonnes avec des en-têtes/pieds répétés
à chaque page. Sans ce nettoyage, tous les motifs de parsing sautent.
"""

import re
import unicodedata

# En-tête répété sur chaque page, ex :
#   "N°3827 – lundi 04 Mars 2024 N° 4320–Jeudi 22 janvier 2026 5"
RE_PAGE_HEADER = re.compile(
    r"^\s*N°\s*\d{3,4}\s*[–-].*?N°\s*\d{3,4}\s*[–-].*?\d+\s*$",
    re.MULTILINE,
)
RE_SITE_FOOTER = re.compile(
    r"^\s*(www\.dgcmef\.gov\.bf|www\.finances\.gov\.bf)[\s\w./]*$",
    re.MULTILINE | re.IGNORECASE,
)

# Coupure de mot en fin de ligne : "AFRIK-\nENERGIE" -> "AFRIK-ENERGIE"
RE_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def normalize(text: str) -> str:
    """Retourne un texte nettoyé, prêt pour la segmentation."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    # Apostrophes et tirets typographiques -> ASCII, sinon les regex ratent
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u00a0", " ")
    text = RE_PAGE_HEADER.sub("", text)
    text = RE_SITE_FOOTER.sub("", text)
    text = RE_HYPHEN_BREAK.sub(r"\1-\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Repliement d'accents caractère par caractère : la chaîne repliée a
# EXACTEMENT la même longueur que l'originale, donc les offsets restent
# valides. On cherche sur le texte replié, on extrait depuis l'original.
_TABLE_FOLD = str.maketrans(
    "àáâãäåçèéêëìíîïñòóôõöùúûüýÿÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜÝ",
    "aaaaaaceeeeiiiinooooouuuuyyAAAAAACEEEEIIIINOOOOOUUUUY",
)


def fold(text: str) -> str:
    """Version sans accents, de même longueur que l'entrée."""
    return text.translate(_TABLE_FOLD)


def chercher(pattern, texte: str, texte_replie: str | None = None):
    """Cherche sur le texte replié, retourne le match ET l'extrait accentué."""
    replie = texte_replie if texte_replie is not None else fold(texte)
    m = pattern.search(replie)
    if not m:
        return None, None
    return m, texte[m.start():m.end()]


def normalize_amount(raw: str) -> int | None:
    """'1 456 828' -> 1456828. Retourne None si non parsable.

    Ne devine jamais : un montant absent reste absent.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None
