"""El 422 dice qué hacer con cada campo (tanda 3 del informe de fricción, P-26) y un error de
negocio con campo lo nombra. Sin base: una aplicación mínima con los manejadores de la API."""

from cem_contracts.egresos import ProveedorNuevo
from cem_contracts.errores import CodigoError
from cem_contracts.invitaciones import InvitacionNueva
from cem_contracts.mandatos import DecisionNueva
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import errores, registros
from app.errores import ErrorApi, detalle_de_campo

# Un CBU de 22 números con el segundo dígito verificador mal.
CUENTA_MALA = "2850590940090418135202"
CUIT = "20123456786"


def _cliente() -> TestClient:
    app = FastAPI()
    errores.instalar(app)
    app.add_middleware(registros.RegistroDePeticiones)

    @app.post("/proveedores")
    def alta(nuevo: ProveedorNuevo) -> dict:
        return {}

    @app.post("/invitaciones")
    def invitar(nueva: InvitacionNueva) -> dict:
        return {}

    @app.post("/decisiones")
    def decision(nueva: DecisionNueva) -> dict:
        return {}

    @app.post("/negocio")
    def negocio() -> dict:
        raise ErrorApi(
            422,
            CodigoError.CUIT_INVALIDO,
            "El CUIT no es válido.",
            detalle=detalle_de_campo("cuit"),
        )

    return TestClient(app)


def test_la_cuenta_invalida_trae_su_mensaje_y_nunca_el_numero():
    r = _cliente().post(
        "/proveedores",
        json={"razon_social": "Ascensores", "cuit": CUIT, "cuenta_destino": CUENTA_MALA},
    )
    assert r.status_code == 422
    detalle = r.json()["detalle"]
    assert detalle["campos"] == ["body.cuenta_destino"]
    assert detalle["mensajes"] == {"body.cuenta_destino": "La cuenta no es un CBU o CVU válido."}
    # Control de privacidad: la cuenta escrita no vuelve en ninguna parte de la respuesta.
    assert CUENTA_MALA not in r.text


def test_un_mensaje_por_campo_segun_lo_que_falla():
    r = _cliente().post(
        "/invitaciones",
        json={
            "unidad_id": "no-es-un-id",
            "tipo": "inquilino",
            "correo": "sin-arroba",
            "nombre": "x" * 101,
        },
    )
    assert r.status_code == 422
    mensajes = r.json()["detalle"]["mensajes"]
    assert mensajes == {
        "body.unidad_id": "Elegir una de las opciones.",
        "body.correo": "Escribir un correo válido, por ejemplo nombre@dominio.com.",
        "body.nombre": "Usar hasta 100 caracteres.",
    }
    assert "sin-arroba" not in r.text


def test_lo_que_falta_y_la_fecha_mal_escrita():
    r = _cliente().post("/decisiones", json={"tipo": "asamblea", "fecha": "2026-13-40"})
    assert r.status_code == 422
    mensajes = r.json()["detalle"]["mensajes"]
    assert mensajes["body.fecha"] == "Escribir una fecha válida."
    assert mensajes["body.descripcion"] == "Falta completar este dato."
    assert mensajes["body.referencia"] == "Falta completar este dato."


def test_sin_cuerpo_no_hay_mensaje_de_campo():
    # Control: lo que no es un campo del cuerpo (el cuerpo entero) no lleva mensaje de campo.
    r = _cliente().post("/proveedores")
    assert r.status_code == 422
    assert r.json()["detalle"]["campos"] == ["body"]
    assert r.json()["detalle"]["mensajes"] == {}


def test_un_error_de_negocio_nombra_su_campo():
    r = _cliente().post("/negocio")
    assert r.status_code == 422
    assert r.json()["detalle"] == {"campos": ["body.cuit"]}


def test_sin_campo_no_hay_detalle():
    assert detalle_de_campo(None) is None
