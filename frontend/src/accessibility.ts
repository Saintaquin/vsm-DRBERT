/** Accessibilité — préférences locales (RGPD-safe : aucune donnée patient,
 *  localStorage uniquement) et aides (focus trap, annonces ARIA).
 *
 *  Fonctionnalités validées (audit outputs/AUDIT_ACCESSIBILITE.md) :
 *  A1 taille de texte · A2 contraste renforcé · A3 thème sombre ·
 *  A4 police Atkinson Hyperlegible (embarquée) · A5 animations réduites ·
 *  B1 mode lecteur d'écran (annonces ARIA) · B2 focus trap · C1 raccourcis. */

import { RefObject, useCallback, useEffect, useState } from "react";

export type TextScale = "std" | "lg" | "xl";
export type ThemePref = "auto" | "light" | "dark";
export type ContrastPref = "normal" | "high";
export type FontPref = "default" | "hyperlegible";
export type MotionPref = "auto" | "reduce";

export interface Prefs {
  textScale: TextScale;
  theme: ThemePref;
  contrast: ContrastPref;
  font: FontPref;
  motion: MotionPref;
  screenReader: boolean;
}

const STORAGE_KEY = "vsm-prefs";

export const DEFAULT_PREFS: Prefs = {
  textScale: "std",
  theme: "auto",
  contrast: "normal",
  font: "default",
  motion: "auto",
  screenReader: false,
};

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function savePrefs(p: Prefs): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    /* stockage indisponible : préférences non persistées (session seulement) */
  }
}

const systemDark = () =>
  window.matchMedia("(prefers-color-scheme: dark)").matches;
const systemReduceMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Applique les préférences sur <html> via des attributs data-* (voir index.css). */
export function applyPrefs(p: Prefs): void {
  const root = document.documentElement;
  root.dataset.textScale = p.textScale;
  const dark = p.theme === "dark" || (p.theme === "auto" && systemDark());
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.contrast = p.contrast;
  root.dataset.font = p.font;
  const reduce = p.motion === "reduce" || (p.motion === "auto" && systemReduceMotion());
  root.dataset.motion = reduce ? "reduce" : "none";
  root.dataset.screenReader = p.screenReader ? "on" : "off";
}

/** Hook : préférences d'accessibilité, appliquées au montage et à chaque mise à jour. */
export function usePrefs(): [Prefs, (p: Prefs) => void] {
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  useEffect(() => {
    applyPrefs(prefs);
  }, [prefs]);
  const setPrefs = useCallback((p: Prefs) => {
    applyPrefs(p);
    savePrefs(p);
    setPrefsState(p);
  }, []);
  return [prefs, setPrefs];
}

/** Annonce ARIA (mode lecteur d'écran B1) : écrite dans la zone live globale,
 *  uniquement si le mode « lecteur d'écran » est actif dans les préférences. */
export function announce(message: string): void {
  const root = document.documentElement;
  if (root.dataset.screenReader !== "on") return;
  const el = document.getElementById("vsm-live");
  if (el) el.textContent = message;
}

/** Piège le focus dans la modale (WCAG 2.4.3) et le restaure à la fermeture. */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
): void {
  useEffect(() => {
    if (!active) return;
    const el = ref.current;
    if (!el) return;
    const prev = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        el.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((f) => f.offsetParent !== null);
    const first = focusables()[0];
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const list = focusables();
      if (!list.length) return;
      const f = list[0];
      const l = list[list.length - 1];
      const cur = document.activeElement as HTMLElement;
      if (e.shiftKey && (cur === f || cur === el)) {
        e.preventDefault();
        l.focus();
      } else if (!e.shiftKey && cur === l) {
        e.preventDefault();
        f.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [active, ref]);
}
