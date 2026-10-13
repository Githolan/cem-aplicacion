import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const raiz = resolve(__dirname, "..");

describe("manifest PWA", () => {
  const manifest = JSON.parse(readFileSync(resolve(raiz, "public/manifest.webmanifest"), "utf-8")) as {
    name: string;
    start_url: string;
    display: string;
    icons: { src: string; purpose: string }[];
  };

  it("declara la aplicación instalable", () => {
    expect(manifest.name).toBe("Consorcio en Mano");
    expect(manifest.start_url).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons.map((i) => i.purpose).sort()).toEqual(["any", "maskable"]);
  });

  it("cada icono existe en public/", () => {
    for (const icono of manifest.icons) {
      expect(() => readFileSync(resolve(raiz, "public", icono.src.replace(/^\//, "")))).not.toThrow();
    }
  });

  it("index.html enlaza el manifest", () => {
    const html = readFileSync(resolve(raiz, "index.html"), "utf-8");
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest" />');
  });
});
