# cem-contracts

Contrato de la API de **Consorcio en Mano**: modelos Pydantic de petición y respuesta y enumeraciones de estados. Es la fuente única de las formas (documento 13, §11.2, decisión AD-06): el backend importa estos modelos y el frontend genera sus tipos TypeScript desde el OpenAPI del backend. Nada se duplica a mano.

## Uso

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest && .venv/bin/ruff check .
```

`openapi.json` es el OpenAPI completo de la API, exportado desde el backend (`cd ../backend && python -m scripts.exportar_openapi`) y comprometido; de él se generan los tipos TypeScript del frontend. `esquemas.json` es la instantánea aprobada del esquema de cada modelo. Si un cambio es intencional, se regenera con `python -m cem_contracts.esquemas > esquemas.json` y la diferencia se revisa en el PR.

El backend lo instala desde `../contratos` del mismo repositorio (monorrepo `cem-aplicacion`, AD-23).
