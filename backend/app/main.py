from fastapi import FastAPI

from app import errores
from app.acceso import crear_verificador
from app.config import Config, cargar
from app.consorcios.router import router as consorcios
from app.db import crear_motor
from app.plataforma.router import router as plataforma
from app.soporte.router import router as soporte


def crear_app(config: Config | None = None) -> FastAPI:
    config = config or cargar()
    app = FastAPI(title="Consorcio en Mano", version=config.version)
    app.state.config = config
    app.state.motor = crear_motor(config.database_url) if config.database_url else None
    app.state.access = crear_verificador(config.access_equipo, config.access_aud)
    errores.instalar(app)
    app.include_router(plataforma)
    app.include_router(consorcios)
    app.include_router(soporte)
    return app
