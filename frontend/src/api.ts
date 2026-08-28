/** Client API local — toutes les requêtes vont vers le backend FastAPI
 *  servi sur 127.0.0.1 (même origine en production : FastAPI sert dist/).
 *  Le cookie de session est httpOnly ; le token CSRF est renvoyé dans
 *  chaque requête via l'en-tête X-CSRF-Token (double submit). */

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  if (init.body && typeof init.body === "string") headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* corps non JSON */ }
    throw new ApiError(res.status, detail);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

// ----------------------------------------------------------------- types
export interface User { username: string; role: "medecin" | "secretaire" | "admin"; csrf: string; }
export interface DocMeta { id: string; dossier_id: string; filename: string; sha256: string; created_at: string; }
export interface ChampTrace {
  valeur: string; confiance: number; a_valider: boolean;
  source?: { document_id?: string; page?: number; passage?: string };
  moteurs?: { ocr?: string; nlp?: string };
  moteur_nlp?: string;
  correction_ocr?: boolean;
  origine?: "llm" | "regles" | "drbert" | string;
  code_normalise?: { systeme: string; code: string; libelle: string } | null;
}
/** Rapport de la phase NLP (XAI) — rempli par le backend : moteur réel
 *  (DrBERT-CASM2 par défaut, règles en repli, LLM sur demande). */
export interface NlpReport {
  moteur: string;
  statut: "regles" | "drbert" | "modele_absent" | "repli_regles" | "llm_complet" | "llm_extraction_seule" | "llm_partiel" | string;
  raison?: string | null;
  phase_correction_ocr?: boolean;
  nb_corrections_ocr?: number;
  duree_correction_sec?: number | null;
  duree_extraction_sec?: number | null;
  modele?: string | null;
  nb_chunks?: number;
  assist_llm?: boolean;
}
export interface Vsm {
  vsm_id?: string; document_id?: string; statut: string;
  date_generation: string; avertissement: string;
  patient?: Record<string, ChampTrace>;
  medecin_traitant?: Record<string, ChampTrace>;
  sections: Record<string, ChampTrace[] | Record<string, ChampTrace>>;
  completude?: Record<string, number>;
  provenance?: { moteur_nlp?: string; nlp?: NlpReport; documents_sources?: unknown[] };
  signature?: { signe_par: string; date_signature: string; empreinte_vsm: string } | null;
}
export interface VsmListItem { id: string; document_id: string; statut: string; created_at: string; }
export interface AuditEntry { ts: string; actor: string; event: string; details: Record<string, unknown>; }
export interface OcrResult { document_id: string; text: string; pages?: { page: number; text: string }[]; pii_detected_count: number; }
export interface ProcessResult {
  vsm_id: string; vsm: Vsm; pii_detected_count: number;
  processing_report: { pages_total: number; pages_ok: number; duration_sec: number; engine: string; anomalies: string[] };
  nlp_report?: NlpReport;
}

/** État d'un traitement asynchrone (voir startProcess / processStatus). */
export interface ProcessJob {
  job_id: string;
  document_id: string;
  filename: string;
  status: "processing" | "done" | "error";
  step?: string;
  vsm_id?: string | null;
  error?: string | null;
  result?: ProcessResult | null;
}

// ----------------------------------------------------------------- endpoints
export interface Health {
  status: string;
  max_upload_mb?: number;
  available_engines?: string[];
  /** Encodeur DrBERT-CASM2 (moteur NLP par défaut) présent en local ? */
  drbert_available?: boolean;
  drbert_path?: string;
  /** LLM génératif optionnel (n'est plus dans le flux par défaut). */
  llm_available?: boolean;
  llm_reason?: string;
}

export interface StatsEntry { code: string; libelle: string; count: number | null; masque: boolean; }

export interface Stats {
  total: number;
  par_statut: Record<string, number>;
  par_mois: Record<string, number>;
  pathologies: StatsEntry[];
  traitements: StatsEntry[];
  completude: Record<string, number>;
  avertissement: string;
  seuil: number;
}

export const api = {
  health: () => request<Health>("/health"),
  bootstrap: (username: string, password: string, role: string) =>
    request<{ created: string }>("/auth/bootstrap", { method: "POST", body: JSON.stringify({ username, password, role }) }),
  login: async (username: string, password: string) => {
    const u = await request<User>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
    setCsrf(u.csrf);
    return u;
  },
  logout: async () => { await request("/auth/logout", { method: "POST" }); setCsrf(null); },
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ document_id: string; sha256: string }>("/documents/upload", { method: "POST", body: fd });
  },
  /** Lance le traitement en arrière-plan (réponse immédiate avec job_id).
   *  Moteur NLP par défaut : « drbert » — encodeur DrBERT-CASM2 local
   *  (décision étape 0 : rapide, borné, sans invention ; repli règles auto).
   *  Le LLM génératif reste disponible via l'API (nlp_engine="llm"). */
  startProcess: (id: string, engine: string, anonymize_mode: string, nlp_engine = "drbert") =>
    request<{ job_id: string; status: string }>(`/documents/${id}/process`, {
      method: "POST",
      body: JSON.stringify({ engine, anonymize_mode, nlp_engine }),
    }),
  /** État du traitement asynchrone : progression puis résultat complet. */
  processStatus: (jobId: string) =>
    request<ProcessJob>(`/documents/process/${jobId}`),
  listDocuments: () => request<DocMeta[]>("/documents"),
  getOcr: (id: string) => request<OcrResult>(`/documents/${id}/ocr`),
  forget: (dossierId: string) =>
    request<{ deleted_documents: number; pii_mapping_removed: boolean }>(`/documents/${dossierId}`, { method: "DELETE" }),
  listVsm: () => request<VsmListItem[]>("/vsm"),
  getVsm: (id: string) => request<Vsm>(`/vsm/${id}`),
  validateVsm: (id: string, body: { sections?: Vsm["sections"]; statut: string; signe_par?: string }) =>
    request<Vsm>(`/vsm/${id}/validate`, { method: "POST", body: JSON.stringify(body) }),
  /** Relance la phase LLM locale sur les champs « À valider » du VSM. */
  llmAssist: (id: string) =>
    request<{ vsm: Vsm; champs_mis_a_jour: number; nlp_report: NlpReport }>(`/vsm/${id}/llm-assist`, { method: "POST" }),
  exportHtmlUrl: (id: string) => `/vsm/${id}/export?fmt=html`,
  exportPdfUrl: (id: string) => `/vsm/${id}/export?fmt=pdf`,
  stats: () => request<Stats>("/stats"),
  audit: (limit = 200) => request<{ chain_valid: boolean; entries: AuditEntry[] }>(`/audit?limit=${limit}`),
};
