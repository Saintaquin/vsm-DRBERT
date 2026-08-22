import { FormEvent, useState } from "react";
import { ApiError, User, api } from "../api";
import { Alerte, Button, Card, CardBody, Field, Input } from "../components/ui";

export function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [mode, setMode] = useState<"login" | "bootstrap">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("medecin");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "bootstrap") {
        await api.bootstrap(username, password, role);
        setMode("login");
        setError(null);
      }
      onLogin(await api.login(username, password));
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Erreur réseau — le backend local est-il lancé ?";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-papier px-4">
      <div className="w-full max-w-sm">
        <header className="mb-6 text-center">
          <div aria-hidden className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-sarcelle text-xl font-bold text-white">V</div>
          <h1 className="text-xl font-semibold text-encre">VSM-OCR</h1>
          <p className="mt-1 text-sm text-sourdine">Volet de Synthèse Médicale — application locale</p>
        </header>
        <Card>
          <CardBody>
            <form onSubmit={submit} className="space-y-4" aria-label={mode === "login" ? "Connexion" : "Création du premier compte"}>
              <Field label="Identifiant" htmlFor="username">
                <Input id="username" autoComplete="username" required
                  value={username} onChange={(e) => setUsername(e.target.value)} />
              </Field>
              <Field label="Mot de passe" htmlFor="password">
                <Input id="password" type="password" autoComplete="current-password" required
                  minLength={mode === "bootstrap" ? 12 : 1}
                  value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              {mode === "bootstrap" && (
                <Field label="Rôle" htmlFor="role">
                  <select id="role" value={role} onChange={(e) => setRole(e.target.value)}
                    className="w-full rounded-md border border-trait bg-carte px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
                    <option value="medecin">Médecin</option>
                    <option value="secretaire">Secrétaire</option>
                    <option value="admin">Administrateur</option>
                  </select>
                </Field>
              )}
              {error && <Alerte kind="erreur">{error}</Alerte>}
              <Button type="submit" disabled={busy} className="w-full justify-center">
                {busy ? "…" : mode === "login" ? "Se connecter" : "Créer le compte et se connecter"}
              </Button>
            </form>
            <button
              className="mt-4 w-full text-center text-sm text-sarcelle underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
              onClick={() => { setMode(mode === "login" ? "bootstrap" : "login"); setError(null); }}
            >
              {mode === "login" ? "Première utilisation ? Créer le premier compte" : "← Retour à la connexion"}
            </button>
          </CardBody>
        </Card>
        <p className="mt-4 text-center text-xs text-sourdine">
          Mot de passe : 12 caractères minimum. Données chiffrées localement (AES-256-GCM).
        </p>
      </div>
    </main>
  );
}
