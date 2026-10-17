# Alta de un consorcio y de su primera persona (equipo de la plataforma)

Hasta que exista la pantalla de alta de consorcios (H2 2b), el primer edificio y sus unidades
los crea el equipo con `python -m scripts.alta_consorcio`, y la primera persona entra con
`python -m scripts.invitar`. Los dos corren **dentro del contenedor de `cem-backend`** (o
`cem-backend-staging`), con su `CEM_DATABASE_URL` (`cem_app`, sujeto a la RLS), y dejan la
auditoría encadenada con origen `soporte` y sin persona.

Lo que hace el script y lo que todavía no: ver el encabezado de
[`backend/scripts/alta_consorcio.py`](../backend/scripts/alta_consorcio.py).

## (a) Preparar el archivo

Un JSON en UTF-8, por ejemplo `edificio.json`:

```json
{
  "consorcio": {
    "nombre": "Edificio Ejemplo 1234",
    "cuit": "30712345678",
    "zona_horaria": "America/Argentina/Buenos_Aires"
  },
  "unidades": [
    {"designacion": "PB"},
    {"designacion": "1° A"},
    {"designacion": "1° B"}
  ]
}
```

- `nombre` es el que verán las personas en la app y en el correo de invitación.
- `cuit`: 11 dígitos sin guiones; si no lo tenés a mano, sacá la línea (queda vacío).
- `designacion`: como la escriben las liquidaciones (`PB`, `1° A`, `Cochera 3`…), una por
  unidad funcional, sin repetir.
- No agregues otros campos (domicilio, coeficientes, modo): la base todavía no los guarda y el
  script rechaza el archivo para que nada se pierda en silencio. Llegan con 2b.
- Falta el `id` a propósito: el primer `--simular` (paso b) propone uno. Pegalo en
  `consorcio` (`"id": "…",`) y **usá el mismo archivo, con el mismo `id`, en staging y en
  producción**. Es lo que hace que correrlo de nuevo no duplique nada.

## Cómo se corre (los dos entornos)

Desde la PC, copiar el archivo a la VM y entrar:

```bash
scp edificio.json oci:~/edificio.json
```

```bash
ssh oci
```

En la VM, ubicar el contenedor. Coolify lo nombra `<uuid de la aplicación>-<fecha>` y el
nombre cambia en cada despliegue; el UUID de cada aplicación está en
`doc/infra/REGISTRO_RECURSOS.md` del repositorio `CEM` (filas `cem-backend-staging` y
`cem-backend`):

```bash
UUID_APP=<uuid de cem-backend-staging o de cem-backend, del registro>
CONT=$(docker ps --format '{{.Names}}' | grep "^${UUID_APP}-")
echo "$CONT"
```

Tiene que imprimir **un solo** nombre. Si no imprime nada o imprime dos, no seguir.

## (b) Simular en staging

```bash
docker exec -i "$CONT" python -m scripts.alta_consorcio --archivo - --simular < ~/edificio.json
```

- La primera vez, sin `id`, termina con código 2 y propone uno: pegarlo en el archivo (en la PC
  y en la VM) y repetir.
- Con el `id`, debe decir `SIMULACIÓN: se crearía (no se escribió nada)` y listar las unidades
  como `(nueva)`. Revisar nombre y unidades.

## (c) Alta real en staging, invitarte y aceptar

```bash
docker exec -i "$CONT" python -m scripts.alta_consorcio --archivo - < ~/edificio.json
```

Salida esperada:

```text
consorcio <uuid-del-consorcio> 'Edificio Ejemplo 1234': creado
  unidad <uuid-unidad>  PB
  unidad <uuid-unidad>  1° A
  ...
```

Control de idempotencia: correr **el mismo comando otra vez**. Tiene que decir
`ya existía igual; no se escribió nada` con los mismos UUID. Si dice que no coincide, no tocar
nada y avisar: lista lo que difiere.

Invitarte como propietario de tu unidad, con los UUID que imprimió:

```bash
docker exec "$CONT" python -m scripts.invitar --consorcio <uuid-del-consorcio> \
  --unidad <uuid-de-tu-unidad> --tipo propietario --correo holan1024@gmail.com --nombre "Horacio"
```

Te llega el correo "Te invitaron a …": abrir el enlace, aceptar, y comprobar que en la app
aparece el edificio con tu vínculo de propietario.

## (d) Producción (solo con tu "sí" escrito, después de (c))

1. **Copia antes.** Confirmar que la copia diaria de anoche quedó verificada (monitor de Kuma de
   la copia en verde) y, además, sacar un volcado a mano desde el contenedor de `cem-postgres`
   (UUID en el registro, mismo método que arriba):

   ```bash
   PG=$(docker ps --format '{{.Names}}' | grep "^<uuid de cem-postgres>-")
   docker exec "$PG" pg_dump -U postgres -Fc cem > ~/cem-antes-del-alta-$(date -u +%Y%m%dT%H%MZ).dump
   ls -l ~/cem-antes-del-alta-*.dump
   ```

   El archivo no puede pesar 0 bytes.
2. Ubicar el contenedor de `cem-backend` (UUID de producción) y repetir (b): `--simular`.
3. Repetir (c) en producción: alta real, segunda corrida de control, invitarte y aceptar en
   `https://cem.holancloud.com`.
4. Anotar en el registro de despliegues la fecha, el `id` del consorcio y la salida de las dos
   corridas.

## Qué hacer si algo falla

| Código | Significa | Qué hacer |
|---|---|---|
| 0 | Hecho (o simulado) | Seguir |
| 1 | El consorcio ya existe con otros datos, o está dado de baja | No se escribió nada. Revisar la lista; corregir un consorcio existente queda para 2b |
| 2 | Archivo inválido, falta el `id` o falta `CEM_DATABASE_URL` | No se escribió nada. Corregir el archivo |
