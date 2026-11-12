// La cuenta destino (CBU o CVU) con sus dos dígitos verificadores, comprobada al escribirla (tanda
// 3 del informe de fricción, P-26), como el CUIT en `CampoCuit`. Es la misma cuenta que hacen el
// contrato (`_cuenta_valida` de `cem_contracts.egresos`) y la base (`nucleo.cbu_valido`, app
// `0048`), que siguen siendo los que deciden.

const PESOS_BLOQUE_1 = [7, 1, 3, 9, 7, 1, 3] as const;
const PESOS_BLOQUE_2 = [3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3] as const;
const VEINTIDOS_NUMEROS = /^[0-9]{22}$/;
const PRIMER_BLOQUE_EN_CEROS = "0000000";

/** El mensaje nunca repite la cuenta escrita. */
export const CUENTA_INVALIDA = "La cuenta no es un CBU o CVU válido. Revisar los 22 números.";

function digito(cifras: string, pesos: readonly number[]): number {
  const suma = pesos.reduce((total, peso, i) => total + Number(cifras[i]) * peso, 0);
  return (10 - (suma % 10)) % 10;
}

export function cuentaValida(cuenta: string): boolean {
  if (!VEINTIDOS_NUMEROS.test(cuenta) || cuenta.startsWith(PRIMER_BLOQUE_EN_CEROS)) return false;
  return digito(cuenta.slice(0, 7), PESOS_BLOQUE_1) === Number(cuenta[7]) && digito(cuenta.slice(8, 21), PESOS_BLOQUE_2) === Number(cuenta[21]);
}

/** Ya tiene los 22 números y un verificador no cierra: se avisa antes de mandar nada. */
export function cuentaConError(cuenta: string): boolean {
  return VEINTIDOS_NUMEROS.test(cuenta) && !cuentaValida(cuenta);
}
