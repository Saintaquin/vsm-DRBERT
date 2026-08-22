import { useCallback, useEffect, useRef, useState } from "react";
import { User, VsmListItem, api } from "./api";
import { useFocusTrap, usePrefs } from "./accessibility";
import { Button } from "./components/ui";
import { AuditTrail, Settings } from "./pages/AuditSettings";
import { Dashboard } from "./pages/Dashboard";
import { DocumentViewer } from "./pages/DocumentViewer";
import { Login } from "./pages/Login";
import { StatsPage } from "./pages/Stats";
import { VSMEditor } from "./pages/VSMEditor";

type View =
  | { name: "dashboard" }
  | { name: "vsm"; vsmId: string }
  | { name: "document"; documentId: string; highlight?: string }
  | { name: "audit" }
  | { name: "stats" }
  | { name: "settings" };

const NAV: { key: View["name"]; label: string; roles?: User["role"][] }[] = [
  { key: "dashboard", label: "Tableau de bord" },
  { key: "audit", label: "Journal d'audit", roles: ["medecin", "admin"] },
  { key: "stats", label: "Statistiques" },
  { key: "settings", label: "Paramètres" },
];

const SHORTCUTS: [string, string][] = [
  ["Ctrl+K", "Recherche globale (Palette)"],
  ["?", "Aide des raccourcis (cette fenêtre)"],
  ["Ctrl+,", "Ouvrir les Paramètres"],
  ["Échap", "Fermer la recherche / l'aide"],
  ["Tab / Maj+Tab", "Naviguer entre les champs du VSM"],
  ["↵", "Confirmer le champ focalisé (confiance 100 %)"],
  ["Ctrl+↵", "Enregistrer le VSM"],
];

function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<View>({ name: "dashboard" });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // D1 — préférences d'accessibilité appliquées au démarrage (et à chaque
  // modification dans Paramètres) ; A/B/C selon outputs/AUDIT_ACCESSIBILITE.md
  usePrefs();

  // Raccourcis globaux : Ctrl+K (recherche), ? (aide), Ctrl+, (paramètres),
  // Échap (fermer les modales). Les raccourcis « ? »/« Ctrl+, » sont ignorés
  // pendant la saisie dans un champ (C1 — aide accessible WCAG 2.1.1).
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      } else if ((e.ctrlKey || e.metaKey) && e.key === ",") {
        e.preventDefault();
        setView({ name: "settings" });
      } else if (e.key === "?" && !isTyping(e)) {
        e.preventDefault();
        setHelpOpen((o) => !o);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
        setHelpOpen(false);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* session déjà expirée */ }
    setUser(null);
    setView({ name: "dashboard" });
  }, []);

  if (!user) return <Login onLogin={(u) => setUser(u)} />;

  return (
    <div className="min-h-screen bg-papier font-corps text-encre">
      {/* B1 — zone d'annonces ARIA (lecteur d'écran) */}
      <div id="vsm-live" aria-live="polite" className="sr-only" role="status" />
      <a href="#contenu" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-sarcelle focus:px-3 focus:py-1.5 focus:text-white">
        Aller au contenu
      </a>
      <header className="border-b border-trait bg-carte">
        <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
          <span className="flex items-center gap-2 font-semibold">
            <span aria-hidden className="flex h-7 w-7 items-center justify-center rounded bg-sarcelle text-sm font-bold text-white">V</span>
            VSM-OCR
          </span>
          <nav aria-label="Navigation principale" className="flex gap-1">
            {NAV.filter((n) => !n.roles || n.roles.includes(user.role)).map((n) => (
              <button key={n.key}
                onClick={() => setView({ name: n.key } as View)}
                aria-current={view.name === n.key ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle ${view.name === n.key ? "bg-sarcelle-pale text-sarcelle-fonce" : "text-sourdine hover:bg-papier hover:text-encre"}`}>
                {n.label}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <button onClick={() => setHelpOpen(true)} aria-label="Aide des raccourcis"
              className="rounded border border-trait px-2 py-1 text-xs font-medium text-sourdine hover:bg-papier focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
              Raccourcis (?)
            </button>
            <span className="text-sourdine">{user.username} · {user.role}</span>
            <Button variant="secondary" onClick={logout}>Se déconnecter</Button>
          </div>
        </div>
      </header>

      <main id="contenu" className="mx-auto max-w-5xl px-4 py-6">
        {view.name !== "dashboard" && (
          <button onClick={() => setView({ name: "dashboard" })}
            className="mb-4 text-sm text-sarcelle underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
            ← Retour au tableau de bord
          </button>
        )}
        {view.name === "dashboard" && (
          <Dashboard
            onOpenVsm={(vsmId) => setView({ name: "vsm", vsmId })}
            onOpenDocument={(documentId) => setView({ name: "document", documentId })}
          />
        )}
        {view.name === "vsm" && (
          <VSMEditor vsmId={view.vsmId} user={user}
            onShowSource={(documentId, passage) => setView({ name: "document", documentId, highlight: passage })} />
        )}
        {view.name === "document" && <DocumentViewer documentId={view.documentId} highlight={view.highlight} />}
        {view.name === "audit" && <AuditTrail />}
        {view.name === "stats" && <StatsPage />}
        {view.name === "settings" && <Settings user={user} />}
      </main>

      <footer className="mx-auto max-w-5xl px-4 pb-6 text-center text-xs text-sourdine">
        Contenu généré automatiquement — à valider par un médecin avant tout usage clinique. Application 100 % locale.
      </footer>

      {paletteOpen && (
        <Palette onClose={() => setPaletteOpen(false)} onGoVsm={(id) => { setView({ name: "vsm", vsmId: id }); setPaletteOpen(false); }} />
      )}
      {helpOpen && <ShortcutsHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}

/** Palette de recherche globale (Ctrl+K) : filtre les VSM par identifiant/statut.
 *  B2 — focus piégé dans la modale et restauré à la fermeture (WCAG 2.4.3). */
function Palette({ onClose, onGoVsm }: { onClose: () => void; onGoVsm: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<VsmListItem[]>([]);
  const boxRef = useRef<HTMLDivElement>(null);
  useFocusTrap(boxRef, true);
  useEffect(() => { api.listVsm().then(setItems).catch(() => setItems([])); }, []);
  const filtered = items.filter((v) => (v.id + " " + v.statut).toLowerCase().includes(q.toLowerCase()));
  return (
    <div role="dialog" aria-modal="true" aria-label="Recherche globale"
      className="fixed inset-0 z-50 flex items-start justify-center bg-encre/40 pt-24" onClick={onClose}>
      <div ref={boxRef} className="w-full max-w-md rounded-lg border border-trait bg-carte shadow-lg" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Rechercher un VSM… (Échap pour fermer)"
          aria-label="Rechercher un VSM"
          className="w-full border-b border-trait bg-transparent px-4 py-3 text-sm focus:outline-none" />
        <ul className="max-h-72 overflow-auto p-2">
          {filtered.length === 0 && <li className="px-2 py-3 text-sm text-sourdine">Aucun résultat.</li>}
          {filtered.map((v) => (
            <li key={v.id}>
              <button onClick={() => onGoVsm(v.id)}
                className="flex w-full items-center justify-between rounded px-2 py-2 text-left text-sm hover:bg-sarcelle-pale focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
                <span className="font-mono">{v.id}</span>
                <span className="text-xs text-sourdine">{v.statut}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Aide des raccourcis clavier (C1) — dialog accessible, focus piégé. */
function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  const boxRef = useRef<HTMLDivElement>(null);
  useFocusTrap(boxRef, true);
  return (
    <div role="dialog" aria-modal="true" aria-label="Aide des raccourcis clavier"
      className="fixed inset-0 z-50 flex items-center justify-center bg-encre/40 p-4" onClick={onClose}>
      <div ref={boxRef} className="w-full max-w-md rounded-lg border border-trait bg-carte p-5 shadow-lg" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-3 text-base font-semibold text-encre">Raccourcis clavier</h2>
        <ul className="space-y-2 text-sm">
          {SHORTCUTS.map(([keys, desc]) => (
            <li key={keys} className="flex items-baseline justify-between gap-4">
              <kbd className="rounded border border-trait bg-papier px-1.5 py-0.5 font-mono text-xs">{keys}</kbd>
              <span className="text-sourdine">{desc}</span>
            </li>
          ))}
        </ul>
        <div className="mt-4 flex justify-end">
          <Button variant="secondary" onClick={onClose}>Fermer (Échap)</Button>
        </div>
      </div>
    </div>
  );
}
