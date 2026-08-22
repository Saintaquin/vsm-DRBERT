import { useEffect, useState } from "react";
import { ApiError, AuditEntry, User, api } from "../api";
import { Alerte, Card, CardBody, CardHeader, Spinner } from "../components/ui";

export function AuditTrail() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [chainValid, setChainValid] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.audit(500)
      .then((r) => { setEntries(r.entries); setChainValid(r.chain_valid); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Chargement impossible"));
  }, []);

  return (
    <Card>
      <CardHeader
        title="Journal d'audit"
        subtitle="Trace immuable de tous les traitements — aucune donnée patient en clair"
        action={chainValid !== null && (
          <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${chainValid ? "border-mousse bg-mousse-fond text-mousse" : "border-alerte bg-[#FBEAEB] text-alerte"}`}>
            {chainValid ? "Chaîne d'intégrité valide" : "⚠ Chaîne d'intégrité ROMPUE"}
          </span>
        )}
      />
      <CardBody>
        {error && <Alerte kind="erreur">{error}</Alerte>}
        {!entries && !error && <Spinner label="Lecture du journal…" />}
        {entries && (
          <div className="max-h-[70vh] overflow-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Entrées du journal d'audit, de la plus récente à la plus ancienne</caption>
              <thead className="sticky top-0 bg-carte">
                <tr className="border-b border-trait text-xs uppercase tracking-wide text-sourdine">
                  <th scope="col" className="py-2 pr-4">Horodatage</th>
                  <th scope="col" className="py-2 pr-4">Acteur</th>
                  <th scope="col" className="py-2 pr-4">Événement</th>
                  <th scope="col" className="py-2">Détails</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => (
                  <tr key={i} className="border-b border-trait/60 align-top">
                    <td className="whitespace-nowrap py-2 pr-4 font-mono text-xs text-sourdine">
                      {new Date(e.ts).toLocaleString("fr-FR")}
                    </td>
                    <td className="py-2 pr-4">{e.actor}</td>
                    <td className="py-2 pr-4 font-medium">{e.event}</td>
                    <td className="py-2 font-mono text-xs text-sourdine">{JSON.stringify(e.details)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function Settings({ user }: { user: User }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Session" />
        <CardBody>
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="font-medium text-sourdine">Utilisateur</dt><dd className="text-encre">{user.username}</dd></div>
            <div><dt className="font-medium text-sourdine">Rôle</dt><dd className="text-encre">{user.role}</dd></div>
            <div><dt className="font-medium text-sourdine">Backend</dt><dd className="font-mono text-encre">127.0.0.1:8741 (local uniquement)</dd></div>
            <div><dt className="font-medium text-sourdine">Expiration de session</dt><dd className="text-encre">15 min d'inactivité (clé de chiffrement effacée)</dd></div>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Confidentialité et conformité" />
        <CardBody className="space-y-2 text-sm text-encre">
          <p>· Toutes les données patient sont chiffrées au repos (AES-256-GCM, clé dérivée par Argon2id de votre mot de passe).</p>
          <p>· L'anonymisation des PII est appliquée <strong>avant</strong> toute extraction ; elle ne peut pas être désactivée.</p>
          <p>· Le coffre-fort de pseudonymisation requiert la variable d'environnement <code className="font-mono">VSM_VAULT_PASSPHRASE</code> (clé maître hors application). Sans elle, les mappings ne sont pas conservés.</p>
          <p>· Aucune donnée ne quitte cette machine : pas de cloud, pas de télémétrie, pas de mise à jour automatique.</p>
          <p>· Droit à l'oubli : bouton « Oublier » sur chaque dossier du tableau de bord.</p>
          <p className="pt-2 text-sourdine">Raccourcis : <kbd className="rounded border border-trait bg-papier px-1">Ctrl+K</kbd> recherche · <kbd className="rounded border border-trait bg-papier px-1">Tab</kbd>/<kbd className="rounded border border-trait bg-papier px-1">Maj+Tab</kbd> navigation champs · <kbd className="rounded border border-trait bg-papier px-1">↵</kbd> confirmer un champ · <kbd className="rounded border border-trait bg-papier px-1">Ctrl+↵</kbd> enregistrer le VSM.</p>
        </CardBody>
      </Card>
    </div>
  );
}
