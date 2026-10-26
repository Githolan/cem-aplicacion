"""Identidad humana por consorcio (A13-C, migración 0026) contra PostgreSQL real.

Diseño: decisiones del propietario del 24-10-2026 (repositorio CEM, `doc/diseno/H2b_6_Diseno.md`).
Se prueba la huella (forma canónica, acotada al consorcio, claves versionadas), que solo soporte
verifica, vincula, desvincula y corrige, que dos cuentas con el mismo documento se detectan sin
unirse solas, que `cem_app` no lee la huella, y que el número en claro no queda en ningún lado.
"""

import io
import os
import secrets
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.contexto import Identidad, require_scope
from app.db import transaccion
from app.identidad import humana
from scripts import identidad_humana as script
from tests.test_mandatos import otra_persona
from tests.test_modo_operacion import consorcio_con_persona

CLAVE_1 = secrets.token_urlsafe(32)
CLAVE_2 = secrets.token_urlsafe(32)
DNI = "30.123.456"
REFERENCIA = "Reunión de Consejo del 12-11, acta 45"


# Forma canónica y huella (sin base) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("numero", "esperado"),
    [
        ("30.123.456", "dni:30123456"),
        ("030123456", "dni:30123456"),
        (" 30 123 456 ", "dni:30123456"),
    ],
)
def test_el_dni_tiene_una_sola_forma(numero, esperado):
    assert humana.canonico(humana.TipoDocumento.DNI, numero) == esperado


def test_el_pasaporte_lleva_su_pais_y_va_en_mayusculas():
    assert humana.canonico(humana.TipoDocumento.PASAPORTE, "aa-123 456", "ury") == (
        "pasaporte:URY:AA123456"
    )


@pytest.mark.parametrize(
    ("tipo", "numero", "pais"),
    [
        (humana.TipoDocumento.DNI, "30A23456", None),
        (humana.TipoDocumento.DNI, "0000", None),
        (humana.TipoDocumento.DNI, "1234567890", None),
        (humana.TipoDocumento.DNI, "30123456", "ARG"),
        (humana.TipoDocumento.PASAPORTE, "AA123456", None),
        (humana.TipoDocumento.PASAPORTE, "AA123456", "AR"),
        (humana.TipoDocumento.PASAPORTE, "A1", "ARG"),
    ],
)
def test_un_documento_mal_formado_se_rechaza_sin_repetir_el_numero(tipo, numero, pais):
    with pytest.raises(humana.DocumentoInvalido) as e:
        humana.canonico(tipo, numero, pais)
    assert numero not in str(e.value)


def test_la_huella_esta_acotada_al_consorcio_y_a_la_clave():
    llavero = humana.cargar_llavero(f"1:{CLAVE_1}")
    a, b = uuid4(), uuid4()
    _, en_a = humana.huellas(llavero, a, "dni:30123456")
    _, en_b = humana.huellas(llavero, b, "dni:30123456")
    _, otra_clave = humana.huellas(humana.cargar_llavero(f"1:{CLAVE_2}"), a, "dni:30123456")
    assert len(en_a[0]) == 32
    assert en_a != en_b and en_a != otra_clave
    # Control: el mismo documento, consorcio y clave dan la misma huella.
    assert humana.huellas(llavero, a, "dni:30123456")[1] == en_a


def test_el_llavero_firma_con_la_version_mas_alta_y_conserva_las_anteriores():
    llavero = humana.cargar_llavero(f"1:{CLAVE_1}, 2:{CLAVE_2}")
    versiones, huellas = humana.huellas(llavero, uuid4(), "dni:1")
    assert (llavero.vigente, versiones, len(huellas)) == (2, [2, 1], 2)


@pytest.mark.parametrize(
    "valor", ["", "1", f"0:{CLAVE_1}", f"x:{CLAVE_1}", "1:corta", f"1:{CLAVE_1},1:{CLAVE_2}"]
)
def test_un_llavero_mal_escrito_falla(valor):
    with pytest.raises(RuntimeError):
        humana.cargar_llavero(valor)


def test_la_referencia_con_el_numero_se_detecta():
    assert humana.contiene_documento("DNI 30.123.456 exhibido", "dni:30123456")
    assert not humana.contiene_documento(REFERENCIA, "dni:30123456")


# Funciones de soporte (base real) -----------------------------------------------------------


def huella_al_azar() -> bytes:
    return os.urandom(32)


def verificar_identidad(motor_app, cons: UUID, persona: UUID, huella: bytes | None = None) -> UUID:
    """Escena: soporte verifica la identidad humana de la cuenta en el consorcio (A13-C). Sin
    huella, una al azar: un documento que nadie más tiene."""
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.verificar_identidad_humana(:p, array[1]::smallint[],"
                " array[:h]::bytea[], 'documento_presencial', :r, 'Equipo de prueba')"
            ),
            {"p": persona, "h": huella or huella_al_azar(), "r": REFERENCIA},
        ).scalar_one()


def vincular_identidad(motor_app, cons: UUID, persona: UUID, huella: bytes) -> UUID:
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.vincular_identidad_humana(:p, array[1]::smallint[],"
                " array[:h]::bytea[], 'documento_presencial', :r, 'Equipo de prueba')"
            ),
            {"p": persona, "h": huella, "r": REFERENCIA},
        ).scalar_one()


def desvincular_identidad(motor_app, cons: UUID, persona: UUID) -> UUID:
    with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
        return c.execute(
            text(
                "select nucleo.desvincular_identidad_humana(:p, :r, 'La cuenta no era suya',"
                " 'Equipo de prueba')"
            ),
            {"p": persona, "r": REFERENCIA},
        ).scalar_one()


def _motivo(e: pytest.ExceptionInfo) -> str:
    return e.value.orig.diag.message_hint


@pytest.mark.db
def test_soporte_verifica_y_un_segundo_documento_igual_no_crea_otra_identidad(
    motor_migrate, motor_app
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    otra_cuenta = otra_persona(motor_migrate, cons, verificada=False)
    huella = huella_al_azar()
    identidad = verificar_identidad(motor_app, cons, ana, huella)
    with pytest.raises(DBAPIError) as e:
        verificar_identidad(motor_app, cons, otra_cuenta, huella)
    assert _motivo(e) == "documento_ya_verificado"
    # La coincidencia no une sola: la otra cuenta sigue sin identidad hasta que soporte vincula.
    with motor_migrate.connect() as c:
        sin = c.execute(
            text("select count(*) from nucleo.identidad_persona where persona_id = :p"),
            {"p": otra_cuenta},
        ).scalar_one()
    assert sin == 0
    assert vincular_identidad(motor_app, cons, otra_cuenta, huella) == identidad


@pytest.mark.db
def test_vincular_exige_un_documento_ya_verificado(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    with pytest.raises(DBAPIError) as e:
        vincular_identidad(motor_app, cons, ana, huella_al_azar())
    assert _motivo(e) == "documento_sin_identidad"


@pytest.mark.db
def test_una_cuenta_tiene_a_lo_sumo_una_identidad_activa(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    verificar_identidad(motor_app, cons, ana)
    with pytest.raises(DBAPIError) as e:
        verificar_identidad(motor_app, cons, ana)
    assert _motivo(e) == "cuenta_ya_verificada"
    desvincular_identidad(motor_app, cons, ana)
    with pytest.raises(DBAPIError) as e:
        desvincular_identidad(motor_app, cons, ana)
    assert _motivo(e) == "cuenta_sin_identidad"
    # Control: desvinculada, se puede volver a verificar.
    verificar_identidad(motor_app, cons, ana)


@pytest.mark.db
def test_la_misma_persona_en_otro_consorcio_no_se_correlaciona(motor_migrate, motor_app):
    """La huella del script depende del consorcio; además, la base solo busca en el propio."""
    cons_a, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    cons_b, _, _ = consorcio_con_persona(motor_migrate, verificada=False)
    huella = huella_al_azar()
    verificar_identidad(motor_app, cons_a, ana, huella)
    otra = otra_persona(motor_migrate, cons_b, verificada=False)
    verificar_identidad(motor_app, cons_b, otra, huella)  # no la ve: es otro consorcio


@pytest.mark.db
def test_solo_soporte_sin_persona_en_la_sesion(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    with pytest.raises(DBAPIError) as e:
        with transaccion(motor_app, persona_id=ana, consorcio_id=cons) as c:
            c.execute(
                text(
                    "select nucleo.verificar_identidad_humana(:p, array[1]::smallint[],"
                    " array[:h]::bytea[], 'documento_presencial', 'x', 'y')"
                ),
                {"p": ana, "h": huella_al_azar()},
            )
    assert _motivo(e) == "origen_invalido"


@pytest.mark.db
def test_cem_app_no_lee_la_huella_ni_las_verificaciones(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    verificar_identidad(motor_app, cons, ana)
    for tabla in ("nucleo.identidad_humana", "nucleo.verificacion_identidad"):
        with pytest.raises(ProgrammingError, match="permission denied"):
            with transaccion(motor_app, persona_id=ana, consorcio_id=cons) as c:
                c.execute(text(f"select * from {tabla}"))
    # Control: el vínculo cuenta → identidad sí se lee, por consorcio.
    with transaccion(motor_app, persona_id=ana, consorcio_id=cons) as c:
        assert c.execute(text("select count(*) from nucleo.identidad_persona")).scalar_one() == 1


@pytest.mark.db
def test_la_identidad_humana_no_escribe_eventos_de_auditoria(motor_migrate, motor_app):
    """S2a (0033): la identidad humana es solo de soporte, y soporte no escribe eventos de
    auditoría funcional. Su registro propio es `verificacion_identidad`."""
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    huella = huella_al_azar()
    identidad = verificar_identidad(motor_app, cons, ana, huella)
    with motor_migrate.connect() as c:
        eventos = c.execute(
            text("select e::text from auditoria.eventos e where consorcio_id = :c"
                 " and (accion like 'identidad.humana.%' or entidad = 'identidad_humana')"),
            {"c": cons},
        ).scalars().all()  # fmt: skip
        constancia = c.execute(
            text("select accion from nucleo.verificacion_identidad where identidad_id = :i"),
            {"i": identidad},
        ).scalars().all()  # fmt: skip
        funcion = c.execute(
            text("select to_regprocedure("
                 "'nucleo.auditar_identidad(uuid, text, uuid, uuid, text)') is null")
        ).scalar_one()  # fmt: skip
    assert eventos == []
    # Control: el acto sí quedó, en su registro propio.
    assert constancia == ["verificar"]
    assert funcion is True


@pytest.mark.db
def test_corregir_reemplaza_la_huella_pero_no_toma_la_de_otra_identidad(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    beto = otra_persona(motor_migrate, cons, verificada=False)
    de_beto = huella_al_azar()
    verificar_identidad(motor_app, cons, ana)
    verificar_identidad(motor_app, cons, beto, de_beto)

    def corregir(con_la_2: bytes, con_la_1: bytes) -> UUID:
        """Como el script con el llavero `2:…,1:…`: la huella del documento con cada clave."""
        with transaccion(motor_app, persona_id=None, consorcio_id=cons) as c:
            return c.execute(
                text(
                    "select nucleo.corregir_identidad_humana(:p, array[2, 1]::smallint[],"
                    " array[:h2, :h1]::bytea[], 'documento_presencial', :r,"
                    " 'Número mal cargado', 'Equipo de prueba')"
                ),
                {"p": ana, "h2": con_la_2, "h1": con_la_1, "r": REFERENCIA},
            ).scalar_one()

    # El documento de Beto, que la clave 1 todavía reconoce, no pasa a Ana.
    with pytest.raises(DBAPIError) as e:
        corregir(huella_al_azar(), de_beto)
    assert _motivo(e) == "documento_ya_verificado"
    # Control: un documento de nadie la corrige, firmado con la clave vigente.
    nueva = huella_al_azar()
    corregir(nueva, huella_al_azar())
    with motor_migrate.connect() as c:
        version = c.execute(
            text("select clave_version from nucleo.identidad_humana where huella = :h"),
            {"h": nueva},
        ).scalar_one()
    assert version == 2


# El script de soporte -----------------------------------------------------------------------


def correr_script(monkeypatch, capsys, argumentos: list[str], numero: str, claves: str):
    """Corre el script como soporte: el número por la entrada estándar, nunca por argumento."""
    monkeypatch.setenv("CEM_IDENTIDAD_CLAVES", claves)
    monkeypatch.setattr("sys.stdin", io.StringIO(numero + "\n"))
    codigo = script.main(argumentos)
    salida = capsys.readouterr()
    return codigo, salida.out + salida.err


def _argumentos(accion, cons, persona, referencia=REFERENCIA, motivo=None) -> list[str]:
    lista = [accion, "--consorcio", str(cons), "--persona", str(persona), "--operador",
             "Equipo de prueba", "--referencia", referencia, "--tipo", "dni", "--metodo",
             "documento_presencial"]  # fmt: skip
    return lista + (["--motivo", motivo] if motivo else [])


@pytest.fixture
def url_app(monkeypatch, motor_app):
    """El script corre con la conexión de `cem_app`, como en el contenedor de `cem-backend`."""
    monkeypatch.setenv("CEM_DATABASE_URL", motor_app.url.render_as_string(hide_password=False))


@pytest.mark.db
def test_el_script_verifica_y_vincula_sin_dejar_el_numero(
    motor_migrate, motor_app, monkeypatch, capsys, url_app
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    otra = otra_persona(motor_migrate, cons, verificada=False)
    claves = f"1:{CLAVE_1}"
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, ana), DNI, claves
    )
    assert codigo == 0, salida
    # La otra cuenta con el mismo documento (escrito distinto) no se verifica: se vincula.
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, otra), "030123456", claves
    )
    assert (codigo, "documento_ya_verificado" in salida) == (1, True)
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("vincular", cons, otra), DNI, claves
    )
    assert codigo == 0, salida
    assert "30123456" not in salida
    with motor_migrate.connect() as c:
        filas = c.execute(
            text(
                "select v.referencia, v.motivo, v.operador, h.huella"
                " from nucleo.verificacion_identidad v"
                " join nucleo.identidad_humana h on h.id = v.identidad_id"
                " where v.consorcio_id = :c"
            ),
            {"c": cons},
        ).all()
        eventos = c.execute(
            text("select despues::text from auditoria.eventos where consorcio_id = :c"),
            {"c": cons},
        ).scalars()
        textos = [str(f) for f in filas] + list(eventos)
    assert len(filas) == 2
    assert not any("30123456" in t or "30.123.456" in t for t in textos)
    llavero = humana.cargar_llavero(claves)
    assert filas[0].huella == humana.huellas(llavero, cons, "dni:30123456")[1][0]


@pytest.mark.db
def test_el_script_reconoce_lo_verificado_con_una_clave_anterior(
    motor_migrate, motor_app, monkeypatch, capsys, url_app
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    otra = otra_persona(motor_migrate, cons, verificada=False)
    assert (
        correr_script(
            monkeypatch, capsys, _argumentos("verificar", cons, ana), DNI, f"1:{CLAVE_1}"
        )[0]
        == 0
    )
    # Con la clave 2 vigente, el documento verificado con la 1 se sigue reconociendo.
    rotado = f"2:{CLAVE_2},1:{CLAVE_1}"
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, otra), DNI, rotado
    )
    assert (codigo, "documento_ya_verificado" in salida) == (1, True)
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("corregir", cons, ana, motivo="Clave nueva"), DNI, rotado
    )
    assert codigo == 0, salida
    # Control: sin la clave 1, la huella ya está con la 2 y lo sigue reconociendo.
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, otra), DNI, f"2:{CLAVE_2}"
    )
    assert (codigo, "documento_ya_verificado" in salida) == (1, True)


@pytest.mark.db
def test_el_script_rechaza_la_referencia_con_el_numero(
    motor_migrate, motor_app, monkeypatch, capsys, url_app
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    codigo, salida = correr_script(
        monkeypatch, capsys,
        _argumentos("verificar", cons, ana, referencia="DNI 30.123.456 exhibido"), DNI,
        f"1:{CLAVE_1}",
    )  # fmt: skip
    assert (codigo, "30123456" in salida) == (2, False)
    with motor_migrate.connect() as c:
        assert (
            c.execute(
                text("select count(*) from nucleo.identidad_persona where persona_id = :p"),
                {"p": ana},
            ).scalar_one()
            == 0
        )


@pytest.mark.db
def test_el_script_sin_llavero_no_hace_nada(motor_migrate, motor_app, monkeypatch, capsys, url_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    argumentos = _argumentos("verificar", cons, ana)
    codigo, salida = correr_script(monkeypatch, capsys, argumentos, DNI, "")
    assert (codigo, "CEM_IDENTIDAD_CLAVES" in salida) == (2, True)


@pytest.mark.db
def test_retirar_una_clave_con_identidades_se_niega(
    motor_migrate, motor_app, monkeypatch, capsys, url_app
):
    """Sin la clave 1, un documento verificado con ella no se reconoce: el mismo ser humano
    quedaría con dos identidades. La base se niega hasta que `corregir` las pase a la 2."""
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    otra = otra_persona(motor_migrate, cons, verificada=False)
    assert correr_script(monkeypatch, capsys, _argumentos("verificar", cons, ana), DNI,
                         f"1:{CLAVE_1}")[0] == 0  # fmt: skip
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, otra), "40.111.222", f"2:{CLAVE_2}"
    )
    assert (codigo, "clave_retirada_con_identidades" in salida) == (1, True)
    # Control: con las dos claves, el documento nuevo se verifica.
    codigo, salida = correr_script(
        monkeypatch, capsys, _argumentos("verificar", cons, otra), "40.111.222",
        f"2:{CLAVE_2},1:{CLAVE_1}",
    )  # fmt: skip
    assert codigo == 0, salida


@pytest.mark.db
def test_el_script_rechaza_el_operador_con_el_numero(
    motor_migrate, motor_app, monkeypatch, capsys, url_app
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    argumentos = _argumentos("verificar", cons, ana)
    argumentos[argumentos.index("--operador") + 1] = "Juan 30123456"
    codigo, salida = correr_script(monkeypatch, capsys, argumentos, DNI, f"1:{CLAVE_1}")
    assert (codigo, "operador" in salida, "30123456" in salida) == (2, True, False)


# R10 en el contexto de la petición ----------------------------------------------------------


def _sesion(motor_migrate, persona: UUID, creada: str, segundo_factor: str | None) -> UUID:
    """Una sesión creada hace `creada` y con el segundo factor verificado hace `segundo_factor`
    (o nunca)."""
    with motor_migrate.begin() as c:
        return c.execute(
            text(
                "insert into nucleo.sesion (persona_id, token_hash, dispositivo, ip, creada_en,"
                " vence_en, segundo_factor_en) values (:p, :t, gen_random_uuid(), '198.51.100.9',"
                " now() - cast(:c as interval), now() + interval '1 day',"
                " now() - cast(:s as interval)) returning id"
            ),
            {"p": persona, "t": secrets.token_bytes(32), "c": creada, "s": segundo_factor},
        ).scalar_one()


@pytest.mark.db
@pytest.mark.parametrize(
    ("creada", "segundo_factor", "esperado"),
    [
        ("1 hour", "5 minutes", (True, True)),
        ("1 hour", "11 minutes", (False, True)),
        ("1 hour", None, (False, True)),
        ("12 hours 1 minute", "5 minutes", (True, False)),
        ("11 hours 59 minutes", "5 minutes", (True, True)),
    ],
)
def test_r10_el_contexto_mide_el_segundo_factor_y_la_sesion(
    motor_migrate, motor_app, creada, segundo_factor, esperado
):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    identidad = verificar_identidad(motor_app, cons, ana)
    sesion = _sesion(motor_migrate, ana, creada, segundo_factor)
    ctx = require_scope(cons, Identidad(ana, sesion), motor_app)
    assert (ctx.segundo_factor_reciente, ctx.sesion_reciente) == esperado
    assert ctx.autorizacion.identidades == {ana: identidad}


@pytest.mark.db
def test_r10_sin_identidad_verificada_el_contexto_no_la_inventa(motor_migrate, motor_app):
    cons, _, ana = consorcio_con_persona(motor_migrate, verificada=False)
    ctx = require_scope(cons, Identidad(ana, _sesion(motor_migrate, ana, "1 hour", "1 minute")),
                        motor_app)  # fmt: skip
    assert (ctx.identidad_humana, dict(ctx.autorizacion.identidades)) == (None, {})
    # La identidad de la misma cuenta en otro consorcio no cuenta en este (acotada al consorcio).
    otro, _, _ = consorcio_con_persona(motor_migrate, verificada=False)
    verificar_identidad(motor_app, otro, ana)
    assert require_scope(cons, Identidad(ana), motor_app).identidad_humana is None
    # Control: verificada en este, aparece.
    identidad = verificar_identidad(motor_app, cons, ana)
    assert require_scope(cons, Identidad(ana), motor_app).identidad_humana == identidad
