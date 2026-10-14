from fastapi import FastAPI
from redis import Redis

from app import errores, limite
from app.config import Config, cargar
from app.consorcios.router import router as consorcios
from app.db import crear_motor
from app.plataforma.router import SIN_LIMITE_DE_TASA
from app.plataforma.router import router as plataforma


def crear_app(config: Config | None = None) -> FastAPI:
    config = config or cargar()
    if not config.redis_url:
        raise RuntimeError("CEM_REDIS_URL es obligatoria: sin Redis no hay límite de tasa (TB-8)")
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
    errores.instalar(app)
    app.add_middleware(limite.LimiteDeTasa, almacen=config.redis_url, exentas=SIN_LIMITE_DE_TASA)
    app.include_router(plataforma)
    app.include_router(consorcios)
    return app
