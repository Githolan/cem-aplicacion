"""El contrato de la pantalla de pagos (informe de fricción, parte 4; tanda 2).

Una sola tabla, `pantallas_datos/pagos.json`, dice para cada situación de una orden y cada paso
que la base le deja a quien mira (su relación con esa orden: prepara, paga, decide, ya participó):

- cómo se llama el estado (`estados`, la lista única de la decisión 11 del 12-11-2026) y la barra,
  que dependen solo de la situación;
- en qué lista de la bandeja aparece, y si cuenta como pendiente;
- la acción principal, las secundarias y las negativas, en el orden en que se dibujan;
- qué decir y qué hace la pantalla después del acto principal.

**La tabla no concede nada.** El backend ofrece una acción solo si su `puede_*` es verdadero, y
esos sí o no los decide la base (`nucleo.acciones_de_ordenes_lectura` y las funciones de cada
acto). La tabla ordena y nombra; la base decide (documento 13, §10.5).

Vive fuera de `politicas_datos/` porque ahí cada JSON es una política de autorización
(`politicas.archivos_politicas` lee todos). El modelo es estricto: un campo mal escrito invalida
el archivo, y el backend no arranca con una tabla inválida.
"""

import json
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from cem_contracts.egresos import (
    AccionPago,
    BarraPago,
    DespuesDelActo,
    EstadoVisible,
    GrupoPago,
    PasoPago,
    SituacionPago,
)

ARCHIVO = Path(__file__).resolve().parent / "pantallas_datos" / "pagos.json"


class _Estricto(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SituacionEnPantalla(_Estricto):
    estado: EstadoVisible
    barra: BarraPago | None
    final: StrictBool
    """Rechazada, retirada, pagada y cotejada: no queda nada por hacer con la orden."""


class ListaEnPantalla(_Estricto):
    pendiente: StrictBool
    plegada: StrictBool
    principal: AccionPago | None = None
    """Para una lista que no trae órdenes (`para_cotejar`): la acción que la atiende."""


class Botones(_Estricto):
    principal: AccionPago | None
    secundarias: list[AccionPago]
    negativas: list[AccionPago]


class FilaPantalla(Botones):
    situacion: SituacionPago
    paso: PasoPago | None
    lista: GrupoPago | None
    lista_si_viejo: GrupoPago | None = None
    """La lista a la que pasa la orden sin movimiento hace `CEM_PAGOS_DIAS_BORRADOR_VIEJO` días."""
    aviso_una_vez: StrictBool = False
    """Fuera de las listas: se le avisa una vez a quien la preparó, hasta «Entendido»."""
    cuenta_como_pendiente: StrictBool
    que_decir: str
    despues: DespuesDelActo
    si_el_comprobante_no_coincide: Botones | None = None
    """Al conciliar, si la lectura del comprobante difiere de lo registrado: observar pasa a ser
    lo principal."""


class PantallaPagos(_Estricto):
    descripcion: str
    estados: dict[EstadoVisible, str]
    situaciones: dict[SituacionPago, SituacionEnPantalla]
    listas: dict[GrupoPago, ListaEnPantalla]
    filas: list[FilaPantalla]

    @model_validator(mode="after")
    def _completa(self) -> "PantallaPagos":
        for nombre, dado, todos in (
            ("estados", self.estados, EstadoVisible),
            ("situaciones", self.situaciones, SituacionPago),
            ("listas", self.listas, GrupoPago),
        ):
            if set(dado) != set(todos):
                raise ValueError(f"{nombre}: faltan {set(todos) - set(dado)}")
        claves = [(f.situacion, f.paso) for f in self.filas]
        repetidas = {c for c in claves if claves.count(c) > 1}
        if repetidas:
            raise ValueError(f"filas repetidas: {repetidas}")
        for f in self.filas:
            esperado = f.lista is not None and self.listas[f.lista].pendiente
            if f.cuenta_como_pendiente != esperado:
                raise ValueError(
                    f"{f.situacion}/{f.paso}: cuenta_como_pendiente no es el de su lista"
                )
        return self

    def fila(self, situacion: SituacionPago, paso: PasoPago | None) -> FilaPantalla:
        """La fila de esa situación y ese paso. Que falte es un error de programación: el backend
        derivó un par que la tabla no conoce."""
        for f in self.filas:
            if f.situacion == situacion and f.paso == paso:
                return f
        raise LookupError(f"la tabla de la pantalla de pagos no tiene {situacion}/{paso}")


@cache
def cargar() -> PantallaPagos:
    return PantallaPagos.model_validate(json.loads(ARCHIVO.read_text(encoding="utf-8")))
