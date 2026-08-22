/** Page « Statistiques » — agrégats anonymes (audit de faisabilité validé :
 *  outputs/AUDIT_STATISTIQUES.md, ADR-0007).
 *  Garde-fous : effectifs < seuil masqués (« < n »), aucun détail patient,
 *  graphiques SVG maison (aucun CDN), 100 % local. */
import { useEffect, useState } from "react";
import { ApiError, Stats, StatsEntry, api } from "../api";
import { Alerte, Card, CardBody, CardHeader, EmptyState, Spinner } from "../components/ui";

function BarChart({ title, entries, seuil }: { title: string; entries: StatsEntry[]; seuil: number }) {
  if (!entries.length) return null;
  const max = Math.max(...entries.map((e) => e.count ?? seuil));
  const W = 520, ROW = 28, PAD = 8;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-encre">{title}</h3>
      <svg role="img" aria-label={title} viewBox={`0 0 ${W} ${entries.length * ROW + PAD}`}
        className="w-full max-w-xl" height={entries.length * ROW + PAD}>
        {entries.map((e, i) => {
          const y = i * ROW + PAD;
          const w = e.masque ? Math.max(20, (seuil / max) * (W - 130)) : Math.max(20, ((e.count ?? 0) / max) * (W - 130));
          return (
            <g key={e.code} role="listitem">
              <text x={0} y={y + 16} className="fill-encre" fontSize="12">
                {e.libelle || e.code}
              </text>
              <rect x={110} y={y + 4} width={w} height={16} rx={3}
                className={e.masque ? "fill-sourdine/60" : "fill-sarcelle"} />
              <text x={110 + w + 6} y={y + 16} fontSize="12" className="fill-encre">
                {e.masque ? `< ${seuil}` : String(e.count)}
              </text>
              <text x={W - 6} y={y + 16} fontSize="10" className="fill-sourdine" textAnchor="end">
                {e.code}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.stats()
      .then(setStats)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Chargement impossible"));
  }, []);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Statistiques anonymes"
          subtitle="Agrégats locaux sur les VSM de cette machine — aucune donnée identifiable n'est affichée ou exportée"
        />
        <CardBody className="space-y-6">
          {error && <Alerte kind="erreur">{error}</Alerte>}
          {!stats && !error && <Spinner label="Calcul des statistiques…" />}
          {stats && (
            <>
              <Alerte kind="info">{stats.avertissement}</Alerte>
              <div className="grid gap-4 sm:grid-cols-3">
                <Card>
                  <CardBody className="text-center">
                    <p className="text-3xl font-bold text-sarcelle">{stats.total}</p>
                    <p className="text-sm text-sourdine">VSM générés</p>
                  </CardBody>
                </Card>
                <Card>
                  <CardBody className="text-center">
                    <p className="text-3xl font-bold text-sarcelle">
                      {Object.values(stats.par_statut).reduce((a, b) => a + b, 0)}
                    </p>
                    <p className="text-sm text-sourdine">VSM par statut</p>
                    <p className="mt-1 text-xs text-sourdine">
                      {Object.entries(stats.par_statut).map(([k, v]) => `${k} : ${v}`).join(" · ")}
                    </p>
                  </CardBody>
                </Card>
                <Card>
                  <CardBody className="text-center">
                    <p className="text-3xl font-bold text-sarcelle">{Object.keys(stats.par_mois).length}</p>
                    <p className="text-sm text-sourdine">Périodes couvertes</p>
                    <p className="mt-1 text-xs text-sourdine">
                      {Object.entries(stats.par_mois).slice(-3).map(([k, v]) => `${k} : ${v}`).join(" · ")}
                    </p>
                  </CardBody>
                </Card>
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <BarChart title="Pathologies les plus fréquentes (CIM-10)" entries={stats.pathologies} seuil={stats.seuil} />
                <BarChart title="Traitements les plus fréquents (ATC)" entries={stats.traitements} seuil={stats.seuil} />
              </div>

              <div>
                <h3 className="mb-2 text-sm font-semibold text-encre">Complétude moyenne par rubrique</h3>
                {Object.keys(stats.completude).length === 0 ? (
                  <p className="text-sm text-sourdine">Aucune rubrique renseignée.</p>
                ) : (
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {Object.entries(stats.completude).map(([k, v]) => (
                      <li key={k} className="flex items-center justify-between rounded border border-trait px-3 py-2 text-sm">
                        <span className="text-sourdine">{k.replaceAll("_", " ")}</span>
                        <span className="font-mono">{Math.round((v ?? 0) * 100)} %</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
