# Manuel utilisateur VSM-OCR

## Premiers pas (tous rôles)

1. Lancer l'application (icône desktop VSM-OCR, ou `python -m src.ui_backend.main`
   puis ouvrir http://127.0.0.1:8741).
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
   des identités, **phase LLM locale** (correction des erreurs OCR puis
   extraction structurée), génération du VSM.
3. Le rapport indique le nombre de pages traitées, de PII masquées et le bilan
   de la phase LLM (nombre de corrections OCR, ou repli règles avec la raison).
   Le VSM créé apparaît avec le statut **« À valider »**.
4. Vous pouvez pré-corriger les champs évidents (fautes d'OCR) ; vous ne pouvez
   pas signer.

## Médecin — relire, corriger, signer

1. Ouvrir le VSM depuis le tableau de bord (ou `Ctrl+K` pour le rechercher).
   L'en-tête indique si la **phase LLM locale a été effectuée** (corrections
   OCR, durées) ou, sinon, la raison du repli sur les règles.
2. Les champs sur **fond ambre « ⚠ À valider »** ont une confiance < 70 % :
   - cliquer **« Voir le passage source → »** affiche le texte d'origine surligné ;
   - la mention « corrigé par le LLM » signale un champ dont l'orthographe a été
     réparée par rapport au scan (accents, erreurs OCR) ;
   - corriger directement le champ, ou appuyer sur **↵** pour le confirmer tel quel.
   Chaque champ affiche aussi son code CIM-10/ATC normalisé et les moteurs utilisés.
3. **« Relire par le LLM local »** relance la phase LLM (correction OCR +
   extraction) uniquement sur les champs encore « À valider » ; relisez les
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
| « ⚠ Phase LLM locale non effectuée » — modèle présent mais « llama-cpp-python » signalé | La bibliothèque d'inférence n'est pas installée (le GGUF seul ne suffit pas) | `python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu` (roue CPU précompilée ; PyPI n'héberge que le code source), puis relancer |
| Phase LLM lente sur un vieux PC (repli règles « délai dépassé ») | Premier chargement du modèle (≈ 2 Go) dépassant le budget | Le modèle se charge une seule fois puis est préchargé au démarrage : relancer le traitement du document suivant ; ajuster `VSM_LLM_LOAD_TIMEOUT_SEC` si besoin |
| Traitement long mais sans erreur (progression « segment X/Y ») | Grand document : le texte OCR est traité **segment par segment** par le LLM local (≈ 2-4 min/segment sur CPU lent, borné par `VSM_LLM_CHUNK_CHARS`) | Normal sur un PC lent : le traitement se termine, les segments trop lents basculent sur les règles sans erreur. Pour un document très volumineux, préférer le modèle ultra-léger : `python -m src.extraction_nlp.llm --model qwen2.5-1.5b` |
