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
   des identités, extraction, génération du VSM.
3. Le rapport indique le nombre de pages traitées et de PII masquées. Le VSM
   créé apparaît avec le statut **« À valider »**.
4. Vous pouvez pré-corriger les champs évidents (fautes d'OCR) ; vous ne pouvez
   pas signer.

## Médecin — relire, corriger, signer

1. Ouvrir le VSM depuis le tableau de bord (ou `Ctrl+K` pour le rechercher).
2. Les champs sur **fond ambre « ⚠ À valider »** ont une confiance < 70 % :
   - cliquer **« Voir le passage source → »** affiche le texte d'origine surligné ;
   - corriger directement le champ, ou appuyer sur **↵** pour le confirmer tel quel.
   Chaque champ affiche aussi son code CIM-10/ATC normalisé et les moteurs utilisés.
3. `Ctrl+↵` (ou « Enregistrer ») sauvegarde vos modifications.
4. **« Signer et finaliser »** : le VSM est scellé (empreinte SHA-256), passe au
   statut **Signé** et n'est plus modifiable. L'action est journalisée à votre nom.
5. **Exporter (HTML)** pour impression ou intégration au DPI ; le rendu inclut
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
