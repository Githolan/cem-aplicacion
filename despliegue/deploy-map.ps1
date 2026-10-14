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
#   Get-CemAplicaciones   aplicaciones de un entorno, en orden de despliegue
#   Get-CemBaseDatos      cem-postgres(-staging) del entorno (tabla "Bases de datos"
#                         del registro), para leer plataforma.alembic_version
# ==============================================================================

# Orden de despliegue: el backend primero; trabajos y planificador (misma imagen)
# después; el frontend al final (su nginx resuelve el backend con `resolver`, AD-05).
$CemOrdenDespliegue = @('backend', 'trabajos', 'planificador', 'frontend')

# Funciones que se construyen con backend/Dockerfile y leen CEM_SHA_IMAGEN en /health.
$CemConShaImagen = @('backend', 'trabajos', 'planificador')

$CemEntornos = @{
    staging    = @{
        Proyecto     = 'consorcio-en-mano-staging'
        Host         = 'cem-staging.holancloud.com'
        BaseDatos    = @{ Nombre = 'cem-postgres-staging'; Uuid = 'ai3mquqkmpcqkg30paos0nxr' }
        Aplicaciones = @{
            backend      = @{ Nombre = 'cem-backend-staging';      Uuid = 'f0zuaaegv16yxl8ioriwtmcs' }
            trabajos     = @{ Nombre = 'cem-trabajos-staging';     Uuid = 'aw4btg2jqc24tnelnca0ubib' }
            planificador = @{ Nombre = 'cem-planificador-staging'; Uuid = 'zhbxjxbfcg0a8zchfbhd4scg' }
            frontend     = @{ Nombre = 'cem-frontend-staging';     Uuid = '7bvgssu01usajeaf6cnzwqnk' }
        }
    }
    produccion = @{
        Proyecto     = 'consorcio-en-mano'
        Host         = 'cem.holancloud.com'
        BaseDatos    = @{ Nombre = 'cem-postgres'; Uuid = 'ijhpfart48jbqhqa0th2th9l' }
        Aplicaciones = @{
            backend  = @{ Nombre = 'cem-backend';  Uuid = '965fpbuy4kldof88tkgf5wrv' }
            frontend = @{ Nombre = 'cem-frontend'; Uuid = 'xc0kpzbog3qdko8moxyqnmn7' }
        }
    }
}

# Repositorio y rama de los que Coolify construye todas las aplicaciones de CEM.
$CemRepositorioGit = 'git@github.com:Githolan/cem-aplicacion.git'
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
            [pscustomobject]@{
                Funcion       = $funcion
                Nombre        = $apps[$funcion].Nombre
                Uuid          = $apps[$funcion].Uuid
                ConShaImagen  = $CemConShaImagen -contains $funcion
            }
        }
    }
}
