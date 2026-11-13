"""Collecte : lecture de l'en-tête et découverte des Quotidiens publiés.

Ces deux points cassent pour la même raison — le site et le gabarit du PDF
changent sans prévenir — et ils cassent en silence. D'où des tests sur des
extraits réels plutôt que sur des cas inventés.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mpbf.ingest import extraire_liens, metadonnees  # noqa: E402

# En-tête réel du Quotidien n°4486 (11 septembre 2026). Le gabarit traîne
# « N°3827 - lundi 04 Mars 2024 » depuis 2024 : c'est le piège.
PAGE_1_REELLE = """N°3827 – lundi 04 Mars 2024
N° 4106(bis) – Vendredi 28 mars 2025 1
www.dgcmef.gov.bf
Quotidien des Marchés Publics N° 4486 – Vendredi 11 septembre 2026
"""

# Extrait réel de la page de publication : suffixe _0, espace avant .pdf,
# mention (bis), « N° » et « n° » mélangés, liens servis en http.
HTML_LISTE = """
<a href="http://www.dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20n%C2%B04486_0.pdf">4486</a>
<a href="http://www.dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04481%20.pdf">4481</a>
<a href="http://www.dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04480%20%28bis%29.pdf">bis</a>
<a href="http://www.dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04480.pdf">4480</a>
<a href="/sites/default/files/2026-09/rapport-annuel.pdf">hors sujet</a>
"""


def test_numero_courant_et_non_le_gabarit_fige():
    """Le bug qui rangeait tous les Quotidiens sous le numéro 3827."""
    numero, date_txt = metadonnees([PAGE_1_REELLE])
    assert numero == "4486"
    assert date_txt == "Vendredi 11 septembre 2026"


PAGE_1_BIS = """00
N° 4480 (bis) – Jeudi 03 septembre 2026
www.dgcmef.gov.bf
N°3827 – lundi 04 Mars 2024 N° 4106(bis) – Vend redi 28 mars 2025 1
"""


def test_numero_bis_reconnu_et_distinct():
    """Un « bis » paraît le même jour mais porte d'autres avis.

    Sans le groupe optionnel, « N° 4480 (bis) – » ne matchait pas du tout
    et le numéro se rabattait sur le gabarit figé.
    """
    numero, date_txt = metadonnees([PAGE_1_BIS])
    assert numero == "4480 (bis)"
    assert date_txt == "Jeudi 03 septembre 2026"


def test_aucun_entete_lisible_ne_leve_pas():
    assert metadonnees(["page sans en-tête"]) == (None, None)


def test_liens_extraits_du_plus_recent_au_plus_ancien():
    numeros = [q["numero"] for q in extraire_liens(HTML_LISTE)]
    assert numeros[0] == "4486"
    assert "4481" in numeros


def test_bis_est_un_document_distinct():
    """Un « (bis) » paraît le même jour mais porte d'autres avis."""
    numeros = [q["numero"] for q in extraire_liens(HTML_LISTE)]
    assert "4480 (bis)" in numeros
    assert "4480" in numeros


def test_pdf_hors_sujet_ignore():
    assert all("rapport" not in q["url"] for q in extraire_liens(HTML_LISTE))


def test_liens_promus_en_https():
    """Le site sert du http ; on ne rétrograde pas la connexion."""
    assert all(q["url"].startswith("https://") for q in extraire_liens(HTML_LISTE))


# --- Frontière de section, lue dans le sommaire ----------------------
# Sommaire réel du Quotidien n°4486. La mise en page sur deux colonnes
# colle « AVIS » au pied de page, et le corps du document ne porte aucune
# bannière de section : le renvoi « P. 49 » est la seule frontière.
SOMMAIRE_REEL = """RESULTATS PROVISOIRES
- Ministeres, institutions et maitrises d'ouvrages deleguees P. 3
392 Avenue Ho Chi Minh - Regions P. 32
Tel. 25 32 46 12 - Fax 25 31 20 25
Site web : www.dgcmef.gov.bf AVIS
Directeur de publication - Marches de fournitures et services courants P. 49
- Marches de travaux P. 62
"""


def test_page_de_debut_des_avis_lue_dans_le_sommaire():
    """Le bug qui laissait la moitié « opportunités » du produit vide."""
    from mpbf.segment import page_debut_avis
    pages = ["couverture", SOMMAIRE_REEL, "corps"]
    offsets, cur = [], 0
    for i, p in enumerate(pages, 1):
        offsets.append((cur, i))
        cur += len(p) + 1
    assert page_debut_avis("\n".join(pages) + "\n", offsets) == 49


def test_sommaire_sans_avis_ne_devine_pas():
    """Sans frontière lisible, tout reste en « resultats ».

    Classer à tort un résultat en opportunité ferait monter un dossier sur
    un marché déjà attribué : l'erreur coûteuse est dans ce sens-là.
    """
    from mpbf.segment import page_debut_avis
    assert page_debut_avis("RESULTATS PROVISOIRES\n- Regions P. 3\n",
                           [(0, 1)]) is None


# --- Date limite : formulations en prose de la section AVIS ----------
# Extraits réels. Sans ces motifs, la date limite n'était extraite que sur
# 1 % des avis : une veille incapable de dire quand une consultation ferme.
def _date_limite(texte):
    from mpbf.normalize import fold
    from mpbf.parse import RE_DATES_LIMITE, _premiere_date
    for motif in RE_DATES_LIMITE:
        m = motif.search(fold(texte))
        if m:
            d = _premiere_date(m.group("v"))
            if d:
                return d
    return None


def test_date_limite_au_plus_tard_le():
    assert _date_limite(
        "Les offres devront etre remises a l'adresse ci-dessous au plus "
        "tard le 29/09/2026 a 9 heures 00 minutes.") == "2026-09-29"


def test_date_limite_avant_le():
    assert _date_limite(
        "Les offres devront parvenir ou etre remises au bureau de la "
        "Personne Responsable de la Commande Publique, avant le 09/09/2026, "
        "a 09 heures 00 minute TU.") == "2026-09-09"


def test_date_limite_explicite_prime_sur_la_prose():
    """« Date limite de depot » est plus sûr qu'un « avant le » en prose."""
    assert _date_limite(
        "Date limite de depot des offres : 05/02/2026. Les plis seront "
        "ouverts avant le 28/02/2026.") == "2026-02-05"


def test_marqueur_sans_date_ne_masque_pas_le_suivant():
    """Un marqueur prioritaire sans date ne doit pas tout bloquer."""
    assert _date_limite(
        "Les offres devront etre remises au plus tard le jour indique au "
        "reglement. Ouverture des plis le 14/09/2026.") == "2026-09-14"
