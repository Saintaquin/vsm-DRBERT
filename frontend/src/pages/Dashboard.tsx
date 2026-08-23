import { ChangeEvent, useEffect, useRef, useState } from "react";
import { ApiError, DocMeta, ProcessResult, VsmListItem, api } from "../api";
import { announce } from "../accessibility";
import { Alerte, Button, Card, CardBody, CardHeader, EmptyState, Spinner, StatutBadge } from "../components/ui";

interface Props {
  onOpenVsm: (vsmId: string) => void;
  onOpenDocument: (documentId: string) => void;
}

export function Dashboard({ onOpenVsm, onOpenDocument }: Props) {
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [vsms, setVsms] = useState<VsmListItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastReport, setLastReport] = useState<ProcessResult | null>(null);
  const [anonymizeMode, setAnonymizeMode] = useState<"pseudo" | "strict">("pseudo");
  // LLM local PAR DÉFAUT (toutes machines) ; les règles servent de repli
  // automatique côté backend si le modèle est absent (docs/ADR-0009).
  const nlpEngine = "llm" as const;
  const [llmAvailable, setLlmAvailable] = useState<boolean>(true);
  const [maxUploadMb, setMaxUploadMb] = useState<number>(50);
  const [ocrEngine, setOcrEngine] = useState<string>("tesseract");
  const [availableEngines, setAvailableEngines] = useState<string[]>(["tesseract"]);
  const fileRef = useRef<HTMLInputElement>(null);

  // Configuration publiée par le backend : limite d'upload, moteurs OCR
  // (« unlimited » n'apparaît que sur poste NVIDIA) et disponibilité du LLM.
  useEffect(() => {
    api.health()
      .then((h) => {
        if (typeof h.max_upload_mb === "number") setMaxUploadMb(h.max_upload_mb);
        if (typeof h.llm_available === "boolean") setLlmAvailable(h.llm_available);
        if (Array.isArray(h.available_engines) && h.available_engines.length) {
          setAvailableEngines(h.available_engines);
          setOcrEngine(h.available_engines.includes("tesseract") ? "tesseract" : h.available_engines[0]);
        }
      })
      .catch(() => { /* backend indisponible : garder les valeurs par défaut */ });
  }, []);

  const refresh = async () => {
    try {
      const [d, v] = await Promise.all([api.listDocuments(), api.listVsm()]);
      setDocs(d);
      setVsms(v);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) window.location.reload();
    }
  };
  useEffect(() => { void refresh(); }, []);

  async function handleUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setBusy("Envoi du document…");
    announce(`Envoi du document ${file.name}`);
    try {
      const { document_id } = await api.upload(file);
      const { job_id } = await api.startProcess(document_id, ocrEngine, anonymizeMode, nlpEngine);
      announce("Traitement du document en cours");
      // Interrogation de l'état (chaque appel maintient la session vivante) :
      // le traitement peut durer plusieurs minutes sur les gros PDF.
      let job = await api.processStatus(job_id);
      while (job.status === "processing") {
        setBusy(`Traitement en cours — ${job.step ?? "analyse"}…`);
        await new Promise((r) => setTimeout(r, 2000));
        job = await api.processStatus(job_id);
      }
      if (job.status === "error") {
        throw new ApiError(500, job.error ?? "Erreur pendant le traitement");
      }
      const result = job.result!;
      announce(`Document traité : ${result.processing_report.pages_ok} pages, ${result.pii_detected_count} informations identifiantes masquées`);
      setLastReport(result);
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Erreur pendant le traitement";
      announce(`Erreur : ${msg}`);
      setError(msg);
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleForget(dossierId: string) {
    if (!window.confirm("Supprimer définitivement ce dossier (documents, résultats, VSM, mapping PII) ? Cette action est irréversible — droit à l'oubli RGPD.")) return;
    try {
      await api.forget(dossierId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Suppression impossible");
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Nouveau document"
          subtitle={`PDF ou image scannée (PNG, JPG, TIFF) — ${maxUploadMb} Mo max (configurable via VSM_MAX_UPLOAD_MB). L'anonymisation est appliquée avant toute extraction.`}
        />
        <CardBody className="space-y-4">
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="sr-only">Mode d'anonymisation</legend>
            <span className="text-sm font-medium text-encre">Anonymisation :</span>
            {(["pseudo", "strict"] as const).map((m) => (
              <label key={m} className="inline-flex cursor-pointer items-center gap-1.5 text-sm">
                <input type="radio" name="anonymize" value={m} checked={anonymizeMode === m}
                  onChange={() => setAnonymizeMode(m)}
                  className="accent-sarcelle focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle" />
                {m === "pseudo" ? "Pseudonymisation (réversible via coffre-fort)" : "Stricte (irréversible)"}
              </label>
            ))}
          </fieldset>
          {!llmAvailable && (
            <Alerte kind="info">
              LLM local non téléchargé — l'extraction utilisera le moteur de règles en repli. Pour activer le LLM
              (Qwen 2.5 3B, ~2 Go, toutes machines) : <code className="font-mono">python -m src.extraction_nlp.llm</code>
            </Alerte>
          )}
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="sr-only">Moteur OCR</legend>
            <span className="text-sm font-medium text-encre">OCR :</span>
            {availableEngines.map((m) => (
              <label key={m} className="inline-flex cursor-pointer items-center gap-1.5 text-sm">
                <input type="radio" name="ocr" value={m} checked={ocrEngine === m}
                  onChange={() => setOcrEngine(m)}
                  className="accent-sarcelle focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle" />
                {m === "unlimited"
                  ? "Unlimited-OCR (NVIDIA, haute qualité)"
                  : m === "tesseract" ? "Tesseract (défaut, CPU)" : m}
              </label>
            ))}
          </fieldset>
          <div className="flex items-center gap-3">
            <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
              onChange={handleUpload} disabled={!!busy} aria-label="Choisir un document à traiter"
              className="text-sm file:mr-3 file:rounded-md file:border-0 file:bg-sarcelle file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-sarcelle-fonce file:cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle" />
            {busy && <Spinner label={busy} />}
          </div>
          {error && <Alerte kind="erreur">{error}</Alerte>}
          {lastReport && (
            <Alerte kind="succes">
              Document traité : {lastReport.processing_report.pages_ok} page(s) en{" "}
              {lastReport.processing_report.duration_sec.toFixed(1)} s ·{" "}
              {lastReport.pii_detected_count} PII détectée(s) et masquée(s).{" "}
              <button className="font-semibold underline underline-offset-2"
                onClick={() => onOpenVsm(lastReport.vsm_id)}>
                Ouvrir le VSM →
              </button>
            </Alerte>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Volets de Synthèse Médicale" subtitle={`${vsms.length} VSM`} />
          <CardBody>
            {vsms.length === 0 ? (
              <EmptyState title="Aucun VSM" hint="Téléversez un document scanné pour générer votre premier VSM." />
            ) : (
              <ul className="divide-y divide-trait" aria-label="Liste des VSM">
                {vsms.map((v) => (
                  <li key={v.id} className="flex items-center justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <button onClick={() => onOpenVsm(v.id)}
                        className="block truncate font-medium text-sarcelle underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
                        {v.id}
                      </button>
                      <p className="text-xs text-sourdine">{new Date(v.created_at).toLocaleString("fr-FR")}</p>
                    </div>
                    <StatutBadge statut={v.statut} />
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Documents sources" subtitle={`${docs.length} document(s) — stockés chiffrés`} />
          <CardBody>
            {docs.length === 0 ? (
              <EmptyState title="Aucun document" hint="Les documents téléversés apparaîtront ici avec leur empreinte SHA-256." />
            ) : (
              <ul className="divide-y divide-trait" aria-label="Liste des documents">
                {docs.map((d) => (
                  <li key={d.id} className="flex items-center justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <button onClick={() => onOpenDocument(d.id)}
                        className="block truncate font-medium text-sarcelle underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle">
                        {d.filename}
                      </button>
                      <p className="truncate font-mono text-xs text-sourdine" title={d.sha256}>
                        SHA-256 {d.sha256.slice(0, 16)}…
                      </p>
                    </div>
                    <Button variant="danger" onClick={() => handleForget(d.dossier_id)}
                      aria-label={`Supprimer définitivement le dossier ${d.filename} (droit à l'oubli)`}>
                      Oublier
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
