import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, OcrResult, api } from "../api";
import { Alerte, Card, CardBody, CardHeader, Spinner } from "../components/ui";

/** Visualiseur de document : affiche le texte OCR (déjà anonymisé) et
 *  surligne les passages sources demandés (XAI : clic sur un champ du VSM →
 *  navigation ici avec `highlights`). Le document image original reste
 *  chiffré dans le store et n'est jamais exposé au navigateur.
 *
 *  Mentions MULTIPLES : une entrée fusionnée par P2 porte les passages
 *  distincts de chaque mention (« Ulcère bulbaire » — 15 mentions) — on
 *  surligne TOUTES les occurrences de TOUS les passages, avec un sélecteur
 *  « Mention k/N » pour faire défiler chaque mention au centre de la vue.
 *  Un seul extrait surligné cachait les autres (constat d'audit UX). */
export function DocumentViewer({ documentId, highlights }: { documentId: string; highlights?: string[] }) {
  const [ocr, setOcr] = useState<OcrResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mention, setMention] = useState(0);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    setOcr(null);
    api.getOcr(documentId)
      .then(setOcr)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Chargement impossible"));
  }, [documentId]);

  // Nouvelle sélection de passages → retour à la première mention.
  useEffect(() => {
    setMention(0);
  }, [documentId, highlights]);

  // Intervalles surlignés : toutes les occurrences de CHAQUE passage demandé
  // (mentions multiples d'une même entrée), intervalles chevauchants FUSIONNÉS
  // (« ulcère » imbriqué dans « ulcère bulbaire » ne compte qu'une mention).
  const ranges = useMemo((): [number, number][] => {
    if (!ocr || !highlights?.length) return [];
    const text = ocr.text;
    const brutes: [number, number][] = [];
    for (const h of highlights) {
      if (!h) continue;
      let from = 0;
      for (;;) {
        const i = text.indexOf(h, from);
        if (i < 0) break;
        brutes.push([i, i + h.length]);
        from = i + h.length;
      }
    }
    brutes.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const fusionnees: [number, number][] = [];
    for (const r of brutes) {
      const dernier = fusionnees[fusionnees.length - 1];
      if (dernier && r[0] < dernier[1]) {
        dernier[1] = Math.max(dernier[1], r[1]);
      } else {
        fusionnees.push([r[0], r[1]]);
      }
    }
    return fusionnees;
  }, [ocr, highlights]);

  const fragments = useMemo(() => {
    if (!ocr) return [] as { text: string; mark: boolean }[];
    const out: { text: string; mark: boolean }[] = [];
    let curseur = 0;
    for (const [debut, fin] of ranges) {
      if (debut > curseur) out.push({ text: ocr.text.slice(curseur, debut), mark: false });
      out.push({ text: ocr.text.slice(debut, fin), mark: true });
      curseur = fin;
    }
    if (curseur < ocr.text.length) out.push({ text: ocr.text.slice(curseur), mark: false });
    return out;
  }, [ocr, ranges]);

  // Défiler : amener la mention COURANTE au centre de la vue — au
  // chargement (première mention) comme à chaque Précédent/Suivant.
  useEffect(() => {
    if (!ocr || ranges.length === 0) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const t = window.setTimeout(() => {
      const marques = preRef.current?.querySelectorAll("mark");
      if (!marques || !marques[mention]) return;
      marques[mention].scrollIntoView({
        block: "center",
        behavior: reduce ? "auto" : "smooth",
      });
    }, 60);
    return () => window.clearTimeout(t);
  }, [ocr, ranges, mention]);

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
            {highlights && highlights.length > 0 && (
              <div className="mb-3 flex flex-wrap items-center gap-3 text-sm text-sourdine">
                <span>
                  Passages surlignés :{" "}
                  <mark className="rounded bg-ambre-fond px-1 text-ambre">
                    {ranges.length > 0
                      ? `${ranges.length} mention${ranges.length > 1 ? "s" : ""}`
                      : "aucune occurrence trouvée"}
                  </mark>
                </span>
                {ranges.length > 1 && (
                  <span className="inline-flex items-center gap-2">
                    <button
                      onClick={() => setMention((m) => Math.max(0, m - 1))}
                      disabled={mention === 0}
                      aria-label="Mention précédente"
                      className="rounded border border-trait bg-carte px-2 py-0.5 text-xs font-medium text-encre hover:bg-papier disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
                    >
                      ‹ Précédent
                    </button>
                    <span aria-live="polite">
                      Mention {mention + 1}/{ranges.length}
                    </span>
                    <button
                      onClick={() => setMention((m) => Math.min(ranges.length - 1, m + 1))}
                      disabled={mention === ranges.length - 1}
                      aria-label="Mention suivante"
                      className="rounded border border-trait bg-carte px-2 py-0.5 text-xs font-medium text-encre hover:bg-papier disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle"
                    >
                      Suivant ›
                    </button>
                  </span>
                )}
              </div>
            )}
            <pre ref={preRef} className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md border border-trait bg-papier p-4 font-mono text-sm leading-6 text-encre"
              aria-label="Texte OCR du document">
              {fragments.map((f, i) =>
                f.mark
                  ? <mark key={i} className="rounded bg-ambre-fond px-0.5 font-semibold text-ambre outline outline-2 outline-offset-1 outline-ambre-bord">{f.text}</mark>
                  : <span key={i}>{f.text}</span>
              )}
            </pre>
          </>
        )}
      </CardBody>
    </Card>
  );
}
