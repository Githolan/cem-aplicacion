"""Importación de unidades y relaciones con informe (RF02; H2b 2b-5, corte 3; migración 0025).

Diseño: `doc/diseno/H2b_5_Diseno.md` del repositorio CEM, §4, con las decisiones del
propietario del 22-10-2026 (D1 a D4 en `doc/rf02_corte3_informe.md` de este repositorio).

**Dos pasos sobre la misma planilla.** El informe la lee, la compara con el consorcio y dice qué
haría cada fila, sin escribir nada. La aplicación vuelve a calcular el informe dentro de su
transacción, con un bloqueo por consorcio, y escribe todo o nada: un solo problema y no se
escribe nada (422). Un reintento con la misma clave de idempotencia responde lo mismo.

**Nunca sobrescribe (PA01, D1).** Una unidad o un vínculo igual a uno que existe no se escribe:
reimportar el mismo contenido no cambia nada. Una designación que difiere de otra solo en
mayúsculas o espacios, o un vínculo que se superpone con uno existente sin ser igual, son
problemas: en este corte no se corrige por importación.

**Alcance (D6, D4.1).** Crear una unidad exige `padron.gestionar` general; cada fila, esa
facultad sobre su unidad; cada fila que crea una invitación, además `invitaciones.crear` sobre
su unidad (general, si es nueva). Si una sola fila queda afuera, se rechaza el archivo entero
(403, auditado) y el informe la nombra.

**Invitaciones (decisiones del 22 y del 23-10-2026).** Cada vínculo nuevo vigente o futuro lleva
su invitación, que lo nombra (`vinculo_id`) y **no se envía**. Vence como las manuales
(`VIGENCIA_INVITACION`). Una pendiente vencida de la misma persona, unidad y carácter se
reemplaza al aplicar, y el informe lo anuncia; una vigente bloquea (D4.2). Reimportar la misma
fila de un vínculo que sigue importado pendiente y cuya invitación venció la renueva: es la única
escritura de una fila igual, y el informe también la anuncia.

**Coeficientes (D3).** Se leen, se validan y se informan: la suma tal cual, las unidades sin
porcentaje y las diferencias con el esquema vigente. No se escriben: los carga el equipo
(`scripts.cargar_coeficientes`) hasta que 2b-6 resuelva la aprobación.

Importar no crea ámbito ni facultad: el vínculo entra sin confirmar y queda importado pendiente
(0024) hasta que se acepte su invitación o se confirme.
"""

import csv
import hashlib
import io
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from cem_contracts.errores import CodigoError
from cem_contracts.estados import TipoVinculo
from cem_contracts.identidad import FORMATO_CORREO, LARGO_MAXIMO_CORREO
from cem_contracts.importacion import (
    AccionInvitacion,
    AccionUnidad,
    AccionVinculo,
    CodigoProblema,
    CoeficientesGrupo,
    FilaInforme,
    ImportacionAplicada,
    InformeImportacion,
    PlanillaPadron,
    Problema,
)
from cem_contracts.politicas import TipoRecurso
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from psycopg.errors import UniqueViolation
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from app import auditoria
from app.autorizacion.evaluador import Entidad
from app.autorizacion.requiere import Permiso, requiere
from app.contexto import Contexto, MotorDep
from app.db import transaccion
from app.errores import ErrorApi, sin_cache
from app.identidad import sesiones
from app.identidad.invitaciones import VIGENCIA_INVITACION
from app.plataforma.idempotencia import IdempotenciaDep, responder_una_vez

router = APIRouter(prefix="/api/v1", tags=["consorcios"], dependencies=[Depends(sin_cache)])
log = logging.getLogger("cem.importacion")

COLUMNA_UNIDAD = "unidad"
COLUMNAS_RELACION = ("nombre", "correo", "caracter", "vigente_desde", "ultimo_dia")
PREFIJO_COEFICIENTE = "coeficiente_"
GRUPO = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
"""El mismo formato de grupo que exige 0021."""
PORCENTAJE = re.compile(r"^\d{1,3}(\.\d{1,6})?$")
FECHA = re.compile(r"^\d{2}-\d{2}-\d{4}$")
SURROGATE = re.compile("[\ud800-\udfff]")
CORREO = re.compile(FORMATO_CORREO)
"""El mismo formato que el enlace, el código y las invitaciones (Codex en app #105): una persona
creada con otro correo no podría entrar ni recibir una invitación válida."""
LARGO_MAXIMO_NOMBRE = 100
"""El tope de `nucleo.invitacion.nombre` (0007)."""
CIEN = Decimal(100)
ULTIMO_ANIO = 9998
"""`ultimo_dia + 1` tiene que seguir siendo una fecha (`date.max` es el 31-12-9999)."""
CAMBIO_DURANTE_LA_APLICACION = frozenset(
    {"vinculo_solapado", "invitacion_pendiente", "vinculo_no_pendiente"}
)
"""Motivos de la base que solo aparecen si el padrón cambió entre el informe recalculado y la
escritura (otra ruta, no otra importación: esas se ordenan con el bloqueo)."""


# Lectura de la planilla -----------------------------------------------------------------------


@dataclass(frozen=True)
class Relacion:
    nombre: str
    correo: str
    tipo: TipoVinculo
    desde: date
    ultimo_dia: date | None

    @property
    def hasta(self) -> date | None:
        """Exclusivo, como lo guarda la base: el primer día sin vigencia."""
        return None if self.ultimo_dia is None else self.ultimo_dia + timedelta(days=1)


@dataclass
class FilaLeida:
    linea: int
    designacion: str
    relacion: Relacion | None = None
    coeficientes: dict[str, Decimal] = field(default_factory=dict)
    problemas: list[Problema] = field(default_factory=list)

    def problema(self, codigo: CodigoProblema, mensaje: str) -> None:
        self.problemas.append(Problema(codigo=codigo, mensaje=mensaje))


@dataclass(frozen=True)
class Planilla:
    sha256: bytes
    grupos: tuple[str, ...]
    filas: list[FilaLeida]
    problemas: list[Problema]


def clave_designacion(designacion: str) -> str:
    """Dos designaciones que solo difieren en mayúsculas o espacios son la misma unidad."""
    return " ".join(designacion.split()).casefold()


def _fecha(valor: str) -> date | None:
    if not FECHA.match(valor):
        return None
    try:
        fecha = datetime.strptime(valor, "%d-%m-%Y").date()
    except ValueError:
        return None
    return fecha if fecha.year <= ULTIMO_ANIO else None


def _encabezado(columnas: list[str]) -> tuple[tuple[str, ...], list[Problema]]:
    """Los grupos de coeficientes y los problemas del encabezado."""
    problemas = []
    if "vigente_hasta" in columnas:
        problemas.append(
            Problema(
                codigo=CodigoProblema.COLUMNA_VIGENTE_HASTA,
                mensaje="La planilla no acepta `vigente_hasta`: cargá `ultimo_dia`, el último día"
                " con vigencia (inclusivo).",
            )
        )
    # Una sola pasada (Codex en app #105): con `columnas.count` por columna, un encabezado de
    # cientos de miles de columnas trababa el proceso varios minutos.
    repetidas = sorted(c for c, n in Counter(columnas).items() if n > 1)
    if repetidas:
        problemas.append(
            Problema(
                codigo=CodigoProblema.ENCABEZADO_INVALIDO,
                mensaje=f"Columnas repetidas: {', '.join(repetidas)}.",
            )
        )
    if COLUMNA_UNIDAD not in columnas:
        problemas.append(
            Problema(
                codigo=CodigoProblema.ENCABEZADO_INVALIDO, mensaje="Falta la columna `unidad`."
            )
        )
    relacion = [c for c in COLUMNAS_RELACION if c in columnas]
    if relacion and len(relacion) != len(COLUMNAS_RELACION):
        faltan = [c for c in COLUMNAS_RELACION if c not in columnas]
        problemas.append(
            Problema(
                codigo=CodigoProblema.ENCABEZADO_INVALIDO,
                mensaje=f"Las columnas de la relación van juntas; faltan: {', '.join(faltan)}.",
            )
        )
    grupos = []
    for c in columnas:
        if c.startswith(PREFIJO_COEFICIENTE):
            grupo = c.removeprefix(PREFIJO_COEFICIENTE)
            if GRUPO.match(grupo):
                grupos.append(grupo)
            else:
                problemas.append(
                    Problema(
                        codigo=CodigoProblema.COEFICIENTE_INVALIDO,
                        mensaje=f"El grupo de `{c}` no es válido (minúsculas, dígitos y guiones"
                        " bajos).",
                    )
                )
        elif c not in (COLUMNA_UNIDAD, "vigente_hasta", *COLUMNAS_RELACION):
            problemas.append(
                Problema(
                    codigo=CodigoProblema.ENCABEZADO_INVALIDO,
                    mensaje=f"Columna desconocida: `{c}`.",
                )
            )
    return tuple(dict.fromkeys(grupos)), problemas


def _relacion(fila: FilaLeida, valores: dict[str, str]) -> None:
    nombre, correo, caracter, desde, ultimo = (valores.get(c, "") for c in COLUMNAS_RELACION)
    if not any((nombre, correo, caracter, desde, ultimo)):
        return
    if not correo:
        fila.problema(
            CodigoProblema.RELACION_SIN_CORREO,
            "La relación no tiene correo: un nombre solo no identifica a nadie. Omití la fila y"
            " agregá la relación después con una invitación.",
        )
    if not (nombre and caracter and desde):
        fila.problema(
            CodigoProblema.RELACION_INCOMPLETA,
            "La relación necesita nombre, correo, carácter y `vigente_desde`.",
        )
    correo = correo.lower()
    if correo and (len(correo) > LARGO_MAXIMO_CORREO or not CORREO.match(correo)):
        fila.problema(CodigoProblema.CORREO_INVALIDO, "El correo no es válido.")
    if nombre and len(nombre) > LARGO_MAXIMO_NOMBRE:
        fila.problema(
            CodigoProblema.NOMBRE_INVALIDO,
            f"El nombre tiene que tener hasta {LARGO_MAXIMO_NOMBRE} caracteres.",
        )
    tipo = None
    if caracter:
        try:
            tipo = TipoVinculo(caracter.lower())
        except ValueError:
            fila.problema(
                CodigoProblema.CARACTER_INVALIDO,
                "El carácter es `propietario`, `inquilino` u `ocupante`.",
            )
    vigente_desde = _fecha(desde) if desde else None
    ultimo_dia = _fecha(ultimo) if ultimo else None
    if (desde and vigente_desde is None) or (ultimo and ultimo_dia is None):
        fila.problema(CodigoProblema.FECHA_INVALIDA, "Las fechas van como DD-MM-AAAA y existen.")
    elif vigente_desde and ultimo_dia and ultimo_dia < vigente_desde:
        fila.problema(CodigoProblema.FECHA_INVALIDA, "`ultimo_dia` es anterior a `vigente_desde`.")
    if fila.problemas or tipo is None or vigente_desde is None:
        return
    fila.relacion = Relacion(nombre, correo, tipo, vigente_desde, ultimo_dia)


def _coeficientes(fila: FilaLeida, valores: dict[str, str], grupos: tuple[str, ...]) -> None:
    for grupo in grupos:
        valor = valores.get(PREFIJO_COEFICIENTE + grupo, "")
        if not valor:
            continue
        if not PORCENTAJE.match(valor) or Decimal(valor) > CIEN:
            fila.problema(
                CodigoProblema.COEFICIENTE_INVALIDO,
                f"El porcentaje de `{grupo}` va entre 0 y 100, con punto decimal y hasta 6"
                " decimales.",
            )
            continue
        fila.coeficientes[grupo] = Decimal(valor)


def leer_planilla(archivo: str) -> Planilla:
    """Lee la planilla sin tocar la base. Cada fila trae sus problemas de forma."""
    # Un surrogate suelto (`"\ud800"`) es texto que no se puede pasar a UTF-8 (Codex en app
    # #105). Por la API lo rechaza antes el contrato (422), pero esta función no depende de eso.
    # El hash se calcula con `surrogatepass`, que da siempre los mismos bytes para el mismo texto
    # y coincide con UTF-8 cuando no hay surrogates; la planilla se rechaza antes del CSV y de
    # cualquier consulta.
    sha256 = hashlib.sha256(archivo.encode("utf-8", "surrogatepass")).digest()
    if SURROGATE.search(archivo):
        suelto = Problema(
            codigo=CodigoProblema.FILA_MAL_FORMADA,
            mensaje="La planilla tiene un carácter Unicode inválido (un surrogate suelto).",
        )
        return Planilla(sha256, (), [], [suelto])
    # `csv.reader` acepta un byte nulo, pero PostgreSQL no lo admite en un texto: se rechaza
    # antes de cualquier consulta (Codex en app #105).
    if "\x00" in archivo:
        nulo = Problema(
            codigo=CodigoProblema.FILA_MAL_FORMADA, mensaje="La planilla tiene un byte nulo."
        )
        return Planilla(sha256, (), [], [nulo])
    try:
        # Estricto (Codex en app #105): `"Ana"xyz` o unas comillas sin cerrar son un error, no un
        # campo leído de otra forma.
        renglones = list(csv.reader(io.StringIO(archivo.removeprefix("\ufeff")), strict=True))
    except csv.Error as e:  # comillas mal formadas o sin cerrar, un campo enorme
        malformada = Problema(
            codigo=CodigoProblema.FILA_MAL_FORMADA, mensaje=f"La planilla no es un CSV válido: {e}."
        )
        return Planilla(sha256, (), [], [malformada])
    if not renglones or not any(c.strip() for c in renglones[0]):
        vacio = Problema(codigo=CodigoProblema.ARCHIVO_VACIO, mensaje="La planilla está vacía.")
        return Planilla(sha256, (), [], [vacio])
    columnas = [c.strip() for c in renglones[0]]
    grupos, problemas = _encabezado(columnas)
    filas = []
    for linea, renglon in enumerate(renglones[1:], start=2):
        if not any(c.strip() for c in renglon):
            continue
        if len(renglon) != len(columnas):
            fila = FilaLeida(linea, "")
            fila.problema(
                CodigoProblema.FILA_MAL_FORMADA,
                f"La fila tiene {len(renglon)} columnas y el encabezado {len(columnas)}.",
            )
            filas.append(fila)
            continue
        valores = {c: v.strip() for c, v in zip(columnas, renglon, strict=True)}
        fila = FilaLeida(linea, valores.get(COLUMNA_UNIDAD, ""))
        if not fila.designacion:
            fila.problema(CodigoProblema.UNIDAD_VACIA, "La fila no dice la unidad.")
        _relacion(fila, valores)
        _coeficientes(fila, valores, grupos)
        filas.append(fila)
    if not filas and not problemas:
        problemas.append(
            Problema(codigo=CodigoProblema.ARCHIVO_VACIO, mensaje="La planilla no tiene filas.")
        )
    return Planilla(sha256, grupos, filas, problemas)


# Informe ---------------------------------------------------------------------------------------


@dataclass
class Plan:
    """Lo que haría cada fila, para el informe y para la aplicación."""

    fila: FilaLeida
    unidad_id: UUID | None = None
    unidad: AccionUnidad | None = None
    vinculo: AccionVinculo | None = None
    vinculo_existente: UUID | None = None
    importado_pendiente: bool = False
    invitacion: AccionInvitacion | None = None
    negada_gestionar: Entidad | None = None
    negada_invitar: Entidad | None = None


@dataclass(frozen=True)
class Analisis:
    informe: InformeImportacion
    planes: list[Plan]


def sobre_unidad(unidad_id: UUID | None) -> Entidad:
    """La unidad de la fila; una unidad nueva no tiene id: hace falta el alcance general."""
    if unidad_id is None:
        return Entidad()
    return Entidad(recursos={TipoRecurso.UNIDAD: str(unidad_id)})


def _se_superponen(a: tuple[date, date | None], b: tuple[date, date | None]) -> bool:
    return (a[1] is None or b[0] < a[1]) and (b[1] is None or a[0] < b[1])


def _unidades(conn: Connection, planes: list[Plan], gestionar: Permiso) -> None:
    """Encuentra o crea la unidad de cada fila. Con `padron.gestionar` acotado, solo cuentan las
    unidades que la persona gestiona: una designación fuera de su alcance, exista o no, esté
    escrita igual o distinto, da el mismo `fuera_de_alcance` con el mismo mensaje, y el informe
    nunca nombra una unidad que no ve (revisión independiente sobre 4b978a1, como los
    coeficientes)."""
    general = gestionar.decidir(Entidad()).permitido
    visibles: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
    for u in conn.execute(
        text("select id, designacion from nucleo.unidad where eliminado_en is null")
    ):
        if general or gestionar.decidir(sobre_unidad(u.id)).permitido:
            visibles[clave_designacion(u.designacion)].append((u.id, u.designacion))
    en_planilla: dict[str, set[str]] = defaultdict(set)
    for p in planes:
        if p.fila.designacion:
            en_planilla[clave_designacion(p.fila.designacion)].add(p.fila.designacion)
    for p in planes:
        d = p.fila.designacion
        if not d:
            continue
        clave = clave_designacion(d)
        mismas = visibles[clave]
        escrituras = en_planilla[clave] | {x for _, x in mismas}
        if len(escrituras) > 1 or len(mismas) > 1:
            p.fila.problema(
                CodigoProblema.DESIGNACION_AMBIGUA,
                f"«{d}» es la misma unidad que «{'», «'.join(sorted(escrituras - {d}) or [d])}»"
                " escrita de otra forma: no se crea una unidad duplicada (PA01).",
            )
            continue
        if mismas:
            p.unidad_id, p.unidad = mismas[0][0], AccionUnidad.EXISTENTE
        elif general:
            p.unidad = AccionUnidad.CREAR
        else:
            p.negada_gestionar = Entidad()
            p.fila.problema(
                CodigoProblema.FUERA_DE_ALCANCE,
                "Esta unidad está fuera de tu alcance de `padron.gestionar`.",
            )


def _vinculos(conn: Connection, planes: list[Plan]) -> None:
    con_relacion = [
        p for p in planes if p.fila.relacion and p.unidad and p.negada_gestionar is None
    ]
    correos = sorted({p.fila.relacion.correo for p in con_relacion})
    existentes: dict[tuple, list[tuple[date, date | None]]] = defaultdict(list)
    detalle: dict[tuple, tuple[UUID, bool]] = {}
    for v in conn.execute(
        text(
            "select correo, unidad_id, tipo::text as tipo, vigente_desde, vigente_hasta,"
            " vinculo_id, importado_pendiente from nucleo.vinculos_de_correos(:c)"
        ),
        {"c": correos},
    ):
        clave = (v.unidad_id, v.correo, TipoVinculo(v.tipo))
        existentes[clave].append((v.vigente_desde, v.vigente_hasta))
        detalle[(*clave, v.vigente_desde, v.vigente_hasta)] = (
            v.vinculo_id,
            v.importado_pendiente,
        )
    vistas: dict[tuple, list[Plan]] = defaultdict(list)
    for p in con_relacion:
        r = p.fila.relacion
        clave = (clave_designacion(p.fila.designacion), r.correo, r.tipo)
        rango = (r.desde, r.hasta)
        anteriores = vistas[clave]
        if any((a.fila.relacion.desde, a.fila.relacion.hasta) == rango for a in anteriores):
            p.fila.problema(
                CodigoProblema.RELACION_DUPLICADA, "La misma relación ya está en otra fila."
            )
            continue
        for a in anteriores:
            if _se_superponen((a.fila.relacion.desde, a.fila.relacion.hasta), rango):
                for x in (a, p):
                    x.fila.problema(
                        CodigoProblema.VINCULO_SOLAPADO,
                        "Otra fila da a la misma persona, unidad y carácter fechas superpuestas"
                        f" (líneas {a.fila.linea} y {p.fila.linea}).",
                    )
        anteriores.append(p)
        actuales = existentes[(p.unidad_id, r.correo, r.tipo)] if p.unidad_id else []
        if rango in actuales:
            p.vinculo = AccionVinculo.EXISTENTE
            p.vinculo_existente, p.importado_pendiente = detalle[
                (p.unidad_id, r.correo, r.tipo, *rango)
            ]
        elif any(_se_superponen(e, rango) for e in actuales):
            p.fila.problema(
                CodigoProblema.CORRIGE_EXISTENTE,
                "Se superpone con un vínculo que ya existe y no es igual: no se corrige por"
                " importación. Finalizalo o anulalo y volvé a cargarlo.",
            )
        else:
            p.vinculo = AccionVinculo.CREAR
    for p in con_relacion:
        if p.vinculo is not None and any(
            x.codigo == CodigoProblema.VINCULO_SOLAPADO for x in p.fila.problemas
        ):
            p.vinculo = None


def _exigir_invitar(p: Plan, invitar: Permiso) -> None:
    """D4.1: la fila que crea o renueva una invitación necesita `invitaciones.crear`."""
    if not invitar.decidir(sobre_unidad(p.unidad_id)).permitido:
        p.negada_invitar = sobre_unidad(p.unidad_id)
        p.fila.problema(
            CodigoProblema.SIN_FACULTAD_INVITAR,
            "La fila crea una invitación y falta `invitaciones.crear` sobre esta unidad"
            + ("." if p.unidad_id else " (general: la unidad es nueva)."),
        )


def _invitaciones(conn: Connection, planes: list[Plan], hoy: date, invitar: Permiso) -> None:
    pendientes: dict[tuple, list[bool]] = defaultdict(list)
    de_otro_vinculo: set[tuple] = set()
    vencidas_de: set[UUID] = set()
    for i in conn.execute(
        text(
            "select unidad_id, tipo::text as tipo, correo, vinculo_id, vence_en > now() as vigente"
            " from nucleo.invitacion where aceptada_en is null and revocada_en is null"
        )
    ):
        clave = (i.unidad_id, TipoVinculo(i.tipo), i.correo)
        pendientes[clave].append(i.vigente)
        if i.vinculo_id is not None:
            de_otro_vinculo.add(clave)
            if not i.vigente:
                vencidas_de.add(i.vinculo_id)
    nuevas: dict[tuple, list[Plan]] = defaultdict(list)
    for p in planes:
        if p.vinculo is None:
            continue
        r = p.fila.relacion
        if r.hasta is not None and r.hasta <= hoy:
            p.invitacion = AccionInvitacion.NINGUNA
            continue
        if p.vinculo == AccionVinculo.EXISTENTE:
            # Una fila igual no escribe nada, salvo un caso (decisión del 23-10-2026): el vínculo
            # sigue importado pendiente y la invitación que lo nombra venció. Entonces se renueva,
            # si no hay otra pendiente vigente para esa persona, unidad y carácter.
            renovar = (
                p.importado_pendiente
                and p.vinculo_existente in vencidas_de
                and not any(pendientes[(p.unidad_id, r.tipo, r.correo)])
            )
            p.invitacion = (
                AccionInvitacion.REEMPLAZAR_VENCIDA if renovar else AccionInvitacion.NINGUNA
            )
            if renovar:
                _exigir_invitar(p, invitar)
            continue
        nuevas[(clave_designacion(p.fila.designacion), r.tipo, r.correo)].append(p)
        previas = pendientes[(p.unidad_id, r.tipo, r.correo)] if p.unidad_id else []
        # D4.2: una vigente bloquea, y una que nombra otro vínculo también, aunque haya vencido
        # (revisión independiente sobre 4b978a1): solo una manual vencida se reemplaza.
        if any(previas) or (p.unidad_id, r.tipo, r.correo) in de_otro_vinculo:
            p.fila.problema(
                CodigoProblema.INVITACION_PENDIENTE,
                "Ya hay una invitación pendiente para esta persona, unidad y carácter que no"
                " nombra este vínculo (vigente, o de otro vínculo importado): no se retira"
                " (D4.2).",
            )
            continue
        p.invitacion = AccionInvitacion.REEMPLAZAR_VENCIDA if previas else AccionInvitacion.CREAR
        _exigir_invitar(p, invitar)
    for grupo in nuevas.values():
        if len(grupo) > 1:
            for p in grupo:
                p.fila.problema(
                    CodigoProblema.INVITACIONES_SIMULTANEAS,
                    "Hay más de un vínculo vigente o futuro nuevo de esta persona, unidad y"
                    f" carácter (líneas {', '.join(str(x.fila.linea) for x in grupo)}): solo"
                    " puede haber una invitación pendiente.",
                )


def _coeficientes_informe(
    conn: Connection, planilla: Planilla, planes: list[Plan], hoy: date, gestionar: Permiso
) -> tuple[list[CoeficientesGrupo], list[str]]:
    por_unidad: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for p in planes:
        d = p.fila.designacion
        for grupo, valor in p.fila.coeficientes.items():
            previo = por_unidad[d].get(grupo)
            if previo is not None and previo != valor:
                p.fila.problema(
                    CodigoProblema.COEFICIENTE_CONTRADICTORIO,
                    f"Otra fila de «{d}» da otro porcentaje para `{grupo}`.",
                )
            por_unidad[d].setdefault(grupo, valor)
    # Solo las unidades que quien importa gestiona: con un alcance acotado no ve el resto.
    activas = {
        u.designacion: u.id
        for u in conn.execute(
            text("select id, designacion from nucleo.unidad where eliminado_en is null")
        )
        if gestionar.decidir(sobre_unidad(u.id)).permitido
    }
    designaciones = sorted(
        {p.fila.designacion for p in planes if p.fila.designacion} | set(activas)
    )
    vigentes = {
        e.grupo: e.id
        for e in conn.execute(
            text(
                "select distinct on (grupo) grupo, id from nucleo.esquema_coeficientes"
                " where vigente_desde <= :hoy order by grupo, version desc"
            ),
            {"hoy": hoy},
        )
    }
    informe = []
    for grupo in planilla.grupos:
        guardados = {}
        if grupo in vigentes:
            guardados = {
                c.unidad_id: c.porcentaje
                for c in conn.execute(
                    text(
                        "select unidad_id, porcentaje from nucleo.coeficiente_juridico"
                        " where esquema_id = :e"
                    ),
                    {"e": vigentes[grupo]},
                )
            }
        valores = {d: g[grupo] for d, g in por_unidad.items() if grupo in g}
        informe.append(
            CoeficientesGrupo(
                grupo=grupo,
                suma=str(sum(valores.values(), Decimal(0))),
                sin_coeficiente=[d for d in designaciones if d not in valores],
                distintos_del_vigente=[
                    d
                    for d, v in sorted(valores.items())
                    if grupo in vigentes and d in activas and guardados.get(activas[d]) != v
                ],
                hay_esquema_vigente=grupo in vigentes,
            )
        )
    nuevas = any(p.unidad == AccionUnidad.CREAR for p in planes)
    return informe, sorted(vigentes) if nuevas else []


def analizar(
    conn: Connection, ctx: Contexto, planilla: Planilla, gestionar: Permiso, invitar: Permiso
) -> Analisis:
    """El informe, con la conexión del ámbito del consorcio. No escribe nada."""
    hoy = conn.execute(
        text("select nucleo.hoy_del_consorcio(:c)"), {"c": ctx.consorcio_id}
    ).scalar_one()
    planes = [Plan(f) for f in planilla.filas]
    _unidades(conn, planes, gestionar)
    _vinculos(conn, planes)
    _invitaciones(conn, planes, hoy, invitar)
    coeficientes, esquemas = _coeficientes_informe(conn, planilla, planes, hoy, gestionar)
    hay_problemas = bool(planilla.problemas) or any(p.fila.problemas for p in planes)
    filas = []
    for p in planes:
        r = p.fila.relacion
        filas.append(
            FilaInforme(
                linea=p.fila.linea,
                designacion=p.fila.designacion,
                unidad=p.unidad,
                tipo=r.tipo if r else None,
                vigente_desde=r.desde if r else None,
                ultimo_dia=r.ultimo_dia if r else None,
                vigente_hasta=r.hasta if r else None,
                vinculo=p.vinculo,
                invitacion=p.invitacion,
                problemas=p.fila.problemas,
            )
        )
    informe = InformeImportacion(
        archivo_sha256=planilla.sha256.hex(),
        aplicable=not hay_problemas,
        sin_cambios=not hay_problemas
        and not any(
            p.unidad == AccionUnidad.CREAR
            or p.vinculo == AccionVinculo.CREAR
            or p.invitacion == AccionInvitacion.REEMPLAZAR_VENCIDA
            for p in planes
        ),
        problemas_archivo=planilla.problemas,
        filas=filas,
        coeficientes=coeficientes,
        esquemas_a_revisar=esquemas,
    )
    return Analisis(informe, planes)


def _exigir_alguna_gestion(motor, ctx: Contexto, planilla: Planilla, gestionar: Permiso) -> None:
    """Sin `padron.gestionar` en ninguna unidad de la planilla ni general, ni el informe: diría
    qué vínculos e invitaciones existen. Niega, audita y responde 403."""
    if gestionar.decidir(Entidad()).permitido:
        return
    designaciones = [f.designacion for f in planilla.filas if f.designacion]
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        unidades = conn.execute(
            text(
                "select id from nucleo.unidad where eliminado_en is null and designacion = any(:d)"
            ),
            {"d": designaciones},
        ).scalars()
        if any(gestionar.decidir(sobre_unidad(u)).permitido for u in unidades):
            return
    gestionar.verificar(Entidad())


def _verificar_alcance(analisis: Analisis, gestionar: Permiso, invitar: Permiso) -> None:
    """La primera fila fuera de alcance rechaza el archivo entero: 403 y auditado."""
    for p in analisis.planes:
        if p.negada_gestionar is not None:
            gestionar.verificar(p.negada_gestionar)
        if p.negada_invitar is not None:
            invitar.verificar(p.negada_invitar)


# Aplicación ------------------------------------------------------------------------------------


def _aplicar(
    conn: Connection,
    ctx: Contexto,
    planilla: Planilla,
    gestionar: Permiso,
    invitar: Permiso,
    ip: str | None,
) -> ImportacionAplicada:
    # Dos importaciones del mismo consorcio a la vez no crean la misma unidad dos veces: la
    # tabla no tiene una restricción de designación única (PA01 lo resuelve el informe).
    conn.execute(
        text("select pg_advisory_xact_lock(hashtextextended('importacion:' || :c, 0))"),
        {"c": str(ctx.consorcio_id)},
    )
    analisis = analizar(conn, ctx, planilla, gestionar, invitar)
    _verificar_alcance(analisis, gestionar, invitar)
    if not analisis.informe.aplicable:
        raise ErrorApi(
            422,
            CodigoError.IMPORTACION_CON_PROBLEMAS,
            "La planilla tiene problemas: no se escribió nada. Pedí el informe para verlos.",
        )
    sha = planilla.sha256.hex()
    if analisis.informe.sin_cambios:
        return ImportacionAplicada(
            importacion_id=None,
            archivo_sha256=sha,
            unidades_creadas=0,
            vinculos_creados=0,
            invitaciones_creadas=0,
            invitaciones_reemplazadas=0,
        )
    try:
        return _escribir(conn, ctx, planilla, analisis, ip)
    except DBAPIError as e:
        if (
            isinstance(e.orig, UniqueViolation)
            or e.orig.diag.message_hint in CAMBIO_DURANTE_LA_APLICACION
        ):
            raise ErrorApi(
                409,
                CodigoError.IMPORTACION_CON_PROBLEMAS,
                "El padrón cambió mientras se aplicaba la planilla: no se escribió nada. Pedí el"
                " informe de nuevo.",
            ) from e
        raise


def _escribir(
    conn: Connection, ctx: Contexto, planilla: Planilla, analisis: Analisis, ip: str | None
) -> ImportacionAplicada:
    importacion = conn.execute(
        text("select nucleo.registrar_importacion_padron(:h, :a)"),
        {"h": planilla.sha256, "a": ctx.persona_id},
    ).scalar_one()
    creadas: dict[str, UUID] = {}
    for p in analisis.planes:
        if p.unidad != AccionUnidad.CREAR or p.fila.designacion in creadas:
            continue
        unidad = conn.execute(
            text(
                "insert into nucleo.unidad (consorcio_id, designacion) values (:c, :d) returning id"
            ),
            {"c": ctx.consorcio_id, "d": p.fila.designacion},
        ).scalar_one()
        creadas[p.fila.designacion] = unidad
        auditoria.registrar(
            conn,
            consorcio_id=ctx.consorcio_id,
            actor=ctx.persona_id,
            origen="api",
            accion="consorcio.unidad.crear",
            entidad="unidad",
            entidad_id=unidad,
            despues={"importacion_id": str(importacion)},
            ip=ip,
        )
    vinculos = invitaciones = reemplazadas = 0
    for p in analisis.planes:
        r = p.fila.relacion
        if p.vinculo == AccionVinculo.EXISTENTE:
            if p.invitacion == AccionInvitacion.REEMPLAZAR_VENCIDA:  # renovación (23-10-2026)
                _invitar(conn, ctx, p.vinculo_existente, r.nombre)
                invitaciones += 1
                reemplazadas += 1
            continue
        if p.vinculo != AccionVinculo.CREAR:
            continue
        vinculo = conn.execute(
            text(
                "select nucleo.importar_vinculo(:i, :u, :c, :n, cast(:t as nucleo.tipo_vinculo),"
                " :d, :h, :a)"
            ),
            {
                "i": importacion,
                "u": p.unidad_id or creadas[p.fila.designacion],
                "c": r.correo,
                "n": r.nombre,
                "t": r.tipo.value,
                "d": r.desde,
                "h": r.hasta,
                "a": ctx.persona_id,
            },
        ).scalar_one()
        vinculos += 1
        if p.invitacion in (AccionInvitacion.CREAR, AccionInvitacion.REEMPLAZAR_VENCIDA):
            _invitar(conn, ctx, vinculo, r.nombre)
            invitaciones += 1
            reemplazadas += p.invitacion == AccionInvitacion.REEMPLAZAR_VENCIDA
    return ImportacionAplicada(
        importacion_id=importacion,
        archivo_sha256=planilla.sha256.hex(),
        unidades_creadas=len(creadas),
        vinculos_creados=vinculos,
        invitaciones_creadas=invitaciones,
        invitaciones_reemplazadas=reemplazadas,
    )


def _invitar(conn: Connection, ctx: Contexto, vinculo: UUID, nombre: str) -> None:
    """Crea (o renueva) la invitación que nombra al vínculo. El token no viaja a ningún lado: la
    invitación no se envía. La persona la ve y la acepta con su sesión (`/me/invitaciones`)."""
    conn.execute(
        text("select nucleo.crear_invitacion_importada(:v, :n, :h, :p, :a)"),
        {
            "v": vinculo,
            "n": nombre,
            "h": sesiones.hash_token(sesiones.nuevo_token()),
            "p": VIGENCIA_INVITACION,
            "a": ctx.persona_id,
        },
    )


# Rutas -----------------------------------------------------------------------------------------

GestionarDep = Annotated[Permiso, Depends(requiere("padron.gestionar"))]
InvitarDep = Annotated[Permiso, Depends(requiere("invitaciones.crear"))]


@router.post(
    "/consorcios/{consorcio_id}/padron/importacion/informe",
    response_model=InformeImportacion,
    summary="Informe previo de una importación de padrón (RF02); no escribe nada",
    responses={403: {"description": "Sin `padron.gestionar` en ninguna unidad de la planilla."}},
)
def informe(
    pedido: PlanillaPadron, gestionar: GestionarDep, invitar: InvitarDep, motor: MotorDep
) -> InformeImportacion:
    """Qué haría cada fila: crear o encontrar la unidad, crear o encontrar el vínculo (con
    `ultimo_dia` y `vigente_hasta` lado a lado) y su invitación, y sus problemas. Los
    coeficientes se informan, no se escriben. Sin correos ni nombres."""
    ctx = gestionar.ctx
    planilla = leer_planilla(pedido.archivo)
    _exigir_alguna_gestion(motor, ctx, planilla, gestionar)
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        return analizar(conn, ctx, planilla, gestionar, invitar).informe


@router.post(
    "/consorcios/{consorcio_id}/padron/importacion",
    response_model=ImportacionAplicada,
    summary="Aplicar una importación de padrón (RF02): todo o nada",
    responses={
        403: {
            "description": "Una fila queda fuera de `padron.gestionar` (o de `invitaciones.crear`"
            " si crea una invitación), o crea una unidad sin alcance general."
        },
        409: {"description": "El padrón cambió mientras se aplicaba: no se escribió nada."},
        422: {"description": "La planilla tiene problemas: no se escribió nada."},
    },
)
def aplicar(
    pedido: PlanillaPadron,
    request: Request,
    gestionar: GestionarDep,
    invitar: InvitarDep,
    idempotencia: IdempotenciaDep,
    motor: MotorDep,
) -> Response:
    """Escribe en una transacción las unidades nuevas y los vínculos nuevos (fuente
    `importacion`, sin confirmar, con quién los cargó) y crea, sin enviarlas, sus invitaciones.
    Lo que ya existe igual no se toca: reimportar el mismo contenido no escribe nada."""
    ctx = gestionar.ctx
    planilla = leer_planilla(pedido.archivo)
    _exigir_alguna_gestion(motor, ctx, planilla, gestionar)
    with transaccion(motor, persona_id=ctx.persona_id, consorcio_id=ctx.consorcio_id) as conn:
        _verificar_alcance(analizar(conn, ctx, planilla, gestionar, invitar), gestionar, invitar)
    ip = sesiones.ip_cliente(request)
    respuesta = responder_una_vez(
        motor,
        ctx,
        idempotencia,
        lambda conn: (200, _aplicar(conn, ctx, planilla, gestionar, invitar, ip)),
    )
    log.info(
        "importación de padrón respondida",
        extra={
            "campos": {
                "consorcio_id": str(ctx.consorcio_id),
                "persona_id": str(ctx.persona_id),
                "archivo_sha256": planilla.sha256.hex(),
            }
        },
    )
    return respuesta
