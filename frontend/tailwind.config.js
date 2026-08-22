/** Palette « blouse » : ardoise clinique + sarcelle profonde, ambre réservé
 *  exclusivement à l'état « À valider » (signal XAI), vert mousse = signé. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        encre: "#1C2B33",
        sarcelle: { DEFAULT: "#0E6E6B", fonce: "#0A5350", pale: "#E3F1F0" },
        ambre: { DEFAULT: "#8A5A00", fond: "#FFF4DC", bord: "#E0B65C" },
        mousse: { DEFAULT: "#2F6B3A", fond: "#E9F3EA" },
        papier: "#F7F8F7",
        carte: "#FFFFFF",
        trait: "#D8DEDD",
        sourdine: "#5A6B70",
        alerte: "#A4282F",
      },
      fontFamily: {
        corps: ["system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
