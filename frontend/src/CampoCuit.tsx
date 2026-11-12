// El CUIT con su dígito verificador, comprobado en el momento (comentario 27, decisión del
// 07-11-2026: «toda la informacion q se pueda verificar gratis y rapido la hacemos»). Es la misma
// cuenta que hace la base en `nucleo.cuit_valido` (app `0012`), que sigue siendo la que decide.

const PESOS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2] as const;
const PATRON_CUIT = "[0-9]{11}";
const ONCE_NUMEROS = /^[0-9]{11}$/;

export const CUIT_INVALIDO = "El CUIT no es válido: el último número no corresponde a los anteriores.";

/** Módulo 11, con los pesos de AFIP: si da 11 el verificador es 0, y si da 10 no hay CUIT válido. */
export function cuitValido(cuit: string): boolean {
  if (!ONCE_NUMEROS.test(cuit)) return false;
  const suma = PESOS.reduce((total, peso, i) => total + Number(cuit[i]) * peso, 0);
  const resto = 11 - (suma % 11);
  const verificador = resto === 11 ? 0 : resto;
  return verificador !== 10 && verificador === Number(cuit[10]);
}

/** Ya tiene los 11 números y el verificador no cierra: se avisa antes de mandar nada. */
export function cuitConError(cuit: string): boolean {
  return ONCE_NUMEROS.test(cuit) && !cuitValido(cuit);
}

type Props = {
  id: string;
  valor: string;
  deshabilitado: boolean;
  /** Lo que dijo la API del CUIT (inválido o repetido), si lo marcó (tanda 3). */
  error?: string | undefined;
  alCambiar: (valor: string) => void;
};

export function CampoCuit({ id, valor, deshabilitado, error, alCambiar }: Props) {
  const conError = cuitConError(valor);
  // El verificador mal se dice al escribir; si no, lo que dijo la API al guardar.
  const mensaje = conError ? CUIT_INVALIDO : error;
  return (
    <>
      <label htmlFor={id}>CUIT (11 números, sin guiones)</label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        required
        pattern={PATRON_CUIT}
        disabled={deshabilitado}
        value={valor}
        aria-invalid={mensaje !== undefined}
        aria-describedby={mensaje === undefined ? undefined : `${id}-error`}
        onChange={(e) => alCambiar(e.target.value)}
      />
      {mensaje !== undefined && (
        <p className="aviso-error" id={`${id}-error`} role={conError ? "alert" : undefined}>
          {mensaje}
        </p>
      )}
    </>
  );
}
