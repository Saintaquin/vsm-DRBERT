/** Page « Statistiques » — agrégats anonymes (audit de faisabilité validé :
 *  outputs/AUDIT_STATISTIQUES.md, ADR-0007).
 *  Garde-fous : effectifs < seuil masqués (« < n »), aucun détail patient,
 *  graphiques SVG maison (aucun CDN), 100 % local.
 *
 *  Lisibilité (constat v8) : le graphique historique empilait libellé, barre,
 *  compteur et code sur la MÊME ligne d'un viewBox fixe 520 — un libellé long
 *  recouvrait sa barre, le compteur et le code se chevauchaient sur les barres
 *  pleines, et l'écran étroit réduisait tout (texte minuscule). Refonte : une
 *  rangée = libellé+code SUR une ligne, barre+compteur SUR la ligne suivante
 *  (aucun chevauchement possible), et la largeur du graphique suit celle du
 *  conteneur (ResizeObserver) pour garder une police lisible partout. */
import { useEffect, useRef, useState, type RefObject } from "react";
import { ApiError, Stats, StatsEntry, api } from "../api";
import { Alerte, Card, CardBody, CardHeader, EmptyState, Spinner } from "../components/ui";

/** Largeur réelle du conteneur (0 = inconnue → largeur par défaut 520). */
function useContainerWidth(): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [largeur, setLargeur] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entrees) => {
      const w = Math.floor(entrees[0].contentRect.width);
      if (w > 0) setLargeur(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, largeur];
}

/** Tronque un libellé long avec « … » (la police UI ≈ 6,5 px/car. à 12 px). */
function tronquer(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function BarChart({ title, entries, seuil }: { title: string; entries: StatsEntry[]; seuil: number }) {
  if (!entries.length) return null;
  const max = Math.max(...entries.map((e) => e.count ?? seuil));
  const [ref, largeur] = useContainerWidth();
  // 1 rangée = 2 lignes SVG (texte puis barre) : libellé et code ne peuvent
  // jamais recouvrir la barre, ni le compteur le code.
  const ROW = 44, PAD = 8, TEXTE = 12;
  const W = Math.max(300, largeur || 520);
  const BARRE_MAX = W - 92; // réserve 92 px pour le compteur (« < 5 »)
  const H = entries.length * ROW + PAD;
  return (
    <div ref={ref} className="min-w-0">
      <h3 className="mb-2 text-sm font-semibold text-encre">{title}</h3>
      <svg role="img" aria-label={title} viewBox={`0 0 ${W} ${H}`}
        className="w-full" height={H} preserveAspectRatio="xMidYMid meet">
        {entries.map((e, i) => {
          const y = i * ROW + PAD;
          const w = e.masque
            ? Math.max(20, (seuil / max) * BARRE_MAX)
            : Math.max(20, ((e.count ?? 0) / max) * BARRE_MAX);
          return (
            <g key={e.code} role="listitem">
              {/* Ligne 1 : libellé tronqué (gauche) + code (droite, séparés). */}
              <text x={0} y={y + 13} className="fill-encre" fontSize={TEXTE}>
                {tronquer(e.libelle || e.code, Math.max(16, Math.floor((W - 90) / 6.5)))}
              </text>
              <text x={W - 2} y={y + 13} fontSize="10" className="fill-sourdine" textAnchor="end">
                {e.code}
              </text>
              {/* Ligne 2 : barre + compteur, sous le libellé. */}
              <rect x={0} y={y + 22} width={w} height={16} rx={3}
                className={e.masque ? "fill-sourdine/60" : "fill-sarcelle"} />
              <text x={w + 6} y={y + 34} fontSize={TEXTE} className="fill-encre">
                {e.masque ? `< ${seuil}` : String(e.count)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Mini-table d'un dictionnaire (statuts, mois) — lignes repliables, aucun
 *  débordement même avec beaucoup d'entrées. */
function MiniTable({ donnees }: { donnees: Record<string, number> }) {
  return (
    <ul className="mt-1 flex flex-wrap justify-center gap-x-3 gap-y-0.5">
      {Object.entries(donnees).map(([k, v]) => (
        <li key={k} className="whitespace-nowrap text-xs text-sourdine">
          {k} : <span className="font-mono text-encre">{v}</span>
        </li>
      ))}
    </ul>
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
                    <MiniTable donnees={stats.par_statut} />
                  </CardBody>
                </Card>
                <Card>
                  <CardBody className="text-center">
                    <p className="text-3xl font-bold text-sarcelle">{Object.keys(stats.par_mois).length}</p>
                    <p className="text-sm text-sourdine">Périodes couvertes</p>
                    <MiniTable donnees={Object.fromEntries(Object.entries(stats.par_mois).slice(-3))} />
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
                      <li key={k} className="flex min-w-0 items-center justify-between gap-3 rounded border border-trait px-3 py-2 text-sm">
                        <span className="min-w-0 truncate text-sourdine">{k.replaceAll("_", " ")}</span>
                        <span className="shrink-0 whitespace-nowrap font-mono">{Math.round((v ?? 0) * 100)} %</span>
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
