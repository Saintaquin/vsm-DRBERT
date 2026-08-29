# Manuel utilisateur VSM-OCR

## Premiers pas (tous rôles)

1. Lancer l'application (icône desktop VSM-OCR, ou `py -3.12 -m
   src.ui_backend.main`), puis ouvrir http://127.0.0.1:8741. Sous Windows,
   utiliser `py -3.12` et non `python` : l'interpréteur `python` par défaut
   n'a généralement pas torch ; dans ce cas l'application le signale au
   démarrage ET sur la page d'accueil (bannière DrBERT indisponible), et
   l'extraction passe par le moteur de règles en repli.
2. **Première utilisation** : « Créer le premier compte » — choisissez un mot de
   passe d'au moins 12 caractères. ⚠️ Ce mot de passe chiffre vos données : il
   est **impossible à récupérer** en cas d'oubli.
3. Connectez-vous. La session expire après **15 minutes d'inactivité** (la clé
   de chiffrement est alors effacée de la mémoire).

## Secrétaire — préparer les dossiers

1. **Tableau de bord → Nouveau document** : choisir le mode d'anonymisation
   (pseudonymisation par défaut) puis sélectionner le scan (PDF/PNG/JPG/TIFF,
   50 Mo max).
2. Le traitement est automatique (indicateur de progression) : OCR, masquage
   des identités, **extraction DrBERT** (encodeur local : chaque valeur est
   un extrait exact du document, jamais du texte inventé), génération du VSM.
3. Le rapport indique le nombre de pages traitées, de PII masquées et le
   bilan de l'extraction (moteur DrBERT et durée, ou repli règles avec la
   raison — modèle absent, document sans contenu clinique). Le VSM créé
   apparaît avec le statut **« À valider »**.
4. Vous pouvez pré-corriger les champs évidents (fautes d'OCR) ; vous ne pouvez
   pas signer.

## Médecin — relire, corriger, signer

1. Ouvrir le VSM depuis le tableau de bord (ou `Ctrl+K` pour le rechercher).
   L'en-tête indique le **moteur d'extraction réellement utilisé** (DrBERT,
   ou le moteur de règles en repli avec la raison).
2. Les champs sur **fond ambre « ⚠ À valider »** ont une confiance < 70 % :
   - cliquer **« Voir le passage source → »** affiche le texte d'origine surligné ;
   - avec DrBERT, la confiance est le **score réel du modèle** sur le passage
     (pas une estimation) et le passage est l'extrait exact du scan ;
   - corriger directement le champ, ou appuyer sur **↵** pour le confirmer tel quel.
   Chaque champ affiche aussi son code CIM-10/ATC normalisé et les moteurs utilisés.
3. **« Relire par le LLM local »** (optionnel — exige le GGUF Qwen téléchargé,
   `python -m src.extraction_nlp.llm`) relance une passe de correction OCR +
   extraction **uniquement sur les champs encore « À valider »** ; relisez les
   changements proposés, puis enregistrez — vous gardez la main.
4. `Ctrl+↵` (ou « Enregistrer ») sauvegarde vos modifications.
5. **« Signer et finaliser »** : le VSM est scellé (empreinte SHA-256), passe au
   statut **Signé** et n'est plus modifiable. L'action est journalisée à votre nom.
6. **Exporter (HTML)** pour impression ou intégration au DPI ; le rendu inclut
   la zone de signature et l'avertissement réglementaire.

> Le contenu généré n'est **jamais** vérifié médicalement par la machine. La
> signature engage votre relecture.

## Administrateur

- **Comptes** : créés via l'API locale (`POST /auth/bootstrap` pour le premier ;
  les suivants par un compte admin — voir README). Rôles : `medecin`,
  `secretaire`, `admin`.
- **Coffre-fort de pseudonymisation** : définir `VSM_VAULT_PASSPHRASE` dans
  l'environnement de lancement (gestionnaire de secrets recommandé). Sans elle,
  les correspondances pseudonyme↔identité ne sont pas conservées.
- **Journal d'audit** : onglet dédié (médecin/admin). Le badge « Chaîne
  d'intégrité valide » garantit qu'aucune entrée n'a été modifiée a posteriori.
  S'il est rouge : appliquer la procédure d'incident (`docs/SECURITY.md`).
- **Droit à l'oubli** : bouton « Oublier » sur un dossier = suppression
  définitive (documents, résultats, VSM, mapping). Action journalisée.
- **Sauvegardes** : sauvegarder `~/.vsm-ocr/` (tout y est chiffré). Tester la
  restauration. Conserver la passphrase du coffre séparément des sauvegardes.

## Raccourcis clavier

| Raccourci | Action |
|---|---|
| `Ctrl+K` | Recherche globale de VSM |
| `Tab` / `Maj+Tab` | Naviguer entre les champs |
| `↵` | Confirmer le champ focalisé (le passe à 100 %) |
| `Ctrl+↵` | Enregistrer le VSM |
| `Échap` | Fermer la recherche |

## Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Erreur réseau — le backend local est-il lancé ? » | Backend arrêté | Relancer l'application |
| Connexion refusée après plusieurs essais | Verrouillage anti-force-brute | Attendre, ou réinitialisation par un admin |
| OCR vide ou illisible | Pack `fra` de Tesseract absent | `sudo apt install tesseract-ocr-fra` |
| PDF refusé | Poppler absent | `sudo apt install poppler-utils` |
| Session fermée seule | 15 min d'inactivité | Comportement normal (sécurité) — se reconnecter |
| Bandeau « ⚠ DrBERT indisponible — … Cause : … » (page d'accueil) | Soit le dossier `models/drbert/` (ou `VSM_DRBERT_PATH`) est absent/incomplet, **soit l'application a été lancée avec un interpréteur sans torch** (ex. `python` au lieu de `py -3.12` sous Windows — la cause exacte est affichée dans le bandeau et au démarrage) | Relancer avec `py -3.12 -m src.ui_backend.main` ; si le modèle manque : réinstaller l'application (vendorisé dans l'installeur) ou relancer `packaging/fetch_models.py` sur le poste de fabrication |
| Extraction DrBERT lente au premier document | Premier chargement du modèle (~440 Mo) | Le modèle reste chargé : les documents suivants sont rapides (~2-10 s/page selon le PC) |
| Démarrage immédiatement arrêté : « Le port 8741 est déjà occupé par une AUTRE instance VSM-OCR » | Un ancien serveur tourne encore dans une autre console (fenêtre oubliée) | Fermer l'ancienne console (Ctrl+C) puis relancer — l'application refuse volontairement de servir si le port est pris, pour éviter de laisser le navigateur parler à une version périmée |
| « Relire par le LLM local » en erreur | Le GGUF Qwen n'est pas installé (retiré du paquet par défaut) | Optionnel : `python -m src.extraction_nlp.llm`, puis `python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu` |
