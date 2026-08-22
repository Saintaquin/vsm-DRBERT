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
  code_normalise?: { systeme: string; code: string; libelle: string } | null;
}
export interface Vsm {
  vsm_id?: string; document_id?: string; statut: string;
  date_generation: string; avertissement: string;
  patient?: Record<string, ChampTrace>;
  medecin_traitant?: Record<string, ChampTrace>;
  sections: Record<string, ChampTrace[] | Record<string, ChampTrace>>;
  completude?: Record<string, number>;
  signature?: { signe_par: string; date_signature: string; empreinte_vsm: string } | null;
}
export interface VsmListItem { id: string; document_id: string; statut: string; created_at: string; }
export interface AuditEntry { ts: string; actor: string; event: string; details: Record<string, unknown>; }
export interface OcrResult { document_id: string; text: string; pages?: { page: number; text: string }[]; pii_detected_count: number; }
export interface ProcessResult {
  vsm_id: string; vsm: Vsm; pii_detected_count: number;
  processing_report: { pages_total: number; pages_ok: number; duration_sec: number; engine: string; anomalies: string[] };
}

// ----------------------------------------------------------------- endpoints
export interface Health {
  status: string;
  max_upload_mb?: number;
  available_engines?: string[];
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
  process: (id: string, engine: string, anonymize_mode: string, nlp_engine = "rules") =>
    request<ProcessResult>(`/documents/${id}/process`, { method: "POST", body: JSON.stringify({ engine, anonymize_mode, nlp_engine }) }),
  listDocuments: () => request<DocMeta[]>("/documents"),
  getOcr: (id: string) => request<OcrResult>(`/documents/${id}/ocr`),
  forget: (dossierId: string) =>
    request<{ deleted_documents: number; pii_mapping_removed: boolean }>(`/documents/${dossierId}`, { method: "DELETE" }),
  listVsm: () => request<VsmListItem[]>("/vsm"),
  getVsm: (id: string) => request<Vsm>(`/vsm/${id}`),
  validateVsm: (id: string, body: { sections?: Vsm["sections"]; statut: string; signe_par?: string }) =>
    request<Vsm>(`/vsm/${id}/validate`, { method: "POST", body: JSON.stringify(body) }),
  exportHtmlUrl: (id: string) => `/vsm/${id}/export?fmt=html`,
  exportPdfUrl: (id: string) => `/vsm/${id}/export?fmt=pdf`,
  stats: () => request<Stats>("/stats"),
  audit: (limit = 200) => request<{ chain_valid: boolean; entries: AuditEntry[] }>(`/audit?limit=${limit}`),
};
