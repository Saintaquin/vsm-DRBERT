/** Composants UI maison dans l'esprit shadcn/ui : primitives accessibles,
 *  stylées par tokens Tailwind. Aucune dépendance externe (offline-first).
 *  Contraste ≥ 4.5:1 vérifié sur tous les couples texte/fond. */
import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, forwardRef } from "react";

const cx = (...c: (string | false | undefined)[]) => c.filter(Boolean).join(" ");

// ----------------------------------------------------------------- Button
type BtnVariant = "primary" | "secondary" | "danger" | "ghost";
const btnStyles: Record<BtnVariant, string> = {
  primary: "bg-sarcelle text-white hover:bg-sarcelle-fonce",
  secondary: "bg-carte text-encre border border-trait hover:bg-papier",
  danger: "bg-alerte text-white hover:bg-[#7E1F25]",
  ghost: "bg-transparent text-sarcelle hover:bg-sarcelle-pale",
};
export const Button = forwardRef<HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant }>(
  ({ variant = "primary", className, ...p }, ref) => (
    <button
      ref={ref}
      className={cx(
        "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sarcelle",
        btnStyles[variant], className)}
      {...p}
    />
  ));
Button.displayName = "Button";

// ----------------------------------------------------------------- Input
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => (
    <input
      ref={ref}
      className={cx(
        "w-full rounded-md border border-trait bg-carte px-3 py-2 text-sm text-encre",
        "placeholder:text-sourdine",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-sarcelle",
        className)}
      {...p}
    />
  ));
Input.displayName = "Input";

export function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium text-encre">{label}</label>
      {children}
    </div>
  );
}

// ----------------------------------------------------------------- Card
export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("rounded-lg border border-trait bg-carte shadow-sm", className)}>{children}</div>;
}
export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between border-b border-trait px-5 py-4">
      <div>
        <h2 className="text-base font-semibold text-encre">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-sourdine">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}
export const CardBody = ({ children, className }: { children: ReactNode; className?: string }) =>
  <div className={cx("px-5 py-4", className)}>{children}</div>;

// ----------------------------------------------------------------- Badges
/** Badge de confiance XAI : ambre = « À valider » (< seuil), sarcelle = ok. */
export function ConfianceBadge({ confiance, aValider }: { confiance: number; aValider: boolean }) {
  const pct = Math.round(confiance * 100);
  return aValider ? (
    <span
      className="inline-flex items-center gap-1 rounded border border-ambre-bord bg-ambre-fond px-1.5 py-0.5 text-xs font-semibold text-ambre"
      role="status" aria-label={`Confiance ${pct} % — champ à valider par un médecin`}
    >
      ⚠ À valider · {pct}%
    </span>
  ) : (
    <span
      className="inline-flex items-center rounded bg-sarcelle-pale px-1.5 py-0.5 text-xs font-medium text-sarcelle-fonce"
      aria-label={`Confiance ${pct} %`}
    >
      ✓ {pct}%
    </span>
  );
}

export function StatutBadge({ statut }: { statut: string }) {
  const map: Record<string, string> = {
    a_valider: "bg-ambre-fond text-ambre border-ambre-bord",
    valide: "bg-sarcelle-pale text-sarcelle-fonce border-sarcelle",
    signe: "bg-mousse-fond text-mousse border-mousse",
  };
  const label: Record<string, string> = { a_valider: "À valider", valide: "Validé", signe: "Signé" };
  return (
    <span className={cx("inline-flex rounded border px-2 py-0.5 text-xs font-semibold", map[statut] ?? map.a_valider)}>
      {label[statut] ?? statut}
    </span>
  );
}

// ----------------------------------------------------------------- divers
export function Spinner({ label }: { label: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-sourdine">
      <span aria-hidden className="h-4 w-4 animate-spin rounded-full border-2 border-sarcelle border-t-transparent" />
      {label}
    </span>
  );
}

export function Alerte({ kind, children }: { kind: "info" | "erreur" | "succes"; children: ReactNode }) {
  const styles = {
    info: "border-sarcelle bg-sarcelle-pale text-sarcelle-fonce",
    erreur: "border-alerte bg-[#FBEAEB] text-alerte",
    succes: "border-mousse bg-mousse-fond text-mousse",
  };
  return (
    <div role={kind === "erreur" ? "alert" : "status"}
      className={cx("rounded-md border px-3 py-2 text-sm", styles[kind])}>
      {children}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="rounded-lg border border-dashed border-trait px-6 py-10 text-center">
      <p className="font-medium text-encre">{title}</p>
      <p className="mt-1 text-sm text-sourdine">{hint}</p>
    </div>
  );
}
