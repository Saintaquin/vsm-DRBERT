/** Panneau « Accessibilité » — inséré dans la page Paramètres.
 *  Fonctionnalités validées : A1–A5 (affichage), B1 (lecteur d'écran),
 *  C1 (raccourcis), D1 (persistance + réinitialisation). */
import { FontPref, MotionPref, Prefs, TextScale, ThemePref, usePrefs } from "../accessibility";
import { Button, Card, CardBody, CardHeader } from "../components/ui";

function Radio<T extends string>({ label, value, current, onChange }: {
  label: string; value: T; current: T; onChange: (v: T) => void;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-1.5 text-sm">
      <input type="radio" name={label} value={value} checked={current === value}
        onChange={() => onChange(value)}
        className="accent-sarcelle focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle" />
      {label}
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-sarcelle focus-visible:outline focus-visible:outline-2 focus-visible:outline-sarcelle" />
      {label}
    </label>
  );
}

export function AccessibilitySettings() {
  const [prefs, setPrefs] = usePrefs();
  const set = <K extends keyof Prefs>(k: K, v: Prefs[K]) => setPrefs({ ...prefs, [k]: v });

  const scales: { id: TextScale; label: string }[] = [
    { id: "std", label: "Standard (100 %)" },
    { id: "lg", label: "Grand (112,5 %)" },
    { id: "xl", label: "Très grand (125 %)" },
  ];
  const themes: { id: ThemePref; label: string }[] = [
    { id: "auto", label: "Automatique (système)" },
    { id: "light", label: "Clair" },
    { id: "dark", label: "Sombre" },
  ];
  const fonts: { id: FontPref; label: string }[] = [
    { id: "default", label: "Police par défaut" },
    { id: "hyperlegible", label: "Atkinson Hyperlegible (basse vision)" },
  ];
  const motions: { id: MotionPref; label: string }[] = [
    { id: "auto", label: "Suivre le système" },
    { id: "reduce", label: "Toujours réduire" },
  ];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Affichage — malvoyants / basse vision"
          subtitle="Préférences locales à cette machine (aucune donnée n'est enregistrée)"
        />
        <CardBody className="space-y-4 text-sm">
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="font-medium text-sourdine">A1 · Taille de texte</legend>
            {scales.map((s) => (
              <Radio key={s.id} label={s.label} value={s.id} current={prefs.textScale}
                onChange={(v) => set("textScale", v)} />
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="font-medium text-sourdine">A3 · Thème</legend>
            {themes.map((t) => (
              <Radio key={t.id} label={t.label} value={t.id} current={prefs.theme}
                onChange={(v) => set("theme", v)} />
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="font-medium text-sourdine">A2 · Contraste renforcé</legend>
            <Toggle label="Activer le contraste élevé (WCAG AAA)" checked={prefs.contrast === "high"}
              onChange={(v) => set("contrast", v ? "high" : "normal")} />
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="font-medium text-sourdine">A4 · Police</legend>
            {fonts.map((f) => (
              <Radio key={f.id} label={f.label} value={f.id} current={prefs.font}
                onChange={(v) => set("font", v)} />
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-4">
            <legend className="font-medium text-sourdine">A5 · Animations</legend>
            {motions.map((m) => (
              <Radio key={m.id} label={m.label} value={m.id} current={prefs.motion}
                onChange={(v) => set("motion", v)} />
            ))}
          </fieldset>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Lecture — aveugles / lecteur d'écran"
          subtitle="Annonces ARIA renforcées (résultats de traitement, statuts, erreurs)"
        />
        <CardBody className="space-y-3 text-sm">
          <Toggle label="B1 · Mode lecteur d'écran (annonces vocales des actions)"
            checked={prefs.screenReader} onChange={(v) => set("screenReader", v)} />
          <p className="text-sourdine">
            Navigation clavier : <kbd className="rounded border border-trait bg-papier px-1">Tab</kbd> parcourt
            les champs, <kbd className="rounded border border-trait bg-papier px-1">↵</kbd> confirme un champ,
            <kbd className="rounded border border-trait bg-papier px-1">Ctrl+↵</kbd> enregistre le VSM,
            <kbd className="rounded border border-trait bg-papier px-1">?</kbd> ouvre l'aide des raccourcis.
          </p>
        </CardBody>
      </Card>

      <div className="flex items-center gap-3">
        <Button variant="secondary" onClick={() => setPrefs({
          textScale: "std", theme: "auto", contrast: "normal", font: "default",
          motion: "auto", screenReader: false,
        })}>
          Réinitialiser les paramètres d'accessibilité
        </Button>
        <p className="text-xs text-sourdine">D1 · Préférences sauvegardées localement (réglages système détectés par défaut).</p>
      </div>
    </div>
  );
}
