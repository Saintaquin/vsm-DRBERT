import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ChampTrace, NlpReport, User, Vsm, api } from "../api";
import { announce } from "../accessibility";
import { Alerte, Button, Card, CardBody, CardHeader, ConfianceBadge, Spinner, StatutBadge } from "../components/ui";

/** Ordre canonique HAS des sections du VSM. */
const SECTION_ORDER: [string, string][] = [
  ["patient", "Identification patient"],
  ["medecin_traitant", "Médecin traitant"],
  ["pathologies_actives", "Pathologies actives"],
  ["antecedents", "Antécédents médicaux et chirurgicaux"],
  ["allergies", "Allergies et intolérances"],
  ["traitements_long_cours", "Traitements au long cours"],
  ["facteurs_risque", "Facteurs de risque"],
  ["vaccinations", "Vaccinations"],
  ["points_vigilance", "Points de vigilance"],
];

const isList = (v: unknown): v is ChampTrace[] => Array.isArray(v);

/** Ligne d'information sur la phase NLP (XAI) : moteur réellement utilisé
 *  (DrBERT-CASM2 par défaut, règles en repli, LLM sur demande), corrections
 *  (lexicales), durée, raison du repli éventuel. */
function NlpInfo({ report }: { report?: NlpReport }) {
  if (!report) return null;
  const llm = report.statut.startsWith("llm");
  const drbert = report.statut === "drbert";
  const partiel = report.statut === "llm_partiel";
  const detail = llm
    ? `LLM local${report.modele ? ` ${report.modele}` : ""} · ${report.nb_corrections_ocr ?? 0} valeur(s) corrigée(s)` +
      (report.duree_extraction_sec != null ? ` · extraction ${report.duree_extraction_sec.toFixed(0)} s` : "") +
      (report.nb_chunks && report.nb_chunks > 1 ? ` · ${report.nb_chunks} segments` : "") +
      (partiel && report.raison ? ` — ${report.raison}` : "")
    : drbert
      ? `DrBERT-CASM2${report.modele ? ` (${report.modele})` : ""} — encodeur local` +
        (report.duree_extraction_sec != null ? ` · extraction ${report.duree_extraction_sec.toFixed(0)} s` : "")
      : report.statut === "modele_absent"
        ? "modèle local absent — extraction par le moteur de règles"
        : `repli moteur de règles${report.raison ? ` — ${report.raison}` : ""}`;
  const ton = llm || drbert ? (partiel ? "text-ambre" : "text-mousse") : "text-ambre";
  return (
    <p className="text-xs text-sourdine">
      <span className={`font-semibold ${ton}`}>
        {partiel
          ? "◐ Phase LLM locale partielle"
          : llm
            ? "✓ Phase LLM locale effectuée"
            : drbert
              ? "✓ Extraction DrBERT effectuée"
              : "◐ Moteur de règles (repli)"}
      </span>{" "}
      — {detail}
      {report.assist_llm && " · relecture assistée par le LLM"}
    </p>
  );
}

interface Props {
  vsmId: string;
  user: User;
  onShowSource: (documentId: string, passage: string) => void;
}

export function VSMEditor({ vsmId, user, onShowSource }: Props) {
  const [vsm, setVsm] = useState<Vsm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setVsm(null);
    api.getVsm(vsmId)
      .then(setVsm)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Chargement impossible"));
  }, [vsmId]);

  const updateItem = useCallback((section: string, index: number, valeur: string) => {
    setVsm((prev) => {
      if (!prev) return prev;
      const list = prev.sections[section];
      if (!isList(list)) return prev;
      const next = list.map((it, i) =>
        // Toute édition manuelle = validation humaine du champ → confiance 1.0
        i === index ? { ...it, valeur, confiance: 1.0, a_valider: false } : it);
      return { ...prev, sections: { ...prev.sections, [section]: next } };
    });
    setDirty(true);
  }, []);

  const confirmItem = useCallback((section: string, index: number) => {
    setVsm((prev) => {
      if (!prev) return prev;
      const list = prev.sections[section];
      if (!isList(list)) return prev;
      const next = list.map((it, i) => (i === index ? { ...it, confiance: 1.0, a_valider: false } : it));
      return { ...prev, sections: { ...prev.sections, [section]: next } };
    });
    setDirty(true);
  }, []);

  async function save(statut: string) {
    if (!vsm) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.validateVsm(vsmId, { sections: vsm.sections, statut });
      setVsm(updated);
      setDirty(false);
      const notice = statut === "signe" ? "VSM signé et scellé (empreinte SHA-256 enregistrée)." : "Modifications enregistrées.";
      announce(notice);
      setNotice(notice);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Enregistrement impossible";
      announce(`Erreur : ${msg}`);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  // Relance la phase LLM locale sur les champs encore « À valider » :
  // correction OCR + extraction, appliquée uniquement aux champs douteux.
  // Le médecin relit ensuite et enregistre (il garde la main).
  const [llmBusy, setLlmBusy] = useState(false);
  async function handleLlmAssist() {
    if (!vsm) return;
    setLlmBusy(true);
    setError(null);
    try {
      const res = await api.llmAssist(vsmId);
      setVsm(res.vsm);
      setDirty(true);
      const msg = res.champs_mis_a_jour > 0
        ? `LLM local : ${res.champs_mis_a_jour} champ(s) corrigé(s) — relisez puis enregistrez.`
        : "LLM local : aucun champ douteux à corriger.";
      announce(msg);
      setNotice(msg);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Assistance LLM impossible";
      announce(`Erreur : ${msg}`);
      setError(msg);
    } finally {
      setLlmBusy(false);
    }
  }

  // Raccourcis clavier : Enter = valider le champ focalisé, Ctrl+Enter = enregistrer.
  // (Tab/Shift+Tab = navigation native ; Ctrl+K géré au niveau App.)
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        void save("valide");
      } else if (e.key === "Enter" && !e.shiftKey && (e.target as HTMLElement).dataset.champ) {
        e.preventDefault();
        const t = e.target as HTMLElement;
        confirmItem(t.dataset.section!, Number(t.dataset.index));
      }
    };
    el.addEventListener("keydown", handler);
    return () => el.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vsm]);

  if (error && !vsm) return <Alerte kind="erreur">{error}</Alerte>;
  if (!vsm) return <Spinner label="Déchiffrement du VSM…" />;

  const signed = vsm.statut === "signe";

  return (
    <div ref={rootRef} className="space-y-6">
      <Card>
        <CardHeader
          title={`VSM ${vsmId}`}
          subtitle={`Généré le ${new Date(vsm.date_generation).toLocaleString("fr-FR")}`}
          action={<StatutBadge statut={vsm.statut} />}
        />
        <CardBody className="space-y-3">
          <Alerte kind="info">{vsm.avertissement}</Alerte>
          <NlpInfo report={vsm.provenance?.nlp} />
          {vsm.signature && (
            <p className="text-sm text-mousse">
              Signé par <strong>{vsm.signature.signe_par}</strong> le{" "}
              {new Date(vsm.signature.date_signature).toLocaleString("fr-FR")} — empreinte{" "}
              <code className="font-mono text-xs">{vsm.signature.empreinte_vsm.slice(0, 16)}…</code>
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => save("valide")} disabled={busy || signed}>
              Enregistrer {dirty && "•"} <kbd className="rounded bg-white/20 px-1 text-xs">Ctrl+↵</kbd>
            </Button>
            {vsm.statut === "a_valider" && !signed && (
              <Button variant="secondary" disabled={busy || llmBusy} onClick={handleLlmAssist}>
                {llmBusy ? "Phase LLM en cours…" : "Relire par le LLM local"}
              </Button>
            )}
            {user.role === "medecin" && (
              <Button variant="secondary" disabled={busy || signed}
                onClick={() => { if (window.confirm("Signer et finaliser ce VSM ? Le document sera scellé (empreinte SHA-256) et ne pourra plus être modifié sans nouvelle signature.")) void save("signe"); }}>
                Signer et finaliser
              </Button>
            )}
            <a className="inline-flex items-center rounded-md border border-trait bg-carte px-4 py-2 text-sm font-medium text-encre hover:bg-papier focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
              href={api.exportHtmlUrl(vsmId)} target="_blank" rel="noreferrer">
              Exporter (HTML) ↗
            </a>
            <a className="inline-flex items-center rounded-md border border-trait bg-carte px-4 py-2 text-sm font-medium text-encre hover:bg-papier focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
              href={api.exportPdfUrl(vsmId)} download>
              Exporter (PDF) ↓
            </a>
          </div>
          {notice && <Alerte kind="succes">{notice}</Alerte>}
          {error && <Alerte kind="erreur">{error}</Alerte>}
        </CardBody>
      </Card>

      {SECTION_ORDER.map(([key, label]) => {
        // « patient » et « medecin_traitant » sont des blocs racine du JSON
        // (champ_trace par clé) ; les rubriques cliniques vivent dans
        // vsm.sections (listes de champ_trace).
        const content =
          key === "patient" ? vsm.patient
          : key === "medecin_traitant" ? vsm.medecin_traitant
          : vsm.sections[key];
        const completude = vsm.completude?.[key];
        return (
          <Card key={key}>
            <CardHeader
              title={label}
              subtitle={completude !== undefined ? `Complétude ${Math.round(completude * 100)} %` : undefined}
            />
            <CardBody>
              {isList(content) ? (
                content.length === 0 ? (
                  <p className="text-sm text-sourdine">Aucun élément extrait — à compléter manuellement si nécessaire.</p>
                ) : (
                  <ul className="space-y-2">
                    {content.map((item, i) => (
                      <li key={i}
                        className={`rounded-md border p-3 ${item.a_valider ? "border-ambre-bord bg-ambre-fond" : "border-trait bg-carte"}`}>
                        <div className="flex flex-wrap items-start gap-2">
                          <input
                            data-champ data-section={key} data-index={i}
                            value={item.valeur}
                            disabled={signed}
                            onChange={(e) => updateItem(key, i, e.target.value)}
                            aria-label={`${label}, élément ${i + 1}${item.a_valider ? " — à valider" : ""}`}
                            className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm text-encre hover:border-trait focus-visible:border-sarcelle focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle disabled:opacity-70"
                          />
                          <ConfianceBadge confiance={item.confiance} aValider={item.a_valider} />
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-sourdine">
                          {item.code_normalise && (
                            <span className="font-mono">
                              {item.code_normalise.systeme} {item.code_normalise.code} · {item.code_normalise.libelle}
                              {item.code_normalise.a_verifier && (
                                <span className="font-sans font-medium text-ambre">
                                  {" "}· code à vérifier (appariement flou)
                                </span>
                              )}
                            </span>
                          )}
                          {item.occurrences != null && item.occurrences > 1 && (
                            <span>
                              {item.occurrences} mentions
                              {item.pages && item.pages.length > 0
                                ? ` · pages ${item.pages[0]} à ${item.pages[item.pages.length - 1]}`
                                : ""}
                            </span>
                          )}
                          {item.correction_ocr && (
                            <span className="font-medium text-sarcelle">valeur corrigée</span>
                          )}
                          {item.moteurs && <span>moteurs : {item.moteurs.ocr ?? "?"} / {item.moteurs.nlp ?? "?"}</span>}
                          {item.origine && <span>origine : {item.origine}</span>}
                          {item.source?.passage && (
                            <button
                              className="text-sarcelle underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
                              onClick={() => onShowSource(item.source!.document_id ?? vsm.document_id ?? "", item.source!.passage!)}>
                              Voir le passage source →
                            </button>
                          )}
                          {item.a_valider && !signed && (
                            <button
                              className="font-semibold text-ambre underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
                              onClick={() => confirmItem(key, i)}>
                              Confirmer (↵)
                            </button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              ) : content && typeof content === "object" ? (
                <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                  {Object.entries(content as Record<string, ChampTrace>).map(([k, champ]) => (
                    <div key={k} className={`rounded-md border p-2.5 ${champ.a_valider ? "border-ambre-bord bg-ambre-fond" : "border-trait"}`}>
                      <dt className="text-xs font-medium uppercase tracking-wide text-sourdine">{k.replaceAll("_", " ")}</dt>
                      <dd className="mt-0.5 flex items-center justify-between gap-2 text-sm text-encre">
                        <span>{champ.valeur || <em className="text-sourdine">non renseigné</em>}</span>
                        <ConfianceBadge confiance={champ.confiance} aValider={champ.a_valider} />
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-sm text-sourdine">Section vide.</p>
              )}
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}
