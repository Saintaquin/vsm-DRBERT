import { useEffect, useMemo, useState } from "react";
import { ApiError, OcrResult, api } from "../api";
import { Alerte, Card, CardBody, CardHeader, Spinner } from "../components/ui";

/** Visualiseur de document : affiche le texte OCR (déjà anonymisé) et
 *  surligne le passage source demandé (XAI : clic sur un champ du VSM →
 *  navigation ici avec `highlight`). Le document image original reste
 *  chiffré dans le store et n'est jamais exposé au navigateur. */
export function DocumentViewer({ documentId, highlight }: { documentId: string; highlight?: string }) {
  const [ocr, setOcr] = useState<OcrResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOcr(null);
    api.getOcr(documentId)
      .then(setOcr)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Chargement impossible"));
  }, [documentId]);

  const fragments = useMemo(() => {
    if (!ocr) return [];
    const text = ocr.text;
    if (!highlight || !text.includes(highlight)) return [{ text, mark: false }];
    const out: { text: string; mark: boolean }[] = [];
    let rest = text;
    while (rest.includes(highlight)) {
      const i = rest.indexOf(highlight);
      if (i > 0) out.push({ text: rest.slice(0, i), mark: false });
      out.push({ text: highlight, mark: true });
      rest = rest.slice(i + highlight.length);
    }
    if (rest) out.push({ text: rest, mark: false });
    return out;
  }, [ocr, highlight]);

  return (
    <Card>
      <CardHeader
        title="Texte extrait (anonymisé)"
        subtitle={ocr ? `Document ${documentId} · ${ocr.pii_detected_count} PII masquée(s)` : documentId}
      />
      <CardBody>
        {error && <Alerte kind="erreur">{error}</Alerte>}
        {!ocr && !error && <Spinner label="Déchiffrement et chargement…" />}
        {ocr && (
          <>
            {highlight && (
              <p className="mb-3 text-sm text-sourdine">
                Passage source surligné : <mark className="rounded bg-ambre-fond px-1 text-ambre">{highlight.slice(0, 80)}{highlight.length > 80 ? "…" : ""}</mark>
              </p>
            )}
            <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md border border-trait bg-papier p-4 font-mono text-sm leading-6 text-encre"
              aria-label="Texte OCR du document">
              {fragments.map((f, i) =>
                f.mark
                  ? <mark key={i} className="rounded bg-ambre-fond px-0.5 font-semibold text-ambre">{f.text}</mark>
                  : <span key={i}>{f.text}</span>
              )}
            </pre>
          </>
        )}
      </CardBody>
    </Card>
  );
}
