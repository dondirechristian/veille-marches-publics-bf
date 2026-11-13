"""Extraits réels du Quotidien DGCMEF N°4320 du jeudi 22 janvier 2026.

Sert de jeu de non-régression : toute modification de l'ingestion doit
continuer à produire les mêmes champs sur ces pages.
"""

PAGES = [
    # --- page 1 : couverture -----------------------------------------
    """N°3827 - lundi 04 Mars 2024 N° 4106(bis) - Vendredi 28 mars 2025 1
www.dgcmef.gov.bf""",

    # --- page 2 : sommaire -------------------------------------------
    # Le renvoi de page porte la frontière de section : le corps du
    # Quotidien ne contient aucune bannière « AVIS ». La fixture comprime
    # un numéro de 31 pages en 6, donc le renvoi pointe la page 6 — celle
    # qui contient réellement la section AVIS ci-dessous. Annoncer P. 31
    # dans un document de 6 pages rendait la fixture incohérente avec
    # elle-même, et masquait le bug de segmentation.
    """www.dgcmef.gov.bf N° 4320 - Jeudi 22 janvier 2026
RESULTATS PROVISOIRES
- Ministeres, institutions et maitrises d'ouvrages deleguees P. 3
AVIS
Marches de fournitures et services courants P. 6""",

    # --- page 3 : résultats, ARCEP -----------------------------------
    """AUTORITE DE REGULATION DES COMMUNICATIONS ELECTRONIQUES ET DES POSTES
DEMANDE DE PRIX N°2025-007/DDP/ARCEP/SE/PRM POUR LE RECRUTEMENT D'UN PRESTATAIRE POUR LE NETTOYAGE DES LOCAUX
DES SITES DU SIEGE DE L'ARCEP AU PROFIT DE L'ARCEP. FINANCEMENT : Fonds propres ARCEP
Publie dans le quotidien N°4297 du lundi 22 decembre 2025. Date d'ouverture et de deliberation : 29/12/2025 et 14/01/2026. Budget previsionnel :
Minimum = 1 854 166 FCFA TTC ; Maximum = 22 250 000 FCFA TTC.
1 PRES-NET-SERVICE-PLUS 1 456 828 TTC Conforme
2 GANZOURGUE BIIGA INNOVATION 1 571 334 HTVA Non conforme
Offre hors enveloppe
8 ENTREPRISE SANGARE NAZI DOMINGO (ESANAD) 1 355 820 TTC Non conforme
Offre anormalement basse au minimum et en deca du seuil de tolerance de 5%.""",

    # --- page 4 : résultats, CENOU -----------------------------------
    """CENTRE NATIONAL DES OEUVRES UNIVERSITAIRES
Appel d'offres ouvert par anticipation N°2026-003/CENOU/DG/PRM du 10/11/2025 pour le nettoyage et l'entretien des locaux du CENOU.
Financement : Budget du CENOU, Exercice 2026 Publication de l'avis : RMP N°4274 du mercredi 19 novembre 2025.
Date de d'ouverture des plis : 18/12/2025.
Lot 3 : Nettoyage et entretien des locaux de la cite universitaire de l'UTS
7 COMPAGNIE D'ENTRETIEN ET DE NETTOYAGE (CEN) Non conforme :
Les pieces administratives n'ont pas ete fournies malgre la correspondance qui lui a ete adressee a cet effet.
13 SANYA PRESTATION Non conforme :
Attestation de situation fiscale non fournie.""",

    # --- page 5 : résultats, marché IT régional ----------------------
    """REGION DE OUBRI
Avis a manifestation d'interet N°2025-01/MESFPT/ROBR/DRESFPT/DPESFPT-GNZ/SGRFM du 16 decembre 2025 relatif a la constitution
d'une base de donnees de fournisseurs/prestataires de la DPESFPT du Ganzourgou pour l'annee 2026.
Reference de la publication : Quotidien N°4302 du lundi 29 decembre 2025. Date limite de depot des offres : 08/01/2026
Lot 5 : Acquisition de materiel informatique (agrement exige)
13 ENTREPRISE ORIMANE ET FRERES Non retenu (absence d'agrement technique)
15 PROSERVICES INFORMATIQUE ET TECHNOLOGIE Non retenu (RCCM incompatible avec le domaine)
32 BEST CONSEILS Non retenu (absence de RCCM)
Lot 7 : Entretien et maintenance (materiel informatique et peri-informatique, photocopieurs)
1 SHOLACOM SERVICE Retenu""",

    # --- page 31 : section AVIS --------------------------------------
    """AVIS
ECOLE NORMALE SUPERIEURE
Demande de prix n°2025-025/MESRI/SG/ENS/DG/SG/PRCP du 10 decembre 2025 par anticipation pour la location de chambre d'hotels a Koudougou
pour l'hebergement des enseignants vacataires de l'Ecole Normale Superieure (ENS). Financement : Budget de l'ENS gestion 2026
Date limite de depot des offres : 05/02/2026
Mnt previsionnel : 37 760 000 ; Borne inferieure : 31 562 200.""",
]
