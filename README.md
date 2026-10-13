# cem-aplicacion

Código de **Consorcio en Mano** en un solo repositorio (decisión AD-23, documento 14 del repositorio `CEM`).

| Carpeta | Qué hace |
|---|---|
| `contratos/` | Contrato de la API: modelos Pydantic y enumeraciones (paquete `cem-contracts`, fuente única, AD-06) |
| `backend/` | API con las reglas del negocio (FastAPI, PostgreSQL con seguridad por fila) |

Cada carpeta tiene su README con cómo instalar y probar. Los comandos se corren desde dentro de la carpeta (`backend/requirements.txt` instala `../contratos` relativo a ella).
