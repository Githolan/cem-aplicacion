from fastapi import FastAPI
from redis import Redis

from app import errores, limite, registros
from app.acceso import crear_verificador
from app.config import Config, cargar
from app.consorcios.router import router as consorcios
from app.db import crear_motor
from app.identidad import sesiones
from app.identidad.invitaciones import router as invitaciones
from app.identidad.router import router as identidad
from app.identidad.router import validar_origen
from app.identidad.segundo_factor import router as segundo_factor
from app.plataforma.router import SIN_LIMITE_DE_TASA
from app.plataforma.router import router as plataforma
from app.soporte.router import router as soporte


def crear_app(config: Config | None = None) -> FastAPI:
    config = config or cargar()
    if not config.redis_url:
        raise RuntimeError("CEM_REDIS_URL es obligatoria: sin Redis no hay límite de tasa (TB-8)")
    if config.origen_publico:
        validar_origen(config.origen_publico)
    if config.codigo_clave:
        sesiones.clave_codigo(config.codigo_clave)
    app = FastAPI(title="Consorcio en Mano", version=config.version)
    app.state.config = config
    app.state.motor = crear_motor(config.database_url) if config.database_url else None
    # `memory://` solo lo usan las pruebas que no miden el límite: ahí no hay Redis que sondear.
    app.state.redis = (
        Redis.from_url(
            config.redis_url,
            socket_connect_timeout=limite.ESPERA_REDIS_S,
            socket_timeout=limite.ESPERA_REDIS_S,
        )
        if config.redis_url.startswith(("redis://", "rediss://"))
        else None
    )
    app.state.access = crear_verificador(config.access_equipo, config.access_aud)
    errores.instalar(app)
    app.state.limitador = limite.crear_limitador(config.redis_url)
    app.add_middleware(
        limite.LimiteDeTasa, limitador=app.state.limitador, exentas=SIN_LIMITE_DE_TASA
    )
    # El último agregado queda por fuera: registra también los 429 y 503 del límite de tasa.
    app.add_middleware(registros.RegistroDePeticiones)
    app.include_router(plataforma)
    app.include_router(identidad)
    app.include_router(invitaciones)
    app.include_router(segundo_factor)
    app.include_router(consorcios)
    app.include_router(soporte)
    return app
