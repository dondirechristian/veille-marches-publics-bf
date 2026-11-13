"""Non-régression sur des extraits réels du Quotidien N°4320.

Toute modification de l'ingestion doit continuer à passer ces tests.
C'est ce qui distingue un produit fiable d'un script.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_4320 import PAGES  # noqa: E402
from mpbf.motifs import extraire_motifs, taux_evitable  # noqa: E402
from mpbf.normalize import fold  # noqa: E402
from mpbf.parse import _montant, _objet, parser  # noqa: E402
from mpbf.segment import Bloc, segmenter  # noqa: E402


def blocs():
    return segmenter(PAGES)


def test_detecte_tous_les_avis():
    assert len(blocs()) == 4


def test_sections_correctes():
    sections = [b.section for b in blocs()]
    assert sections == ["resultats", "resultats", "resultats", "avis"]


def test_procedures_reconnues():
    procs = [b.procedure for b in blocs()]
    assert procs == ["demande_prix", "appel_offres_anticipation",
                     "manifestation_interet", "demande_prix"]


def test_reference_et_organisation():
    b = blocs()[3]
    a = parser(b, "test.pdf")
    assert a.reference == "2025-025/MESRI/SG/ENS/DG/SG/PRCP"
    assert a.organisation == "ECOLE NORMALE SUPERIEURE"
    assert a.date_limite == "2026-02-05"
    assert a.montant_previsionnel == 37_760_000


def test_agrement_uniquement_ou_il_est_exige():
    flags = [parser(b, "t.pdf").agrement_exige for b in blocs()]
    assert flags == [False, False, True, False]


def test_aucun_champ_invente():
    """Un montant absent doit rester None, jamais 0 ni une estimation."""
    a = parser(blocs()[1], "t.pdf")
    assert a.montant_previsionnel is None


def test_motifs_de_rejet_extraits():
    tous = []
    for b in blocs():
        tous += extraire_motifs(b.texte, b.reference, b.page_debut)
    cats = {m.categorie for m in tous}
    assert {"rccm_absent", "rccm_domaine", "agrement_absent", "fiscal"} <= cats
    assert taux_evitable(tous) > 0.5


def test_traçabilite_page():
    for b in blocs():
        assert b.page_debut >= 1


# --- Objet : gabarit réel de la section AVIS -------------------------
# Extrait du Quotidien n°4477. L'objet n'est pas dans l'en-tête : celui-ci
# enchaîne référence, financement et montant. Il vient dans la description
# des lots. Chercher dans les 4 premières lignes ne trouvait rien.
def _bloc(texte):
    return Bloc(section="avis", organisation=None, procedure="demande_prix",
                reference="X", texte=texte, page_debut=1, page_fin=1,
                lignes=[l for l in texte.split("\n") if l.strip()])


def test_objet_lu_dans_la_description_des_lots():
    texte = (
        "demande de prix n° 2026-021/MJ/SG/DMP du 26/08/2026 "
        "Financement : Budget Etat, exercice 2026 "
        "Montant previsionnel : onze millions (11 474 576) FCFA HTVA. "
        "3. Les acquisitions se decomposent en deux (02) lots repartis "
        "comme suit : - Lot 1 : Fourniture de pauses-cafe et location de "
        "salles au profit de la DGPCP ; - Lot 2 : autre chose"
    )
    assert _objet(_bloc(texte)) == (
        "Fourniture de pauses-cafe et location de salles au profit de la DGPCP")


def test_un_montant_de_lot_n_est_pas_un_objet():
    """« Lot unique : » introduit tantôt une description, tantôt un prix."""
    texte = (
        "demande de prix N°2026-094/MEF/SG/DMP du 24/08/2026 "
        "Financement : Budget de l'Etat -Lot unique : huit millions trois "
        "cent cinq mille (8 305 085) francs CFA HTVA."
    )
    assert _objet(_bloc(texte)) is None


def test_objet_explicite_prime():
    texte = ("appel d'offres n°2026-01/X ayant pour objet la construction "
             "d'un batiment administratif ; Lot 1 : autre chose")
    assert _objet(_bloc(texte)) == "la construction d'un batiment administratif"


# --- Montant prévisionnel : gabarit en toutes lettres ----------------
def test_montant_lu_entre_parentheses_apres_les_lettres():
    """La section AVIS écrit le montant en lettres puis en chiffres.

    Plus de 70 caractères séparent le libellé du premier chiffre.
    """
    texte = ("demande de prix n°2026-021/MJ/SG/DMP Montant previsionnel : "
             "onze millions quatre cent soixante-quatorze mille cinq cent "
             "soixante-seize (11 474 576) FCFA HTVA soit treize millions "
             "cinq cent quarante mille (13 540 000) FCFA TTC.")
    assert _montant(fold(texte)) == 11_474_576


def test_une_parenthese_de_comptage_n_est_pas_un_budget():
    """« en deux (02) lots », « (100) jours » ne sont pas des montants."""
    texte = ("Montant previsionnel du marche reparti en deux (02) lots, "
             "avec un delai de (100) jours.")
    assert _montant(fold(texte)) is None


# --- Segments de rejet : la matière du jeu à annoter -----------------
def test_un_segment_par_soumissionnaire_ecarte():
    """Un bloc de résultats couvre des dizaines de soumissionnaires.

    Mélanger leurs motifs interdirait de mesurer quoi que ce soit : on
    découpe une fenêtre après chaque mention de rejet.
    """
    from mpbf.motifs import segments_de_rejet
    texte = ("1 ALPHA SARL 1 456 828 TTC Conforme\n"
             "2 BETA SA 1 571 334 Non conforme Offre hors enveloppe\n"
             "3 GAMMA Non retenu absence de RCCM\n")
    segments = segments_de_rejet(texte)
    assert len(segments) == 2
    assert "hors enveloppe" in segments[0]
    assert "absence de RCCM" in segments[1]


def test_aucun_rejet_aucun_segment():
    from mpbf.motifs import segments_de_rejet
    assert segments_de_rejet("1 ALPHA SARL 1 456 828 TTC Conforme") == []


def test_texte_entrelace_detecte():
    """Sur quelques pages en tableau, l'extraction superpose les colonnes.

    « M M a i x n i i : : » est « Min : » et « Max : » entrelacés. Un
    annotateur ne peut rien en faire : ces segments sortent du jeu.
    """
    from mpbf.motifs import est_illisible
    assert est_illisible("Non conforme M M a i x n i i : : 1 7 0 1 7 2 7 5")
    assert not est_illisible(
        "Non conforme : Agrement technique et documents administratifs "
        "non fournis")


def test_un_intitule_de_rubrique_n_est_pas_un_acheteur():
    """`organisation` affichait 100 % de complétude, dont 9 % de faux.

    Une ligne en majuscules n'est pas forcément le nom d'un acheteur : le
    Quotidien titre ses rubriques de la même manière.
    """
    from mpbf.segment import RE_ORG_EXCLUE, RE_ORG_LINE
    for faux in ("AVIS DE DEMANDE DE PRIX",
                 "RECTIFICATIF DU DOSSIER D'APPEL D'OFFRES",
                 "TRANSFORMATEURS AU PROFIT DU CHUR DE OUAHIGOUYA",
                 # Seconde moitié d'un nom coupé par la mise en page.
                 "DE OUAHIGOUYA ET DE BAPORO"):
        assert RE_ORG_LINE.match(faux), "le motif large doit encore matcher"
        assert RE_ORG_EXCLUE.search(faux), f"{faux} doit être exclu"
    for vrai in ("MINISTERE DE L'ECONOMIE ET DES FINANCES",
                 "OFFICE NATIONAL DE L'EAU ET DE L'ASSAINISSEMENT",
                 "COMMUNE DE SOLENZO", "REGION DE BANKUI"):
        assert RE_ORG_LINE.match(vrai)
        assert not RE_ORG_EXCLUE.search(vrai), f"{vrai} doit être conservé"
