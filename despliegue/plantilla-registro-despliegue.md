# Despliegue {{FECHA}} — `cem-aplicacion` {{SHA7}}

Registro de despliegue del documento 13, §18.6. Lo genera `despliegue/cd.ps1` de `cem-aplicacion` y va en `doc/deploys/` del repositorio `CEM`. Un archivo por día y SHA; cada entorno agrega su sección.

| Dato | Valor |
|---|---|
| Commit publicado | `{{SHA}}` (rama `main` de `Githolan/cem-aplicacion`) |
| Alimentadores | `cem-aplicacion` es el único (monorrepositorio, AD-23); `contratos/` va dentro del contexto de construcción |
| Compuertas (`ci.ps1`) | {{CI_RESULTADO}} el {{CI_FECHA}} en `{{CI_EQUIPO}}` ({{CI_MINUTOS}} min) |
| Etapas de `ci.ps1` | {{CI_ETAPAS}} |

<!-- seccion-por-entorno -->

## {{ENTORNO}} — {{HORA_INICIO}} a {{HORA_FIN}} UTC

| Dato | Valor |
|---|---|
| Entorno | `{{ENTORNO}}` (`{{HOST}}`, proyecto de Coolify `{{PROYECTO}}`) |
| Autorizó | {{AUTORIZACION}} |
| Requisito previo | {{REQUISITO_PREVIO}} |
| Copia previa | {{COPIA_PREVIA}} |
| Migración | La aplica el arranque del backend (`alembic upgrade head` con `cem_migrate`, AD-09) |
| Resultado | **{{RESULTADO}}** |

### Aplicaciones

| Orden | Aplicación | UUID de Coolify | Despliegue | Estado | Commit construido |
|---|---|---|---|---|---|
{{FILAS_APLICACIONES}}

### Verificación sobre contenido

```text
{{VERIFICACION}}
```
