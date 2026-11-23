"""Ayuda por una persona autorizada (`0127`; RF53, decisiones del propietario del 25-10-2026, §1.7;
H2b, criterio 5).

Contra servicios reales: PostgreSQL, el almacén S3 de pruebas y ClamAV. La escena es la de los
reclamos (`0123`, Modo Consejo): la propietaria confirmada de «1° A» y «1-A» con la liquidación de
septiembre confirmada, el Consejo, la administración, una inquilina y una ocupante confirmadas de
«PB-A» y una propietaria sin confirmar. Se suma quien ayuda: una cuenta sin ningún vínculo con el
edificio. Cada negación lleva su control: el mismo pedido de quien sí puede, o sin la ayuda, pasa.
Datos SINTETICOS / NO REAL.
"""

import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cem_contracts import ayuda as contrato
from cem_contracts.ayuda import ENCABEZADO_AYUDA, AlcanceAyuda
from cem_contracts.estados import ModoOperacion, TipoVinculo
from cem_contracts.politicas import Politica
from psycopg.errors import InsufficientPrivilege
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.autorizacion.evaluador import (
    ContextoAutorizacion,
    Entidad,
    Facultad,
    Mandato,
    Motivo,
    Vinculo,
    alcanza,
    evaluar,
)
from app.autorizacion.politicas import POLITICAS
from app.plataforma.copia_continua import CADENA
from scripts import exportar_consorcio
from tests.conftest import clave_idempotencia
from tests.escenarios_autorizacion import CATALOGO, PAQUETES
from tests.test_exportacion_2c3 import (  # noqa: F401  (`claves` y `con_url` son fixtures)
    argumentos,
    claves,
    con_url,
    descifrar,
    entidades,
    verificar_manifiesto,
)
from tests.test_liquidacion_oficial_0119 import bucket, firmas_de_la_imagen  # noqa: F401
from tests.test_mandatos import otra_persona
from tests.test_orden_pago_neutra import nueva_sesion
from tests.test_reclamos_liquidacion_0123 import (
    TEXTO,
    armar,
    correo_de,
    correos,
    presentado,
    terminar_vinculo,
)
from tests.test_rutas_egresos import cliente

pytestmark = [pytest.mark.db, pytest.mark.redis]
# ruff: noqa: F811

__all__ = ["bucket", "firmas_de_la_imagen"]

MIGRACION = Path(__file__).resolve().parent.parent / "migrations/versions/0127_ayuda_autorizada.py"
TABLAS = ("nucleo.autorizacion_ayuda", "nucleo.autorizacion_ayuda_revocacion")
CONSULTAR = ["consultar_expensas"]
TODO = ["consultar_expensas", "preparar_reclamos"]


def _migracion():
    spec = importlib.util.spec_from_file_location("migracion_0127", MIGRACION)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def armar_con_ayuda(motor_migrate, motor_app) -> dict:
    e = armar(motor_migrate, motor_app)
    e["ayu"] = otra_persona(motor_migrate, e["cons"], verificada=False)
    e["sesion_ayu"] = nueva_sesion(motor_migrate, e["ayu"])
    return e


@pytest.fixture
def e(motor_migrate, motor_app, bucket) -> dict:
    return armar_con_ayuda(motor_migrate, motor_app)


def ruta(e: dict, *partes: str) -> str:
    return "/".join([f"/api/v1/consorcios/{e['cons']}", *partes])


def otorgar(e: dict, motor_migrate, quien: str = "prop", a: str = "ayu", alcances=CONSULTAR,
            correo: str | None = None):  # fmt: skip
    datos = {"correo": correo or correo_de(motor_migrate, e[a]), "alcances": alcances}
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        return c.post(ruta(e, "ayudas"), json=datos, headers=clave_idempotencia())


def otorgada(e: dict, motor_migrate, alcances=CONSULTAR, a: str = "ayu") -> dict:
    r = otorgar(e, motor_migrate, alcances=alcances, a=a)
    assert r.status_code == 201, r.text
    return r.json()


def revocar(e: dict, autorizacion: str, quien: str = "prop", ayudando: str | None = None):
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        encabezados = clave_idempotencia() | ({} if ayudando is None
                                              else {ENCABEZADO_AYUDA: ayudando})  # fmt: skip
        return c.post(ruta(e, "ayudas", autorizacion, "revocacion"), headers=encabezados)


def ayudando(e: dict, autorizacion: str, metodo: str, url: str, quien: str = "ayu", **kw):
    """El pedido de quien ayuda, con la autorización en el encabezado."""
    with cliente(e[quien], e[f"sesion_{quien}"]) as c:
        encabezados = {ENCABEZADO_AYUDA: autorizacion} | kw.pop("headers", {})
        return c.request(metodo, url, headers=encabezados, **kw)


def codigo(r) -> tuple[int, str]:
    return r.status_code, r.json()["codigo"]


def hint(error: pytest.ExceptionInfo) -> str | None:
    return error.value.orig.diag.message_hint


def eventos(motor_migrate, cons, accion: str) -> list[dict]:
    with motor_migrate.connect() as c:
        return [dict(f) for f in c.execute(text(
            "select actor_persona_id, en_representacion_de, autorizacion_id, despues"
            " from auditoria.eventos where consorcio_id = :c and accion = :a order by secuencia"),
            {"c": cons, "a": accion}).mappings()]  # fmt: skip


def en_la_base_con_ayuda(motor_app, e: dict, autorizacion, sql: str, **p):
    """Una función de la base en la transacción de la asistida con `app.ayuda_id` y la sesión de
    quien ayuda, como la deja `app/db.py` en una petición de ayuda; deshecha al final."""
    with motor_app.connect() as cx, cx.begin() as t:
        cx.execute(text(
            "select set_config('app.persona_id', :p, true),"
            " set_config('app.consorcio_id', :c, true),"
            " set_config('app.ayuda_id', :a, true)"),
            {"p": str(e["prop"]), "c": str(e["cons"]),
             "a": "" if autorizacion is None else str(autorizacion)})  # fmt: skip
        try:
            return cx.execute(text(sql), {"actor": e["prop"], "s": e["sesion_ayu"]} | p).all()
        finally:
            t.rollback()


# Constantes y políticas --------------------------------------------------------------------


def test_la_migracion_copia_los_alcances_del_contrato():
    m = _migracion()
    assert m.ALCANCES == tuple(a.value for a in contrato.ALCANCES_OTORGABLES)
    assert dict(m.REQUIERE) == {a.value: b.value for a, b in contrato.REQUIERE.items()}


def test_ninguna_politica_decisoria_admite_ayuda():
    con_ayuda = {a: p.ayuda for a, p in POLITICAS.items() if p.ayuda is not None}
    assert con_ayuda == {
        "consorcios.mis_acciones": AlcanceAyuda.ORIENTARSE,
        "cuenta.ver_propia": AlcanceAyuda.CONSULTAR_EXPENSAS,
        "fiscalizacion.ver_resumen": AlcanceAyuda.CONSULTAR_EXPENSAS,
        "reclamos_liquidacion.ver": AlcanceAyuda.CONSULTAR_EXPENSAS,
        "reclamos_liquidacion.presentar": AlcanceAyuda.PREPARAR_RECLAMOS,
    }
    # El contrato no deja declarar ayuda en una económica, con separación o con respaldo.
    base = POLITICAS["reclamos_liquidacion.presentar"].model_dump()
    for cambio in ({"economica": True}, {"separacion": ["no_es_quien_preparo"]},
                   {"respaldo": "acta"}, {"destinatario_unico": True},
                   {"concede_por_vinculo": None, "facultad": "reclamos.atender"}):  # fmt: skip
        with pytest.raises(ValidationError, match="RF53"):
            Politica.model_validate(base | cambio)
    Politica.model_validate(base)  # control


# Evaluador: los actos decisorios, uno por categoría -----------------------------------------

HOY = date(2026, 6, 15)
CONS = UUID("0192f0c4-0000-7000-8000-00000000000a")
YO = UUID("0192f0c4-0000-7000-8000-000000000001")
U3A = UUID("0192f0c4-0000-7000-8000-0000000003a0")


def asistida_con_todo(ayuda: frozenset[AlcanceAyuda] | None) -> ContextoAutorizacion:
    """La persona asistida lo tiene todo: propietaria confirmada, los paquetes completos del
    Consejo y de la administración, cada facultad del catálogo, la identidad verificada y el
    segundo factor reciente. Si aun así quien ayuda no puede, es la regla de la ayuda."""
    mandatos = [Mandato(UUID(int=i + 1), CONS, date(2026, 1, 1), None, tipo=t)
                for i, t in enumerate(("miembro_consejo", "administrador"))]  # fmt: skip
    facultades = [Facultad(c, None, mandatos[0].id) for c in CATALOGO]
    facultades += [Facultad(c, None, mandatos[1].id) for c in PAQUETES["administrador"]]
    return ContextoAutorizacion(
        persona_id=YO, consorcio_id=CONS, hoy=HOY,
        vinculos=(Vinculo(TipoVinculo.PROPIETARIO, U3A, confirmado=True),),
        modo=ModoOperacion.ADMINISTRADOR_AUMENTADO, mandatos=tuple(mandatos),
        facultades=tuple(facultades), hay_administrador_vigente=True,
        identidades={YO: UUID(int=99)}, segundo_factor_reciente=True,
        autorizaciones=frozenset(POLITICAS), ayuda=ayuda,
    )  # fmt: skip


TODA_AYUDA = frozenset(AlcanceAyuda)
CATEGORIAS = {
    "votar o registrar una decisión": ["decisiones.registrar"],
    "firmar": ["solicitud_formal.firmar"],
    "aprobar": ["egresos.aprobar_orden", "coeficientes.aprobar", "liquidacion.aprobar",
                "mandatos.aprobar_asociacion", "fondo_reserva.autorizar"],
    "pagar": ["egresos.ejecutar_pago", "egresos.conciliar_pago"],
    "facultades económicas": sorted(a for a, p in POLITICAS.items() if p.economica),
    "permisos": ["invitaciones.crear", "padron.confirmar_vinculo", "proveedores.autorizar",
                 "ayuda.otorgar", "ayuda.revocar", "contactos.gestionar"],
    "modo": ["modo.cambiar"],
    "mandatos": ["mandatos.alta", "mandatos.baja", "mandatos.asociar_persona"],
}  # fmt: skip
"""Verificar identidades y resolver la privacidad no tienen acción en la API (las hace soporte con
sus scripts; la copia propia es de la sesión): las cubre
`test_privacidad_soporte_y_cuenta_rechazan_el_encabezado`."""


@pytest.mark.parametrize("categoria", sorted(CATEGORIAS))
def test_cada_categoria_decisoria_se_niega_a_quien_ayuda(categoria):
    con, sin = asistida_con_todo(TODA_AYUDA), asistida_con_todo(None)
    entidad = Entidad(recursos={"unidad": str(U3A)}, respaldo_presente=True)
    for accion in CATEGORIAS[categoria]:
        d = evaluar(accion, con, entidad)
        assert (d.permitido, d.estado, d.motivo_interno) == (
            False, 403, Motivo.AYUDA_NO_ADMITIDA), accion  # fmt: skip
        assert not alcanza(accion, con), accion
        # Control: la asistida misma, sin ayuda, no choca con la regla de la ayuda.
        assert evaluar(accion, sin, entidad).motivo_interno != Motivo.AYUDA_NO_ADMITIDA, accion
        if accion != "modo.cambiar":  # nadie lo alcanza desde la API (C7)
            assert alcanza(accion, sin), accion


def test_ayudando_toda_accion_sin_ayuda_declarada_se_niega():
    con = asistida_con_todo(TODA_AYUDA)
    negadas = {a for a in POLITICAS if evaluar(a, con, Entidad()).motivo_interno
               == Motivo.AYUDA_NO_ADMITIDA}  # fmt: skip
    assert negadas == {a for a, p in POLITICAS.items() if p.ayuda is None}
    assert len(negadas) == len(POLITICAS) - 5


def test_el_alcance_se_respeta_y_los_mandatos_no_se_prestan():
    solo_consultar = asistida_con_todo(frozenset({AlcanceAyuda.ORIENTARSE,
                                                  AlcanceAyuda.CONSULTAR_EXPENSAS}))  # fmt: skip
    unidad = Entidad(recursos={"unidad": str(U3A)})
    d = evaluar("reclamos_liquidacion.presentar", solo_consultar, unidad)
    assert d.motivo_interno == Motivo.FUERA_DEL_ALCANCE_DE_AYUDA
    con_preparar = replace(solo_consultar, ayuda=TODA_AYUDA)
    assert evaluar("reclamos_liquidacion.presentar", con_preparar, unidad).permitido  # control
    # La lectura de reclamos concede por el vínculo, no por la facultad de atender.
    assert evaluar("reclamos_liquidacion.ver", solo_consultar, Entidad()).motivo_interno == (
        Motivo.CONCEDIDO_POR_VINCULO)  # fmt: skip
    sin_vinculo = replace(solo_consultar, vinculos=())
    assert evaluar("reclamos_liquidacion.ver", sin_vinculo, Entidad()).estado == 404
    sin_ayuda = replace(sin_vinculo, ayuda=None)
    assert evaluar("reclamos_liquidacion.ver", sin_ayuda, Entidad()).motivo_interno == (
        Motivo.CONCEDIDO_POR_FACULTAD)  # control: la misma persona, por sí misma  # fmt: skip


# Otorgar -----------------------------------------------------------------------------------


def test_otorgar_avisa_a_quien_ayuda_y_queda_auditado(e, motor_migrate):
    a = otorgada(e, motor_migrate, TODO)
    assert (a["alcances"], a["revocada_en"]) == (TODO, None)
    [aviso] = [x["carga"] for x in correos(motor_migrate, e["cons"])
               if x["carga"]["para"] == correo_de(motor_migrate, e["ayu"])]  # fmt: skip
    assert "https://cem.ejemplo.com/ayuda" in aviso["texto"]
    for dato in ("consultar_expensas", "preparar_reclamos", "1-A", "1° A"):
        assert dato not in aviso["texto"] + aviso["asunto"], dato
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.ayuda.otorgar")
    assert (ev["actor_persona_id"], ev["en_representacion_de"], ev["autorizacion_id"]) == (
        e["prop"], None, None)  # fmt: skip
    assert ev["despues"] == {"autorizado_id": str(e["ayu"]), "alcances": TODO}
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        recibidas = c.get("/api/v1/me/ayudas").json()["elementos"]
    assert [(x["id"], x["consorcio_id"], x["alcances"]) for x in recibidas] == [
        (a["id"], str(e["cons"]), TODO)]  # fmt: skip
    with cliente(e["prop"], e["sesion_prop"]) as c:
        lista = c.get(ruta(e, "ayudas")).json()
    assert [x["id"] for x in lista["elementos"]] == [a["id"]]
    assert lista["alcances_otorgables"] == TODO


def test_otorgar_solo_a_una_cuenta_ajena_una_vez_y_quien_puede(e, motor_migrate):
    assert codigo(otorgar(e, motor_migrate, correo="nadie-sintetico@ejemplo.invalid")) == (
        422, "ayuda_sin_cuenta")  # fmt: skip
    assert codigo(otorgar(e, motor_migrate, a="prop")) == (422, "ayuda_a_si_misma")
    r = otorgar(e, motor_migrate, alcances=["preparar_reclamos"])
    assert r.status_code == 422  # preparar pide consultar
    otorgada(e, motor_migrate)
    assert codigo(otorgar(e, motor_migrate)) == (409, "ayuda_ya_vigente")
    for quien in ("inq", "oc", "sin_confirmar", "ad"):
        assert otorgar(e, motor_migrate, quien=quien, a="ayu").status_code == 403, quien
    # Control: la misma propietaria autoriza a otra persona.
    assert otorgar(e, motor_migrate, a="inq").status_code == 201


# Ayudar: consultar y preparar --------------------------------------------------------------


def test_quien_ayuda_consulta_lo_de_la_asistida_y_nada_mas(e, motor_migrate):
    propio = presentado(e)
    a = otorgada(e, motor_migrate)
    acciones = ayudando(e, a["id"], "GET", ruta(e, "mis-acciones")).json()["acciones"]
    assert acciones == ["consorcios.mis_acciones", "cuenta.ver_propia",
                        "fiscalizacion.ver_resumen", "reclamos_liquidacion.ver"]  # fmt: skip
    cuenta = ayudando(e, a["id"], "GET", ruta(e, "mi-cuenta"))
    assert cuenta.status_code == 200, cuenta.text
    assert {u["designacion"] for u in cuenta.json()["liquidacion"]["unidades"]} == {"1-A", "1° A"}
    assert ayudando(e, a["id"], "GET", ruta(e, "resumen")).status_code == 200
    reclamos = ayudando(e, a["id"], "GET", ruta(e, "reclamos-liquidacion")).json()
    assert [x["id"] for x in reclamos["elementos"]] == [propio["id"]]
    assert reclamos["atiende"] is False
    edificios = ayudando(e, a["id"], "GET", "/api/v1/me/consorcios").json()
    # Un vínculo por unidad, como para la propia persona; sin el mandato que la asistida tuviera.
    assert [(x["id"], x["vinculos"]) for x in edificios] == [
        (str(e["cons"]), ["propietario", "propietario"])]  # fmt: skip
    # Fuera del alcance: no se ofrece y se niega.
    r = ayudando(e, a["id"], "POST", ruta(e, "reclamos-liquidacion"), json={
        "liquidacion_id": e["liq"], "unidad_id": str(e["u"]["1-A"]), "texto": TEXTO},
        headers=clave_idempotencia())  # fmt: skip
    assert codigo(r) == (403, "sin_permiso")
    # Control: sin el encabezado, quien ayuda no tiene nada en el edificio.
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        assert c.get(ruta(e, "mi-cuenta")).status_code == 404
        assert c.get("/api/v1/me/consorcios").json() == []


def test_quien_ayuda_prepara_y_presenta_el_reclamo_auditado_en_representacion(e, motor_migrate):
    a = otorgada(e, motor_migrate, TODO)
    r = ayudando(e, a["id"], "POST", ruta(e, "reclamos-liquidacion"), json={
        "liquidacion_id": e["liq"], "unidad_id": str(e["u"]["1-A"]), "texto": TEXTO},
        headers=clave_idempotencia())  # fmt: skip
    assert r.status_code == 201, r.text
    reclamo = r.json()
    assert reclamo["propio"] is True and reclamo["acciones"] == ["retirar"]
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.reclamo_liquidacion.presentar")
    assert (ev["actor_persona_id"], ev["en_representacion_de"], ev["autorizacion_id"]) == (
        e["ayu"], e["prop"], UUID(a["id"]))  # fmt: skip
    # La asistida lo ve como suyo y lo puede retirar ella.
    with cliente(e["prop"], e["sesion_prop"]) as c:
        assert [x["id"] for x in c.get(ruta(e, "reclamos-liquidacion")).json()["elementos"]] == [
            reclamo["id"]]  # fmt: skip
    # Retirar lo preparado también va en representación.
    r = ayudando(
        e,
        a["id"],
        "POST",
        ruta(e, "reclamos-liquidacion", reclamo["id"], "retiro"),
        json={"motivo": "Lo aclaró por teléfono"},
        headers=clave_idempotencia(),
    )
    assert r.status_code == 200, r.text
    [cierre] = eventos(motor_migrate, e["cons"], "consorcio.reclamo_liquidacion.cerrar")
    assert (cierre["actor_persona_id"], cierre["en_representacion_de"]) == (e["ayu"], e["prop"])


def test_ayudando_lo_decisorio_se_niega_y_la_negacion_se_audita(e, motor_migrate):
    a = otorgada(e, motor_migrate, TODO)
    intentos = [
        ("POST", ruta(e, "ayudas"), {"correo": correo_de(motor_migrate, e["inq"]),
                                     "alcances": CONSULTAR}),
        ("GET", ruta(e, "ayudas"), None),
        ("POST", ruta(e, "invitaciones"), {"unidad_id": str(e["u"]["1-A"]), "tipo": "inquilino",
                                           "correo": "x@ejemplo.invalid", "nombre": "X"}),
        ("GET", ruta(e, "documentos"), None),
        ("GET", ruta(e, "auditoria"), None),
    ]  # fmt: skip
    antes = len(eventos(motor_migrate, e["cons"], "autorizacion.denegada"))
    for metodo, url, datos in intentos:
        r = ayudando(e, a["id"], metodo, url, json=datos, headers=clave_idempotencia())
        assert r.status_code == 403, (url, r.text)
    negaciones = eventos(motor_migrate, e["cons"], "autorizacion.denegada")[antes:]
    assert len(negaciones) == len(intentos)
    for n in negaciones:
        assert (n["actor_persona_id"], n["en_representacion_de"], n["autorizacion_id"]) == (
            e["ayu"], e["prop"], UUID(a["id"]))  # fmt: skip
        assert n["despues"]["motivo"] == "ayuda_no_admitida"
    # Control: la asistida misma ve sus autorizaciones.
    with cliente(e["prop"], e["sesion_prop"]) as c:
        assert c.get(ruta(e, "ayudas")).status_code == 200


def test_la_base_niega_a_quien_ayuda_lo_que_la_ayuda_no_alcanza(e, motor_migrate, motor_app):
    solo = otorgada(e, motor_migrate)
    presentar = ("select * from nucleo.presentar_reclamo_liquidacion(:i, :u, null, 'T', false,"
                 " :actor, 'api', :s)")  # fmt: skip
    p = {"i": e["liq"], "u": e["u"]["1-A"]}
    with pytest.raises(DBAPIError) as error:
        en_la_base_con_ayuda(motor_app, e, solo["id"], presentar, **p)
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Sin la ayuda en la transacción, la sesión de quien ayuda no es la de la asistida.
    with pytest.raises(DBAPIError) as error:
        en_la_base_con_ayuda(motor_app, e, None, presentar, **p)
    assert hint(error) == "sesion_invalida"
    # Lo que ninguna ayuda alcanza (atender reclamos, preparar solicitudes) se niega en la base.
    with pytest.raises(DBAPIError) as error:
        en_la_base_con_ayuda(
            motor_app,
            e,
            solo["id"],
            "select * from nucleo.ayudas_otorgadas_lectura(:actor, 'api', :s)",
        )
    assert isinstance(error.value.orig, InsufficientPrivilege)
    # Control: con preparar_reclamos, la misma llamada pasa (y se deshace).
    revocar(e, solo["id"])
    todo = otorgada(e, motor_migrate, TODO)
    assert len(en_la_base_con_ayuda(motor_app, e, todo["id"], presentar, **p)) >= 1


# Revocar -----------------------------------------------------------------------------------


def test_revocar_solo_quien_la_dio_y_deja_de_servir_en_el_acto(e, motor_migrate):
    a = otorgada(e, motor_migrate)
    assert ayudando(e, a["id"], "GET", ruta(e, "mi-cuenta")).status_code == 200  # control
    # Quien ayuda no la revoca (ni ayudando ni por sí: no tiene vínculo).
    assert codigo(revocar(e, a["id"], "ayu", ayudando=a["id"])) == (403, "sin_permiso")
    assert revocar(e, a["id"], "ayu").status_code == 404
    # Otra propietaria del edificio no la ve.
    e["prop2"] = otra_persona(motor_migrate, e["cons"], verificada=False)
    from tests.test_liquidacion_oficial_0119 import vincular

    vincular(motor_migrate, e["cons"], e["prop2"], e["u"]["PB-A"], "propietario", confirmado=True)
    e["sesion_prop2"] = nueva_sesion(motor_migrate, e["prop2"])
    assert codigo(revocar(e, a["id"], "prop2")) == (404, "no_encontrado")
    r = revocar(e, a["id"])
    assert r.status_code == 200 and r.json()["revocada_en"] is not None
    assert codigo(revocar(e, a["id"])) == (409, "ayuda_ya_revocada")
    # Quien la tenía recibe `ayuda_revocada` (la pantalla sale sola de la ayuda); cualquier otra
    # persona con ese encabezado, 404: no se dice que existió.
    for url in (ruta(e, "mi-cuenta"), "/api/v1/me/consorcios"):
        assert codigo(ayudando(e, a["id"], "GET", url)) == (403, "ayuda_revocada")
        assert codigo(ayudando(e, a["id"], "GET", url, quien="prop2")) == (404, "no_encontrado")
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        assert c.get("/api/v1/me/ayudas").json()["elementos"] == []
    # La historia queda: la lista la muestra revocada y hay un evento por cada acto.
    with cliente(e["prop"], e["sesion_prop"]) as c:
        [x] = c.get(ruta(e, "ayudas")).json()["elementos"]
    assert x["id"] == a["id"] and x["revocada_en"] is not None
    [ev] = eventos(motor_migrate, e["cons"], "consorcio.ayuda.revocar")
    assert (ev["actor_persona_id"], ev["en_representacion_de"]) == (e["prop"], None)
    # Una nueva, después de revocar, sí.
    assert otorgar(e, motor_migrate).status_code == 201


def test_deja_de_servir_si_la_asistida_ya_no_es_propietaria(e, motor_migrate):
    a = otorgada(e, motor_migrate)
    for unidad in ("1-A", "1° A"):
        terminar_vinculo(motor_migrate, e, "prop", unidad)
    assert codigo(ayudando(e, a["id"], "GET", ruta(e, "mis-acciones"))) == (403, "ayuda_revocada")
    assert codigo(ayudando(e, a["id"], "GET", "/api/v1/me/consorcios")) == (403, "ayuda_revocada")
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        assert c.get("/api/v1/me/ayudas").json()["elementos"] == []


def test_una_autorizacion_no_se_cambia_ni_se_borra_y_tiene_rls_forzada(e, motor_migrate):
    a = otorgada(e, motor_migrate)
    revocar(e, a["id"])
    intentos = [
        "update nucleo.autorizacion_ayuda set alcances = array['preparar_reclamos'] where id = :a",
        "delete from nucleo.autorizacion_ayuda where id = :a",
        "update nucleo.autorizacion_ayuda_revocacion set revocada_en = now()"
        " where autorizacion_id = :a",
        "delete from nucleo.autorizacion_ayuda_revocacion where autorizacion_id = :a",
        "truncate nucleo.autorizacion_ayuda_revocacion",
    ]
    for sql in intentos:
        with pytest.raises(DBAPIError) as error:
            with motor_migrate.begin() as cx:
                cx.execute(text(sql), {"a": a["id"]})
        assert hint(error) == "historia_inmutable", sql
    with motor_migrate.connect() as cx:
        rls = cx.execute(text(
            "select c.oid::regclass::text, c.relrowsecurity, c.relforcerowsecurity,"
            " (select p.qual from pg_policies p where p.schemaname = 'nucleo'"
            "   and p.tablename = c.relname and p.policyname = 'por_consorcio')"
            " from pg_class c where c.oid = any(cast(:t as regclass[])) order by 1"),
            {"t": list(TABLAS)}).all()  # fmt: skip
        politica = "(consorcio_id = plataforma.consorcio_actual())"
        assert [tuple(f) for f in rls] == [(t, True, True, politica) for t in sorted(TABLAS)]
        for tabla in TABLAS:
            assert not cx.execute(
                text("select has_table_privilege('cem_app', :t, 'select, insert, update, delete')"),
                {"t": tabla}).scalar_one(), tabla  # fmt: skip


def test_otro_consorcio_no_ve_la_autorizacion_ni_la_usa(e, motor_migrate, motor_app):
    otro = armar_con_ayuda(motor_migrate, motor_app)
    a = otorgada(e, motor_migrate)
    # La autorización de este edificio, en el otro: 404, aunque quien ayuda tenga ahí la suya.
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        r = c.get(f"/api/v1/consorcios/{otro['cons']}/mi-cuenta",
                  headers={ENCABEZADO_AYUDA: a["id"]})  # fmt: skip
    assert r.status_code == 404
    # La propietaria del otro edificio no ve ni revoca la de este.
    with cliente(otro["prop"], otro["sesion_prop"]) as c:
        assert c.get(f"/api/v1/consorcios/{otro['cons']}/ayudas").json()["elementos"] == []
        assert c.post(f"/api/v1/consorcios/{otro['cons']}/ayudas/{a['id']}/revocacion",
                      headers=clave_idempotencia()).status_code == 404  # fmt: skip
    # La RLS: con el otro consorcio en el ámbito, la base no la encuentra.
    with motor_app.connect() as cx, cx.begin() as t:
        cx.execute(text("select set_config('app.persona_id', :p, true),"
                        " set_config('app.consorcio_id', :c, true)"),
                   {"p": str(e["ayu"]), "c": str(otro["cons"])})  # fmt: skip
        assert cx.execute(text("select * from nucleo.ayuda_vigente(:a, :s)"),
                          {"a": a["id"], "s": e["sesion_ayu"]}).all() == []  # fmt: skip
        cx.execute(text("select set_config('app.consorcio_id', :c, true)"), {"c": str(e["cons"])})
        assert (
            len(
                cx.execute(
                    text("select * from nucleo.ayuda_vigente(:a, :s)"),
                    {"a": a["id"], "s": e["sesion_ayu"]},
                ).all()
            )
            == 1
        )  # control
        t.rollback()
    # Control: en su edificio sirve.
    assert ayudando(e, a["id"], "GET", ruta(e, "mi-cuenta")).status_code == 200


def test_privacidad_soporte_y_cuenta_rechazan_el_encabezado(e, motor_migrate):
    a = otorgada(e, motor_migrate)
    for metodo, url in (
        ("GET", "/api/v1/privacidad/casos"),
        ("GET", "/api/v1/auth/sesiones"),
        ("GET", "/api/v1/me/ayudas"),
        ("GET", "/api/v1/soporte/sesion"),
        ("POST", ruta(e, "pagos-por-aclarar", str(uuid4()), "no-realizado")),
    ):
        r = ayudando(e, a["id"], metodo, url, json={}, headers=clave_idempotencia())
        assert codigo(r) == (403, "ayuda_no_admitida"), url
    # Control: sin el encabezado, la misma cuenta lee sus casos de privacidad.
    with cliente(e["ayu"], e["sesion_ayu"]) as c:
        assert c.get("/api/v1/privacidad/casos").status_code == 200
    # Un encabezado que no es una autorización suya: 404, como un edificio que no existe.
    assert ayudando(e, str(uuid4()), "GET", ruta(e, "mi-cuenta")).status_code == 404
    assert ayudando(e, "no-es-un-id", "GET", ruta(e, "mi-cuenta")).status_code == 404
    assert ayudando(e, a["id"], "GET", ruta(e, "mi-cuenta"), quien="inq").status_code == 404


# Auditoría ---------------------------------------------------------------------------------


def test_la_auditoria_registra_quien_actuo_y_la_cadena_lo_cubre(e, motor_migrate, motor_app):
    a = otorgada(e, motor_migrate, TODO)
    ayudando(e, a["id"], "GET", ruta(e, "ayudas"))  # una negación auditada
    with motor_migrate.connect() as cx:
        [fila] = cx.execute(text(
            "select * from auditoria.eventos where consorcio_id = :c and autorizacion_id = :a"),
            {"c": e["cons"], "a": a["id"]}).mappings().all()  # fmt: skip
        assert (fila["actor_persona_id"], fila["en_representacion_de"]) == (e["ayu"], e["prop"])
        total, rotos, distintos, control = cx.execute(CADENA).one()
        assert (rotos, distintos) == (0, 0) and control == total > 0
        # Cambiar en representación de quién rompe el hash: la cadena lo cubre.
        alterado = cx.execute(text(
            "select encode(sha256(convert_to(concat_ws('|', hash_previo, consorcio_id, id,"
            " secuencia, to_char(creado_en at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24\\:MI\\:SS.US'),"
            " actor_persona_id, origen, accion, entidad, entidad_id, antes, despues, motivo, ip,"
            " cast(:otra as uuid), autorizacion_id), 'UTF8')), 'hex') = hash"
            " from auditoria.eventos where id = :i"),
            {"otra": str(e["inq"]), "i": fila["id"]}).scalar_one()  # fmt: skip
        assert alterado is False
    # La aplicación no llena esas columnas, ni escribe otra persona durante la ayuda.
    with pytest.raises(DBAPIError) as error, motor_app.begin() as cx:
        cx.execute(text("select set_config('app.persona_id', :p, true),"
                        " set_config('app.consorcio_id', :c, true)"),
                   {"p": str(e["prop"]), "c": str(e["cons"])})  # fmt: skip
        cx.execute(text(
            "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
            " entidad, en_representacion_de, autorizacion_id, hash_previo, hash)"
            " values (:c, :p, 'api', 'x', 'x', :p, :a, '', '')"),
            {"c": e["cons"], "p": e["prop"], "a": a["id"]})  # fmt: skip
    assert isinstance(error.value.orig, InsufficientPrivilege)
    with pytest.raises(DBAPIError) as error, motor_app.begin() as cx:
        cx.execute(text("select set_config('app.persona_id', :p, true),"
                        " set_config('app.consorcio_id', :c, true),"
                        " set_config('app.ayuda_id', :a, true)"),
                   {"p": str(e["prop"]), "c": str(e["cons"]), "a": a["id"]})  # fmt: skip
        cx.execute(text(
            "insert into auditoria.eventos (consorcio_id, actor_persona_id, origen, accion,"
            " entidad, hash_previo, hash) values (:c, :o, 'api', 'x', 'x', '', '')"),
            {"c": e["cons"], "o": e["mc"]})  # fmt: skip
    assert isinstance(error.value.orig, InsufficientPrivilege)


def test_la_exportacion_lleva_las_autorizaciones_sin_identificadores(
    e, motor_migrate, con_url, claves, tmp_path
):
    a = otorgada(e, motor_migrate, TODO)
    revocar(e, a["id"])
    salida = tmp_path / f"exportacion-{uuid4().hex[:8]}.zip.age"
    assert exportar_consorcio.main(argumentos(e, claves, salida)) == 0
    paquete = descifrar(salida, claves["privada"])
    assert verificar_manifiesto(paquete) == []
    datos = entidades(paquete)
    [x] = datos["autorizacion_ayuda"]
    [r] = datos["autorizacion_ayuda_revocacion"]
    assert (x["id"], x["alcances"], r["autorizacion_id"]) == (a["id"], TODO, a["id"])
    assert x["otorgante_nombre"] and x["autorizado_nombre"] and r["revocada_por_nombre"]
    crudo = json.dumps([x, r])
    for persona in ("prop", "ayu"):
        assert str(e[persona]) not in crudo
        assert correo_de(motor_migrate, e[persona]) not in crudo
