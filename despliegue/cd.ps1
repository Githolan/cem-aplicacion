# ==============================================================================
# Consorcio en Mano - despliegue local por la API de Coolify (documento 13, §18.3)
# ==============================================================================
# Despliega UN entorno desde UNA SHA de main y lo verifica sobre el contenido.
# Sin CI/CD en la nube: corre en la PC del propietario (PowerShell 7).
#
# Compuertas (se niega y sale con código 1 si alguna no se cumple; nada cambia):
#   - la SHA está en origin/main y es su punta (Coolify construye la punta de main)
#   - ci.ps1 terminó en verde sobre esa SHA (constancia local de ci.ps1)
#   - fuera de la ventana de actualización de Coolify, 02:45-03:10 UTC
#   - producción: solo con -Entorno produccion -Confirmar, y solo si cd.ps1 ya
#     desplegó y verificó esa misma SHA en staging
#   - cada UUID del mapa se llama en Coolify como su nombre canónico, construye
#     cem-aplicacion desde main y no tiene un commit fijado
#
# Copia previa a la migración (§18.3, regla 5). Se compara la última revisión de
# Alembic de la SHA con plataforma.alembic_version de cem-postgres(-staging), leída
# por ssh con psql dentro del contenedor de la base:
#   - sin migraciones nuevas: no se copia y el registro lo dice
#   - con migraciones nuevas: antes de desplegar, cem-trabajos(-staging) hace una
#     copia verificada (la misma de §17: volcado, restauración y comparación,
#     cifrado, subida y relectura) y se espera su resultado; si falla, no se despliega
#   - con migraciones nuevas y sin cem-trabajos en el mapa del entorno: se niega,
#     salvo con -SinCopiaPrevia, que queda anotado en el registro
#   - si la base está en una revisión que la SHA no conoce: se niega
# En -SoloVerificar solo se informa la decisión; nunca se copia.
#
# Despliegue: por cada aplicación del entorno en deploy-map.ps1, en orden
# (backend; trabajos y planificador si existen; frontend): actualiza
# CEM_SHA_IMAGEN donde corresponde, pide el despliegue y espera que termine con
# el commit esperado. Un despliegue fallido detiene todo.
#
# Verificación sobre contenido (por ssh a la VM, a través de Traefik con el Host
# del entorno; lo mismo en -SoloVerificar):
#   - cada contenedor corre, está healthy y su SOURCE_COMMIT es la SHA
#   - CEM_SHA_IMAGEN en Coolify es la SHA
#   - /health: "estado":"ok" y sha_imagen == SHA
#   - /api/v1/health/deps y /api/v1/health/cola: "estado":"ok"
#   - control: con un Host que no existe, Traefik NO devuelve "estado":"ok"
#
# Deja un registro de despliegue (§18.6) en <estado>\deploys\AAAA-MM-DD-<sha7>.md
# (ver compuertas.ps1) para copiar a doc/deploys/ del repositorio CEM, y, si el
# entorno es staging y todo dio verde, la constancia que exige producción.
#
# Credenciales: solo por variable de entorno (ver api-coolify.ps1). Nunca se imprimen.
#
# Uso:
#   pwsh despliegue/cd.ps1 -Entorno staging -Sha <sha> -SoloVerificar
#   pwsh despliegue/cd.ps1 -Entorno staging -Sha <sha> -Autorizacion "Horacio, chat 14-10 10:05"
#   pwsh despliegue/cd.ps1 -Entorno produccion -Sha <sha> -Confirmar -Autorizacion "..."
#   ... -SinCopiaPrevia   despliega migraciones nuevas sin copia previa (queda en el registro)
# ==============================================================================

param(
    [Parameter(Mandatory)][ValidateSet('staging', 'produccion')][string]$Entorno,
    [Parameter(Mandatory)][string]$Sha,
    # Quién autorizó el despliegue y dónde (va al registro). Obligatoria si se despliega.
    [string]$Autorizacion,
    [switch]$Confirmar,
    # Solo lee y verifica: no cambia variables, no despliega, no escribe constancias.
    [switch]$SoloVerificar,
    # Permite desplegar migraciones nuevas sin la copia previa de §18.3, regla 5.
    # Solo por decisión explícita del propietario; queda escrito en el registro.
    [switch]$SinCopiaPrevia,
    [int]$MinutosPorDespliegue = 15
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

. (Join-Path $PSScriptRoot 'deploy-map.ps1')
. (Join-Path $PSScriptRoot 'api-coolify.ps1')
. (Join-Path $PSScriptRoot 'compuertas.ps1')

$RemotoGit          = 'origin'
$SegundosEntreConsultas = 10
$EstadosFinales     = @('finished', 'failed', 'cancelled-by-user')
$HostInexistente    = 'cem-control-host-inexistente.holancloud.com'
$RutasDeSalud       = @('/api/v1/health/deps', '/api/v1/health/cola')
$Plantilla          = Join-Path $PSScriptRoot 'plantilla-registro-despliegue.md'
$SegundosCurl       = 10
# psql dentro de cem-postgres(-staging): superusuario local del contenedor y base de CEM.
$CemUsuarioPsql     = 'postgres'
$CemBaseCem         = 'cem'
# Variable de cem-trabajos con el bucket de las copias (app/plataforma/copias.py); no es secreta.
$CemVariableBucketCopias = 'CEM_COPIAS_BUCKET'

$repo = Split-Path -Parent $PSScriptRoot
$cfg = $CemEntornos[$Entorno]
$aplicaciones = @(Get-CemAplicaciones -Entorno $Entorno)
$baseDatos = Get-CemBaseDatos -Entorno $Entorno
$horaInicio = (Get-Date).ToUniversalTime()

function Negarse([string]$motivo) {
    Write-Host "NO SE DESPLIEGA: $motivo" -ForegroundColor Red
    exit 1
}
function Info([string]$m) { Write-Host $m -ForegroundColor Cyan }
function Bien([string]$m) { Write-Host "  BIEN  $m" -ForegroundColor Green }

# --- SHA en main --------------------------------------------------------------
git -C $repo fetch --quiet $RemotoGit $CemRamaDespliegue
if ($LASTEXITCODE -ne 0) { Negarse "git fetch $RemotoGit $CemRamaDespliegue falló: no se puede saber qué hay en main" }
$shaCompleta = git -C $repo rev-parse --verify --quiet "$Sha^{commit}"
if ($LASTEXITCODE -ne 0 -or -not $shaCompleta) { Negarse "el commit '$Sha' no existe en este repositorio" }
$shaCompleta = "$shaCompleta".Trim()
Assert-CemShaCompleta $shaCompleta
$sha7 = $shaCompleta.Substring(0, 7)
$puntaMain = "$(git -C $repo rev-parse "$RemotoGit/$CemRamaDespliegue")".Trim()
git -C $repo merge-base --is-ancestor $shaCompleta "$RemotoGit/$CemRamaDespliegue"
if ($LASTEXITCODE -ne 0) { Negarse "$sha7 no está en $RemotoGit/$CemRamaDespliegue" }
Info "CEM - $(if ($SoloVerificar) { 'verificación (solo lectura)' } else { 'despliegue' }) de $Entorno en $shaCompleta"
Bien "$sha7 está en $RemotoGit/$CemRamaDespliegue (punta: $($puntaMain.Substring(0, 7)))"

# --- Compuertas del despliegue --------------------------------------------------
$requisitoPrevio = 'ninguno (staging)'
if (-not $SoloVerificar) {
    if (Test-CemVentanaProhibida -HoraUtc $horaInicio) {
        Negarse "son las $($horaInicio.ToString('HH:mm')) UTC, dentro de la ventana de actualización de Coolify ($CemVentanaProhibidaDesde-$CemVentanaProhibidaHasta UTC)"
    }
    Bien "fuera de la ventana de actualización de Coolify ($($horaInicio.ToString('HH:mm')) UTC)"
    if ($Entorno -eq 'produccion') {
        if (-not $Confirmar) { Negarse 'producción exige -Confirmar' }
        if (-not (Test-CemVerificadoStaging -Sha $shaCompleta)) {
            Negarse "$sha7 no está registrada como desplegada y verificada en staging por cd.ps1 ($(Get-CemCarpetaEstado verificado-staging))"
        }
        $requisitoPrevio = "$sha7 desplegada y verificada en staging (constancia de cd.ps1)"
        Bien $requisitoPrevio
    }
    if (-not (Test-CemCiAprobado -Sha $shaCompleta)) {
        Negarse "ci.ps1 no tiene un resultado verde sobre $sha7 ($(Get-CemCarpetaEstado ci)); correr: pwsh despliegue/ci.ps1 -Sha $sha7"
    }
    Bien "ci.ps1 en verde sobre $sha7"
    if (-not $Autorizacion) { Negarse 'falta -Autorizacion (quién lo autorizó y dónde; va al registro de despliegue)' }
    if ($shaCompleta -ne $puntaMain) {
        Negarse "$sha7 no es la punta de main ($($puntaMain.Substring(0, 7))); Coolify construye la punta de main y desplegaría otro commit"
    }
    Bien "$sha7 es la punta de main"
}

# --- Coolify: el mapa coincide ---------------------------------------------------
Assert-CemCredenciales
foreach ($app in $aplicaciones) {
    $leida = Invoke-CemCoolify GET "/applications/$($app.Uuid)"
    if ($leida.name -cne $app.Nombre) { Negarse "$($app.Uuid) se llama '$($leida.name)' en Coolify, no '$($app.Nombre)'" }
    if ($leida.git_repository -ne $CemRepositorioGit -or $leida.git_branch -ne $CemRamaDespliegue) {
        Negarse "$($app.Nombre) construye $($leida.git_repository)@$($leida.git_branch), no $CemRepositorioGit@$CemRamaDespliegue"
    }
    if (-not $SoloVerificar -and $leida.git_commit_sha -ne 'HEAD') {
        Negarse "$($app.Nombre) tiene fijado el commit '$($leida.git_commit_sha)' en Coolify; se espera HEAD"
    }
    Bien "$($app.Nombre) = $($app.Uuid) (Coolify: $($leida.status))"
}
$leidaBase = Invoke-CemCoolify GET "/databases/$($baseDatos.Uuid)"
if ($leidaBase.name -cne $baseDatos.Nombre) { Negarse "$($baseDatos.Uuid) se llama '$($leidaBase.name)' en Coolify, no '$($baseDatos.Nombre)'" }
Bien "$($baseDatos.Nombre) = $($baseDatos.Uuid) (Coolify: $($leidaBase.status))"

# --- Copia previa a la migración (§18.3, regla 5) --------------------------------
Info "`n== Migraciones y copia previa =="
$cadena = Get-CemCadenaAlembicEnSha -Repo $repo -Sha $shaCompleta
# Coolify llama al contenedor de una base por su UUID (registro de recursos, nota 1).
$versionEntorno = Invoke-CemSsh "docker exec $($baseDatos.Uuid) psql -U $CemUsuarioPsql -d $CemBaseCem -Atc 'select version_num from plataforma.alembic_version'"
$trabajos = $aplicaciones | Where-Object Funcion -eq 'trabajos'
try {
    $decision = Get-CemDecisionCopiaPrevia -Cadena $cadena -VersionEntorno $versionEntorno.Trim() `
        -HayTrabajos ($null -ne $trabajos) -SinCopiaPrevia $SinCopiaPrevia.IsPresent
} catch {
    Negarse "no se puede decidir la copia previa: $($_.Exception.Message)"
}
Write-Host "  Alembic: $sha7 llega a $($decision.Cabeza); $($baseDatos.Nombre) está en $($decision.VersionEntorno)"
Write-Host "  Copia previa: $($decision.Accion) - $($decision.Motivo)"
$copia = $null
$bucketCopia = $null
if (-not $SoloVerificar) {
    if ($decision.Accion -eq 'negar') { Negarse $decision.Motivo }
    if ($decision.Accion -eq 'copiar') {
        $enMarcha = @((Invoke-CemSsh "docker ps --filter 'name=^$($trabajos.Uuid)-' --format '{{.Names}}'") -split "`n" | Where-Object { $_ })
        if ($enMarcha.Count -ne 1) { Negarse "$($trabajos.Nombre): $($enMarcha.Count) contenedores en marcha (se espera 1); no se puede hacer la copia previa" }
        $bucketCopia = Invoke-CemSsh "docker exec $($enMarcha[0]) printenv $CemVariableBucketCopias"
        Write-Host "  copia verificada en $($enMarcha[0]) (bucket $bucketCopia); espero el resultado"
        try {
            $salida = Invoke-CemSsh "docker exec $($enMarcha[0]) python -c '$CemProgramaCopiaPrevia'"
            $copia = Read-CemResultadoCopia -Salida $salida
        } catch {
            Negarse "la copia previa falló, nada se desplegó: $($_.Exception.Message)"
        }
        Bien "copia previa: $($copia.Objetos -join ', ') SHA-256 $($copia.Sha256) ($($copia.Bytes) bytes, $($copia.Tablas) tablas, $($copia.Filas) filas)"
    }
}

# --- Despliegue -----------------------------------------------------------------
$filas = [System.Collections.Generic.List[string]]::new()
$desplegadoOk = $true
if (-not $SoloVerificar) {
    $orden = 0
    foreach ($app in $aplicaciones) {
        $orden++
        Info "`n== $($app.Nombre) =="
        if ($app.ConShaImagen) {
            Set-CemVariablePublicaApp -Uuid $app.Uuid -Clave 'CEM_SHA_IMAGEN' -Valor $shaCompleta
            Bien "CEM_SHA_IMAGEN = $sha7 (leída de vuelta)"
        }
        $pedido = Invoke-CemCoolify POST "/deploy?uuid=$($app.Uuid)&force=false"
        $dep = @($pedido.deployments)[0].deployment_uuid
        if (-not $dep) { throw "Coolify no devolvió el identificador del despliegue de $($app.Nombre)" }
        Write-Host "  despliegue $dep pedido; espero (hasta $MinutosPorDespliegue min)"
        $limite = (Get-Date).AddMinutes($MinutosPorDespliegue)
        do {
            Start-Sleep -Seconds $SegundosEntreConsultas
            $s = Invoke-CemCoolify GET "/deployments/$dep"
        } while ($s.status -notin $EstadosFinales -and (Get-Date) -lt $limite)
        $estado = if ($s.status -in $EstadosFinales) { $s.status } else { "sin terminar a los $MinutosPorDespliegue min ($($s.status))" }
        $filas.Add("| $orden | ``$($app.Nombre)`` | ``$($app.Uuid)`` | ``$dep`` | $estado | ``$($s.commit)`` |")
        if ($s.status -ne 'finished') {
            Write-Host "  FALLA: despliegue $estado" -ForegroundColor Red
            if ($s.logs) {
                ($s.logs | ConvertFrom-Json) | Where-Object { -not $_.hidden } | Select-Object -Last 8 |
                    ForEach-Object { Write-Host "    $($_.output)" -ForegroundColor DarkGray }
            }
            $desplegadoOk = $false
            break
        }
        if ($s.commit -ne $shaCompleta) {
            Write-Host "  FALLA: Coolify construyó $($s.commit), no $shaCompleta" -ForegroundColor Red
            $desplegadoOk = $false
            break
        }
        Bien "despliegue terminado con el commit $sha7"
    }
}

# --- Verificación sobre contenido ------------------------------------------------
$lineas = [System.Collections.Generic.List[string]]::new()
$fallas = 0
function Anotar([bool]$ok, [string]$texto) {
    $linea = "$(if ($ok) { 'BIEN' } else { 'MAL ' })  $texto"
    $script:lineas.Add($linea)
    if ($ok) { Write-Host "  $linea" -ForegroundColor Green } else { Write-Host "  $linea" -ForegroundColor Red; $script:fallas++ }
}
# Pide una ruta a Traefik desde la VM con el Host dado; devuelve código y cuerpo.
function Pedir-Traefik([string]$hostHttp, [string]$ruta) {
    $salida = Invoke-CemSsh "curl -sk -m $SegundosCurl -w '\n%{http_code}' -H 'Host: $hostHttp' 'https://localhost$ruta'"
    $partes = $salida -split "`n"
    [pscustomobject]@{ Codigo = $partes[-1].Trim(); Cuerpo = ($partes[0..($partes.Count - 2)] -join "`n").Trim() }
}
function Leer-Json([string]$texto) { try { $texto | ConvertFrom-Json -ErrorAction Stop } catch { $null } }

if ($desplegadoOk) {
    Info "`n== Verificación sobre contenido ($Entorno, Host $($cfg.Host)) =="
    foreach ($app in $aplicaciones) {
        $contenedores = @((Invoke-CemSsh "docker ps --filter 'name=^$($app.Uuid)-' --format '{{.Names}}'") -split "`n" | Where-Object { $_ })
        if ($contenedores.Count -ne 1) { Anotar $false "$($app.Nombre): $($contenedores.Count) contenedores en marcha (se espera 1)"; continue }
        $c = $contenedores[0]
        $salud = Invoke-CemSsh "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}sin-healthcheck{{end}}' $c"
        Anotar ($salud -eq 'running healthy') "$($app.Nombre): contenedor $c $salud"
        $commit = Invoke-CemSsh "docker exec $c printenv SOURCE_COMMIT"
        Anotar ($commit -eq $shaCompleta) "$($app.Nombre): SOURCE_COMMIT $commit"
        if ($app.ConShaImagen) {
            $var = Get-CemVariableApp -Uuid $app.Uuid -Clave 'CEM_SHA_IMAGEN'
            Anotar ($var -eq $shaCompleta) "$($app.Nombre): CEM_SHA_IMAGEN en Coolify $var"
        }
    }
    $r = Pedir-Traefik $cfg.Host '/health'
    $j = Leer-Json $r.Cuerpo
    Anotar ($r.Codigo -eq '200' -and $j.estado -eq 'ok' -and $j.sha_imagen -eq $shaCompleta) "/health [$($r.Codigo)] $($r.Cuerpo)"
    foreach ($ruta in $RutasDeSalud) {
        $r = Pedir-Traefik $cfg.Host $ruta
        $j = Leer-Json $r.Cuerpo
        Anotar ($r.Codigo -eq '200' -and $j.estado -eq 'ok') "$ruta [$($r.Codigo)] $($r.Cuerpo)"
    }
    $r = Pedir-Traefik $HostInexistente '/health'
    $j = Leer-Json $r.Cuerpo
    Anotar ($r.Codigo -ne '200' -and $j.estado -ne 'ok') "control: Host $HostInexistente -> [$($r.Codigo)] $($r.Cuerpo) (no debe dar estado ok)"
} else {
    $lineas.Add('No se verificó: el despliegue no terminó bien.')
}
$verificacionOk = $desplegadoOk -and ($fallas -eq 0)
$horaFin = (Get-Date).ToUniversalTime()

# --- Registro y constancias (nunca en -SoloVerificar) ----------------------------
if (-not $SoloVerificar) {
    $ci = Read-CemResultadoCi -Sha $shaCompleta
    $valores = @{
        FECHA = $horaInicio.ToString('yyyy-MM-dd'); SHA = $shaCompleta; SHA7 = $sha7
        CI_RESULTADO = $ci.resultado; CI_FECHA = $ci.fecha_utc; CI_EQUIPO = $ci.equipo; CI_MINUTOS = $ci.minutos
        CI_ETAPAS = (($ci.etapas.PSObject.Properties | ForEach-Object { "$($_.Name): $($_.Value)" }) -join '; ')
        ENTORNO = $Entorno; HOST = $cfg.Host; PROYECTO = $cfg.Proyecto
        HORA_INICIO = $horaInicio.ToString('HH:mm'); HORA_FIN = $horaFin.ToString('HH:mm')
        AUTORIZACION = $Autorizacion; REQUISITO_PREVIO = $requisitoPrevio
        COPIA_PREVIA = Format-CemCopiaPrevia -Decision $decision -Copia $copia -Bucket $bucketCopia
        RESULTADO = if ($verificacionOk) { 'verde' } else { 'ROJO' }
        FILAS_APLICACIONES = ($filas -join "`n"); VERIFICACION = ($lineas -join "`n")
    }
    $registro = Add-CemRegistroDespliegue -Plantilla $Plantilla -Valores $valores `
        -Ruta (Join-Path (Get-CemCarpetaEstado deploys) "$($valores.FECHA)-$sha7.md")
    Info "`nRegistro de despliegue: $registro"
    Write-Host "  copiarlo a doc/deploys/ del repositorio CEM y comprometerlo (§18.6)"
    if ($Entorno -eq 'staging' -and $verificacionOk) {
        $marca = Write-CemVerificadoStaging -Sha $shaCompleta -Datos @{
            sha = $shaCompleta; entorno = 'staging'; verificacion = 'ok'
            fecha_utc = $horaFin.ToString('yyyy-MM-ddTHH:mm:ssZ'); registro = $registro
        }
        Write-Host "  constancia de staging verificado: $marca"
    }
}

Write-Host ""
if ($verificacionOk) {
    Write-Host "VERDE: $Entorno en $sha7 $(if ($SoloVerificar) { 'verificado (solo lectura)' } else { 'desplegado y verificado' })" -ForegroundColor Green
    exit 0
}
Write-Host "ROJO: $Entorno en $sha7 ($fallas comprobaciones fallaron$(if (-not $desplegadoOk) { '; el despliegue no terminó bien' }))" -ForegroundColor Red
exit 1
