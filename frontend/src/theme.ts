import { useEffect, useState } from "react";

// Recharts needs concrete colours, so resolve the CSS tokens and re-resolve when the
// theme changes (OS preference or the in-app toggle).
const TOKENS = [
  "surface", "surface-2", "ink", "ink-2", "muted", "grid", "axis", "accent",
  "spy", "qqq", "dia", "iwm", "vti", "strat-a", "strat-b", "cash", "good-text", "bad-text",
] as const;
export type Colors = Record<(typeof TOKENS)[number], string>;

function read(): Colors {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, css.getPropertyValue(`--${t}`).trim()])) as Colors;
}

export function useColors(): Colors {
  const [colors, setColors] = useState<Colors>(read);
  useEffect(() => {
    const update = () => setColors(read());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      mq.removeEventListener("change", update);
      obs.disconnect();
    };
  }, []);
  return colors;
}

/** Entity -> colour token. Colour follows the ETF everywhere in the app. */
export const SYMBOL_TOKEN: Record<string, keyof Colors> = {
  SPY: "spy", QQQ: "qqq", DIA: "dia", IWM: "iwm", VTI: "vti",
};
