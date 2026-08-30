"""Tests des filtres VSM (correctifs P1-P7, analyse de dossiers réels).

Chaque correctif est né de l'analyse de deux VSM réels produits par DrBERT
(dossiers « BANANE » et « ABRICOT », 75 pages, 20 ans de suivi) :
- P1 : 40 antibiotiques d'un ANTIBIOGRAMME présentés comme traitements au
  long cours — risque clinique ;
- P2 : 143 pathologies dont ~30 distinctes (le même ulcère décrit à chaque
  compte rendu avec les fautes d'OCR propres à chaque page) ;
- P3 : actes chirurgicaux (excision, cholécystectomie…) en traitements ;
- P4 : « douleur » sans qualificatif ;
- P5 : une seule entrée facteurs de risque juste sur neuf ;
- P6 : « Maladies du Foie » (papier à lettres) en pathologies actives ;
- P7 : « OGAST » au lieu d'« OGAST 1 gél/j en permanence ».
"""

from __future__ import annotations

from types import SimpleNamespace

from src.extraction_nlp.filtres_vsm import (
    SEUIL_SIMILARITE,
    carte_pages,
    dedupliquer,
    entites_hors_pages_rejetees,
    est_facteur_risque,
    est_trop_generique,
    est_un_acte,
    etendre_posologie,
    formes_en_tete_repetees,
    ligne_rejet,
    page_de,
    page_non_prescriptive,
    pages_non_prescriptives,
    tracer_rejet,
    zone_en_tete,
)
from src.extraction_nlp.rubriques import rubrique_de


def _ent(label: str, texte: str, debut: int, fin: int | None = None):
    """Fausse entité DrBERT (label + offsets), pour les filtres par page."""
    return SimpleNamespace(label=label, texte=texte, debut=debut,
                           fin=fin if fin is not None else debut + len(texte))


# ---------------------------------------------------------------------------
# P1 — pages non prescriptives
# ---------------------------------------------------------------------------


def test_p1_page_antibiogramme_rejetee():
    """Une page d'antibiogramme (S/I/R, CMI, souche) n'est pas clinique."""
    page = (
        "ANTIBIOGRAMME - souche Escherichia coli\n"
        "CMI (mg/L) - S / I / R\n"
        "PENICILLINE : resistant\nCEFOTAXIME : sensible\n"
        "VANCOMYCINE : intermediaire\n"
    )
    motif = page_non_prescriptive(page, [])
    assert motif == "antibiogramme"


def test_p1_page_reference_rejetee():
    """Une fiche de référence (maladie des griffes du chat) n'est pas clinique."""
    page = (
        "Maladie des griffes du chat - fiche technique\n"
        "Rochalimaea henselae, angiomatose bacillaire.\n"
        "Valeurs de référence et rappel épidémiologique pour information.\n"
    )
    assert page_non_prescriptive(page, []) == "reference"


def test_p1_densite_traitements_rejetee():
    """40 molécules sur une page = tableau de laboratoire, pas une ordonnance."""
    page = "Bilan microbiologique du prélèvement.\n"
    entites = [_ent("treatment", f"MOL{i}", i * 10) for i in range(12)]
    motif = page_non_prescriptive(page, entites)
    assert motif and motif.startswith("densite")


def test_p1_page_clinique_conservee():
    """Une page de consultation normale passe (3 traitements, pas de vocabu-
    laire de laboratoire)."""
    page = (
        "Consultation du jour. Diabète de type 2 équilibré.\n"
        "Traitement : Metformine, Ramipril, Atorvastatine.\n"
        "Poursuite du suivi habituel.\n"
    )
    entites = [_ent("treatment", "Metformine", 50),
               _ent("problem", "Diabète de type 2", 30)]
    assert page_non_prescriptive(page, entites) is None


def test_p1_journal_et_filtrage_des_pages():
    """Le rejet en masse est JOURNALISÉ (page, motif, nb d'entités) et les
    entités de la page écartée disparaissent — pas celles des autres pages."""
    pages_ocr = [
        {"page": 1, "text": "Consultation. Ulcère bulbaire en cours."},
        {"page": 2, "text": "ANTIBIOGRAMME souche : CMI S/I/R pénicilline."},
        {"page": 3, "text": "Traitement : Metformine poursuivi."},
    ]
    texte = "\n\n".join(p["text"] for p in pages_ocr)
    carte = carte_pages(texte, pages_ocr)
    entites = [
        _ent("problem", "Ulcère bulbaire", 15),
        _ent("treatment", "PENICILLINE", 60),
        _ent("treatment", "CEFOTAXIME", 80),
        _ent("treatment", "Metformine", 110),
    ]
    rejets = pages_non_prescriptives(entites, carte)
    assert len(rejets) == 1
    assert rejets[0]["page"] == 2
    assert rejets[0]["motif"] == "antibiogramme"
    assert rejets[0]["entites_supprimees"] == 2
    restantes = entites_hors_pages_rejetees(entites, carte)
    assert [e.texte for e in restantes] == ["Ulcère bulbaire", "Metformine"]


def test_carte_pages_invalide_desactive_les_filtres():
    """Un texte non reconstruisable (écart majeur) → carte vide → filtres
    par page désactivés proprement (dégradation sûre)."""
    pages_ocr = [{"page": 1, "text": "texte court"}]
    assert carte_pages("un texte complètement différent et bien plus long "
                       "que la somme des pages", pages_ocr) == []


# ---------------------------------------------------------------------------
# P2 — déduplication sémantique
# ---------------------------------------------------------------------------


def _champ(valeur: str, page: int | None = None, code: str | None = None,
           confiance: float = 0.9) -> dict:
    champ = {
        "valeur": valeur,
        "confiance": confiance,
        "source": {"page": page, "passage": valeur},
    }
    if code:
        champ["code_normalise"] = {"systeme": "CIM-10", "code": code}
    return champ


def test_p2_fusion_par_forme_et_occurrences():
    """« ulcère », « ulcère bulbaire » ×2 → 1 entrée « ulcère bulbaire » (la
    forme la plus fréquente), avec occurrences=3 et pages."""
    champs = [
        _champ("Ulcère bulbaire", page=8),
        _champ("ulcère", page=12),
        _champ("Ulcère bulbaire", page=62),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 1
    assert fusion[0]["valeur"] == "Ulcère bulbaire"  # la plus fréquente
    assert fusion[0]["occurrences"] == 3
    assert fusion[0]["pages"] == [8, 12, 62]
    # Passages DISTINCTS des mentions, dans l'ordre du document : le
    # visualiseur surligne puis fait DÉFILER chaque mention. La forme
    # répétée à l'identique n'est gardée qu'une fois (le surlignage la
    # retrouve seule dans le texte).
    assert fusion[0]["passages"] == ["Ulcère bulbaire", "ulcère"]
    # Le passage du représentant reste inchangé (rétrocompatible).
    assert fusion[0]["source"]["passage"] == "Ulcère bulbaire"


def test_p2_fusion_par_code_desactivee():
    """La fusion par code normalisé est DÉSACTIVÉE : deux libellés
    lexicalement éloignés avec le MÊME code CIM-10 restent deux entrées.
    L'audit du normalisateur (outputs/AUDIT_NORMALISATEUR.md) mesure ~14 %
    de codes faux — fusionner sur le code, c'est hériter de ses erreurs.
    Prix assumé : les synonymes purs (« HTA » / « hypertension
    artérielle », ratio ~15) ne fusionnent PLUS tant que le normalisateur
    n'est pas corrigé. Seules les passes TEXTE (forme, similarité)
    fusionnent : elles comparent le texte réel."""
    champs = [
        _champ("hypertension artérielle", code="I10", page=3),
        _champ("HTA", code="I10", page=40),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 2  # même code, textes éloignés : PAS de fusion


def test_p2_non_regression_renale_vs_coronaire():
    """NON-RÉGRESSION (constat réel, dossier ABRICOT/BANANE) : « maladie
    rénale chronique » et « maladie coronaire » ne doivent JAMAIS fusionner.
    Le normalisateur attribue N18 (maladie rénale chronique) aux DEUX avec
    une confiance de 0,732 — l'ancienne fusion par code fusionnait donc les
    deux maladies en une seule entrée du VSM. Les formes lexicales ne se
    ressemblent pas (ratio ~73) : avec les passes texte, jamais de fusion,
    même si le code collide."""
    champs = [
        _champ("maladie rénale chronique", code="N18", page=5),
        _champ("maladie coronaire", code="N18", page=20),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 2
    valeurs = sorted(c["valeur"] for c in fusion)
    assert valeurs == ["maladie coronaire", "maladie rénale chronique"]


def test_p2_fusion_par_similarite_chaine_ocr():
    """La chaîne complète fusionne : « ulcère libéaire » (71.8 avec « bulbaire
    linéaire ») fusionne VIA « ulcère linéaire » (93.3) — le cluster-pont
    relie les familles. C'est ce qui fait tomber les 15 mentions réelles de
    l'ulcère à une seule entrée."""
    champs = [
        _champ("Ulcère bulbaire linéaire", page=8),
        _champ("ulcère linéaire", page=22),
        _champ("ulcère bulbaire", page=30),
        _champ("ulcère libéaire", page=40),  # faute d'OCR profonde
        _champ("Ulcèra bulbaire", page=55),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 1
    assert fusion[0]["occurrences"] == 5
    assert fusion[0]["pages"] == [8, 22, 30, 40, 55]
    # Les 5 formes distinctes (fautes d'OCR comprises) restent visibles :
    # chaque mention a son passage surlignable dans le visualiseur.
    assert fusion[0]["passages"] == [
        "Ulcère bulbaire linéaire",
        "ulcère linéaire",
        "ulcère bulbaire",
        "ulcère libéaire",
        "Ulcèra bulbaire",
    ]


def test_p2_bruit_ocr_direct_absorbe():
    """Bruit d'OCR simple : « ulcère bulbmre » → « ulcère bulbaire » (89.7)."""
    champs = [
        _champ("ulcère bulbaire", page=10),
        _champ("ulcère bulbmre", page=30),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 1
    assert fusion[0]["valeur"] == "ulcère bulbaire"
    assert fusion[0]["occurrences"] == 2


def test_p2_familles_distinctes_non_fusionnees():
    """« kyste hépatique » et « kyste urétral » sont deux choses différentes
    (similarité < seuil, aucune latéralité en jeu)."""
    champs = [
        _champ("kyste hépatique", page=5),
        _champ("kyste urétral", page=30),
    ]
    assert len(dedupliquer(champs)) == 2


def test_p2_lateralite_differentes_jamais_fusionnees():
    """« kyste de l'ovaire droit » ≠ « kyste de l'ovaire gauche » : clinique-
    ment distinct. Deux raisons INDEPENDANTES de ne jamais fusionner, toutes
    deux testées : la similarité (80 < 88) ET le garde-fou latéralité —
    qui protégerait même si le seuil était abaissé (droite/gauche/bilatéral
    sont lexicalement éloignés, mais l'invariant doit être explicite)."""
    from src.extraction_nlp.filtres_vsm import _fusionnables

    champs = [
        _champ("kyste de l'ovaire droit", page=5),
        _champ("kyste de l'ovaire gauche", page=30),
    ]
    assert len(dedupliquer(champs)) == 2
    # Le garde-fou lui-même (indépendamment du seuil) :
    assert not _fusionnables(
        "retention urinaire droite", "retention urinaire gauche"
    )
    assert not _fusionnables("hernie bilaterale", "hernie droite")


def test_p2_représentant_le_score_tranche_pas_la_longueur():
    """À fréquence égale, le représentant est le membre au MEILLEUR score —
    jamais la forme la plus longue (souvent la plus bruitée)."""
    champs = [
        _champ("fuites urinaires", confiance=0.95, page=10),
        _champ("fuites urinaires d'effort", confiance=0.72, page=44),
    ]
    fusion = dedupliquer(champs)
    assert len(fusion) == 1
    assert fusion[0]["valeur"] == "fuites urinaires"


def test_p2_entites_singles_inchangees():
    """Une entité seule ressort intacte (pas d'occurrences ajoutées)."""
    fusion = dedupliquer([_champ("Hypertension artérielle", page=2)])
    assert len(fusion) == 1
    assert "occurrences" not in fusion[0]


# ---------------------------------------------------------------------------
# P3 — actes chirurgicaux
# ---------------------------------------------------------------------------


def test_p3_actes_reconnus():
    for acte in (
        "excision du pertuis cutané",
        "cholécystectomie",
        "cholécystectomie rétrograde",
        "pose de stent",
        "Exérèse plurifragmentaire",
        "ostéosynthèse par plaque vissée",
        "anesthésie loco régionale",
        "cure de kyste sous urétrale",
        "biopsie du nodule",
    ):
        assert est_un_acte(acte), acte


def test_p3_medicaments_non_actes():
    for med in ("Metformine", "Ramipril", "Aspirine", "MAALOX", "insuline"):
        assert not est_un_acte(med), med


def test_p3_acte_route_vers_antecedents():
    """Un acte étiqueté « treatment » par CASM2 part dans antécédents — même
    sans contexte d'historique."""
    assert rubrique_de("treatment", "excision du pertuis cutané",
                       None, "excision du pertuis cutané") == "antecedents"
    # Un médicament reste un traitement.
    assert rubrique_de("treatment", "Metformine", None, "Metformine") == \
        "traitements_long_cours"


# ---------------------------------------------------------------------------
# P4 — termes trop génériques
# ---------------------------------------------------------------------------


def test_p4_generiques_isoles_rejetes():
    for valeur in ("lésion", "Lésions", "douleur", "DOULEURS", "kyste",
                   "tuméfaction", "atteinte", "infection", "obstacle",
                   "traitement", "traitement médical", "anomalie de structure",
                   "lésion osseuse"):
        assert est_trop_generique(valeur), valeur


def test_p4_qualifies_conserves():
    for valeur in (
        "douleur thoracique",
        "kyste hépatique",
        "infection urinaire à répétition",
        "lésion du ligament croisé",
    ):
        assert not est_trop_generique(valeur), valeur


# ---------------------------------------------------------------------------
# P5 — facteurs de risque (liste fermée)
# ---------------------------------------------------------------------------


def test_p5_expositions_reconnues():
    for valeur in (
        "Tabagisme actif",
        "tabagisme sevré",
        "fumeur",
        "20 paquets-année",
        "alcool",
        "obésité",
        "surpoids",
        "IMC à 32",
        "sédentarité",
        "exposition professionnelle à l'amiante",
        "antécédents familiaux de cancer",
        "ménopause",
        "contraception orale",
    ):
        assert est_facteur_risque(valeur), valeur


def test_p5_diagnostics_non_facteurs_de_risque():
    """Sténose urétrale, Trichomonas, dilatation des bronches : des
    diagnostics, pas des expositions — retour pathologies actives."""
    for valeur in ("sténose urétrale", "Trichomonas vaginalis",
                   "dilatation des bronches", "tumeur", "pneumopathie"):
        assert not est_facteur_risque(valeur), valeur


def test_p5_contexte_large_ne_route_plus():
    """L'ancienne règle CONTEXTUELLE (consommation, poids…) routait des
    diagnostics entiers : « consommation » dans le contexte ne suffit plus —
    seule l'entité liste fermée compte."""
    assert rubrique_de("problem", "sténose urétrale (consommation à "
                       "surveiller)", None, "sténose urétrale") == \
        "pathologies_actives"
    assert rubrique_de("problem", "Tabagisme actif", None,
                       "Tabagisme actif") == "facteurs_risque"


# ---------------------------------------------------------------------------
# P6 — en-tête de cabinet répété
# ---------------------------------------------------------------------------


def test_p6_en_tete_repete_supprime():
    """« Maladies du Foie » dans l'en-tête de 4 pages → supprimé ; un
    diagnostic du corps du texte (une page) est conservé. Les vraies pages
    dépassent 500 caractères : le corps échappe à la zone d'en-tête."""
    lettre = ("Cabinet du Dr X - Consultations Maladies du Foie et de "
              "l'Appareil Digestif\n12 rue de l'Hôpital\n")
    corps = ("Compte rendu de consultation. Examen clinique, bilan "
             "biologique et échographique détaillés. " * 8)  # > 500 car.
    pages_ocr = [
        {
            "page": i,
            "text": lettre + corps + f"\nPage {i}. Ulcère bulbaire linéaire "
                                    "décrit ce jour, à contrôler.",
        }
        for i in range(1, 5)
    ]
    texte = "\n\n".join(p["text"] for p in pages_ocr)
    carte = carte_pages(texte, pages_ocr)
    rejetees = formes_en_tete_repetees(
        ["Maladies du Foie", "Ulcère bulbaire linéaire"], carte
    )
    assert "maladies du foie" in rejetees
    assert "ulcere bulbaire lineaire" not in rejetees


def test_p6_en_tete_3_pages_passe_encore():
    """La barre est « PLUS de 3 pages » : un mot de l'en-tête sur exactement
    3 pages est conservé (le papier à lettres est sur TOUTES les pages)."""
    lettre = "Consultations Maladies du Foie\n"
    corps = "Compte rendu détaillé. " * 40
    pages_ocr = [
        {"page": i, "text": lettre + corps} for i in range(1, 4)
    ]
    texte = "\n\n".join(p["text"] for p in pages_ocr)
    carte = carte_pages(texte, pages_ocr)
    assert "maladies du foie" not in formes_en_tete_repetees(
        ["Maladies du Foie"], carte
    )


def test_p6_zone_en_tete_bornee_au_titre():
    """La zone d'en-tête s'arrête au premier titre de rubrique reconnu —
    même au-delà de 500 caractères, le papier à lettres repousse la mention
    (borne haute 1200 : au-delà, c'est du corps de texte)."""
    page = ("Cabinet du Dr X\n" + "adresse sur plusieurs lignes\n" * 30 +
            "Maladies du Foie\n" + "\n" + "ANTÉCÉDENTS :\nCholécystectomie.")
    zone = zone_en_tete(page)
    assert "Maladies du Foie" in zone  # à ~850 caractères : dans la zone
    assert "Cholécystectomie" not in zone  # après le titre : hors zone


# ---------------------------------------------------------------------------
# P7 — posologies
# ---------------------------------------------------------------------------


def test_p7_extension_posologie():
    """« OGAST 1 gél/j en permanence » : l'empan s'étend dans le texte
    source, jamais au-delà de 60 caractères."""
    texte = "OGAST 1 gél/j en permanence. TOLERANCE BONNE."
    fin = len("OGAST")
    nouvelle = etendre_posologie(texte, fin)
    assert texte[len("OGAST"):nouvelle].strip() == "1 gél/j en permanence"


def test_p7_pas_de_posologie_pas_d_extension():
    """Un médicament suivi de texte non posologique n'est pas étendu."""
    texte = "METFORMINE puis contrôle biologique."
    assert etendre_posologie(texte, len("METFORMINE")) == len("METFORMINE")


def test_p7_extension_bornee_a_60_caracteres():
    """La limite de 60 caractères tient même face à une succession de motifs."""
    texte = "MEDICAMENT " + "1 cp matin 1 cp soir " * 10
    nouvelle = etendre_posologie(texte, len("MEDICAMENT"))
    assert nouvelle - len("MEDICAMENT") <= 60


# ---------------------------------------------------------------------------
# Seuil de similarité (garde-fou P2)
# ---------------------------------------------------------------------------


def test_seuil_similarite_88():
    """Le seuil documenté est bien 88 (rapidfuzz token_set_ratio)."""
    assert SEUIL_SIMILARITE == 88


def test_page_de_retrouve_la_page():
    pages_ocr = [
        {"page": 1, "text": "aaaa"},
        {"page": 2, "text": "bbbb"},
        {"page": 3, "text": "cccc"},
    ]
    texte = "\n\n".join(p["text"] for p in pages_ocr)
    carte = carte_pages(texte, pages_ocr)
    assert page_de(carte, 0) == 1
    assert page_de(carte, 6) == 2
    assert page_de(carte, 12) == 3
    assert page_de(carte, 999) is None


# ---------------------------------------------------------------------------
# INTÉGRATION — run_pipeline, DrBERT simulé, filtres câblés de bout en bout
# ---------------------------------------------------------------------------

ENTETE = (
    "Cabinet Dr X - Consultations Maladies du Foie et de l'Appareil "
    "Digestif\n12 rue de l'Hôpital\n"
)
REMPLISSAGE = (
    "Compte rendu de consultation. Examen clinique, bilan biologique et "
    "échographique détaillés. " * 10
)  # > 500 caractères : le corps échappe à la zone d'en-tête P6


def test_integration_pipeline_p1_a_p7(monkeypatch):
    """Câblage complet : OCR multi-pages → DrBERT (simulé) → P1 (antibio-
    gramme journalisé) → P5 (tabagisme) → P6 (papier à lettres) → P7
    (posologie) → P2 (occurrences et pages)."""
    from src.extraction_nlp import drbert_extractor as dtx
    from src.extraction_nlp.drbert_extractor import Entite
    from src.extraction_nlp.pipeline import run_pipeline

    pages = [
        ENTETE + REMPLISSAGE + "\nUlcère bulbaire décrit ce jour.",
        ENTETE + REMPLISSAGE + "\nANTIBIOGRAMME : souche E. coli, CMI "
                               "pénicilline résistante.",
        # Faute d'OCR volontaire (« ulcére ») : la même pathologie revient
        # avec le bruit propre à chaque page — le cas réel de P2.
        ENTETE + REMPLISSAGE + "\nulcére bulbaire persistant au contrôle.",
        ENTETE + REMPLISSAGE + "\nTabagisme actif. OGAST 1 gél/j en "
                               "permanence.",
    ]
    texte = "\n\n".join(pages)
    ocr_json = {
        "document_id": "doc_test",
        "ocr_engine": "tesseract",
        "text": texte,
        "pages": [
            {"page": i + 1, "text": t, "confidence": 0.9}
            for i, t in enumerate(pages)
        ],
    }

    def _ent(label: str, mot: str, apres: int = 0) -> Entite:
        debut = texte.index(mot, apres)
        return Entite(label, mot, debut, debut + len(mot), 0.9)

    sep1 = texte.index("\n\n")
    debut_page3 = texte.index("\n\n", sep1 + 2) + 2
    entites = [
        _ent("problem", "Ulcère bulbaire"),                     # page 1
        _ent("problem", "ulcére bulbaire", debut_page3),        # page 3
        _ent("treatment", "pénicilline"),                       # page 2
        _ent("problem", "Maladies du Foie"),                    # page 1
        _ent("problem", "Tabagisme actif"),                     # page 4
        _ent("treatment", "OGAST"),                             # page 4
    ]

    class _FakeMoteur:
        def annoter(self, t: str) -> list[Entite]:
            return list(entites)

    monkeypatch.setattr(dtx, "_MOTEUR", _FakeMoteur())

    vsm = run_pipeline(ocr_json, nlp_engine="drbert")
    sections = vsm["sections"]
    toutes = [c["valeur"] for items in sections.values() for c in items]

    # P1 : la pénicilline de l'antibiogramme n'est PAS un traitement —
    # et le rejet est JOURNALISÉ (page, motif, entités supprimées).
    assert "pénicilline" not in toutes
    rejets = vsm["provenance"]["nlp"]["pages_ecartees"]
    assert rejets == [{"page": 2, "motif": "antibiogramme",
                       "entites_supprimees": 1}]

    # P6 : « Maladies du Foie » (papier à lettres, 4 en-têtes) supprimé.
    assert "Maladies du Foie" not in toutes

    # P5 : le tabagisme est un facteur de risque.
    assert "Tabagisme actif" in [c["valeur"] for c in
                                 sections["facteurs_risque"]]

    # P7 : la posologie est absorbée depuis le texte source (y compris
    # « en permanence » — motif posologique lui aussi).
    assert "OGAST 1 gél/j en permanence" in [
        c["valeur"] for c in sections["traitements_long_cours"]
    ]

    # P2 : l'ulcère des pages 1 et 3 → UNE entrée, 2 mentions, pages 1 et 3.
    pathos = sections["pathologies_actives"]
    assert len(pathos) == 1
    assert pathos[0]["valeur"] == "Ulcère bulbaire"
    assert pathos[0]["occurrences"] == 2
    assert pathos[0]["pages"] == [1, 3]
    # Les DEUX passages sources traversent la chaîne complète (to_champ →
    # pipeline → VSM) : « Voir les 2 passages sources » dans l'éditeur.
    assert pathos[0]["passages"] == ["Ulcère bulbaire", "ulcére bulbaire"]
    # La page de chaque entité est tracée à la source (XAI).
    assert pathos[0]["source"]["page"] == 1


# ---------------------------------------------------------------------------
# Journal des rejets — chaque entité écartée est TRACÉE avec sa règle
# ---------------------------------------------------------------------------


def test_ligne_rejet_format_fixe():
    """Le format de ligne est FIXE et greppable (demande explicite) :
    rejet | page=41 | «valeur» | score=0.90 | regle=… | detail=…"""
    ligne = ligne_rejet(
        {"page": 41, "valeur": "maladie rénale chronique",
         "score": 0.9, "regle": "P6_entete_repété", "detail": "12 pages"}
    )
    assert ligne == (
        "rejet | page=41 | «maladie rénale chronique» | score=0.90 "
        "| regle=P6_entete_repété | detail=12 pages"
    )
    # Page inconnue (rejet précoce, avant la carte) : « ? », jamais de crash.
    assert ligne_rejet({"valeur": "MRC", "score": 0.5, "regle": "x",
                        "detail": ""}).startswith("rejet | page=? | «MRC»")


def test_tracer_rejet_sans_journal_est_un_noop():
    """journal=None (appels historiques, moteur règles) : aucun changement."""
    assert tracer_rejet(None, "douleur", 0.9, "P4", "test") is None


def test_journal_rejets_par_regle(monkeypatch):
    """INTÉGRATION : chaque étage qui écarte une entité produit une entrée
    NOMMÉE dans report['rejets'] — validateur, P1, P4, P6. Plus jamais de
    disparition sans trace (constat ABRICOT : pathologies absentes du VSM
    sans aucune trace du responsable)."""
    from src.extraction_nlp import drbert_extractor as dtx
    from src.extraction_nlp.drbert_extractor import Entite
    from src.extraction_nlp.pipeline import run_pipeline

    entete = "Cabinet Dr X - Consultations Maladies du Foie\n"
    remplissage = "Compte rendu de consultation détaillé. " * 40  # > 500 car.
    pages = [
        # Page 1 : un terme générique (P4) + l'en-tête répété (P6) + une
        # entité clinique saine.
        entete + remplissage + "\nDouleur. Kyste hépatique stable.",
        # Page 2 : antibiogramme (P1) — la pénicilline disparaît AVEC sa page.
        entete + remplissage + "\nANTIBIOGRAMME souche E. coli, CMI "
                               "pénicilline résistante.",
        # Page 3 : contenu clinique sain.
        entete + remplissage + "\nTabagisme actif. OGAST 1 gél/j.",
        # Page 4 : pour que l'en-tête dépasse la barre des 3 pages (P6).
        entete + remplissage + "\nPoursuite du suivi habituel.",
    ]
    texte = "\n\n".join(pages)
    ocr_json = {
        "document_id": "doc_journal",
        "ocr_engine": "tesseract",
        "text": texte,
        "pages": [
            {"page": i + 1, "text": t, "confidence": 0.9}
            for i, t in enumerate(pages)
        ],
    }

    def _ent(label: str, mot: str, apres: int = 0) -> Entite:
        debut = texte.index(mot, apres)
        return Entite(label, mot, debut, debut + len(mot), 0.9)

    sep1 = texte.index("\n\n")
    debut_p3 = texte.index("\n\n", sep1 + 2) + 2
    entites = [
        _ent("problem", "Douleur"),                 # P4 : générique isolé
        _ent("problem", "Kyste hépatique"),         # survit
        _ent("problem", "Maladies du Foie"),        # P6 : en-tête ×4 pages
        _ent("treatment", "pénicilline", sep1),     # P1 : page antibiogramme
        _ent("problem", "Tabagisme actif", debut_p3),
        _ent("treatment", "OGAST", debut_p3),
    ]

    class _FakeMoteur:
        def annoter(self, t: str) -> list[Entite]:
            return list(entites)

    monkeypatch.setattr(dtx, "_MOTEUR", _FakeMoteur())

    vsm = run_pipeline(ocr_json, nlp_engine="drbert")
    rejets = vsm["provenance"]["nlp"]["rejets"]

    par_regle: dict[str, list[dict]] = {}
    for r in rejets:
        par_regle.setdefault(r["regle"], []).append(r)

    # P4 : « Douleur » isolé, avec sa page.
    assert any(
        r["valeur"] == "Douleur" and r["page"] == 1
        for r in par_regle["P4_terme_generique"]
    )
    # P1 : la pénicilline rejetée avec le motif de SA page.
    assert any(
        r["valeur"] == "pénicilline" and r["page"] == 2
        and "antibiogramme" in r["detail"]
        for r in par_regle["P1_page_non_prescriptive"]
    )
    # P6 : l'en-tête de cabinet rejeté avec son nombre de pages.
    assert any(
        r["valeur"] == "Maladies du Foie" and r["page"] == 1
        and "4 pages" in r["detail"]
        for r in par_regle["P6_entete_repété"]
    )
    # Les survivants ne sont PAS dans le journal.
    valeurs_rejetees = {r["valeur"] for r in rejets}
    assert "Kyste hépatique" not in valeurs_rejetees
    assert "OGAST 1 gél/j" not in valeurs_rejetees
    assert "Tabagisme actif" not in valeurs_rejetees


def test_journal_rejet_validateur_et_extracteur(monkeypatch):
    """Rejets du VALIDATEUR (règle nommée) et de l'EXTRACTEUR (score) :
    la même observabilité partout dans la chaîne."""
    from src.extraction_nlp import drbert_extractor as dtx
    from src.extraction_nlp.drbert_extractor import Entite
    from src.extraction_nlp.entity_extractor import _drbert_vers_entities

    texte = (
        "Laboratoire de biologie.\n"
        "Traitement par antibiotique.\n"
        "Gastrite aiguë récente.\n"
        "Œsophagite chronique stable."
    )

    def _ent(label: str, mot: str, score: float) -> Entite:
        debut = texte.index(mot)
        return Entite(label, mot, debut, debut + len(mot), score)

    entites = [
        _ent("problem", "Laboratoire", 0.95),          # validateur_blocklist
        _ent("treatment", "antibiotique", 0.95),       # validateur_classe_seule
        _ent("problem", "Gastrite aiguë", 0.4),        # extracteur_score
        _ent("problem", "Œsophagite chronique", 0.9),  # saine : passe tout
    ]

    class _FakeMoteur:
        def annoter(self, t: str) -> list[Entite]:
            return list(entites)

    monkeypatch.setattr(dtx, "_MOTEUR", _FakeMoteur())
    report: dict = {}
    entites_finales = _drbert_vers_entities(texte, pages=None, report=report)
    rejets = report["rejets"]
    regles = {r["regle"] for r in rejets}
    assert "validateur_blocklist" in regles
    assert "validateur_classe_seule" in regles
    assert "extracteur_score" in regles
    # L'entité saine passe et n'est pas journalisée.
    assert [e.valeur for e in entites_finales] == ["Œsophagite chronique"]


# ---------------------------------------------------------------------------
# Normalisateur : seuil CIM-10 relevé à 78 (audit 2026-08-24)
# ---------------------------------------------------------------------------


def test_seuil_cim10_78_chasse_les_collisions():
    """« maladie coronaire » (0,73 → N18 rénal !) et « maladie de Basedow »
    (0,74 → G20 Parkinson) ne reçoivent PLUS de code : la frontière mesurée
    est franche (faux ≤ 0,74, corrects ≥ 0,80), le seuil 78 ne garde que
    ce qui est au-dessus de la zone grise."""
    from src.extraction_nlp.normalizer import normalize_diagnosis

    assert normalize_diagnosis("maladie coronaire")["code_cim10"] is None
    assert normalize_diagnosis("maladie de Basedow")["code_cim10"] is None
    # Les codes corrects à ≥ 0,80 survivent au nouveau seuil.
    assert normalize_diagnosis("fibrillation auriculaire")["code_cim10"] == "I48"
    assert normalize_diagnosis("diabète de type 2")["code_cim10"] == "E11"


def test_code_flou_marque_a_verifier():
    """Un code issu d'un appariement flou < 0,85 est marqué « à vérifier »
    dans le VSM : le médecin voit l'incertitude, jamais un fait établi."""
    from src.extraction_nlp.normalizer import normalize_diagnosis

    # « fibrillation auriculaire » matche I48 en flou à 0,80 → à vérifier.
    n = normalize_diagnosis("fibrillation auriculaire")
    assert n["code_cim10"] == "I48"
    assert 0.78 <= n["confidence"] < 0.85
    # Le marquage pipeline (SEUIL_CODE_A_VERIFIER = 0,85) :
    from src.extraction_nlp.pipeline import SEUIL_CODE_A_VERIFIER

    assert SEUIL_CODE_A_VERIFIER == 0.85
    assert bool(n["confidence"] < SEUIL_CODE_A_VERIFIER) is True
    # Un match exact (1,0) n'est jamais marqué.
    exact = normalize_diagnosis("asthme")
    assert exact["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Règle de spécificité ATC (audit 2026-08-24, recommandation appliquée)
# ---------------------------------------------------------------------------


def test_specificite_refuse_les_qualificatifs_absents():
    """« insuline » ≠ glargine, « vitamine D » ≠ calcium + vitamine D : le
    libellé officiel ne doit pas affirmer PLUS que le texte extrait.
    token_set_ratio rend 100 pour un sous-ensemble — le piège est
    structurel, la règle le neutralise (aucun code plutôt qu'un code faux)."""
    from src.extraction_nlp.normalizer import normalize_medication

    assert normalize_medication("insuline")["code_atc"] is None
    assert normalize_medication("vitamine D")["code_atc"] is None
    # Le nom COMPLET de la substance garde son code.
    assert normalize_medication("Insuline glargine")["code_atc"] == "A10AE04"
    assert normalize_medication("Calcium + vitamine D")["code_atc"] == "A12AX"


def test_specificite_alias_et_posologie_passent():
    """Un terme qui nomme la substance par son ALIAS parenthésé
    (« Aspirine » pour « Acide acétylsalicylique (Aspirine/Kardégic) ») ou
    qui la couvre AVEC sa posologie (« Metformine 1000 mg ») garde son
    code : il nomme le produit, pas son genre."""
    from src.extraction_nlp.normalizer import normalize_medication

    assert normalize_medication("Aspirine")["code_atc"] == "B01AC06"
    assert normalize_medication("Aspirine 75 mg par jour")["code_atc"] == "B01AC06"
    assert normalize_medication("Metformine 1000 mg matin")["code_atc"] == "A10BA02"
    # « Lévothyroxine sodique » : « sodique » complète le nom canonique
    # sans le préciser (mot bénin curaté).
    assert normalize_medication("Lévothyroxine")["code_atc"] == "H03AA01"


def test_specificite_remonter_au_parent():
    """Refus de la feuille → on remonte au parent du référentiel s'il
    n'apporte pas lui-même de qualificatif absent, sinon rien."""
    from src.extraction_nlp.normalizer import _remonter_au_parent

    rows = [
        {"code": "A10AE", "libelle": "Insuline", "dci": "insuline"},
        {"code": "A10AE04", "libelle": "Insuline glargine", "dci": "insuline glargine"},
    ]
    parent = _remonter_au_parent("A10AE04", rows, "insuline")
    assert parent is not None and parent["code"] == "A10AE"
    # Parent absent du référentiel → ne rien afficher.
    assert _remonter_au_parent("A12AX", [], "vitamine D") is None


def test_pantoprazole_code_correct():
    """Entrée ATC corrigée (constat VSM réel ABRICOT) : PANTOPRAZOLE
    recevait A02BC01 (oméprazole !) par appariement flou — la bonne entrée
    A02BC02 fait du match un exact, et l'oméprazole reste A02BC01."""
    from src.extraction_nlp.normalizer import normalize_medication

    assert normalize_medication("PANTOPRAZOLE")["code_atc"] == "A02BC02"
    assert normalize_medication("Pantoprazole 20 mg")["code_atc"] == "A02BC02"
    assert normalize_medication("Oméprazole")["code_atc"] == "A02BC01"
