# cem-aplicacion

Código de **Consorcio en Mano** en un solo repositorio (decisión AD-23, documento 14 del repositorio `CEM`).

| Carpeta | Qué hace |
|---|---|
| `contratos/` | Contrato de la API: modelos Pydantic y enumeraciones (paquete `cem-contracts`, fuente única, AD-06) |
| `backend/` | API con las reglas del negocio (FastAPI, PostgreSQL con seguridad por fila) |
| `frontend/` | Aplicación web instalable que ven los usuarios (React, Vite, PWA, servida por nginx) |
| `despliegue/` | Integración y despliegue desde la PC del propietario, sin CI en la nube: `ci.ps1` (suite oficial completa), `cd.ps1` (despliega un entorno por la API de Coolify y lo verifica), `deploy-map.ps1` (nombre canónico → UUID) y sus pruebas |

Cada carpeta tiene su README con cómo instalar y probar. Los comandos se corren desde dentro de la carpeta (`backend/requirements.txt` instala `../contratos` relativo a ella; el frontend genera sus tipos desde `../contratos/openapi.json`). Las dos imágenes se construyen desde la raíz: `backend/Dockerfile` y `frontend/Dockerfile`.
