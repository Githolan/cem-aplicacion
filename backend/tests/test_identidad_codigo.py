"""Código de seis dígitos por correo contra PostgreSQL 18 y Redis 7 reales (documento 13,
§10.3, §10.6 y §16.1; plan H2 2a).

Para la persona que pide la entrada en un dispositivo y lee el correo en otro: el enlace está
ligado al navegador que lo pidió y ahí no sirve. El código llega en el mismo correo que el
enlace; se saca del evento que quedó en el outbox, como lo leería el trabajo de correo. Cada
navegador es un cliente con sus cookies y su IP (`test_identidad.Navegadores`).
"""

import hashlib
import hmac
import json
import re
import secrets
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db import transaccion
from app.identidad import eventos, router, sesiones
from app.limite import LIMITES_CODIGO_CORREO
from app.plataforma import cola, tareas
from app.registros import redactar
from tests.conftest import alembic_config, vaciar_mandatos
from tests.test_identidad import (  # noqa: F401  (`app` es un fixture)
    ANA,
    CANJE,
    CLAVE_CODIGO,
    ENLACE,
    NADIE,
    SESION,
    Navegadores,
    app,
    app_identidad,
    es_401,
    eventos_de,
    h,
    navegador,
    pedir,
    set_cookies,
    ultimo_token,
)
from tests.test_identidad_tabla import nueva_persona
from tests.test_registros import salida_json  # noqa: F401  (fixture)

pytestmark = [pytest.mark.db, pytest.mark.redis]

CODIGO = "/api/v1/auth/codigo"
BETO = "beto@ejemplo.com"
FUNCIONES = (
    "nucleo.registrar_codigo(bytea, bytea, interval)",
    "nucleo.canjear_codigo(text, bytea, bytea, uuid, text, inet, interval, integer)",
)


def ultimo_codigo(motor_migrate, direccion: str) -> str:
    """El código del último correo, como lo ve la persona (`042 917`), sin el espacio."""
    texto = eventos_de(motor_migrate, direccion)[-1]["texto"]
    [(a, b)] = re.findall(r"este código:\n\n(\d{3}) (\d{3})\n", texto)
    return a + b


def otro_codigo(codigo: str, salto: int = 1) -> str:
    """Un código de seis dígitos distinto del dado."""
    return f"{(int(codigo) + salto) % 10**sesiones.DIGITOS_CODIGO:06d}"


def canjear(c, direccion: str, codigo: str, **kw):
    return c.post(CODIGO, json={"correo": direccion, "codigo": codigo}, **kw)


def es_codigo_invalido(r) -> bool:
    return es_401(r, "codigo_invalido")


def fila_codigo(motor_migrate, direccion: str):
    """El último código de la persona, con su enlace."""
    with motor_migrate.connect() as conn:
        return conn.execute(
            text(
                "select c.*, e.usado_en, e.token_hash as enlace_token from nucleo.codigo_acceso c"
                " join nucleo.enlace_acceso e on e.id = c.enlace_id"
                " join nucleo.persona p on p.id = c.persona_id"
                " where p.correo = :c order by c.creado_en desc, c.id desc limit 1"
            ),
            {"c": direccion},
        ).one()


def sesiones_de(motor_migrate, direccion: str) -> int:
    with motor_migrate.connect() as conn:
        return conn.execute(
            text(
                "select count(*) from nucleo.sesion s join nucleo.persona p on p.id = s.persona_id"
                " where p.correo = :c"
            ),
            {"c": direccion},
        ).scalar_one()


# Pedido: el mismo correo lleva enlace y código --------------------------------------------


def test_el_correo_lleva_el_enlace_y_el_codigo_y_la_base_solo_su_hmac(app, motor_migrate):  # noqa: F811
    assert pedir(navegador(app), ANA).status_code == 202
    carga = eventos_de(motor_migrate, ANA)[-1]
    assert set(carga) == {"para", "asunto", "texto"}
    codigo = ultimo_codigo(motor_migrate, ANA)
    token = ultimo_token(motor_migrate, ANA)
    fila = fila_codigo(motor_migrate, ANA)
    # El código es del mismo pedido que el enlace.
    assert fila.enlace_token == h(token)
    assert fila.intentos == 0 and fila.usado_en is None
    assert fila.vence_en - fila.creado_en == sesiones.VIGENCIA_CODIGO
    clave = sesiones.clave_codigo(CLAVE_CODIGO)
    assert bytes(fila.codigo_hash) == sesiones.hash_codigo(clave, ANA, codigo)
    with motor_migrate.connect() as conn:
        todo = conn.execute(
            text("select c::text from nucleo.codigo_acceso c where id = :i"), {"i": fila.id}
        ).scalar_one()
    assert codigo not in todo


def test_un_correo_sin_cuenta_no_registra_codigo(app, motor_migrate):  # noqa: F811
    with motor_migrate.connect() as conn:
        antes = conn.execute(text("select count(*) from nucleo.codigo_acceso")).scalar_one()
    assert pedir(navegador(app), NADIE).status_code == 202
    with motor_migrate.connect() as conn:
        assert conn.execute(text("select count(*) from nucleo.codigo_acceso")).scalar_one() == antes
    assert eventos_de(motor_migrate, NADIE) == []


# Canje ------------------------------------------------------------------------------------


def test_el_codigo_correcto_crea_la_sesion_en_otro_dispositivo(app, motor_migrate, datos):  # noqa: F811
    pide, otro = navegador(app), navegador(app)
    pedir(pide, ANA)
    codigo, token = ultimo_codigo(motor_migrate, ANA), ultimo_token(motor_migrate, ANA)
    r = canjear(otro, ANA.upper(), codigo)  # el correo escrito distinto también sirve
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert (r.json()["persona_id"], r.json()["correo"]) == (str(datos.persona_a), ANA)
    cookies = set_cookies(r)
    assert {sesiones.COOKIE_SESION, sesiones.COOKIE_CSRF, sesiones.COOKIE_DISPOSITIVO} <= set(
        cookies
    )
    for nombre in (sesiones.COOKIE_SESION, sesiones.COOKIE_DISPOSITIVO):
        partes = {p.strip().lower() for p in cookies[nombre].split(";")}
        assert {"httponly", "secure", "samesite=lax", "path=/"} <= partes
    assert otro.get(SESION).json()["sesion_id"] == r.json()["sesion_id"]
    assert [x["id"] for x in otro.get("/api/v1/me/consorcios").json()] == [str(datos.consorcio_a)]
    # La sesión es del dispositivo que canjeó, no del que pidió.
    with motor_migrate.connect() as conn:
        s = conn.execute(
            text("select dispositivo from nucleo.sesion where token_hash = :t"),
            {"t": h(otro.cookies[sesiones.COOKIE_SESION])},
        ).one()
    assert str(s.dispositivo) == otro.cookies[sesiones.COOKIE_DISPOSITIVO]
    assert str(s.dispositivo) != pide.cookies[sesiones.COOKIE_DISPOSITIVO]
    # Usar el código gasta el pedido: el enlace ya no sirve ni en el navegador que lo pidió.
    assert es_401(pide.post(CANJE, json={"token": token}), "enlace_invalido")


def test_usar_el_enlace_gasta_el_codigo(app, motor_migrate):  # noqa: F811
    pide = navegador(app)
    pedir(pide, ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    assert pide.post(CANJE, json={"token": ultimo_token(motor_migrate, ANA)}).status_code == 200
    antes = sesiones_de(motor_migrate, ANA)
    assert es_codigo_invalido(canjear(navegador(app), ANA, codigo))
    assert sesiones_de(motor_migrate, ANA) == antes


def test_un_codigo_usado_no_se_reusa(app, motor_migrate):  # noqa: F811
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    assert canjear(navegador(app), ANA, codigo).status_code == 200
    antes = sesiones_de(motor_migrate, ANA)
    assert es_codigo_invalido(canjear(navegador(app), ANA, codigo))
    assert sesiones_de(motor_migrate, ANA) == antes


def test_el_incorrecto_descuenta_y_al_quinto_queda_agotado(app, motor_migrate):  # noqa: F811
    pide = navegador(app)
    pedir(pide, ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    antes = sesiones_de(motor_migrate, ANA)
    for intento in range(1, sesiones.INTENTOS_CODIGO + 1):
        assert es_codigo_invalido(canjear(navegador(app), ANA, otro_codigo(codigo, intento)))
        assert fila_codigo(motor_migrate, ANA).intentos == intento
    # El correcto, después del quinto fallido, ya no entra y no descuenta más.
    assert es_codigo_invalido(canjear(navegador(app), ANA, codigo))
    assert fila_codigo(motor_migrate, ANA).intentos == sesiones.INTENTOS_CODIGO
    assert sesiones_de(motor_migrate, ANA) == antes
    # El enlace del mismo pedido sigue sirviendo en el navegador que lo pidió: quien agota el
    # código desde otro lado no lo tiene.
    assert pide.post(CANJE, json={"token": ultimo_token(motor_migrate, ANA)}).status_code == 200


def test_cuatro_fallidos_no_agotan_el_codigo(app, motor_migrate):  # noqa: F811
    """Control del anterior: el límite es el quinto, no antes."""
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    for intento in range(1, sesiones.INTENTOS_CODIGO):
        assert es_codigo_invalido(canjear(navegador(app), ANA, otro_codigo(codigo, intento)))
    assert canjear(navegador(app), ANA, codigo).status_code == 200


def test_un_codigo_vencido_se_rechaza_y_no_descuenta(app, motor_migrate):  # noqa: F811
    # Una persona nueva: envejecer su código no deja «último» a uno de otra prueba.
    _, correo = nueva_persona(motor_migrate)
    pedir(navegador(app), correo)
    codigo = ultimo_codigo(motor_migrate, correo)
    fila = fila_codigo(motor_migrate, correo)
    corrimiento = sesiones.VIGENCIA_CODIGO + timedelta(seconds=1)

    def envejecer(cuanto: timedelta) -> None:
        with motor_migrate.begin() as conn:
            conn.execute(
                text(
                    "update nucleo.codigo_acceso set creado_en = creado_en - :d,"
                    " vence_en = vence_en - :d where id = :i"
                ),
                {"d": cuanto, "i": fila.id},
            )

    envejecer(corrimiento)
    assert es_codigo_invalido(canjear(navegador(app), correo, codigo))
    assert fila_codigo(motor_migrate, correo).intentos == 0
    envejecer(-corrimiento)  # control: el mismo código, vigente, entra
    assert canjear(navegador(app), correo, codigo).status_code == 200


def test_el_codigo_de_otra_persona_no_sirve(app, motor_migrate):  # noqa: F811
    pedir(navegador(app), ANA)
    de_ana = ultimo_codigo(motor_migrate, ANA)
    pedir(navegador(app), BETO)
    de_beto = ultimo_codigo(motor_migrate, BETO)
    while de_beto == de_ana:  # una coincidencia (1 en 10⁶) no prueba nada: se pide otro
        pedir(navegador(app), BETO)
        de_beto = ultimo_codigo(motor_migrate, BETO)
    assert es_codigo_invalido(canjear(navegador(app), BETO, de_ana))
    assert es_codigo_invalido(canjear(navegador(app), ANA, de_beto))
    # Control: cada uno con el suyo entra (el fallido de arriba descontó uno, no los cinco).
    assert canjear(navegador(app), ANA, de_ana).json()["correo"] == ANA
    assert canjear(navegador(app), BETO, de_beto).json()["correo"] == BETO


def test_solo_vale_el_codigo_del_ultimo_pedido(app, motor_migrate):  # noqa: F811
    """Con varios vigentes, cada intento tendría varias chances de acertar."""
    pedir(navegador(app), ANA)
    primero = ultimo_codigo(motor_migrate, ANA)
    pedir(navegador(app), ANA)
    segundo = ultimo_codigo(motor_migrate, ANA)
    if primero != segundo:  # si coinciden (1 en 10⁶), el primero acertaría como el segundo
        assert es_codigo_invalido(canjear(navegador(app), ANA, primero))
    assert canjear(navegador(app), ANA, segundo).status_code == 200


def test_un_codigo_inventado_o_mal_formado(app, motor_migrate):  # noqa: F811
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    assert es_codigo_invalido(canjear(navegador(app), ANA, otro_codigo(codigo)))
    for malo in ("12345", "1234567", "12 345", "abcdef", ""):
        r = canjear(navegador(app), ANA, malo)
        assert r.status_code == 422
        assert r.json()["detalle"] == {"campos": ["body.codigo"]}
    # El 422 no descuenta: no llega a la base.
    assert fila_codigo(motor_migrate, ANA).intentos == 1


def test_el_canje_no_acepta_el_codigo_en_la_url(app, motor_migrate):  # noqa: F811
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    c = navegador(app)
    assert c.get(CODIGO, params={"correo": ANA, "codigo": codigo}).status_code == 405
    assert c.post(CODIGO, params={"correo": ANA, "codigo": codigo}).status_code == 422
    assert canjear(c, ANA, codigo).status_code == 200  # control: no se gastó


def test_desde_otro_sitio_o_sin_json_se_rechaza_sin_descontar(app, motor_migrate):  # noqa: F811
    """Lo que reemplaza al nonce contra una falsificación de inicio de sesión."""
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    cuerpo = json.dumps({"correo": ANA, "codigo": codigo})
    c = navegador(app)
    for sitio in ("cross-site", "same-site", "none"):
        r = canjear(c, ANA, codigo, headers={"Sec-Fetch-Site": sitio})
        assert (r.status_code, r.json()["codigo"]) == (403, "csrf_invalido")
    # Lo que un formulario o un `fetch` `no-cors` de otro sitio puede mandar (un `Blob` sin tipo
    # no lleva el encabezado). FastAPI 0.141 interpreta como JSON solo `application/json`
    # (`strict_content_type`); si eso cambiara, un cuerpo sin tipo llegaría a la ruta.
    for tipo in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None):
        encabezados = {"content-type": tipo} if tipo else {}
        r = c.post(CODIGO, content=cuerpo.encode(), headers=encabezados)
        assert (r.status_code, r.json()["codigo"]) == (422, "peticion_invalida"), tipo
    assert fila_codigo(motor_migrate, ANA).intentos == 0
    r = canjear(c, ANA, codigo, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200  # control


# Misma respuesta exista o no la cuenta ----------------------------------------------------


REPETICIONES_TIEMPO = 3


def test_respuesta_identica_y_mismo_tiempo_con_o_sin_cuenta(
    app,  # noqa: F811
    motor_migrate,
    salida_json,  # noqa: F811
):
    """Cuatro causas que no se distinguen hacia afuera: sin cuenta, cuenta sin código pedido,
    código incorrecto y código agotado. Mismo estado, mismo cuerpo (salvo el id de
    correlación), mismos encabezados y ninguna antes de la duración mínima."""
    with motor_migrate.begin() as conn:
        conn.execute(
            text("insert into nucleo.persona (correo, nombre) values ('flor@ejemplo.com', 'Flor')")
        )
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    with motor_migrate.begin() as conn:
        conn.execute(
            text("update nucleo.codigo_acceso set intentos = :m where id = :i"),
            {"m": sesiones.INTENTOS_CODIGO, "i": fila_codigo(motor_migrate, ANA).id},
        )
    pedir(navegador(app), BETO)
    de_beto = ultimo_codigo(motor_migrate, BETO)
    casos = {
        "sin_cuenta": (NADIE, codigo),
        "sin_codigo": ("flor@ejemplo.com", codigo),
        "incorrecto": (BETO, otro_codigo(de_beto)),
        "agotado": (ANA, codigo),
    }
    respuestas, duraciones = [], {n: [] for n in casos}
    for _ in range(REPETICIONES_TIEMPO):
        for nombre, (direccion, cod) in casos.items():
            inicio = time.monotonic()
            respuestas.append(canjear(navegador(app), direccion, cod))
            duraciones[nombre].append(time.monotonic() - inicio)
    assert {r.status_code for r in respuestas} == {401}
    cuerpos = {json.dumps({**r.json(), "id_correlacion": None}) for r in respuestas}
    assert len(cuerpos) == 1 and "codigo_invalido" in cuerpos.pop()
    assert len({tuple(sorted(r.headers.keys())) for r in respuestas}) == 1
    assert {json.dumps(set_cookies(r)) for r in respuestas} == {"{}"}
    minimo = router.DURACION_MINIMA_PEDIDO.total_seconds()
    assert min(min(d) for d in duraciones.values()) >= minimo, duraciones
    lineas = salida_json()
    motivos = [x["mensaje"] for x in lineas if x["mensaje"].startswith("canje de código rechazado")]
    # Control: el servidor sí distinguió las cuatro causas (solo en los registros).
    assert {m.rsplit(": ", 1)[1] for m in motivos} == set(casos)
    assert not [x for x in lineas if "superó la duración mínima" in x["mensaje"]]
    medianas = [statistics.median(t) for t in duraciones.values()]
    assert max(medianas) - min(medianas) < minimo / 2, duraciones
    # El código incorrecto de Beto descontó, pero no lo agotó (tres de cinco).
    assert fila_codigo(motor_migrate, BETO).intentos == REPETICIONES_TIEMPO


# Límite de tasa (§10.6) -------------------------------------------------------------------


def test_limite_de_intentos_por_correo_aunque_cambie_la_ip(app, motor_migrate):  # noqa: F811
    por_hora, por_dia = LIMITES_CODIGO_CORREO
    assert (por_hora.amount, por_dia.amount) == (10, 20)
    assert por_hora.get_expiry() == 3600 and por_dia.get_expiry() == 86400
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    # Cada intento desde otra IP: el cupo es del correo. Los primeros cinco agotan el código y
    # los demás siguen gastando el cupo.
    for intento in range(1, por_hora.amount + 1):
        assert es_codigo_invalido(canjear(navegador(app), ANA, otro_codigo(codigo, intento)))
    r = canjear(navegador(app), ANA.upper(), codigo)  # el mismo correo, escrito distinto
    assert r.status_code == 429
    assert r.json()["codigo"] == "demasiadas_peticiones"
    assert 1 <= int(r.headers["Retry-After"]) <= 3600
    # Un correo sin cuenta tiene su propio cupo y se comporta igual: no delata nada.
    for _ in range(por_hora.amount):
        assert es_codigo_invalido(canjear(navegador(app), NADIE, codigo))
    assert canjear(navegador(app), NADIE, codigo).status_code == 429
    # Control: otro correo no está limitado.
    pedir(navegador(app), BETO)
    assert canjear(navegador(app), BETO, ultimo_codigo(motor_migrate, BETO)).status_code == 200


def test_el_cupo_por_ip_de_auth_tambien_cubre_el_codigo(app):  # noqa: F811
    c = navegador(app, ip="203.0.113.90")
    for i in range(20):
        assert canjear(c, f"p{i}@ejemplo.com", "000000").status_code == 401
    assert canjear(c, "otra@ejemplo.com", "000000").status_code == 429


# El hash no se invierte sin la clave ------------------------------------------------------


def test_el_hash_no_se_invierte_por_fuerza_bruta_sin_la_clave(app, motor_migrate):  # noqa: F811
    """Probar los 10⁶ códigos: con SHA-256 sin clave (del código solo y del mismo mensaje que el
    HMAC) y con HMAC de otra clave no aparece ninguno; con la clave de la API, el código del
    correo y solo ese. El último es el control: la fuerza bruta funciona cuando hay con qué."""
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    guardado = bytes(fila_codigo(motor_migrate, ANA).codigo_hash)
    clave = sesiones.clave_codigo(CLAVE_CODIGO)
    otra = secrets.token_bytes(32)
    todos = [f"{n:06d}" for n in range(10**sesiones.DIGITOS_CODIGO)]
    prefijo = b"cem-codigo-acceso\0" + ANA.encode() + b"\0"
    assert not [x for x in todos if hashlib.sha256(x.encode()).digest() == guardado]
    assert not [x for x in todos if hashlib.sha256(prefijo + x.encode()).digest() == guardado]
    con_otra = hmac.new(otra, digestmod=hashlib.sha256)
    assert not [x for x in todos if _hmac_desde(con_otra, prefijo, x) == guardado]
    con_la_clave = hmac.new(clave, digestmod=hashlib.sha256)
    assert [x for x in todos if _hmac_desde(con_la_clave, prefijo, x) == guardado] == [codigo]


def _hmac_desde(base: "hmac.HMAC", prefijo: bytes, codigo: str) -> bytes:
    h_ = base.copy()
    h_.update(prefijo + codigo.encode())
    return h_.digest()


# Registros (§16.1) ------------------------------------------------------------------------


def test_los_registros_nunca_llevan_el_codigo(app, motor_migrate, salida_json):  # noqa: F811
    pedir(navegador(app), ANA)
    codigo = ultimo_codigo(motor_migrate, ANA)
    incorrecto = otro_codigo(codigo)
    canjear(navegador(app), ANA, incorrecto)
    c = navegador(app)
    assert canjear(c, ANA, codigo).status_code == 200
    lineas = salida_json()
    # Control: se registraron el pedido, el rechazo y la entrada.
    mensajes = {x["mensaje"] for x in lineas}
    assert {"enlace de acceso pedido", "canje de código rechazado: incorrecto"} <= mensajes
    assert "sesión creada con código" in mensajes
    volcado = json.dumps(lineas, ensure_ascii=False)
    for secreto in (codigo, f"{codigo[:3]} {codigo[3:]}", incorrecto):
        assert secreto not in volcado
    assert ANA not in volcado


@pytest.mark.parametrize(
    "texto",
    [
        'cuerpo {"correo": "x", "codigo": "042917"}',
        "codigo=042917",
        "este código:\n\n042 917\n",
        "Código: 042917",
    ],
)
def test_la_redaccion_cubre_el_codigo(texto):
    assert "042917" not in redactar(texto)
    assert "042 917" not in redactar(texto)


def test_la_redaccion_no_toca_el_codigo_de_un_error():
    texto = '{"codigo": "codigo_invalido", "intentos": 123456}'
    assert redactar(texto) == texto


def test_el_texto_del_correo_agrupa_el_codigo():
    assert eventos.agrupar("042917") == "042 917"


# Configuración ----------------------------------------------------------------------------


def test_sin_clave_de_codigo_pedir_y_canjear_responden_503(motor_app, datos, redis_limpio,
                                                          motor_migrate):  # fmt: skip
    sin_clave = Navegadores(motor_app, clave_codigo="")
    c = sin_clave.abrir()
    antes = len(eventos_de(motor_migrate, ANA))
    r = pedir(c, ANA)
    assert (r.status_code, r.json()["codigo"]) == (503, "servicio_no_disponible")
    assert len(eventos_de(motor_migrate, ANA)) == antes  # no salió un correo sin código
    r = canjear(c, ANA, "123456")
    assert (r.status_code, r.json()["codigo"]) == (503, "servicio_no_disponible")
    sin_clave.cerrar()


@pytest.mark.parametrize("clave", ["corta", "x" * 42, "+" * 43, secrets.token_hex(32)])
def test_una_clave_mal_escrita_impide_el_arranque(clave):
    with pytest.raises(RuntimeError, match="CEM_CODIGO_CLAVE"):
        app_identidad(clave_codigo=clave)


# Tabla, funciones y privilegios (§8.5, §8.6) ----------------------------------------------


def test_cem_app_no_toca_los_codigos(motor_app, datos):
    for sql in (
        "select count(*) from nucleo.codigo_acceso",
        "update nucleo.codigo_acceso set intentos = 0",
        "delete from nucleo.codigo_acceso",
    ):
        with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(sql))


def test_las_funciones_son_security_definer_con_search_path_fijo(motor_app, datos):
    with motor_app.connect() as conn:
        for firma in FUNCIONES:
            definer, config, publico = conn.execute(
                text(
                    "select prosecdef, proconfig, has_function_privilege('public', oid, 'execute')"
                    " from pg_proc where oid = cast(:f as regprocedure)"
                ),
                {"f": firma},
            ).one()
            assert definer and config == ["search_path=pg_catalog, pg_temp"], firma
            assert publico is False, firma


def _pedido_directo(motor_app, correo: str) -> tuple[str, str]:
    """Pide enlace y código como la API, sin la API. Devuelve (token, código)."""
    token, nonce, codigo = secrets.token_urlsafe(32), secrets.token_urlsafe(32), "314159"
    clave = sesiones.clave_codigo(CLAVE_CODIGO)
    with motor_app.begin() as conn:
        assert conn.execute(
            text("select nucleo.pedir_enlace(:c, :t, :n, :d, '198.51.100.7', :v)"),
            {"c": correo, "t": h(token), "n": h(nonce), "d": uuid4(),
             "v": sesiones.VIGENCIA_ENLACE},
        ).scalar_one()  # fmt: skip
        conn.execute(
            text("select nucleo.registrar_codigo(:t, :h, :v)"),
            {"t": h(token), "h": sesiones.hash_codigo(clave, correo, codigo),
             "v": sesiones.VIGENCIA_CODIGO},
        )  # fmt: skip
    return token, codigo


def _canjear_directo(motor_app, correo: str, codigo: str) -> str:
    clave = sesiones.clave_codigo(CLAVE_CODIGO)
    with motor_app.begin() as conn:
        return conn.execute(
            text(
                "select resultado from nucleo.canjear_codigo(:c, :h, :s, :d, 'prueba',"
                " '198.51.100.7', :v, :m)"
            ),
            {"c": correo, "h": sesiones.hash_codigo(clave, correo, codigo),
             "s": h(secrets.token_urlsafe(32)), "d": uuid4(), "v": sesiones.VIGENCIA_SESION,
             "m": sesiones.INTENTOS_CODIGO},
        ).scalar_one()  # fmt: skip


def test_intentos_simultaneos_no_pasan_del_tope(motor_app, motor_migrate, datos):
    """Ocho intentos fallidos a la vez: el bloqueo de la fila los ordena; cinco descuentan y
    los demás encuentran el código agotado, sin pasarse del tope ni fallar."""
    _, correo = nueva_persona(motor_migrate)
    _, codigo = _pedido_directo(motor_app, correo)
    simultaneos = 8
    barrera = threading.Barrier(simultaneos)

    def intentar(i: int) -> str:
        barrera.wait()
        return _canjear_directo(motor_app, correo, otro_codigo(codigo, i + 1))

    with ThreadPoolExecutor(simultaneos) as hilos:
        resultados = sorted(hilos.map(intentar, range(simultaneos)))
    assert resultados == sorted(
        ["incorrecto"] * sesiones.INTENTOS_CODIGO
        + ["agotado"] * (simultaneos - sesiones.INTENTOS_CODIGO)
    )
    assert _canjear_directo(motor_app, correo, codigo) == "agotado"


def test_la_base_impone_los_topes(motor_app, motor_migrate, datos):
    _, correo = nueva_persona(motor_migrate)
    token, codigo = _pedido_directo(motor_app, correo)
    with motor_migrate.begin() as conn, pytest.raises(IntegrityError, match="codigo_acceso"):
        conn.execute(
            text("update nucleo.codigo_acceso set intentos = 6 where enlace_id ="
                 " (select id from nucleo.enlace_acceso where token_hash = :t)"),
            {"t": h(token)},
        )  # fmt: skip
    with motor_migrate.begin() as conn, pytest.raises(IntegrityError, match="codigo_acceso"):
        conn.execute(
            text("update nucleo.codigo_acceso set vence_en = creado_en + interval '11 minutes'"
                 " where enlace_id = (select id from nucleo.enlace_acceso where token_hash = :t)"),
            {"t": h(token)},
        )  # fmt: skip
    with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="no existe el enlace"):
        conn.execute(
            text("select nucleo.registrar_codigo(:t, :h, :v)"),
            {"t": h(secrets.token_urlsafe(32)), "h": secrets.token_bytes(32),
             "v": sesiones.VIGENCIA_CODIGO},
        )  # fmt: skip
    with motor_app.begin() as conn, pytest.raises(ProgrammingError, match="32 bytes"):
        conn.execute(
            text("select * from nucleo.canjear_codigo(:c, :h, :s, :d, null, '198.51.100.7',"
                 " :v, :m)"),
            {"c": correo, "h": b"corto", "s": h("x"), "d": uuid4(),
             "v": sesiones.VIGENCIA_SESION, "m": sesiones.INTENTOS_CODIGO},
        )  # fmt: skip
    assert _canjear_directo(motor_app, correo, codigo) == "ok"  # control


# Purga y migración ------------------------------------------------------------------------


def test_la_purga_diaria_borra_los_codigos_viejos_con_su_enlace(motor_app, motor_migrate, datos):
    _, correo = nueva_persona(motor_migrate)
    viejo, _ = _pedido_directo(motor_app, correo)
    reciente, _ = _pedido_directo(motor_app, correo)
    envejecer = sesiones.RETENCION_ACCESO + timedelta(days=1)
    with motor_migrate.begin() as conn:
        for tabla, columna in (("enlace_acceso", "id"), ("codigo_acceso", "enlace_id")):
            conn.execute(
                text(
                    f"update nucleo.{tabla} set creado_en = creado_en - :d,"
                    f" vence_en = vence_en - :d where {columna} ="
                    " (select id from nucleo.enlace_acceso where token_hash = :t)"
                ),
                {"d": envejecer, "t": h(viejo)},
            )
    trabajo = cola.Trabajo(uuid4(), "plataforma.purgar_cola", None, {}, 1, 3)
    with transaccion(motor_app, persona_id=None) as conn:
        tareas.purgar_cola(conn, trabajo)
    with motor_migrate.connect() as conn:
        quedan = conn.execute(
            text(
                "select e.token_hash from nucleo.codigo_acceso c"
                " join nucleo.enlace_acceso e on e.id = c.enlace_id"
                " join nucleo.persona p on p.id = c.persona_id where p.correo = :c"
            ),
            {"c": correo},
        ).scalars()
        assert [bytes(x) for x in quedan] == [h(reciente)]


def test_la_migracion_0006_baja_y_sube(base_de_prueba, motor_migrate):
    def existe() -> tuple[bool, ...]:
        with motor_migrate.connect() as conn:
            return tuple(
                conn.execute(
                    text(
                        "select to_regclass('nucleo.codigo_acceso') is not null,"
                        " to_regprocedure(:f1) is not null, to_regprocedure(:f2) is not null,"
                        " to_regclass('nucleo.enlace_acceso') is not null"
                    ),
                    dict(zip(("f1", "f2"), FUNCIONES, strict=True)),
                ).one()
            )

    assert existe() == (True, True, True, True)
    cfg = alembic_config()
    vaciar_mandatos(motor_migrate)
    command.downgrade(cfg, "0005")
    assert existe() == (False, False, False, True)  # la 0004 queda
    command.upgrade(cfg, "head")
    assert existe() == (True, True, True, True)


def test_el_pedido_sigue_respondiendo_igual_por_la_ruta_de_siempre(app):  # noqa: F811
    """El pedido de entrada es la misma ruta: el frontend no cambia para pedir."""
    r = navegador(app).post(ENLACE, json={"correo": ANA})
    assert r.status_code == 202
    assert r.json() == {"estado": "pedido", "mensaje": router.MENSAJE_PEDIDO}
