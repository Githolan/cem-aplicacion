# ==============================================================================
# Consorcio en Mano - mapa de despliegue (nombre canónico -> UUID de Coolify)
# ==============================================================================
# Copia EXACTA de la tabla "Aplicaciones" de doc/infra/REGISTRO_RECURSOS.md del
# repositorio CEM (fuente de verdad de los UUID). Si allí cambia o se agrega un
# recurso, se cambia acá en el mismo momento y se corre test-deploy-map.ps1.
#
# Producción todavía no tiene cem-trabajos ni cem-planificador: están ausentes a
# propósito (no se inventan UUID). cd.ps1 despliega solo lo que figura acá.
#
# Lo cargan (dot-source) cd.ps1 y test-deploy-map.ps1. Define:
#   $CemEntornos          entornos, su Host público y sus aplicaciones
#   $CemOrdenDespliegue   orden de despliegue por función (§18.3, regla 3)
#   $CemConShaImagen      funciones cuya imagen lleva CEM_SHA_IMAGEN
#   $CemImagenPorFuncion  imagen de ghcr.io que corresponde a cada función
#   Get-CemAplicaciones   aplicaciones de un entorno, en orden de despliegue (falla si
#                         la imagen de una aplicación no es la de su función)
#   Get-CemBaseDatos      cem-postgres(-staging) del entorno (tabla "Bases de datos"
#                         del registro), para leer plataforma.alembic_version
# ==============================================================================

# Orden de despliegue: el backend primero; trabajos, planificador y copia continua
# (misma imagen) después; el frontend al final (su nginx resuelve el backend con
# `resolver`, AD-05).
$CemOrdenDespliegue = @('backend', 'trabajos', 'planificador', 'copia_continua', 'frontend')

# Funciones que se construyen con backend/Dockerfile y leen CEM_SHA_IMAGEN en /health.
$CemConShaImagen = @('backend', 'trabajos', 'planificador')

# Imagen de ghcr.io (bajo $CemRegistroImagenes de compuertas.ps1) por función: el
# backend y sus tres procesos usan la misma imagen (eligen qué correr con CEM_PROCESO).
# La copia continua no sirve HTTP: no lleva CEM_SHA_IMAGEN; su salud es el latido.
$CemImagenPorFuncion = @{
    backend        = 'cem-backend'
    trabajos       = 'cem-backend'
    planificador   = 'cem-backend'
    copia_continua = 'cem-backend'
    frontend       = 'cem-frontend'
}

# Todas las aplicaciones son de tipo dockerimage en Coolify (decisión del 15-10-2026):
# Coolify baja la imagen de ghcr.io con la SHA como etiqueta; no construye.
$CemEntornos = @{
    staging    = @{
        Proyecto     = 'consorcio-en-mano-staging'
        Host         = 'cem-staging.holancloud.com'
        BaseDatos    = @{ Nombre = 'cem-postgres-staging'; Uuid = 'ai3mquqkmpcqkg30paos0nxr' }
        # Apps dockerimage de ghcr.io desde el 15-10-2026 (reemplazo hecho por la sesión principal).
        Aplicaciones = @{
            backend      = @{ Nombre = 'cem-backend-staging';      Imagen = 'cem-backend';  Uuid = 'yepjjanuzkdvtibdk1hngn21' }
            trabajos     = @{ Nombre = 'cem-trabajos-staging';     Imagen = 'cem-backend';  Uuid = 'wqndx1oq0tdsshbjutmbbifg' }
            planificador = @{ Nombre = 'cem-planificador-staging'; Imagen = 'cem-backend';  Uuid = 'lqli8onu8sojoo5ai59fyiuu' }
            # Creada el 27-10-2026 (2c-4, H2c §6).
            copia_continua = @{ Nombre = 'cem-copia-continua-staging'; Imagen = 'cem-backend'; Uuid = 'virszak1hbblkiqwgl0k36lm' }
            frontend     = @{ Nombre = 'cem-frontend-staging';     Imagen = 'cem-frontend'; Uuid = 'qor6tckbjzlenawynxxepchp' }
        }
    }
    produccion = @{
        Proyecto     = 'consorcio-en-mano'
        Host         = 'cem.holancloud.com'
        BaseDatos    = @{ Nombre = 'cem-postgres'; Uuid = 'ijhpfart48jbqhqa0th2th9l' }
        # Apps dockerimage de ghcr.io desde el 15-10-2026 03:11-03:15 UTC (orden del propietario:
        # "pasar producción a ghcr con identidad"; imagen f48858a, la validada en staging).
        Aplicaciones = @{
            backend      = @{ Nombre = 'cem-backend';      Imagen = 'cem-backend';  Uuid = 'xqm5yub6sbfygenrsd1nxo3w' }
            trabajos     = @{ Nombre = 'cem-trabajos';     Imagen = 'cem-backend';  Uuid = 'hlt2qkoijhrq9slhxmxplkih' }
            planificador = @{ Nombre = 'cem-planificador'; Imagen = 'cem-backend';  Uuid = '1c1y4dkwcyic4hknug92y7mt' }
            # Creada el 27-10-2026 (2c-4, H2c §6), después del ensayo PA26 en staging.
            copia_continua = @{ Nombre = 'cem-copia-continua'; Imagen = 'cem-backend'; Uuid = '6y0zook9mqqeslim6z84ad2o' }
            frontend     = @{ Nombre = 'cem-frontend';     Imagen = 'cem-frontend'; Uuid = '53rot1nz8zmpjppgz3yib6tq' }
        }
    }
}

# Rama de la que sale toda SHA desplegable (cd.ps1 despliega solo su punta).
$CemRamaDespliegue = 'main'

# Base transaccional del entorno (tabla "Bases de datos" del registro de recursos).
# Coolify llama a su contenedor por el UUID; cd.ps1 lee allí la revisión de Alembic.
function Get-CemBaseDatos {
    param([Parameter(Mandatory)][string]$Entorno)
    if (-not $CemEntornos.ContainsKey($Entorno)) {
        throw "entorno desconocido '$Entorno' (válidos: $($CemEntornos.Keys -join ', '))"
    }
    $b = $CemEntornos[$Entorno].BaseDatos
    [pscustomobject]@{ Nombre = $b.Nombre; Uuid = $b.Uuid }
}

# Devuelve las aplicaciones de un entorno en orden de despliegue, cada una con su
# función, nombre canónico y UUID. Falla si el entorno no existe.
function Get-CemAplicaciones {
    param([Parameter(Mandatory)][string]$Entorno)
    if (-not $CemEntornos.ContainsKey($Entorno)) {
        throw "entorno desconocido '$Entorno' (válidos: $($CemEntornos.Keys -join ', '))"
    }
    $apps = $CemEntornos[$Entorno].Aplicaciones
    foreach ($funcion in $CemOrdenDespliegue) {
        if ($apps.ContainsKey($funcion)) {
            if ($apps[$funcion].Imagen -ne $CemImagenPorFuncion[$funcion]) {
                throw "$($apps[$funcion].Nombre): el mapa dice imagen '$($apps[$funcion].Imagen)', la función $funcion usa '$($CemImagenPorFuncion[$funcion])'"
            }
            [pscustomobject]@{
                Funcion       = $funcion
                Nombre        = $apps[$funcion].Nombre
                Uuid          = $apps[$funcion].Uuid
                Imagen        = $apps[$funcion].Imagen
                ConShaImagen  = $CemConShaImagen -contains $funcion
            }
        }
    }
}
