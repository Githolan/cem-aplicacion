"""0054: la revisión niega la cuenta completa por la identidad histórica (P3 115) y el
documento del proveedor tiene que respaldar (P3 93).

Quien preparó la orden o registró el contrato no recibe la cuenta destino completa en la revisión,
tampoco con otra cuenta suya, aunque la cuenta con la que actuó ya no esté vinculada a su identidad.
Se compara con la identidad guardada en el acto (`identidad_preparo`, `identidad_registro`), no con
los vínculos de hoy. Otro ser humano del Consejo sí la recibe.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import transaccion
from tests.test_egresos_1a import contrato, documento_de_contrato, enviada
from tests.test_orden_pago_neutra import (
    Escena,
    cbu_sintetico,
    escena_economica,
    llamar,
    niega,
    otra_cuenta_en_el_consejo,
)
from tests.test_proveedor_0048 import ALTA, DATOS, VERSION
from tests.test_rutas_egresos import get

pytestmark = pytest.mark.db

PREPARO = "select nucleo.lee_quien_preparo_la_orden(:orden, :actor)"
REGISTRO = "select nucleo.lee_quien_registro_el_contrato(:contrato, :actor)"


@pytest.fixture
def e(motor_migrate, motor_app) -> Escena:
    return escena_economica(motor_migrate, motor_app)


def desvincular(motor_migrate, e, persona: UUID) -> None:
    """La cuenta `persona` deja de estar vinculada a su identidad (lo que hace soporte)."""
    with motor_migrate.begin() as c:
        n = c.execute(
            text("update nucleo.identidad_persona set desvinculada_en = now()"
                 " where consorcio_id = :c and persona_id = :p and desvinculada_en is null"),
            {"c": e.cons, "p": persona},
        ).rowcount  # fmt: skip
    assert n == 1


def cuentas_de_la_orden(e, quien, o) -> set:
    r = get(e, quien, f"ordenes-pago/{o}/revision")
    assert r.status_code == 200, r.json()
    return {v["cuenta_destino"] for v in r.json()["versiones"]}


def cuenta_del_contrato(e, quien) -> str | None:
    r = get(e, quien, "contratos/revision")
    assert r.status_code == 200, r.json()
    (k,) = r.json()["elementos"]
    assert k["cuenta_ultimos_digitos"] == cbu_sintetico(1)[-4:]
    return k["cuenta_destino"]


def test_orden_quien_preparo_con_otra_cuenta_y_la_suya_desvinculada(motor_migrate, motor_app, e):
    """P3 115: el administrador preparó; después su cuenta se desvincula de su identidad. Otra
    cuenta suya en el Consejo sigue siendo quien preparó y no recibe la cuenta completa."""
    o = enviada(motor_migrate, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    assert cuentas_de_la_orden(e, otra, o) == {None}  # misma identidad, otra cuenta
    desvincular(motor_migrate, e, e.adm)
    assert cuentas_de_la_orden(e, otra, o) == {None}  # el vínculo de hoy no cambia quién preparó
    assert llamar(motor_app, e, otra, PREPARO, orden=o) is True
    # Control: otro ser humano del Consejo la ve entera.
    assert cuentas_de_la_orden(e, e.mc1, o) == {cbu_sintetico(1)}
    assert llamar(motor_app, e, e.mc1, PREPARO, orden=o) is False


def test_contrato_quien_registro_con_otra_cuenta_y_la_suya_desvinculada(motor_migrate, motor_app,
                                                                         e):  # fmt: skip
    """P3 115 en los contratos: quien registró ocupa el lugar de quien preparó."""
    k = contrato(motor_migrate, e)
    otra = otra_cuenta_en_el_consejo(motor_migrate, motor_app, e, e.adm)
    assert cuenta_del_contrato(e, otra) is None
    desvincular(motor_migrate, e, e.adm)
    assert cuenta_del_contrato(e, otra) is None
    assert llamar(motor_app, e, otra, REGISTRO, contrato=k) is True
    assert cuenta_del_contrato(e, e.mc1) == cbu_sintetico(1)  # control
    assert llamar(motor_app, e, e.mc1, REGISTRO, contrato=k) is False


def test_sin_identidad_vigente_falla_cerrada(motor_migrate, motor_app, e):
    """Un consejero cuya cuenta perdió la identidad no recibe la cuenta completa."""
    o = enviada(motor_migrate, e)
    k = contrato(motor_migrate, e)
    assert llamar(motor_app, e, e.mc1, PREPARO, orden=o) is False  # control antes
    desvincular(motor_migrate, e, e.mc1)
    assert llamar(motor_app, e, e.mc1, PREPARO, orden=o) is True
    assert llamar(motor_app, e, e.mc1, REGISTRO, contrato=k) is True
    # La ruta ni siquiera la atiende: sin identidad humana no hay revisión.
    for ruta in (f"ordenes-pago/{o}/revision", "contratos/revision"):
        r = get(e, e.mc1, ruta)
        assert (r.status_code, r.json()["codigo"]) == (403, "sin_permiso"), r.json()


def test_un_vinculo_corregido_despues_tambien_niega_al_dueno_real(motor_migrate, motor_app, e):
    """Revisión independiente de #180: el administrador preparó y registró con su identidad; después
    soporte corrige el vínculo y su cuenta pasa a la identidad de mc2. La identidad guardada no es
    la de mc2, pero hoy son el mismo ser humano: mc2 tampoco recibe la cuenta (como en la
    aprobación, que mira las dos)."""
    o = enviada(motor_migrate, e)
    k = contrato(motor_migrate, e)
    desvincular(motor_migrate, e, e.adm)
    with motor_migrate.begin() as c:
        c.execute(
            text("insert into nucleo.identidad_persona (consorcio_id, persona_id, identidad_id)"
                 " values (:c, :p, nucleo.identidad_humana_de(:c, :de))"),
            {"c": e.cons, "p": e.adm, "de": e.mc2},
        )  # fmt: skip
    assert cuentas_de_la_orden(e, e.mc2, o) == {None}
    assert cuenta_del_contrato(e, e.mc2) is None
    assert llamar(motor_app, e, e.mc2, PREPARO, orden=o) is True
    assert llamar(motor_app, e, e.mc2, REGISTRO, contrato=k) is True
    assert cuentas_de_la_orden(e, e.mc1, o) == {cbu_sintetico(1)}  # control
    assert cuenta_del_contrato(e, e.mc1) == cbu_sintetico(1)  # control


def test_de_otro_consorcio_o_inexistente_falla_cerrada(motor_migrate, motor_app, e):
    """Una orden o un contrato que no es del consorcio actual no se compara: verdadero."""
    o = enviada(motor_migrate, e)
    k = contrato(motor_migrate, e)
    ajena = escena_economica(motor_migrate, motor_app)
    assert llamar(motor_app, ajena, ajena.mc1, PREPARO, orden=o) is True
    assert llamar(motor_app, ajena, ajena.mc1, REGISTRO, contrato=k) is True
    assert llamar(motor_app, e, e.mc1, PREPARO, orden=uuid4()) is True
    assert llamar(motor_app, e, e.mc1, REGISTRO, contrato=uuid4()) is True
    # Y desde otro consorcio la revisión ni la encuentra.
    assert get(ajena, ajena.mc1, f"ordenes-pago/{o}/revision").status_code == 404


def test_solo_para_la_persona_actual(motor_migrate, motor_app, e):
    """Nadie pregunta por otro: el actor tiene que ser la persona de la petición."""
    o = enviada(motor_migrate, e)
    k = contrato(motor_migrate, e)
    with pytest.raises(DBAPIError) as error:
        with transaccion(motor_app, persona_id=e.mc1, consorcio_id=e.cons) as c:
            c.execute(text(PREPARO), {"orden": o, "actor": e.adm})
    assert error.value.orig.diag.sqlstate == "42501"
    niega(motor_app, e, None, REGISTRO, "42501", contrato=k)


def test_el_documento_del_proveedor_tiene_que_respaldar(motor_migrate, e):
    """P3 93: el documento de respaldo de una versión nueva del proveedor tiene que tener una
    versión `disponible`, como el de los contratos. Sin versiones, en cuarentena o rechazado por
    el antivirus no sirve."""
    for estado in (None, "en_cuarentena", "rechazado"):
        doc = documento_de_contrato(motor_migrate, e, version=estado)
        niega(motor_migrate, e, e.adm, ALTA, "documento_invalido",
              **{**DATOS, "documento": doc})  # fmt: skip
    doc = documento_de_contrato(motor_migrate, e)
    assert llamar(motor_migrate, e, e.adm, ALTA, **{**DATOS, "documento": doc})  # control


def test_un_documento_retirado_no_cambia_la_historia_pero_no_sirve_para_lo_nuevo(motor_migrate,
                                                                                  e):  # fmt: skip
    """Era válido cuando se cargó: la versión ya creada queda como está. Para una versión nueva
    ya no sirve; sin documento, sí."""
    doc = documento_de_contrato(motor_migrate, e)
    p = llamar(motor_migrate, e, e.adm, ALTA, **{**DATOS, "documento": doc})
    with motor_migrate.begin() as c:
        c.execute(text("update nucleo.version_documento set estado = 'rechazado',"
                       " motivo_rechazo = 'virus', firmas_en = null"
                       " where documento_id = :d"), {"d": doc})  # fmt: skip
        antes = c.execute(text("select documento_id from nucleo.proveedor_version"
                               " where proveedor_id = :p"), {"p": p}).scalars().all()  # fmt: skip
    assert antes == [doc]
    nueva = {**DATOS, "proveedor": p, "version": 1, "contacto": "otro@cem-prueba.invalid"}
    niega(motor_migrate, e, e.adm, VERSION, "documento_invalido", **{**nueva, "documento": doc})
    llamar(motor_migrate, e, e.adm, VERSION, **{**nueva, "documento": None})  # control
