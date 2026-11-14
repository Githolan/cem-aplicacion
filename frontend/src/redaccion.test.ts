import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

// Regla de redacción (pregunta 9 del propietario, 13-11-2026; tanda 4): todo texto que ve la persona
// va impersonal y en infinitivo, sin voseo, sin tuteo, sin «usted» y sin primera persona. Las palabras
// y los controles salen de `contratos/cem_contracts/pantallas_datos/redaccion.json`, el mismo archivo
// que usa `cem_contracts.redaccion` para los mensajes del backend: los dos clasifican igual sus
// controles. Se recorre cada texto del código (literales, plantillas y texto JSX), no lo que una
// prueba alcanzó a dibujar: un aviso que sale una vez por mes también se revisa.

type Reglas = {
  segunda_persona: string[];
  voseo_terminaciones: string[];
  voseo_no_son: string[];
  preterito_terminaciones: string[];
  preterito_no_son: string[];
  primera_plural_terminaciones: string[];
  primera_plural_no_son: string[];
  imperativo_al_empezar_no_son: string[];
  formas: string[];
  control_con_falta: string[];
  control_sin_falta: string[];
};

const ARCHIVO_REGLAS = resolve(process.cwd(), "../contratos/cem_contracts/pantallas_datos/redaccion.json");
const CARPETA = resolve(process.cwd(), "src");
/** Generado desde el contrato: no tiene textos de pantalla, y no se escribe a mano. */
const NO_SE_REVISAN = new Set(["api/tipos.ts"]);

const reglas = JSON.parse(readFileSync(ARCHIVO_REGLAS, "utf8")) as Reglas;
const segunda = new Set(reglas.segunda_persona);
const formas = new Set(reglas.formas);
const voseoNoSon = new Set(reglas.voseo_no_son);
const preteritoNoSon = new Set(reglas.preterito_no_son);
const primeraNoSon = new Set(reglas.primera_plural_no_son);
const alEmpezarNoSon = new Set(reglas.imperativo_al_empezar_no_son);
const termina = (palabra: string, finales: readonly string[]) => finales.some((f) => palabra.endsWith(f));

/** Las palabras de `texto` que rompen la regla, con su motivo (igual que `cem_contracts.redaccion.faltas`). */
export function faltas(texto: string): string[] {
  const encontradas: string[] = [];
  for (const palabra of (texto.match(/\p{L}+/gu) ?? []).map((p) => p.toLowerCase())) {
    if (segunda.has(palabra)) encontradas.push(`${palabra} (segunda persona)`);
    else if (formas.has(palabra)) encontradas.push(`${palabra} (voseo, tuteo o primera persona)`);
    else if (termina(palabra, reglas.voseo_terminaciones) && !voseoNoSon.has(palabra)) encontradas.push(`${palabra} (voseo)`);
    else if (termina(palabra, reglas.preterito_terminaciones) && !preteritoNoSon.has(palabra))
      encontradas.push(`${palabra} (pretérito en segunda persona)`);
    else if (termina(palabra, reglas.primera_plural_terminaciones) && !primeraNoSon.has(palabra))
      encontradas.push(`${palabra} (primera persona del plural)`);
  }
  for (const m of texto.matchAll(/(?:^|[.:!?]\s+|[¿¡«(]\s*)(\p{L}+)/gu)) {
    const palabra = (m[1] ?? "").toLowerCase();
    if (termina(palabra, ["á", "é", "í"]) && !alEmpezarNoSon.has(palabra) && !formas.has(palabra))
      encontradas.push(`${palabra} (voseo o primera persona al empezar)`);
  }
  return encontradas;
}

function archivos(carpeta: string): string[] {
  return readdirSync(carpeta).flatMap((nombre) => {
    const ruta = join(carpeta, nombre);
    if (statSync(ruta).isDirectory()) return archivos(ruta);
    return /\.tsx?$/.test(nombre) && !/\.test\.tsx?$/.test(nombre) ? [ruta] : [];
  });
}

/** Una ruta, una clave o una clase («/api/v1/me/consorcios», «registrada_por_vos»), igual que en el backend. */
const esIdentificador = (texto: string) => !/\s/.test(texto) && /[/_:]/.test(texto);

/** Un literal que es un valor del contrato y no un texto: se compara (`le_toca_a === "vos"`), es un
 * caso de `switch`, un tipo o la clave de un objeto. */
function esValorDeCodigo(nodo: ts.Node): boolean {
  const padre = nodo.parent;
  if (ts.isLiteralTypeNode(padre) || ts.isCaseClause(padre)) return true;
  if (ts.isPropertyAssignment(padre) && padre.name === nodo) return true;
  if (ts.isElementAccessExpression(padre) && padre.argumentExpression === nodo) return true;
  if (!ts.isBinaryExpression(padre)) return false;
  const k = padre.operatorToken.kind;
  return k === ts.SyntaxKind.EqualsEqualsEqualsToken || k === ts.SyntaxKind.ExclamationEqualsEqualsToken;
}

/** Cada texto del archivo que puede llegar a la pantalla: literales, partes de plantillas y texto JSX.
 * Los módulos que se importan no son texto. */
function textos(ruta: string): { linea: number; texto: string }[] {
  const fuente = ts.createSourceFile(ruta, readFileSync(ruta, "utf8"), ts.ScriptTarget.Latest, true);
  const hallados: { linea: number; texto: string }[] = [];
  const visitar = (nodo: ts.Node) => {
    if (ts.isImportDeclaration(nodo) || ts.isExportDeclaration(nodo)) return;
    if (
      ts.isStringLiteral(nodo) ||
      ts.isNoSubstitutionTemplateLiteral(nodo) ||
      ts.isTemplateHead(nodo) ||
      ts.isTemplateMiddle(nodo) ||
      ts.isTemplateTail(nodo) ||
      ts.isJsxText(nodo)
    ) {
      const texto = nodo.text.trim();
      if (texto !== "" && !esIdentificador(texto) && !esValorDeCodigo(nodo)) hallados.push({ linea: fuente.getLineAndCharacterOfPosition(nodo.getStart()).line + 1, texto });
    }
    ts.forEachChild(nodo, visitar);
  };
  visitar(fuente);
  return hallados;
}

describe("redacción impersonal (pregunta 9)", () => {
  it("control: el detector ve cada falta de los controles y no marca los textos bien escritos", () => {
    for (const texto of reglas.control_con_falta) expect(faltas(texto), texto).not.toEqual([]);
    for (const texto of reglas.control_sin_falta) expect(faltas(texto), texto).toEqual([]);
  });

  it("control: el recorrido encuentra un texto que se sabe que existe", () => {
    const todos = archivos(CARPETA).flatMap((ruta) => textos(ruta).map((t) => t.texto));
    expect(todos).toContain("Nada pendiente.");
    expect(todos.length).toBeGreaterThan(1000);
  });

  it("ningún texto del frontend tiene voseo, tuteo ni primera persona", () => {
    const encontrados = archivos(CARPETA)
      .filter((ruta) => !NO_SE_REVISAN.has(relative(CARPETA, ruta).replaceAll("\\", "/")))
      .flatMap((ruta) =>
        textos(ruta).flatMap(({ linea, texto }) => {
          const f = faltas(texto);
          return f.length === 0 ? [] : [`${relative(CARPETA, ruta)}:${linea}: ${f.join(", ")} :: ${texto}`];
        }),
      );
    expect(encontrados, encontrados.join("\n")).toEqual([]);
  });
});
