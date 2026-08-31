# Changelog — vsm-ocr

Format : [Keep a Changelog] · Versionnage : SemVer.

## [1.2.10] — 2026-08-31

### N1 — ConText : négation, expérienceur, modalité

Le VSM ne conservait que le terme extrait et son passage, jamais ce qui le
QUALIFIAIT : toute entité était traitée comme un fait affirmé, concernant le
patient, au présent. Mesure préalable sur les quatre dossiers (doctrine du
projet) : **118 entités sur 758 (16 %)** portent un marqueur dans leur
contexte gauche — « mort subite » en antécédent personnel alors que le
document dit « antécédents familiaux… chez un frère » (MANGUE p107) ;
« signe de malignité » en pathologie active alors que le compte rendu dit
« **Absence de** signe de malignité » (MANGUE p100, DRAGON p096/p115,
ABRICOT p033, BANANE p039/p073). Et TOUTES les occurrences de « insuffisance
cardiaque droite » de DRAGON sont niées (« **Pas de signe d'**insuffisance
cardiaque droite », p011/p019) : la « non-régression I50 » de la v9 était
elle-même une affirmation fausse — la mesure tranche, le rejet s'applique.

- **Évaluation de medkit d'abord** (exigée par le relecteur) : medkit n'est
  PAS une dépendance du projet — le DrBERT-CASM2 est chargé via transformers,
  « medkit/DrBERT-CASM2 » est le nom du modèle HuggingFace — et l'axe
  EXPÉRIENCEUR (celui qui répond au cas « mort subite ») y est absent.
  Implémentation minimale des règles du correctif : module
  `contexte_conext.py`.
- **Quatre pièges mesurés et corrigés** au fil de l'évaluation :
  double négation (« IL n'est pas exclu qu'il se soit agi d'un abcès »
  AFFIRME l'abcès — « exclu/éliminé/écarté » ne sont que des nuances, jamais
  des rejets) ; PORTÉE rompue par une virgule, « et/ou » ou « jusqu'à »
  (« sans anomalie décelée, et qui lui prescrit du CLAMOXYL » ;
  « essayé sans succès jusqu'à ce qu'elle prenne du Rivotril ») ; ANAPHORE
  (« expliquer cette symptomatologie » — le démonstratif/possessif immédiat
  signale un concept affirmé plus tôt) ; négation SUR LA TECHNIQUE (« n'a
  pas été validée dans les cas d'obésité extrême » nie la validation, pas
  l'obésité : seule la négation FRANCHE rejette). Cinquième piège trouvé par
  la relecture du rendu : les APOSTROPHES TYPOGRAPHIQUES de l'OCR (' U+2019)
  rendaient « pas d'anomalie » invisible au motif franc — classe de
  caractères dédiée.
- **Routage** : niée → rejet TRACÉ dans le journal (règle `N1_entite_niee`,
  contexte reproduit) ; familial → facteurs de risque, mention « antécédent
  familial » ; hypothétique → points de vigilance, mention « à confirmer » ;
  nuance → mention seule, rubrique inchangée (précaution : conserver et
  marquer plutôt que supprimer). La mention est visible dans les TROIS rendus
  (markdown, HTML, PDF) et corrigeable dans l'éditeur.
- **Primauté de la mention affirmée** (§3.4) : le rejet est PAR occurrence,
  avant la déduplication — mesuré sur « toux quotidienne non productive »
  (ABRICOT), « Rivotril » (BANANE), « endocardite mitrale » (DRAGON),
  « pneumopathie » (affirmée en pathologies + « probable » en vigilance),
  hernies (MANGUE) : l'affirmée survit toujours au rejet de la niée.
- **Garde-fou −10 % : dépassé (−11,4 à −15,7 %) — enquête menée comme
  exigé** : inspection manuelle des 201 rejets `N1_entite_niee` (47 MANGUE,
  71 DRAGON, 30 ABRICOT, 53 BANANE), vérification des cas douteux dans le
  texte source (fièvre/toux ABRICOT = vraies négations ; BAV, transfusions
  et endocardite DRAGON = toutes les occurrences détectées sont niées ou
  hypothétiques). Conclusion : la baisse EST la correction — 201 occurrences
  niées étaient affirmées à tort, dont 104 entités n'avaient AUCUNE
  occurrence affirmée survivante. Faux positifs résiduels identifiés :
  Rivotril (corrigé par la portée « jusqu'à »), « cystite chronique »
  (« aucune douleur vésicale DE cystite chronique » — négation sur le
  complément, limite documentée).
- **Limites** : le moteur « rules » natif (repli de repli) ne passe pas par
  le validateur commun — le ConText s'applique aux moteurs DrBERT et LLM ;
  la divergence niée/affirmée d'une même entité est tracée dans le journal
  mais pas encore remontée dans le VSM comme l'est la latéralité divergente.

## [1.2.9] — 2026-08-30

### Correctifs MANGUE v9 (M1-M5) : la métrique ne séparait plus, le mot
### discriminant si

Analyse du dossier MANGUE (médecine interne, 131 pages, chirurgie pariétale
et hépatique, IRC) — six familles de défauts. Tout est mesuré avant d'être
corrigé (aucun réglage à l'aveugle), vérifié par diff avant/après sur les
QUATRE dossiers (MANGUE, DRAGON, ABRICOT, BANANE) et couvert par 23 tests
nouveaux (289 au total, tous verts).

- **M1 — critère de mot DISCRIMINANT (CIM-10)** : « insuffisance rénale »
  recevait I50 « Insuffisance cardiaque » à 0,780 et « zygarthrose » M15
  « Polyarthrose » à 0,783 — EXACTEMENT à la frontière du seuil 78. Le
  score global est dominé par la tête commune du libellé : aucun réglage de
  seuil ne sépare ces cas. Nouvelle règle `code_recevable`
  (`normalizer.py`) : chaque mot discriminant du terme (hors têtes
  génériques, latéralité, sévérité — « insuffisance cardiaque DROITE »
  garde I50, non-régression explicite) doit avoir un correspondant
  (fuzz.ratio ≥ 85) dans le libellé, sinon AUCUN code. Exceptions mesurées
  : libellés de REGROUPEMENT (« Fibrillation ET flutter » → I48 garde une
  fibrillation isolée) et générique-à-générique (« allergie » ↔ « Allergie,
  sans précision »).
- **M1 — bug AVC** : « AVC » seul recevait I63 à 1,00 — `_nomme_par_alias`
  acceptait une simple INTERSECTION de jetons ; il exige désormais
  l'INCLUSION (l'abréviation nue ne documente pas l'ischémie ; I64 absent
  du référentiel → aucun code). « AVC ischémique » garde I63.
- **M1 — régression C1 réparée (ATC)** : « Kardegic 75mg » était passé à
  None (0,76 < 0,95 — la posologie accolée fait chuter token_set_ratio
  face au libellé long). Les ALIAS parenthésés sont désormais des CLÉS du
  référentiel : « Kardegic 75mg » → B01AC06 à 1,00. Audit normalisateur :
  29/29 codes corrects, 0 faux.
- **M2 — scission des entités concaténées** (sauts de ligne perdus à
  l'OCR) : « HTA Gastrite chronique » était UNE entité DrBERT.
  `scinder_concatenation` coupe sur majuscule interne après mot autonome
  (minuscules ≥ 3 lettres, ou abréviation 2-5 capitales — le regex du
  relecteur ne coupait pas son propre exemple « HTA »), avec offsets
  recalculés (chaque fragment reste un extrait EXACT du document). Les
  éponymes sont protégés par construction (« maladie de Crohn »,
  « d'Achille » collent à l'apostrophe). GARDE : si aucun fragment ne
  survit, l'entité originale reprend sa place — la scission ne perd jamais
  une information.
- **M3 — actes complémentaires** : interposition, exsufflation,
  dissection, réparation, incision, voie d'abord, coelioscopie, pariétex
  ajoutés à la liste d'actes (« Interposition prothétique » et « cure
  d'éventration » quittent les traitements pour les antécédents) ;
  « cure d » remplace « cure de » (l'apostrophe de « cure d'éventration »
  est normalisée en espace). « gel de xylocaïne » et « anesthésique
  local » routés en points de vigilance. NON-RÉGRESSION vérifiée : PPC
  autopilotée, traitement ventilatoire, aide ventilatoire restent des
  traitements.
- **M4 — rangement** : une CLASSE médicamenteuse étiquetée « problem »
  (glucocorticoïdes, tolérant aux fautes d'OCR) est routée en traitements ;
  les dosages d'auto-anticorps (« anti-TRIM21 » : cible en
  majuscule/chiffre) sont rejetés en rubrique diagnostique — mais PAS en
  vaccinations (« triple vaccination anti COVID » tombait dans le piège,
  mesuré) ; l'en-tête de service répété (« Pathologies du Sommeil ») est
  rejeté par P6 (zone d'en-tête plafonnée à 200 caractères + STABILITÉ de
  position ± 50 car. : un intitulé de service est toujours au même endroit,
  un diagnostic cité en début de corps bouge) ; « Type 2 » orphelin de
  « diabète de type 2 » est rejeté.
- **M5 — fragments résiduels** : termes d'un seul mot hors LEXIQUE MÉDICAL
  rejetés (« froid », « douloureux », « gaz », « échostructure »,
  « incision ») — lexique calibré sur la mesure (~150 un-mot des trois
  dossiers réels : abréviations + familles de suffixes + liste blanche
  manuelle, mots du set P4 exclus pour garder la règle précise de P4) ;
  fins tronquées étendues (en, intra, inter, supra, rétro, péri, « + » :
  « hypersignal en », « dilatation des voies biliaires intra »,
  « Lavage eau + ») ; verbe d'état conjugué au milieu rejeté (« foie est
  augmenté de taille » = phrase de compte rendu, pas une entrée de liste).
- **Limites connues** (mesurées, assumées) : les fragments multi-mots non
  médicaux (« masse musculaire », « soucis nocturnes ») survivent — les
  tuer demanderait un dictionnaire français complet ; la scission M2 peut
  produire en traitements des fragments courts (« Moléculaire » après
  « Héparine de Bas Poids ») — la règle un-mot ne s'applique pas aux
  traitements pour ne pas tuer les noms commerciaux hors référentiel
  (CLAMOXYL, MAALOX…) ; M6 (redondance rénale IRC/N18/insuffisance) est
  un chantier lexique de synonymes, non traité ici.

## [1.2.8] — 2026-08-30

### Navigation : retour au VSM sans piège

Constat d'audit UX : « Voir le passage source » ouvrait le document mais la
flèche Retour du navigateur ramenait à la page d'accueil du navigateur — la
navigation était un état React pur, jamais inscrit dans l'historique. Le seul
chemin de retour était « Tableau de bord » puis la liste des VSM « à valider ».

- **Navigation par historique** (pushState/popstate natif, sans dépendance) :
  chaque changement de vue est inscrit dans l'historique du navigateur — la
  flèche Retour revient à la vue précédente DANS l'application (document →
  VSM → tableau de bord), la flèche Avant revient au document avec ses
  surlignages.
- **Bouton de retour contextuel** : « ← Retour au VSM » quand on vient de
  l'éditeur (document ouvert par « Voir le passage source »), « ← Retour au
  tableau de bord » sinon — il remonte l'historique au lieu de tout rabattre
  sur l'accueil.
- **URL signifiante** (`#/vsm/123`, `#/document/456`) : un F5 ou un lien
  direct conserve la vue au lieu de retomber sur le tableau de bord.
- La déconnexion remplace l'entrée courante (aucune vue post-login ne
  survit dans l'historique) ; le retour ne sort jamais de l'application.
- TypeScript et build propres.

## [1.2.7] — 2026-08-30

### Statistiques : plus aucun champ qui déborde

Constat : la page Statistiques empilait sur une même ligne SVG libellé,
barre, compteur et code dans un viewBox fixe 520 — un libellé long
recouvrait sa barre, le compteur et le code se chevauchaient sur les barres
pleines, et l'écran étroit réduisait le tout (texte minuscule). Les cartes
« VSM par statut » / « Périodes » tenaient tout sur une ligne jointe par
« · » (débordement), et un libellé de rubrique long poussait le pourcentage
hors de sa carte.

- **Graphique refondu en rangées empilées** : une rangée = libellé (tronqué
  avec « … ») + code sur une ligne, barre + compteur sur la ligne suivante —
  aucun chevauchement possible, quelle que soit la longueur du libellé.
- **Largeur adaptative** (ResizeObserver, aucun CDN) : le graphique suit la
  largeur réelle de son conteneur, la police reste lisible sur petit écran
  comme en colonne double.
- **Statuts et périodes en mini-lignes repliables** (`flex-wrap`) : chaque
  « clé : valeur » passe à la ligne au lieu de déborder.
- **Complétude** : libellé tronqué (`min-w-0 truncate`) + pourcentage en
  `whitespace-nowrap` — plus rien ne sort de la carte.
- TypeScript et build propres ; aucun changement de contrat d'API.

## [1.2.6] — 2026-08-30

### Audit DRAGON v7 (C1-C5) : codes sûrs, actes reclassés, fragments rejetés

Analyse du dossier DRAGON (cardiologie interventionnelle, endocardite,
néphrologie, chirurgie tendineuse) — des défauts qu'aucun dossier précédent
ne révélait. Vérifié sur les trois dossiers régénérés (DRAGON 131 pages,
ABRICOT 88, BANANE 114) : 25/27 cases de la checklist du relecteur.

- **C1 — seuil ATC 0,95, aucun code en dessous** (risque clinique direct) :
  « SPIRAMYCINE » recevait B01AC06 (ASPIRINE !), « GENTALLINE » N06AB06
  (sertraline), « ofloxacine » N06AB03 (fluoxétine) par pure ressemblance
  graphique — lire B01AC06 sur une spiramycine, c'est conclure « patient
  sous aspirine » (anticoagulation, contre-indication chirurgicale). Un nom
  de molécule est un IDENTIFIANT : les appariements légitimes (exact ou
  posologie accolée) sont des sur-ensembles à 1,00 ; tout le reste est du
  bruit. Vérifié : SPIRAMYCINE sans code, PARACETAMOL 1g garde N02BE01,
  Metformine/Aspirine/Omprazole/Pantoprazole/Lévothyroxine inchangés.
- **C2 — règle de spécificité étendue à CIM-10** (déclenchement 0,90) :
  « diabète » ne porte plus E11 « de type 2 », « cardiopathie » plus I25
  « ischémique chronique », « tumeur » plus C50 « du sein », « anémie »
  plus D50 « par carence en fer ». La distinction retenue n'est pas
  ATC/CIM-10 mais la NATURE du qualificatif : coordination/imprécision →
  le code regroupe, on accepte (I48 « Fibrillation ET flutter » reste le
  code d'une fibrillation isolée) ; localisation/étiologie/type → le code
  affirme plus que le texte, on refuse. Complétions canoniques bénines :
  « sucré », « aigu », « autre », enveloppes (« maladie », « présence »,
  « antécédents personnels ») ; négations finales retirées (« sans
  précision » n'affirme rien — M81 garde l'ostéoporose nue). Non-régression
  mesurée : insuffisance cardiaque droite [I50], maladie rénale chronique
  [N18] dans DRAGON, E11/D50/C50/I25 refusés sur ABRICOT.
- **Bug latent découvert en mesurant C2** : `token_set_ratio` découpe sur
  les espaces SEULS — la ponctuation restait collée aux jetons. « allergie »
  ne matchait JAMAIS « Allergie, sans précision », « BPCO » jamais
  « (BPCO) », « pacemaker » jamais « (pacemaker) », et « AVC ischémique »
  ratait I63 pour tomber sur I25 « Cardiopathie ischémique chronique » à
  0,83 ! Un processeur découpe désormais la ponctuation — BPCO → J44,
  AVC ischémique → I63, pacemaker → Z95.0, Levothyrox → H03AA01.
- **C3 — actes et supports hors des traitements** : suffixes chirurgicaux
  complétés (-plastie, -tripsie, -pexie, -stomie, -centèse, -desis, et
  « -iastie »/« icature » pour les fautes d'OCR réelles « annulopiastie »,
  « Piicature ») + vocabulaire cardio/ortho (cardioplégie, reperfusion,
  CEC, sternotomie, treillis, prothèse, TENOLIG, botte, contention…).
  Les supports thérapeutiques (transfusion, oxygénothérapie, support
  inotrope, dialyse, épuration) partent en points de vigilance. Traitements
  DRAGON : 51 → 24 entrées ; total DRAGON : ~210 → 183.
- **C4 — fragments tronqués rejetés** (règle nommée
  `validateur_fragment_tronque`) : préposition/article en tête (« de
  résistance », « du murmure vésiculaire ») ou mot-outil en fin
  (« Résection de la ») = découpe ratée. Vérifié : plus aucun élément
  commençant par de/du/des/à sur les trois dossiers.
- **C5 — déduplication latéralisée + signalement** : couverture floue de
  jetons (« Achilie » couvre « Achille » à 85,7) mesurée sur DRAGON (6
  paires utiles, 0 faux positif sur les négatifs). **Bug de pont corrigé**
  (mesuré sur le code d'avant) : « hernie inguinale » (sans côté) reliait
  « ... droite » et « ... gauche » en UNE entrée — le garde-fou latéralité
  s'applique désormais au NIVEAU DU CLUSTER. Tendon d'Achille DRAGON :
  7 → 4 entrées (familles droit/gauche en pathologies, les deux marquées
  **« ⚠ latéralité divergente — à confirmer »** dans l'éditeur — le dossier
  documente une atteinte bilatérale, le médecin la voit désormais).
- **Audit du normalisateur** (24/24 corrects, 0 faux) : benchmark étendu
  aux cas DRAGON (spiramycine/gentalline/ofloxacine sans code, salmétérol
  refusé, diabète/cardiopathie/tumeur/anémie/hypercholestérolémie sans
  code sur-spécifique).
- Honnêteté sur les 2 cases restées rouges : tendon d'Achille à 4 entrées
  (les fragments d'antécédents « rupture complète »/« rupture à droite »
  exigent un lexique de complétion — chantier C6) et total DRAGON à 183
  (l'excès restant est le fractionnement des pathologies, synonymes —
  chantier C6). 266 tests verts ; ruff à la baseline.

## [1.2.5] — 2026-08-24

### « Voir le passage source » : toutes les mentions, avec défilement

Constat : une entrée fusionnée par P2 porte « 15 mentions, pages 8 à 74 »
mais un SEUL passage surligné dans le visualiseur — les passages des autres
mentions étaient perdus à la fusion, et avec eux les fautes d'OCR propres
à chaque page.

- **P2 conserve tous les passages** (`filtres_vsm.dedupliquer`) : chaque
  entrée fusionnée porte `passages` — les extraits sources DISTINCTS de
  chaque mention, dans l'ordre du document (les formes strictement
  identiques ne sont gardées qu'une fois : le surlignage les retrouve
  toutes). Toujours des découpes EXACTES du texte source (garantie
  anti-hallucination inchangée). Vérifié sur ABRICOT réel : « douleur
  thoracique » 8 mentions → 4 passages distincts.
- **Visualiseur multi-surlignages** (`DocumentViewer`) : toutes les
  occurrences de TOUS les passages sont surlignées (intervalles
  chevauchants fusionnés — « ulcère » imbriqué dans « ulcère bulbaire »
  compte une seule mention) ; un sélecteur « Mention k/N » avec boutons
  Précédent/Suivant fait défiler chaque mention au centre de la vue ; la
  mention courante est annoncée au lecteur d'écran (aria-live) ; « aucune
  occurrence trouvée » est signalée honnêtement si un passage a disparu du
  texte. Entrée à mention unique : comportement inchangé (surlignage +
  centrage automatique).
- **Éditeur** : le bouton devient « Voir les N passages sources → » dès
  qu'il y a plusieurs passages distincts.
- Tests : 254 verts (P2 étendu : passages distincts, cluster-pont 5 formes,
  bout-en-bout avec variante OCR).

## [1.2.4] — 2026-08-24

### Éditeur VSM : suppression d'une entrée

L'éditeur ne permettait que de CORRIGER les champs — impossible d'écarter
un faux positif de l'extraction (pathologie, traitement… qui n'a pas sa
place). Expérience utilisateur handicapante pour la relecture médicale.

- **Bouton « Supprimer » sur chaque entrée** des rubriques en liste
  (pathologies, antécédents, allergies, traitements, facteurs de risque,
  vaccinations, points de vigilance) : confirmation explicite avec la
  valeur de l'entrée, disparition immédiate, restitution au lecteur
  d'écran. Masqué sur un VSM signé (scellé = non modifiable).
- **La suppression n'est persistée qu'à l'enregistrement** (« Enregistrer
  • » reste le point de passage obligatoire) : une suppression accidentelle
  se rattrape en rechargeant la page tant qu'on n'a pas enregistré.
- Backend inchangé et testé : `/validate` remplace chaque rubrique par la
  liste envoyée (`update` par clé) — la persistance de la suppression est
  verrouillée par un test de non-régression (liste amputée persistée au
  rechargement, dernière entrée supprimée → rubrique vide valide).
- Tests : 254 verts (+1).

## [1.2.3] — 2026-08-24

### Règle de spécificité ATC + entrée Pantoprazole corrigée

Constat sur le VSM ABRICOT régénéré : « PANTOPRAZOLE [ATC A02BC01] » — le
code de l'OMÉPRAZOLE. Et application de la recommandation d'audit restante.

- **Entrée Pantoprazole ajoutée au référentiel ATC (A02BC02)** : absente du
  TSV, la molécule accrochait « Oméprazole » par appariement flou (~0,77 ≥
  seuil 70 du premier passage). Vérifié sur le document réel : PANTOPRAZOLE
  → A02BC02 (1,00), OMEPRAZOLE → A02BC01 (1,00).
- **Règle de spécificité ATC** (`normalizer`) : un libellé officiel portant
  des qualificatifs absents du terme extrait affirme PLUS que le texte —
  « insuline » → A10AE04 « Insuline glargine », « vitamine D » → A12AX
  « Calcium + vitamine D ». Le piège est structurel : token_set_ratio rend
  100 dès que les jetons du terme forment un sous-ensemble de ceux du
  libellé. La règle ne déclenque QUE sur ce cas exact (les matches flous
  < 1,00 — fautes d'OCR — gardent le filet « à vérifier » sous 0,85) :
  refus de la feuille, remontée au code parent du référentiel s'il existe
  sans qualificatif absent, sinon aucun code. Les alias parenthésés
  (« Aspirine » pour « Acide acétylsalicylique (Aspirine/Kardégic) ») et
  les termes couvrant le nom canonique avec posologie (« Metformine
  1000 mg ») passent : ils nomment le produit, pas son genre. Mots bénins
  curatés : « sodique » (« Lévothyroxine sodique » = la lévothyroxine).
  Périmètre ATC seul : les catégories CIM-10 REGROUPENT (I48 = fibrillation
  ET flutter auriculaires est le code correct d'une fibrillation seule) ;
  la règle y refuserait E78.0 « Hypercholestérolémie pure » sans corriger
  aucun faux — à réévaluer quand le référentiel s'enrichira de codes à
  point (stades N18.x).
- **Audit régénéré : 25/25 codes corrects parmi les attribués (100 %),
  0 faux** — les deux faux ATC (« insuline », « vitamine D ») sont
  maintenant des absences correctes ; reste le chantier séparé de
  l'enrichissement des référentiels.
- Tests : 253 verts (+4 : piège du sous-ensemble, alias/posologie,
  remontée parent, pantoprazole).

## [1.2.2] — 2026-08-24

### Journal des rejets + audit appliqué (seuil CIM-10 78, codes flous « à vérifier »)

Constat déclencheur : « maladie rénale chronique » et « MRC » absents du VSM
ABRICOT sans AUCUNE trace du responsable — à chaque fois, le problème n'était
pas le code mais l'absence d'observabilité.

- **Journal des rejets** (`filtres_vsm.tracer_rejet` / `finaliser_rejets`) :
  chaque entité écartée par UN filtre est tracée dans
  `provenance.nlp.rejets` ET dans le journal applicatif, au format fixe
  greppable —
  `rejet | page=3 | «maladie rénale chronique» | score=0.90 | regle=P1_page_non_prescriptive | detail=page 3 : reference`.
  Couvre TOUTE la chaîne : filtres de l'extracteur DrBERT (score, bords de
  mots, label test, anonymisation), P1 (page non prescriptive), validateur
  (chaque règle nommée : validateur_ancrage, _longueur, _blocklist,
  _classe_seule…), P4 (terme générique), P6 (en-tête répété). Les valeurs
  journalisées proviennent du texte anonymisé : aucune PII.
- **Mystère ABRICOT résolu** (`tools/repro_abricot.py`, reproduction hors
  ligne sur les 82 pages OCR) : « maladie rénale chronique » et « MRC »
  n'apparaissent que page 3 — un compte rendu de laboratoire (DFG/MDRD) dont
  le tableau de classification CKD imprime l'intitulé ; DrBERT les détecte
  bien (0,90 / 0,80), c'est P1 qui écarte la page (« reference ») — rejet
  CORRECT : c'est du matériel de référence, pas un diagnostic du patient.
- **Seuil flou CIM-10 relevé de 72 à 78** (`normalizer.SEUIL_CIM10`,
  recommandation d'audit appliquée) : frontière franche mesurée (faux
  ≤ 0,74 / corrects ≥ 0,80) — « maladie coronaire » et « maladie de
  Basedow » ne reçoivent plus de code. L'absence vaut mieux qu'un code faux.
- **Codes flous marqués « à vérifier »**
  (`pipeline.SEUIL_CODE_A_VERIFIER = 0,85`) : tout code CIM-10/ATC issu d'un
  appariement flou sous 0,85 porte `a_verifier` dans le VSM et s'affiche
  « code à vérifier (appariement flou) » dans l'éditeur. Audit régénéré :
  **0 code CIM-10 faux** (2 faux ATC restants = spécificité trompeuse,
  chantier séparé déclaré).
- **P6 sur-rejet documenté (NON corrigé, décision en attente)** : le journal
  révèle que P6 supprime de VRAIES pathologies — « ulcère bulbaire »
  (0,96, pages 8/62/73…) tué parce que la zone « jusqu'au premier titre
  ≤ 1200 » avale les antécédents restatés de chaque lettre hospitalière.
  Mesures : le vrai papier à lettres (« Maladies du Foie ») est à offset
  ≤ 168, les antécédents cliniques à ≥ 231 — un plafond de zone ~200 car.
  séparerait les deux ; la zone stricte 500 ne suffit PAS (« ulcère » reste
  dans les 500 premiers caractères de 12 pages).
- Tests : 249 verts (+6 : format de ligne, rejets par règle sur toute la
  chaîne, seuil 78, marquage à vérifier).

## [1.2.1] — 2026-08-24

### Fusion par code désactivée + audit du normalisateur (CIM-10/ATC)

Constat sur les VSM réels : « maladie rénale chronique » et « maladie
coronaire » fusionnaient — le normalisateur attribue N18 aux DEUX (confiance
0,73). Un code faux ne doit jamais décider d'une fusion.

- **Fusion par code CIM-10/ATC DÉSACTIVÉE** dans `filtres_vsm.dedupliquer`
  (P2) : seules les passes TEXTE fusionnent (forme normalisée, similarité
  ≥ 88) — elles comparent le texte réel. Prix assumé : les synonymes purs
  (« HTA » / « hypertension artérielle ») ne fusionnent plus tant que le
  normalisateur n'est pas corrigé.
- **Test de non-régression** : « maladie rénale chronique » et « maladie
  coronaire » ne fusionnent JAMAIS, même si le normalisateur leur attribue
  le même code.
- **Audit du normalisateur** (`tools/audit_normalisateur.py`, régénérable →
  `outputs/AUDIT_NORMALISATEUR.md`) : échantillon de 48 entités réalistes
  (dossiers réels + cas synthétiques), verdicts manuels vérifiables.
  **Réponse : 86 % de codes corrects parmi les attribués (24/28), 4 faux** :
  « maladie coronaire » → N18 (rénal !) et « maladie de Basedow » → G20
  (Parkinson) par seuil flou trop bas (72 — tous les faux ≤ 0,74, tous les
  corrects ≥ 0,80) ; « insuline » → glargine et « vitamine D » → calcium +
  vitamine D par spécificité trompeuse. Cause racine dominante : référentiel
  trop petit (35 CIM-10, 38 ATC) — 22/48 entités sans code (absence bénigne).
  Recommandations (NON implémentées, à valider) : relever le seuil à 78-80,
  enrichir les référentiels, règle de spécificité ATC, afficher la confiance
  de normalisation < 0,85 comme « à vérifier ».
- Tests : 243 verts (+3 : fusion par code désactivée, non-régression
  rénale/coronaire, garde-fou latéralité).

## [1.2.0] — 2026-08-24

### Filtres VSM — analyse de deux dossiers réels (correctifs P1-P7)

Analyse de deux VSM réels produits par DrBERT (75 pages, 20 ans de suivi,
nominatifs) : l'extraction était correcte mais le CLASSEMENT et le VOLUME
posaient problème (199 éléments pour l'un, ~290 pour l'autre). Sept
correctifs priorisés — P1 clinique, P2 lisibilité, le reste de la finition —
dans le nouveau module `src/extraction_nlp/filtres_vsm.py` (fonctions
pures, docstrings, sans dépendance circulaire).

- **P1 — Pages non prescriptives écartées AVANT les rubriques** (risque
  clinique : 40 antibiotiques d'un ANTIBIOGRAMME présentés comme traitements
  au long cours, « maladie des griffes du chat » d'une fiche de référence en
  pathologies). Signaux : vocabulaire d'antibiogramme (CMI, souche,
  S/I/R…), fiches de référence (notice, valeurs de référence…), densité
  anormale (> 10 traitements/page). **Chaque rejet est journalisé** dans le
  rapport NLP (`pages_ecartees` : page, motif, entités supprimées) — un
  rejet en masse doit être auditable.
- **P2 — Déduplication sémantique par rubrique** (143 pathologies dont ~30
  distinctes) : 3 passes — même code CIM-10/ATC → même forme normalisée →
  similarité rapidfuzz `token_set_ratio ≥ 88` (absorption des fautes d'OCR
  par chaînage de clusters). Latéralité droite/gauche bloquée (kyste de
  l'ovaire droit ≠ gauche), familles distinctes jamais fusionnées (kyste
  hépatique / urétral). Représentant = forme la plus fréquente (jamais la
  plus longue) ; chaque entrée fusionnée porte `occurrences` et `pages`
  (« Ulcère bulbaire linéaire — 15 mentions, pages 8 à 62 »), affichés dans
  l'éditeur VSM.
- **P3 — Actes chirurgicaux → antécédents** (CASM2 les étiquette
  « treatment ») : liste de mots (excision, pose de stent, biopsie…) et
  suffixes (-ectomie, -otomie, -oplastie…) ; « cholécystectomie
  rétrograde » reconnue en sous-chaîne.
- **P4 — Termes trop génériques isolés rejetés** (« douleur », « kyste »,
  « traitement médical » sans qualificatif) : ni où, ni quand, ni pourquoi.
- **P5 — Facteurs de risque par LISTE FERMÉE sur l'entité** (tabac, alcool,
  obésité, ménopause, exposition professionnelle…) : l'ancienne règle
  CONTEXTUELLE routait des diagnostics entiers (sténose urétrale,
  Trichomonas…) — une seule entrée juste sur neuf dans les dossiers réels ;
  tout ce qui n'y figure pas retourne en pathologies actives.
- **P6 — En-tête de cabinet supprimé** (« Maladies du Foie », papier à
  lettres) : zone d'en-tête étendue à 500 caractères ou jusqu'au premier
  titre de rubrique (borne 1200) ; une forme présente dans l'en-tête de
  PLUS de 3 pages est du papier à lettres, pas un diagnostic.
- **P7 — Posologies accolées aux traitements** (« OGAST » → « OGAST 1
  gél/j en permanence ») : l'empan s'étend vers la droite tant que le texte
  est une posologie (≤ 60 caractères) — **découpe du texte source
  uniquement, la garantie anti-hallucination tient** (vérifié E2E :
  passage = valeur).
- **Carte des pages** : frontières reconstruites par longueurs cumulées du
  join `\n\n` (approximative — les tokens de pseudonymisation peuvent
  diverger par page) ; texte non reconstruisable → filtres par page
  désactivés proprement. Chaque entité porte sa page dans
  `source.page` (XAI).
- **Garde-fous** : `valider_sortie(dedup_exact=False)` pour la chaîne
  DrBERT (les répétitions sont FUSIONNÉES avec comptage au lieu d'être
  jetées en silence) ; `rubrique_de()` gagne le paramètre `entite` (les
  règles P3/P5 portent sur l'entité, pas sur son contexte).
- **Tests** : +35 (un par correctif, cas réels mesurés + intégration
  `run_pipeline` multi-pages) → **242 tests verts** ; frontend build OK ;
  E2E vrai modèle OK (P3/P7 visibles : « pose de stent » → antécédents,
  « Metformine 1000 mg matin »).

## [1.1.0] — 2026-08-22

### DrBERT-MedicalNER-FR — NER médical français (extraction d'entités, CPU léger)

- **Nouveau moteur** (`src/extraction_nlp/drbert.py`) : NER de
  token-classification de **DrBERT-MedicalNER-FR** (CamemBERT biomédical).
  Très léger (CPU ≤ 1 Go, ~500 Mo fp32 / ~150 Mo quantizé) → **tourne même sur
  les postes 4-8 Go sans GPU** ; spécialisé français médical.
- **Automatiquement complémentaire** (`entity_extractor.py`) : `_augment_with_drbert`
  ajoute les entités **manquantes** (non destructif, dédupliqué) en aval du
  moteur **règles** **ou** **LLM** — le rappel est amélioré sur les documents
  non rubriqués. Provenance tracée : `moteur_nlp="drbert-nlp-v1"`.
- **Regroupement au niveau du mot** (correction d'un bug de fragmentation) :
  le NER est entraîné par mot et répète « B- » sur les sous-mots → agrégation
  sous-mots→mots (`word_ids` + `offset_mapping`) puis regroupement basé sur
  l'étiquette (B-/I- ignorés). « Metformine » n'est plus scindé en fragments ;
  les **offsets caractères sont précis** (le surlignage « Voir le passage
  source » fonctionne).
- **Confiance réelle** : probabilité softmax du label (sous `DRBERT_CONFIDENCE`
  = 0,7 → « À valider »), pas une constante.
- **Licence** : base **Apache 2.0** (propre) ; checkpoint en licence « style
  OpenRAIL » **personnalisée** → **compatible annexe 1** (usage commercial
  permis, pas de clause interdisant l'usage médical ; disclaimer de validation
  à respecter). Verdict détaillé dans `docs/ADR/0010-drbert-extraction.md` et
  `outputs/AUDIT_DRBERT.md` (§2a) ; brique isolée pour un repli sans impact.
- **Dépendances** : `torch` (CPU) + `transformers >=4.53,<5` (épinglé — la 5.x
  casse le tokenizer CamemBERT). Téléchargement du modèle **à l'installation**
  (`python -m src.extraction_nlp.drbert`), jamais au traitement (art. 9).
- **Conformité** : art. 9 (100 % local, modèle à l'installation) ; art. 7
  (rappel d'extraction, XAI conservée avec confiance et moteur tracés).
- **Tests** : +8 (regroupement BIO, mapping étiquettes→VSM, contexte
  antécédent, disponibilité, augmentation dédupliquée) → **120 tests verts**.

## [1.0.1] — 2026-08-20

### Corrections d'audit (concours IA & Santé — rapport outputs/AUDIT_CONFORMITE_RAPPORT.md)

- **Anonymisation (critique)** : le détecteur PII ne masquait pas les noms sur
  les documents réels (« Monsieur ABRICOT Anthony », « Mme BANANE Sophie »).
  Corrections dans `src/anonymization/pii_detector.py` :
  - insensibilité à la casse des titres (`(?i:…)`, limitée au titre — les
    titres sont capitalisés dans les vrais documents) ;
  - support du format « NOM-en-MAJUSCULES + prénom » (CR de laboratoire) ;
  - titres étendus : `MR`, `M`, `Bénéficiaire`, `Prénom` ;
  - repli « ligne d'identité » pour les variantes OCR (« ABRICOT Antrony ») ;
  - contextes de naissance `DDN`/`DON`/`naissance`/`netssance` et recherche
    par ligne (dates répétées dans les tableaux) ; années à 2 chiffres ;
  - `NUMERO_DOSSIER` : format « Demande n° X » ; stopwords laboratoire.
- **Remplacement par valeur** (`pseudonymizer.py`, `anonymizer.py`) : une PII
  détectée quelque part dans le document est masquée partout (passe 2).
- **Export HTML** (`src/ui_backend/main.py`) : les GET/HEAD de lecture sont
  exemptés du token CSRF (cookie SameSite=Strict) — le lien « Exporter (HTML) »
  fonctionne (403 → 200).
- **Identité VSM** (`src/extraction_nlp/pipeline.py`) : les blocs `patient` et
  `medecin_traitant` sont remplis depuis les tokens de pseudonymisation.
- **Dataset** (`generate_dataset.py`) : cas_006 (CR de laboratoire) et cas_007
  (compte-rendu d'anapath) aux formats réels ; police de repli Windows.
- **Tests** : 65 tests (21 ajoutés : formats réels, passe 2, CSRF, identité).

### Format VSM conforme au gabarit HAS (aligné sur des exemples de vrais VSM)

- **Rendu** (`src/vsm_generation/renderer.py`) : le document généré (markdown,
  HTML, PDF) reprend la structure d'un vrai Volet de Synthèse Médicale :
  1. Identification du patient (pseudonymisée) · 2. Médecin traitant ·
  3. Pathologies actives · 4. Antécédents · 5. Allergies · 6. Traitements au
  long cours · 7. Facteurs de risque · 8. Vaccinations · 9. Points de
  vigilance — avec date de génération, statut, avertissement médical,
  badges de confiance XAI, zone de signature et empreinte SHA-256.
- **Identité enrichie** (`src/extraction_nlp/pipeline.py`) : sexe (« Sexe : H/F/
  Masculin/Féminin ») et ADELI ajoutés aux blocs patient/médecin traitant ;
  schéma VSM (`schema/vsm_schema.json`) : `medecin_traitant.adelI`.
- **Extraction** (`entity_extractor.py`) : les lignes « CONCLUSION /
  OBSERVATIONS » clôturent la dernière rubrique au lieu d'y être rattachées
  (qualité de la synthèse).
- **Tests** : 69 tests (rendu HAS numéroté, blocs d'identité, absence de PII
  en clair dans les rendus, non-pollution CONCLUSION).

### Repli « texte libre » — extraction sur documents non rubriqués (P0-4)
- `entity_extractor.py` : nouvelle passe de repli, déclenchée **par rubrique**
  uniquement si l'extraction par en-têtes ne l'a pas remplie (aucun doublon) :
  - « CONCLUSION : … » → **Points de vigilance** (segment borné, bruit filtré) ;
  - « Dans ses antécédents on note : », « Sur le plan (médical/chirurgical/
    familial/…) » → **Antécédents** ;
  - « traitement par X », molécule du référentiel ATC + dosage → **Traitements
    au long cours** ;
  - « allergie à X » (négations « aucune/pas de/sans » exclues) → **Allergies** ;
  - facteurs de risque (tabac, alcool, obésité, sédentarité…) → **Facteurs de
    risque** ; vaccinations (rappel/année) → **Vaccinations** ; diagnostics
    CIM-10 en lignes courtes (match fuzzy) → **Pathologies/antécédents**.
  - Confiances volontairement basses (0,55–0,8) → champs « À valider » par le
    médecin (XAI).
- Résultat sur les vrais dossiers fournis : le VSM de BANANE contient désormais
  antécédents, traitement et conclusions cliniques (avant : vide) ; aucun bruit
  (signatures, pagination) capturé.
- **Tests** : 73 (repli texte libre, bornage, négations d'allergie, non-doublon
  avec les en-têtes).

### Upload de gros documents (> 50 Mo)

- `src/ui_backend/main.py` : la limite d'upload est **configurable** via
  `VSM_MAX_UPLOAD_MB` (Mo, défaut 50) ; le téléversement est lu **par blocs de
  1 Mo** (un fichier trop volumineux est rejeté en cours de flux, sans être
  chargé en mémoire) ; message d'erreur explicite. La limite est exposée par
  `GET /health` (`max_upload_mb`).
- `frontend/src/pages/Dashboard.tsx` : la limite affichée dans l'UI reflète la
  valeur configurée côté serveur.
- **Tests** : 75 (limite configurable — rejet 413 au-delà, acceptation en
  dessous — et exposition via /health) ; docs SECURITY.md/README mises à jour.

### Export PDF du VSM

- `src/ui_backend/main.py` : nouvel export `GET /vsm/{id}/export?fmt=pdf` —
  génération 100 % locale (ReportLab) puis réponse `application/pdf`
  téléchargeable (`Content-Disposition: attachment`).
- `frontend` : bouton « Exporter (PDF) ↓ » dans l'éditeur VSM, à côté de
  « Exporter (HTML) ↗ » ; nouvelle URL `api.exportPdfUrl`.
- **Tests** : 76 (export PDF : statut 200, type `application/pdf`, en-tête
  `%PDF`, contenu > 1 Ko ; format inconnu → 400).

### LLM local (extraction optionnelle, 100 % offline)

- **Audit des modèles** (`docs/ADR/0004-llm-local-choix-modele.md`) : critères
  concours (licence annexe 1, RGPD/offline art. 9, RAM 16 Go, français,
  JSON) → **recommandation : Mistral NeMo 12B Instruct Q4_K_M (Apache 2.0)**,
  repli 8 Go : Qwen 2.5 7B Instruct.
- `src/extraction_nlp/llm.py` : catalogue des modèles (métadonnées
  licence/taille/RAM), `download_model()` (GGUF validé par signature, hors
  flux de traitement), CLI `python -m src.extraction_nlp.llm --list/--model`.
- `entity_extractor.py` : adaptateur LLM renforcé (prompt système, parsing
  JSON tolérant, confiance conservative 0,65 → « À valider », XAI).
- API : `ProcessIn.nlp_engine` (`rules`|`llm`, validation stricte) ; frontend :
  sélecteur « Extraction : Règles / LLM local » ; provenance XAI tracée
  (`moteur_effectif`, repli inclus).
- **Tests** : 86 (10 ajoutés : config, métadonnées, repli, parsing JSON, API).

### Moteur OCR optionnel « Unlimited-OCR » (baidu, licence MIT — GPU NVIDIA)

- Étude de https://github.com/baidu/Unlimited-OCR (OCR documentaire panoptique
  en une passe, marqueurs de structure `<|det|>`) → ADR-0005.
- `src/ingestion_ocr/ocr_engines.py` : adaptateur `UnlimitedOCREngine`
  (torch/transformers, strictement local, sortie nettoyée des marqueurs).
- **Exigence carte NVIDIA** : sans `torch.cuda.is_available()`, le moteur
  **n'existe pas** (absent de `ENGINES`, de `/health`, de l'UI ; API → 400).
- `GET /health` expose `available_engines` ; sélecteur OCR dans l'UI
  (n'affiche « Unlimited-OCR (NVIDIA) » que si disponible).
- **Tests** : 93 (+5 : nettoyage marqueurs, gating GPU, rejet API, /health).
- Recette sur poste NVIDIA documentée (benchmark CER/WER français) —
  `outputs/AUDIT_OCR_UNLIMITED.md` ; Tesseract+fra reste le défaut.

### Accessibilité — page « Paramètres » (malvoyants, aveugles, clavier)

- Audit (outputs/AUDIT_ACCESSIBILITE.md) → **fonctionnalités validées** :
  A1 taille de texte (100/112,5/125 %) · A2 contraste renforcé (AAA) ·
  A3 thème sombre · A4 police **Atkinson Hyperlegible embarquée** (SIL OFL,
  aucun CDN) · A5 animations réduites · B1 mode lecteur d'écran (annonces
  `aria-live`) · B2 **focus trap** Palette/aide corrigé (WCAG 2.4.3) ·
  C1 raccourcis `?` (aide) et `Ctrl+,` (Paramètres) · D1 persistance
  `localStorage` + réinitialisation + détection préférences système.
- Fichiers : `frontend/src/accessibility.ts`, `pages/Accessibility.tsx`,
  `App.tsx`, `index.css`, `AuditSettings.tsx`, `Dashboard.tsx`, `VSMEditor.tsx`.
- Conformité : art. 7 (ergonomie 10 %), RGPD neutre (préférences locales,
  aucune donnée patient), 100 % local ; backend inchangé (93 tests verts).
- ADR-0006 ; checklist de recette D3 dans le rapport d'audit.

### Statistiques anonymes — visualisations locales (POC validé)

- Audit de faisabilité (règlement art. 9 + RGPD/CNIL + HAS) → **POC implémenté**.
- `GET /stats` (`src/ui_backend/main.py`) : nb VSM (total/statut/période),
  récurrences **CIM-10** (pathologies) et **ATC** (traitements), complétude —
  agrégats uniquement, **sans jamais lire le coffre de mapping** (aucun lien
  identité), **masquage n < 5** (secret statistique CNIL, seuil configurable
  `VSM_STATS_MIN_COUNT`), **recalcul à la demande** (le droit à l'oubli
  met à jour les stats — testé), aucun croisement externe, avertissement
  « descriptif, non représentatif ».
- Frontend : page « Statistiques » (cartes + graphiques **SVG maison**, aucun
  CDN) — `pages/Stats.tsx`, entrée de navigation.
- ADR-0007 ; rapport : `outputs/AUDIT_STATISTIQUES.md`.
- **Tests** : 96 (+3 : agrégats/masquage, absence de détail patient, oubli).

### Logging structuré local + redaction (audit FastAPI/logs validé)

- Audit de faisabilité (outputs/AUDIT_FASTAPI_LOGS.md) — FastAPI est confirmé
  comme backend (uvicorn 127.0.0.1, `/audit` existant, docs désactivés).
- `src/ui_backend/logging_setup.py` : fichiers `<VSM_DATA_DIR>/logs/app.log`
  (rotation 1 Mo × 5, niveau `VSM_LOG_LEVEL`), **filtre de redaction**
  systématique (NIR, téléphone, email, RPPS/ADELI, tokens de pseudonymisation
  masqués dans TOUTE entrée — défense en profondeur), **aucun handler réseau**
  (art. 9 : pas de logs cloud).
- Points de log sans PII dans `main.py` : démarrage, upload, traitement
  (moteur OCR/NLP, nb PII), changement de statut VSM, export, erreur moteur.
- **Tests** : 102 (+6 : écriture/rotation, idempotence, absence de handler
  réseau, redaction des PII, application à l'écrit).

### Traitement asynchrone des documents (correction « temps infini »)

- **Problème** : le traitement (OCR) s'exécutait dans la requête HTTP → sur
  les gros PDF, attente sans progression ; au-delà de ~15 min, la session
  expirait pendant le traitement → le rafraîchissement échouait (401) et le
  VSM n'apparaissait qu'après reconnexion.
- **Correction** : `POST /documents/{id}/process` répond **immédiatement**
  (`job_id`, statut 202) ; le pipeline s'exécute dans un **thread d'arrière-
  plan** ; `GET /documents/process/{job_id}` expose la **progression** puis le
  résultat (ou l'erreur). Chaque interrogation « touche » la session et la
  clé de chiffrement → **aucune expiration pendant le traitement** ; la clé
  est aussi rafraîchie avant les écritures chiffrées du job.
- Frontend : l'UI interroge l'état toutes les 2 s (avec étape affichée), puis
  rafraîchit la liste — **le VSM apparaît sans reconnexion** ; si l'onglet est
  fermé, le job se termine en arrière-plan et le VSM est présent au
  prochain accès.
- **Tests** : 103 (+1 : flux asynchrone processing → done, VSM visible sans
  nouvelle session).

### Correction « Voir le passage source » (incohérence d'identifiants)

- **Problème** : `run_pipeline()` générait son propre `document_id` interne à
  chaque exécution, différent de l'id du document uploadé → le VSM référençait
  un id fantôme (`source.document_id`) → `GET /documents/{id}/ocr` renvoyait
  404 dans le visualiseur. Bug latent depuis l'origine.
- **Correction** : `run_pipeline(document_id=…)` accepte l'id externe à
  préserver ; le job de traitement lui passe l'id d'upload. `source.document_id`
  == id uploadé == clé de stockage OCR → le surlignage fonctionne.
- **Tests** : 104 (+1 : cohérence des ids + passage retrouvable via
  `/documents/{id}/ocr`).

### Visualiseur : défilement automatique vers le passage source

- `DocumentViewer.tsx` : au chargement, le premier `<mark>` est amené **au
  centre de la vue** (`scrollIntoView`, douceur adaptée à
  `prefers-reduced-motion`) — plus besoin de chercher le surlignage dans le
  bloc ; le passage est aussi entouré d'un liseré ambre (`outline`) pour le
  rendre immédiatement visible.

### Veille technologique — modèles de compréhension de documents (ADR-0008)

- Comparaison LayoutLM / LayoutLMv2 / LayoutXLM / LayoutLMv3 (Microsoft, MIT,
  encodeurs, CPU) vs **LFM2-VL** (Liquid AI, vision-langage, GPU,
  ⚠️ **LFM Open License v1.0** custom) — licences vérifiées via l'API HF.
- **Décision validée : ne pas intégrer pour la soutenance.** Famille LayoutLM
  écartée (anglais, fine-tuning FR médical indispensable, hors périmètre) ;
  **LFM2-VL** écarté (licence custom → risque annexe 1 ; doublon d'Unlimited-OCR
  MIT) ; **LayoutXLM** documenté comme **piste P2** (fine-tuning FR médical
  futur) au dossier scientifique. Veille : `outputs/AUDIT_LLM_DOCMODELS.md`.

### LLM local PAR DÉFAUT sur toutes les machines (ADR-0009)

- **Audit petits LLM** (< 8 Go, sans GPU) : Qwen 2.5 3B/1.5B (Apache 2.0) vs
  SmolLM2, Llama 3.2, Gemma 2, Phi-3.5 → **défaut universel : Qwen 2.5 3B
  Q4_K_M (~2 Go, Apache 2.0, CPU, ≥ 4 Go)** ; repli **Qwen 2.5 1.5B** (< 4 Go).
- **Système de prompt efficace** (`build_llm_messages`) : rôle, schéma JSON
  strict (7 rubriques), **anti-hallucination** (« n'invente rien », rubrique
  absente → []), normalisation (orthographe + dosage), négations, pseudonymes
  exclus des valeurs, **few-shot** (exemple complet), troncature au contexte.
- **Non optionnel** : `ProcessIn.nlp_engine` défaut **« llm »** ; l'UI n'affiche
  plus le choix Règles/LLM ; `/health` expose `llm_available` (alerte si modèle
  absent) ; le moteur **règles reste le repli automatique** (testé).
- Conformité : art. 9 (offline, modèle à l'installation), annexe 1 (Apache 2.0),
  art. 7 (extraction améliorée, XAI conservée).
- **Tests** : 108 (+4 : prompt system, troncature, santé LLM, défaut + repli).

### Diagnostic « traitement infini / À valider vide » — garde-fou RAM LLM

- **Cause** : le LLM étant par défaut, llama.cpp tentait de charger le modèle
  (~2 Go) même avec très peu de RAM libre → swap/blocage (traitement « infini »)
  ou échec du job (aucun VSM → liste « À valider » vide).
- **Correction** : `llm_feasible()` dans `src/extraction_nlp/llm.py` —
  le LLM n'est lancé que si le modèle existe ET la **RAM disponible** (mesurée
  en direct) couvre le modèle + une marge de 1,5 Go ; sinon **repli automatique
  sur les règles** (aucune tentative llama.cpp) avec raison tracée.
- `/health` expose `llm_available` + `llm_reason` ; l'UI affiche la raison
  exacte (modèle absent ou RAM insuffisante).
- **Tests** : 110 (+2 : garde-fou RAM, LLM sauté quand infaisable).

### Correction « VSM vide » : découpage PDF par lots + hybride LLM→règles

- **Problème (captures du dossier bug)** : l'identité était extraite mais
  toutes les rubriques cliniques restaient vides. Causes : (1) sur les gros
  PDF, `convert_from_path` chargeait TOUTES les pages en mémoire → OOM/swap →
  pages vides ; (2) un LLM dégradé (petit modèle quantizé sous pression
  mémoire) pouvait renvoyer un JSON tout « [] ».
- **Correction** :
  - `src/ingestion_ocr/pipeline.py` : conversion/OCR des PDF **par LOTS de
    pages** (défaut 20, configurable `VSM_OCR_PDF_BATCH`) — mémoire bornée,
    numéros de page réels conservés ;
  - `entity_extractor.py` : **hybride** — si le LLM renvoie une sortie vide,
    les règles prennent le relais (elles peuvent trouver du contenu omis) ;
    si le LLM trouve du contenu, il est conservé.
- **Tests** : 113 (+3 : découpage en lots avec numéros de page, hybride
  vide→règles, LLM non vide conservé).

### LLM tenté sur TOUTES les machines (même lentes) — remplacement de la barrière RAM

- **Problème** : le garde-fou RAM (marge 1,5 Go) bloquait le LLM sur les postes
  à 8 Go → message « LLM indisponible : RAM insuffisante » alors que l'exigence
  est un **LLM sur toutes les machines**.
- **Correction** : le LLM est **toujours TENTÉ** dès que le modèle est présent
  (`llm_attemptable` = modèle présent ; la RAM ne bloque plus). La RAM faible
  ne produit qu'un **avertissement non bloquant** (`llm_ram_warning`).
  Pour éviter le blocage infini sur machines lentes : **timeout d'inférence**
  (`VSM_LLM_TIMEOUT_SEC`, défaut 300 s) — au-delà, repli règles + drapeau
  global qui ne re-tente pas le LLM dans la session (évite d'empiler des
  chargements de 2 Go). Hybride conservé (sortie vide → règles).
- `/health` : `llm_available` = modèle présent ; `llm_reason` = avertissement
  (peut être lent). UI : message « non téléchargé » si absent, « ℹ en cours
  d'utilisation — peut être lent » sinon.
- **Tests** : 114 (+1 timeout ; tests de la nouvelle sémantique).

### Correction « Document illisible : No module named 'pypdf' »

- Le découpage par lots utilisait `pypdf` pour compter les pages — absente de
  Python 3.12 (l'environnement LLM) → erreur au traitement d'un PDF.
- **Correction** : suppression de la dépendance — la fin du document est
  détectée quand un lot est vide (pdf2image retourne `[]` au-delà de la
  dernière page) ; l'erreur poppler de conversion de lot est rattachée à
  « Document illisible ». Seule `pdf2image` (déjà requise) est utilisée.
- **Tests** : 114 (découpage en lots validé sans pypdf).

## [1.0.0] — 2026-06-12

### Phase 1 — Anonymisation
- `src/anonymization/` : détecteur PII hybride (13 types : noms, NIR, INS,
  RPPS, ADELI, FINESS, dates naissance/décès, tél, email, adresse, n° dossier,
  n° séjour), pseudonymiseur à tokens stables, anonymiseur strict, coffre-fort
  de mapping chiffré (AES-256-GCM / Argon2id, `forget()` = droit à l'oubli),
  audit sans PII. Adaptateur spaCy optionnel.

### Phase 2 — Pipeline OCR
- `pipeline.run_pipeline()` : PDF/image → JSON contractuel (sha256,
  anonymisation `pseudo`/`strict` intégrée, tolérance pages corrompues,
  rapport de traitement).
- Benchmark CER/WER (jiwer) avec/sans preprocessing → `outputs/benchmark.csv`
  + `BENCHMARK_REPORT.md`. Deskew rendu robuste (vérification du gain de
  profil, choix du sens empirique).
- Dataset synthétique reproductible (5 cas × 4 dégradations, seed 42).

### Phase 3 — Extraction NLP
- Extraction par rubriques (règles, offline) + adaptateur LLM local optionnel ;
  normalisation CIM-10/ATC (rapidfuzz) avec parsing dosage/fréquence ;
  sortie conforme `schema/vsm_schema.json` (v1.1.0).

### Phase 4 — Génération VSM
- Assemblage ordre canonique HAS, complétude par section, validation
  jsonschema, avertissement médical obligatoire ; rendus markdown / HTML / PDF
  (ReportLab) avec badges de confiance, fond distinct « À valider », zone de
  signature.

### Phase 5 — Stockage chiffré
- SQLite + AES-256-GCM champ par champ (AAD), `SessionKey` Argon2id avec
  timeout 15 min et zéroïsation, auth multi-rôles avec verrouillage,
  audit log chaîné par hash (`verify_audit_chain`), `delete_dossier()`.

### Phase 6 — UI
- Backend FastAPI 127.0.0.1:8741 (cookies httpOnly SameSite=Strict + CSRF,
  Pydantic partout) ; endpoints auth/documents/vsm/audit/oubli/export.
- Frontend React+TS+Vite+Tailwind (style shadcn, WCAG AA) : Login, Dashboard,
  DocumentViewer (surlignage source XAI), VSMEditor (9 rubriques, badges
  confiance, signature médecin), AuditTrail, Settings ; raccourcis Ctrl+K,
  Tab, ↵, Ctrl+↵.
- Wrapper Tauri 2 (backend en sous-process, bundles msi/AppImage/deb, sans
  télémétrie ni auto-update).

### Phase 7 — Docs & CI
- README, USER_MANUAL, ANONYMIZATION, COMPLIANCE, SECURITY, 3 ADR (MADR),
  gabarit VSM ; CI GitHub Actions (pytest, ruff, bandit, pip-audit, garde-fou
  anti-PII, build frontend, build Tauri sur tag) ; 44 tests verts ; E2E validé.
