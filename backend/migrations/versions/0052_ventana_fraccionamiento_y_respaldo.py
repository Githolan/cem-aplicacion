"""Ventana de la alerta de fraccionamiento y respaldo de contratos con una versión analizada (H3).

Mensaje del propietario del 30-10-2026, puntos 9 y 10: el fraccionamiento sigue siendo una alerta
y nunca un bloqueo; la decisión 1A agrupa por proveedor, período y consorcio sin fijar el período.
Hasta acá `evaluar_orden_pago` (`0050`) sumaba las órdenes del mismo mes calendario, y un gasto
partido entre el 30 y el 1 escapaba a la alerta. Ahora suma las de los últimos
`nucleo.ventana_fraccionamiento()` días (30, provisional), contados hacia atrás desde la
preparación.

- El parámetro vive en una sola función: cambiarlo es un `create or replace` de esa función, sin
  tocar datos ni otras definiciones.
- Nunca deniega: solo decide `alerta_fraccionamiento`, que ya estaba fuera de la huella. Las
  versiones ya evaluadas guardan la alerta con la que se prepararon; esta migración no las toca.

**Respaldo de un contrato** (punto 34): hasta acá alcanzaba con que el documento estuviera
`vigente`, y un documento es vigente desde que se crea, aunque su única versión siga en cuarentena
o la haya rechazado el antivirus: el contrato podía respaldarse en un archivo que nadie puede
abrir. Ahora `nucleo.documento_respalda` exige además al menos una versión `disponible`, y la usan
el registro (`documento_invalido`), la vigencia (`contrato_vigente`, que decide lo rutinario y la
emisión) y la lectura (`documento_no_vigente`). Lo ya aprobado no cambia de historia: una
rutinaria aprobada cuyo respaldo deja de servir no se emite y se renueva sin contrato, como con un
documento borrado (`0050`).

Downgrade: vuelve al mes calendario y al control de `0050`, y borra las dos funciones.

Revisión: 0052
Anterior: 0051
"""

from alembic import op
from sqlalchemy import text

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

VENTANA_DIAS = 30
"""Provisional (punto 10): un valor técnico conservador mientras no haya un período decidido."""
EVALUAR = (
    "nucleo.evaluar_orden_pago(uuid, uuid, nucleo.proveedor_version, bigint, text, boolean, uuid)"
)
VIGENCIA = "nucleo.contrato_vigente(uuid, uuid, date)"
REGISTRAR = (
    "nucleo.registrar_contrato_proveedor(uuid, text, text, bigint, text, date, date, uuid, uuid,"
    " uuid, text, uuid)"
)
LECTURA = "nucleo.contratos_proveedor_lectura(uuid, text, uuid, text, uuid)"
_SANGRIA = " " * 38
RESPALDO = {
    VIGENCIA: (
        ("         and d.estado = 'vigente'\n",),
        ("         and nucleo.documento_respalda(d.consorcio_id, d.id)\n",),
    ),
    REGISTRAR: (
        ("          where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'\n",),
        (
            "          where d.consorcio_id = c and d.id = documento\n"
            "            and nucleo.documento_respalda(c, documento)\n",
        ),
    ),
    LECTURA: (
        (f"{_SANGRIA}and d.id = k.documento_id and d.estado = 'vigente')\n",),
        (
            f"{_SANGRIA}and d.id = k.documento_id\n"
            f"{_SANGRIA}and nucleo.documento_respalda(d.consorcio_id, d.id))\n",
        ),
    ),
}

MES = (
    "    -- Fraccionamiento: las demás órdenes no rechazadas del mismo proveedor y moneda"
    " (o, si es\n"
    "    -- rutinaria, del mismo contrato) preparadas este mes del consorcio."
    " Alerta, no bloqueo.\n",
    "         and date_trunc('month', v2.creada_en at time zone k.zona_horaria)\n"
    "             = date_trunc('month', hoy::timestamp);\n",
)
VENTANA = (
    "    -- Fraccionamiento: las demás órdenes no rechazadas del mismo proveedor y moneda\n"
    "    -- (o, si es rutinaria, del mismo contrato) preparadas dentro de\n"
    "    -- `ventana_fraccionamiento`. Alerta, no bloqueo.\n",
    "         and v2.creada_en > now() - nucleo.ventana_fraccionamiento();\n",
)

UPGRADE = f"""
-- Vigente y con al menos una versión que pasó el antivirus.
create function nucleo.documento_respalda(c uuid, documento uuid)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.documento d
        join nucleo.version_documento v on v.consorcio_id = d.consorcio_id and v.documento_id = d.id
       where d.consorcio_id = c and d.id = documento and d.estado = 'vigente'
         and v.estado = 'disponible')
  $$;

create function nucleo.ventana_fraccionamiento()
  returns interval
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$ select interval '{VENTANA_DIAS} days' $$;
comment on function nucleo.ventana_fraccionamiento() is
  'Parámetro técnico provisional (30-10-2026): cuánto hacia atrás suma la alerta de'
  ' fraccionamiento. No deniega nada.';
"""

DOWNGRADE_FINAL = """
drop function nucleo.ventana_fraccionamiento();
drop function nucleo.documento_respalda(uuid, uuid);
"""


def _reemplazar(funcion: str, antes: tuple[str, ...], despues: tuple[str, ...]) -> None:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for a, d in zip(antes, despues, strict=True):
        if definicion.count(a) != 1:
            raise RuntimeError(f"0052: {funcion} no tiene exactamente una vez {a!r}")
        definicion = definicion.replace(a, d)
    op.execute(definicion)


def upgrade() -> None:
    op.execute(UPGRADE)
    _reemplazar(EVALUAR, MES, VENTANA)
    for funcion, (antes, despues) in RESPALDO.items():
        _reemplazar(funcion, antes, despues)


def downgrade() -> None:
    _reemplazar(EVALUAR, VENTANA, MES)
    for funcion, (antes, despues) in RESPALDO.items():
        _reemplazar(funcion, despues, antes)
    op.execute(DOWNGRADE_FINAL)
