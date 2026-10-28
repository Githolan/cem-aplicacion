"""H3, infraestructura común (`0044`): ningún paquete ni mandato acumula las etapas que la
separación de funciones reparte, y nadie tiene todavía una facultad económica de aprobar.

Diseño: `doc/diseno/H3_Paquete_Decisiones_Economicas.md` del repositorio CEM, «Infraestructura
común a todas las opciones». Las guardas son diferidas: se prueban con `set constraints all
immediate` y la transacción se deshace, para no dejar el catálogo cambiado.
"""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_mandatos_0012 import alta, escena  # noqa: F401  (`escena` es un fixture)
from tests.test_vinculo_0014 import base_aparte  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

# Decisión 7 del 21-10: hasta que decida el propietario, ningún paquete las tiene. La migración
# que lo decida cambia esta lista en el mismo PR.
APROBAR_SIN_DECIDIR = ("egresos.aprobar_orden", "liquidacion.aprobar", "coeficientes.aprobar")


def _probar(motor, *sentencias, params=None) -> None:
    """Corre las sentencias, fuerza las guardas diferidas y deshace siempre."""
    with motor.connect() as c:
        tx = c.begin()
        try:
            for s in sentencias:
                c.execute(text(s), params or {})
            c.execute(text("set constraints all immediate"))
        finally:
            tx.rollback()


def _niega(motor, motivo, *sentencias, params=None) -> None:
    with pytest.raises(DBAPIError) as error:
        _probar(motor, *sentencias, params=params)
    assert error.value.orig.diag.sqlstate == "P0001"
    assert error.value.orig.diag.message_hint == motivo


def _paquete(tipo, facultad) -> str:
    return f"insert into nucleo.paquete_facultad (tipo, facultad) values ('{tipo}', '{facultad}')"


def test_ningun_paquete_ni_mandato_tiene_una_facultad_de_aprobar_sin_decidir(
    motor_migrate,
    motor_app,
    escena,  # noqa: F811
):
    """Con el catálogo real y un mandato de cada tipo recién dado de alta."""
    e = escena
    for tipo, persona in (("administrador", e["p1"]), ("miembro_consejo", e["p2"])):
        alta(motor_app, e["cons"], e["per"], e["acta"], tipo=tipo, persona=persona)
    with motor_migrate.connect() as c:
        en_paquetes = c.execute(
            text("select facultad from nucleo.paquete_facultad where facultad = any(:f)"),
            {"f": list(APROBAR_SIN_DECIDIR)},
        ).all()
        otorgadas = c.execute(
            text("select facultad from nucleo.facultad_otorgada where facultad = any(:f)"),
            {"f": list(APROBAR_SIN_DECIDIR)},
        ).all()
        catalogo = c.execute(
            text("select count(*) from nucleo.facultad where codigo = any(:f) and economica"),
            {"f": list(APROBAR_SIN_DECIDIR)},
        ).scalar_one()
        del_mandato = c.execute(
            text("select count(*) from nucleo.facultad_otorgada f join nucleo.mandato m"
                 " on m.id = f.mandato_id where m.consorcio_id = :c"),
            {"c": e["cons"]},
        ).scalar_one()  # fmt: skip
    assert en_paquetes == [] and otorgadas == []
    assert catalogo == 3  # control: las tres existen en el catálogo como económicas
    assert del_mandato > 0  # control: las altas sí otorgaron facultades


def test_el_paquete_no_acumula_preparar_y_aprobar_la_liquidacion(motor_migrate):
    _niega(
        motor_migrate, "paquete_acumula_etapas", _paquete("administrador", "liquidacion.aprobar")
    )
    # Control: el Consejo no prepara, así que la guarda no lo frena (la decisión es otra cosa).
    _probar(motor_migrate, _paquete("miembro_consejo", "liquidacion.aprobar"))


def test_el_paquete_no_acumula_el_ciclo_de_egresos(motor_migrate):
    _niega(
        motor_migrate, "paquete_acumula_etapas", _paquete("administrador", "egresos.aprobar_orden")
    )
    # Controles: dos de las tres etapas no frenan (el administrador ya tiene preparar y ejecutar)
    # y aprobar en un paquete sin las otras tampoco.
    _probar(motor_migrate, _paquete("miembro_consejo", "egresos.aprobar_orden"))
    _probar(
        motor_migrate,
        _paquete("miembro_consejo", "egresos.aprobar_orden"),
        _paquete("miembro_consejo", "egresos.ejecutar_pago"),
    )


def test_cambiar_una_facultad_del_paquete_tambien_pasa_por_la_guarda(motor_migrate):
    _niega(
        motor_migrate,
        "paquete_acumula_etapas",
        "update nucleo.paquete_facultad set facultad = 'liquidacion.aprobar'"
        " where tipo = 'administrador' and facultad = 'coeficientes.proponer'",
    )


def test_la_guarda_mira_el_resultado_al_final_de_la_transaccion(motor_migrate):
    """Diferida: una migración que mueve facultades en dos pasos solo falla si el final viola."""
    _probar(
        motor_migrate,
        _paquete("administrador", "egresos.aprobar_orden"),
        "delete from nucleo.paquete_facultad"
        " where tipo = 'administrador' and facultad = 'egresos.ejecutar_pago'",
    )


def test_un_mandato_no_acumula_el_ciclo_de_egresos(motor_migrate, motor_app, escena):  # noqa: F811
    e = escena
    adm = alta(motor_app, e["cons"], e["per"], e["acta"], persona=e["p1"])
    mc = alta(motor_app, e["cons"], e["per"], e["acta"], tipo="miembro_consejo", persona=e["p2"])
    otorgar = (
        "insert into nucleo.facultad_otorgada (consorcio_id, mandato_id, facultad, decision_id)"
        " values (:c, :m, 'egresos.aprobar_orden', :d)"
    )
    _niega(motor_migrate, "mandato_acumula_etapas", otorgar,
           params={"c": e["cons"], "m": adm, "d": e["acta"]})  # fmt: skip
    _probar(motor_migrate, otorgar, params={"c": e["cons"], "m": mc, "d": e["acta"]})  # control


def test_cem_app_no_toca_el_catalogo_ni_las_guardas(motor_migrate):
    with motor_migrate.connect() as c:
        privilegios = {
            (t, p): c.execute(
                text("select has_table_privilege('cem_app', :t, :p)"), {"t": t, "p": p}
            ).scalar_one()
            for t in ("nucleo.paquete_facultad", "nucleo.facultad_otorgada", "nucleo.facultad")
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
        funciones = [
            c.execute(
                text("select has_function_privilege('cem_app', :f, 'EXECUTE')"), {"f": f}
            ).scalar_one()
            for f in (
                "nucleo.paquete_que_acumula_etapas()",
                "nucleo.exigir_paquete_sin_etapas_acumuladas()",
                "nucleo.exigir_mandato_sin_ciclo_de_egresos()",
            )
        ]
    assert privilegios == {
        (t, p): p == "SELECT"
        for t in ("nucleo.paquete_facultad", "nucleo.facultad_otorgada", "nucleo.facultad")
        for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }
    assert funciones == [False, False, False]


def test_0044_ida_y_vuelta(base_aparte):  # noqa: F811
    cfg, motor = base_aparte

    def triggers() -> list[str]:
        with motor.connect() as c:
            return sorted(
                c.execute(
                    text(
                        "select tgname from pg_trigger where tgname in"
                        " ('paquete_sin_etapas_acumuladas', 'mandato_sin_ciclo_de_egresos')"
                    )
                ).scalars()
            )

    command.upgrade(cfg, "0043")
    assert triggers() == []
    command.upgrade(cfg, "0044")
    assert triggers() == ["mandato_sin_ciclo_de_egresos", "paquete_sin_etapas_acumuladas"]
    command.downgrade(cfg, "0043")
    assert triggers() == []
    _probar(motor, _paquete("administrador", "liquidacion.aprobar"))  # sin la guarda, pasa
    command.upgrade(cfg, "0044")  # control: vuelve a subir limpio
    _niega(motor, "paquete_acumula_etapas", _paquete("administrador", "liquidacion.aprobar"))
