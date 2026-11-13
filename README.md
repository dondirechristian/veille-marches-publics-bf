# Veille marchés publics — Burkina Faso

Ingestion des Quotidiens DGCMEF, extraction des avis et **des motifs de
rejet**, exposée à un agent via un **serveur MCP**.

Phase 1 du plan : produire le jeu de 100 avis annotés qui servira de
référence à tout le reste.

## Démarrage

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/macOS : .venv/bin/pip
export PYTHONPATH=src

# Voir les Quotidiens publiés et ceux qui manquent en base
python -m mpbf lister --nombre 10

# Ingérer un Quotidien (URL DGCMEF ou fichier local déjà téléchargé)
python -m mpbf ingerer https://www.dgcmef.gov.bf/sites/default/files/.../Quotidien.pdf
python -m mpbf ingerer data/pdf/Quotidien_4320.pdf

# Voir les motifs de rejet accumulés
python -m mpbf stats

# Exporter les jeux à annoter à la main (les deux actifs du projet)
python -m mpbf annoter --nombre 100 --sortie data/annotation.csv
python -m mpbf annoter-motifs --nombre 100 --sortie data/annotation_motifs.csv

# Serveur MCP sur stdio
python -m mpbf mcp

python -m pytest tests/ -q
```

## La découverte qui oriente le produit

Le Quotidien comporte deux parties. Les agrégateurs concurrents
n'exploitent que la seconde (les avis ouverts) et traitent la première
comme du bruit.

C'est une erreur. La section **RESULTATS PROVISOIRES** dit, entreprise par
entreprise, *pourquoi* chaque offre a été écartée : RCCM absent, RCCM
incompatible avec le domaine du lot, agrément technique manquant,
attestation fiscale non fournie, chiffre d'affaires insuffisant, lettre de
soumission non signée.

### Le taux évitable, mesuré

Le chiffre de 71 % annoncé au départ venait de cinq pages d'extraits
écrites à la main, contre lesquelles les regex du catalogue avaient été
calibrées — une mesure circulaire. Il a été refait sur les onze Quotidiens
réels (4477 à 4486, 31 août au 11 septembre 2026) par **deux méthodes
indépendantes** :

| Méthode | Base | Résultat |
|---|---|---|
| Annotation ligne par ligne | 100 segments de rejet lus un par un | **84 %** |
| Complément des causes concurrentielles | **1212 segments**, tout le corpus | **83 %** |

La seconde méthode part de l'autre bout : elle isole les causes qui
dépendent des **autres candidats**, donc inconnaissables à l'avance, et
traite le reste comme évitable.

| Cause dépendant des concurrents | Part du corpus |
|---|---:|
| offre anormalement basse / sous le seuil de tolérance | 16,1 % |
| hors enveloppe budgétaire | 0,7 % |
| non présélectionné au classement | 0,3 % |
| pas le moins-disant | 0,2 % |
| **total non évitable** | **17,2 %** |

Les deux méthodes convergent à un point près. **Le chiffre de départ était
donc prudent, pas exagéré : la valeur mesurée est 83 %.**

Trois limites à garder en tête avant d'en faire un argumentaire :

- L'énoncé exact est **« 83 % des motifs invoqués pour écarter une offre
  portent sur un point que le candidat pouvait vérifier avant de
  déposer »**. C'est un taux par *motif*, pas par *offre* : une offre
  écartée pour trois raisons compte trois fois.
- La seconde méthode mesure par complément. Elle suppose que tout ce qui
  n'est pas concurrentiel est maîtrisable par le candidat. La lecture des
  100 segments ne contredit pas cette hypothèse — aucune troisième
  catégorie n'y apparaît — mais elle ne la démontre pas sur les 1112
  autres.
- L'annotation des 100 est de Claude, **pas encore validée par un
  praticien**. Elle porte sur deux Quotidiens seulement (87 lignes du
  4477, 13 du 4478). Son accord avec la mesure corpus-wide est ce qui lui
  donne du poids, pas son échantillonnage.

Le catalogue, lui, ne reconnaît toujours que **16 des 100 segments** : le
taux est établi, la *détection automatique* ne l'est pas. C'est le
chantier suivant, et il peut désormais se calibrer sur
`data/annotation_motifs_annote_claude.csv`.

Le module `motifs.py` marque chaque motif d'un drapeau
`verifiable_en_amont`. Cette distinction est le cœur du produit.

## Le serveur MCP

Le CLI sert l'opérateur ; le serveur MCP sert l'agent qui répond à « ce
marché est-il pour nous ». Les deux tapent dans la même base et appellent
le même pipeline d'ingestion — un second chemin de code aurait divergé, et
c'est l'ingestion, seule étape irréversible, qu'on aurait laissée diverger.

| Outil | Ce qu'il fait |
|---|---|
| `rechercher_avis` | Consultations ouvertes, par objet, acheteur, procédure, montant |
| `detail_avis` | Fiche complète : texte source, révisions, Quotidien d'origine |
| `verifier_eligibilite` | **check_fit** — confronte l'avis au profil entreprise |
| `motifs_de_rejet` | Causes d'échec classées, filtrables par acheteur |
| `catalogue_motifs` | Les 15 causes reconnues et leur drapeau `verifiable_en_amont` |
| `profil_entreprise` | Le profil confronté aux avis |
| `etat_base` | Volumétrie, complétude mesurée, anomalies d'ingestion |
| `lister_quotidiens` | Numéros parus sur le site, et lesquels manquent en base |
| `ingerer_quotidien` | Dépouille un Quotidien (seul outil qui écrit) |

Huit outils sur neuf sont annotés en lecture seule : un client qui refuse
l'écriture garde toute la veille.

### Branchement

`.mcp.json` est déjà écrit pour Claude Code. Pour Claude Desktop, la même
entrée va dans `claude_desktop_config.json`, avec des chemins absolus :

```json
{
  "mcpServers": {
    "mpbf": {
      "command": "C:/dev/MCP marchés publiques/.venv/Scripts/python.exe",
      "args": ["-m", "mpbf", "--base", "data/marches.db", "mcp"],
      "cwd": "C:/dev/MCP marchés publiques",
      "env": { "PYTHONPATH": "src", "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

### check_fit

`verifier_eligibilite` croise deux sources d'exigence, et ne les confond
jamais dans son rendu :

1. **Ce que l'avis dit** — catégorie d'agrément, chiffre d'affaires
   minimum, références exigées. Cité mot pour mot depuis le texte source,
   que la base conserve désormais (colonne `avis.texte`).
2. **Ce qui a réellement écarté des concurrents** — la table
   `motif_rejet`. Chaque contrôle porte son nombre de rejets observés. Un
   contrôle adossé à 40 rejets ne pèse pas comme un contrôle adossé à 1.

La seconde source est l'actif du projet. Les concurrents n'ont que la
première, et encore : sans la citer.

Chaque contrôle rend un statut — `bloquant`, `risque`, `conforme`,
`indetermine`, `sans_objet` — et **sa justification en français**. Le
verdict global arrive avec sa `couverture` : la part des contrôles
réellement tranchés. Un « favorable » à 40 % de couverture ne vaut rien,
et le lecteur doit pouvoir le voir.

Le profil vit dans `profil.json`, un fichier JSON qui se relit à l'œil et se
corrige à la main. Il contient les données de l'entreprise et n'est donc pas
versionné : copiez `profil.exemple.json` (fictif) en `profil.json` pour commencer.

## Décisions d'architecture

**Rien n'est inventé.** Un champ absent du source reste `None`. Jamais de
zéro par défaut, jamais d'estimation. La complétude est *mesurée* et
affichée après chaque ingestion (`objet 79 %`, `montant 55 %`), elle
n'est pas déclarée.

**Mais la complétude ne mesure pas la justesse.** `organisation` affichait
100 % — un champ toujours rempli, dont 9 % valaient « AVIS DE DEMANDE DE
PRIX » : un intitulé de rubrique, pas un acheteur. Toute ligne en
majuscules n'est pas le nom d'une organisation, et un indicateur au vert
peut cacher un champ faux. Trois familles de faux positifs sont désormais
exclues : titres de rubrique, fragments d'objet en capitales, et secondes
moitiés de noms coupés par la mise en page (« … REGIONAL \\n **DE
OUAHIGOUYA** »). Aucun indicateur de complétude n'aurait signalé ces trois
cas : il faut relire les valeurs, pas seulement les compter.

**Repliement d'accents à longueur constante.** L'extraction PDF rend les
accents instables. Les regex tournent sur un texte replié (`é`→`e`) de
longueur identique, et les valeurs sont découpées dans l'original accentué
via les offsets. Sans ça, une moitié des avis échappait au parsing — c'est
le bug qui a fait passer la détection de 3 à 4 sur le jeu de test.

**Révisions, pas écrasement.** Un avis dont la date limite est reportée
crée une révision 2. L'historique est conservé : `db.enregistrer_avis`
renvoie `nouveau`, `revision` ou `inchange`. La ré-ingestion est
idempotente.

**Les deux sections ne parlent pas la même langue.** La section RESULTATS
récapitule en style télégraphique ; la section AVIS rédige en prose. Les
motifs d'extraction avaient tous été calibrés sur la première. Mesuré sur
les 212 avis réels de 11 Quotidiens :

| Champ | Gabarit RESULTATS attendu | Gabarit AVIS réel | Avant | Après |
|---|---|---|---:|---:|
| date limite | `Date limite de dépôt : 08/01/2026` | « remises … **au plus tard le** 29/09/2026 à 9 heures » | 1 % | 90 % |
| objet | `… pour <objet>` en en-tête | « … répartis comme suit : **- Lot 1 :** Fourniture de … » | 19 % | 79 % |
| montant | `Mnt prévisionnel : 37 760 000` | « onze millions … soixante-seize **(11 474 576)** FCFA HTVA » | 20 % | 55 % |

Trois symptômes, une seule cause : l'objet n'était cherché que dans les
quatre premières lignes, et le montant n'admettait que 40 caractères entre
son libellé et le premier chiffre — là où le gabarit réel en intercale
plus de 70, le montant étant d'abord écrit en toutes lettres.

Quand HTVA et TTC sont tous deux donnés, c'est le HTVA qui est retenu :
les offres se comparent hors taxes.

**La frontière de section vit dans le sommaire, pas dans le corps.** Le
corps du Quotidien ne porte aucune bannière « AVIS » — vérifié sur onze
numéros. Chercher un séparateur dans le corps classait donc 100 % des
blocs en « resultats », et `rechercher_avis` ne pouvait rien retourner :
la moitié « opportunités » du produit était vide sans que rien ne le
signale. La section commence à la page annoncée par le renvoi du sommaire
(« AVIS ... P. 49 »). Faute de sommaire lisible, tout reste en
« resultats » : classer à tort un résultat en opportunité ferait monter un
dossier sur un marché déjà attribué.

**Le gabarit du PDF ment sur sa propre date.** Chaque Quotidien de 2026
porte encore, en tête de page, l'en-tête figé « N°3827 - lundi 04 Mars
2024 » avant son vrai numéro. Lire la première correspondance rangeait
donc *tous* les numéros sous 3827, et les avis de jours différents se
télescopaient sur la clé `(reference, quotidien)`. `metadonnees` retient
le plus grand numéro vu : la numérotation est croissante, et le gabarit
est plus ancien que toute parution en cours. Le cas réel est figé dans
`tests/test_ingest.py`.

**Les URLs ne se devinent pas.** Les fichiers publiés s'appellent
`Quotidien n°4486_0.pdf`, `Quotidien N°4481 .pdf`, `Quotidien N°4480
(bis).pdf` — casse, suffixes et espaces varient d'un jour à l'autre.
`lister_recents` lit les liens réellement publiés au lieu de reconstruire
un nom à partir du numéro.

**Zéro avis détecté = alerte, pas succès.** Un Quotidien qui ne produit
aucun avis remonte un statut `ALERTE` dans la table `journal` et un code
de sortie non nul. Un agrégateur qui rate silencieusement trois jours de
publication est pire qu'inutile.

**Traçabilité.** Chaque avis porte son PDF source, sa page, et depuis
l'ajout de `check_fit`, son texte. Relire une exigence chiffrée ou
re-parser un avis après correction d'une regex ne demande plus de rouvrir
le PDF.

**Le serveur ne tranche que ce qu'il sait.** `check_fit` distingue
`indetermine` (le profil ou l'avis est muet) de `conforme`. Les
instructions envoyées au client MCP disent explicitement qu'un `null`
signifie « absent du document source » et ne doit jamais être remplacé par
zéro ou par une estimation. Un agent est un lecteur naïf : ce qui n'est
pas dit sera comblé.

## Limites connues

- **Le catalogue de motifs est calibré sur la fixture, pas sur le
  corpus.** Mesuré segment par segment sur les 100 premiers rejets réels,
  il est **muet sur 84 %** d'entre eux. Deux causes distinctes : des
  formulations absentes du catalogue, et des motifs pourtant listés dont
  la regex est trop serrée. Exemple du second cas : « Agrément technique
  et documents administratifs **COS SARL 59 960 000** non fournis » — la
  mise en page intercale le nom et le montant, dépassant la tolérance de
  30 caractères de `agrement_absent`. Élargir cette tolérance à l'aveugle
  créerait des faux positifs : c'est `annotation_motifs.csv` qui doit en
  décider, pas une lecture d'échantillon.

  Les formulations réelles les plus fréquentes qui lui échappent :
  « Fiches techniques, catalogues ou prospectus : non fourni » (41
  occurrences au voisinage d'un rejet), « erreur de calcul / de sommation »
  (43), « Item n : spécification non conforme » (39), « Lettre de
  soumission fournie non conforme » (23), « L'agrément fourni ne couvre pas
  la région de X » (10). Toutes sont vérifiables en amont, et aucune n'est
  détectée. C'est le premier chantier, et il se mesure contre le jeu
  annoté.
- **Le montant prévisionnel plafonne à 55 %.** Les 45 % restants ne
  publient pas de montant, ou le répartissent par lot sans total. Rien
  n'est estimé : le champ reste `None`. Sur les marchés multi-lots,
  `objet` ne retient que le premier lot — c'est une simplification, pas
  une extraction complète.
- Le sommaire est supposé tenir sur les 3 premières pages. Vérifié sur 11
  numéros consécutifs : il tient toujours sur la page 2.
- **Les noms d'acheteurs coupés sur deux lignes restent tronqués.** La
  seconde moitié est écartée, donc « CENTRE HOSPITALIER UNIVERSITAIRE
  REGIONAL DE OUAHIGOUYA » est enregistré sans « DE OUAHIGOUYA ».
  Identifiable, mais incomplet. Recoller les lignes majuscules
  consécutives réglerait le cas — au risque d'agglomérer les noms
  d'entreprises des tableaux de résultats, qui se suivent de la même
  manière. À trancher sur le jeu annoté.
- **L'extraction PDF entrelace les colonnes sur quelques tableaux.**
  « M M a i x n i i : : » est « Min : » et « Max : » superposés. Mesuré :
  **1 % des blocs** (5 sur 450) — assez rare pour ne pas justifier un autre
  moteur d'extraction, mais ces segments sont écartés de l'export
  d'annotation, où ils ne feraient perdre que du temps.
- **Les numéros « bis » ne sont pas dépouillés.** Ce sont des numéros de
  rectificatifs (« FICHE DE SYNTHESE RECTIFICATIVE ») dont la mise en forme
  ne suit pas `<procédure> N°<référence>` : la segmentation n'y trouve
  aucun avis et remonte une `ALERTE`. Or un rectificatif peut reporter une
  date limite ou renverser une attribution — c'est exactement ce qu'une
  veille ne doit pas manquer.
- **L'ordre des catégories d'agrément n'est pas codé.** Un B1 face à un B2
  exigé sort en `risque`, pas en `bloquant` : sans barème d'équivalence
  sourcé, trancher reviendrait à inventer. À fermer dès que le barème
  officiel est en main.
- **`check_fit` ne contrôle qu'une seule pièce administrative nommément**,
  l'attestation de situation fiscale, parce que c'est la seule que le
  corpus désigne comme cause de rejet. Les autres pièces déclarées au
  profil sont vérifiées pour leur date, mais signalées en `risque` :
  rien ne prouve que tel avis les exige. La liste s'étendra avec les
  motifs réellement observés, pas avec des suppositions.
- Pas encore de classification sectorielle : c'est la Phase 2, et elle se
  mesurera contre `data/annotation.csv`.
- Un seul bailleur couvert (DGCMEF). Les avis PNUD, Banque mondiale et BAD
  passent par d'autres canaux.

## Ce que la première confrontation au réel a appris

Onze Quotidiens (4477 à 4486, du 31 août au 11 septembre 2026) ont été
ingérés. Quatre défauts que la fixture ne pouvait pas révéler — elle avait
été écrite pour correspondre au code :

| Défaut | Effet | État |
|---|---|---|
| En-tête figé du gabarit (« N°3827 – 2024 ») | Tous les numéros rangés sous 3827 | corrigé |
| `(bis)` entre le numéro et le tiret | En-tête non reconnu, repli sur 3827 | corrigé |
| Aucune bannière « AVIS » dans le corps | **100 % des blocs en « resultats », zéro opportunité** | corrigé |
| Date limite rédigée en prose | Extraite sur 1 % des avis | corrigé |
| Objet cherché dans les 4 premières lignes | Extrait sur 19 % des avis | corrigé |
| Montant écrit en toutes lettres | Extrait sur 20 % des avis | corrigé |
| Rapprochement RCCM sensible au pluriel | « materiel informatique » ≠ « matériels péri-informatiques » | corrigé |
| Intitulé de rubrique pris pour l'acheteur | 9 % des `organisation`, derrière une complétude à 100 % | corrigé |
| Catalogue de motifs calibré sur la fixture | 38 % des rejets sans motif reconnu | **ouvert** |

Leçon transposable : **un jeu de test écrit à la main mesure la cohérence
du code avec lui-même, pas sa justesse.** Les quatre premiers défauts
passaient les tests au vert. Le cinquième est le plus coûteux, parce qu'il
touche le chiffre qui fonde l'argumentaire commercial.

## Prochaine étape

Onze Quotidiens sont ingérés. Reste l'annotation, et il en faut **deux**,
pas une — elles ne mesurent pas la même chose :

`python -m mpbf annoter` → `data/annotation.csv`
: 100 avis ouverts. Mesure la classification sectorielle (Phase 2) et la
  justesse de `check_fit` via la colonne `pieces_manquantes`.

`python -m mpbf annoter-motifs` → `data/annotation_motifs.csv`
: 100 segments de rejet, chacun en face de ce que le catalogue a cru y
  reconnaître. Mesure le catalogue, et **tranche la question du taux
  évitable** — celle dont dépend l'argumentaire commercial.

Le second est le plus urgent : c'est le seul qui puisse valider ou tuer le
chiffre mis en avant. L'export affiche directement sur combien de segments
le catalogue reste muet.

**Critère d'arrêt du projet**, inchangé : si la classification sectorielle
des concurrents est juste sur plus de 90 % des 100 avis, l'angle
« meilleure classification » est mort. Mais la confrontation au réel a
déplacé le centre de gravité — l'angle défendable n'est pas la
classification, c'est le « pourquoi les offres échouent », que personne
d'autre n'exploite. C'est `annotation_motifs.csv` qui dira s'il tient.
