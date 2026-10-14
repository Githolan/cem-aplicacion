# ==============================================================================
# Consorcio en Mano - prueba del mapa de despliegue contra la API de Coolify
# ==============================================================================
# SOLO LECTURA: únicamente pedidos GET. No cambia nada en Coolify.
#
# Para cada aplicación de deploy-map.ps1 lee GET /applications/<uuid> y exige:
#   - nombre en Coolify == nombre canónico del mapa
#   - repositorio == cem-aplicacion y rama == main
# Y para la base de cada entorno, GET /databases/<uuid>: nombre == nombre canónico.
# Controles (si un control falla, ningún verde vale):
#   - un UUID inventado debe dar 404 (la API distingue lo que existe de lo que no)
#   - un token de Coolify inventado debe dar 401 (responde la autenticación de
#     Coolify, no una página de Access ni un proxy)
#   - el mapa no repite UUID ni nombres
#
# Uso:  pwsh despliegue/test-deploy-map.ps1      código 0 = todo verde
# ==============================================================================
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'deploy-map.ps1')
. (Join-Path $PSScriptRoot 'api-coolify.ps1')

# UUID con la forma de los de Coolify (24 caracteres [a-z0-9]) que no existe.
$UuidInventado  = 'cemnoexiste0000000000000'
$TokenInventado = 'token-inventado-por-test-deploy-map'

$bien = 0; $mal = 0
function Comprobar($etiqueta, $condicion, $detalle) {
    if ($condicion) { Write-Host "  BIEN  $etiqueta" -ForegroundColor Green; $script:bien++ }
    else            { Write-Host "  MAL   $etiqueta -> $detalle" -ForegroundColor Red; $script:mal++ }
}
function CodigoDe([scriptblock]$bloque) {
    try { & $bloque | Out-Null; 200 } catch { if ($_.Exception.Data['CodigoHttp']) { $_.Exception.Data['CodigoHttp'] } else { "error: $($_.Exception.Message)" } }
}

Assert-CemCredenciales

Write-Host "`n=== Controles ===" -ForegroundColor Cyan
$c = CodigoDe { Invoke-CemCoolify GET "/applications/$UuidInventado" }
Comprobar "UUID inventado -> 404" ($c -eq 404) "respondió $c"
$c = CodigoDe { Invoke-CemCoolify GET "/applications/$($CemEntornos.staging.Aplicaciones.backend.Uuid)" -TokenCoolify $TokenInventado }
Comprobar "token de Coolify inventado -> 401" ($c -eq 401) "respondió $c"

$todas = foreach ($e in $CemEntornos.Keys) { Get-CemAplicaciones -Entorno $e }
$uuidRepetidos = $todas | Group-Object Uuid | Where-Object Count -gt 1
$nombresRepetidos = $todas | Group-Object Nombre | Where-Object Count -gt 1
Comprobar "el mapa no repite UUID" (-not $uuidRepetidos) ($uuidRepetidos.Name -join ', ')
Comprobar "el mapa no repite nombres" (-not $nombresRepetidos) ($nombresRepetidos.Name -join ', ')

foreach ($entorno in @('staging', 'produccion')) {
    Write-Host "`n=== $entorno ($($CemEntornos[$entorno].Host)) ===" -ForegroundColor Cyan
    foreach ($app in Get-CemAplicaciones -Entorno $entorno) {
        $sufijoOk = if ($entorno -eq 'staging') { $app.Nombre -like '*-staging' } else { $app.Nombre -notlike '*-staging' }
        Comprobar "$($app.Nombre): el nombre corresponde al entorno" $sufijoOk $app.Nombre
        try {
            $leida = Invoke-CemCoolify GET "/applications/$($app.Uuid)"
        } catch {
            Comprobar "$($app.Nombre) ($($app.Uuid)) existe en Coolify" $false $_.Exception.Message
            continue
        }
        Comprobar "$($app.Uuid) se llama '$($app.Nombre)' en Coolify" ($leida.name -ceq $app.Nombre) "se llama '$($leida.name)'"
        Comprobar "$($app.Nombre): repositorio cem-aplicacion" ($leida.git_repository -eq $CemRepositorioGit) $leida.git_repository
        Comprobar "$($app.Nombre): rama $CemRamaDespliegue" ($leida.git_branch -eq $CemRamaDespliegue) $leida.git_branch
        Write-Host "        estado en Coolify: $($leida.status); commit fijado: $($leida.git_commit_sha)" -ForegroundColor DarkGray
    }
    $base = Get-CemBaseDatos -Entorno $entorno
    try {
        $leida = Invoke-CemCoolify GET "/databases/$($base.Uuid)"
        Comprobar "base $($base.Uuid) se llama '$($base.Nombre)' en Coolify" ($leida.name -ceq $base.Nombre) "se llama '$($leida.name)'"
    } catch {
        Comprobar "base $($base.Nombre) ($($base.Uuid)) existe en Coolify" $false $_.Exception.Message
    }
}
$c = CodigoDe { Invoke-CemCoolify GET "/databases/$UuidInventado" }
Comprobar "control: base con UUID inventado -> 404" ($c -eq 404) "respondió $c"

Write-Host ""
Write-Host "BIEN=$bien  MAL=$mal" -ForegroundColor $(if ($mal -eq 0) { 'Green' } else { 'Red' })
exit $(if ($mal -eq 0) { 0 } else { 1 })
