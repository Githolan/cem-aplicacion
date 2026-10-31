# ==============================================================================
# Consorcio en Mano - prueba de las compuertas de cd.ps1 (sin red, sin Coolify)
# ==============================================================================
# 1. Ventana de actualización de Coolify: bordes 02:44/02:45 y 03:10/03:11 UTC.
# 2. Constancia de ci.ps1: sin archivo, en rojo, de otra SHA y en verde.
# 3. Constancia de staging verificado: sin archivo, en rojo y en verde.
# 4. cd.ps1 de punta a punta, SIN credenciales en el proceso (no puede llegar a
#    Coolify aunque una compuerta fallara): producción sin -Confirmar, producción
#    sin staging verificado, staging sin ci.ps1 en verde y staging sin -Autorizacion.
#    Cada caso debe salir con código 1 y el motivo esperado.
# 5. Revisiones de Alembic leídas de commits reales de este repositorio (0001;
#    0001,0002; la raíz sin migraciones falla) y cadenas inválidas.
# 6. Decisión de la copia previa: sin migración nueva, con migración nueva, sin
#    cem-trabajos, con -SinCopiaPrevia, base en una revisión desconocida o vacía.
# 7. Resumen de la copia (JSON válido e inválidos) y la fila del registro.
# 8. cd.ps1 acepta -SinCopiaPrevia y con él las demás compuertas siguen valiendo.
# 10. Cobertura de PA: la constancia guarda cubiertas y faltantes; un informe roto falla.
# 11. Proyecto de compose por corrida: formato, dos corridas distintas, y solo las corridas
#     de procesos muertos se toman por abandonadas (nunca una viva ni otro formato).
#
# Usa una carpeta de estado temporal (CEM_DESPLIEGUE_ESTADO); no toca la real.
# Uso:  pwsh despliegue/test-compuertas.ps1      código 0 = todo verde
# ==============================================================================
$ErrorActionPreference = 'Stop'
$prefijo = 'cem-test-compuertas-'
$raiz = Join-Path ([IO.Path]::GetTempPath()) "$prefijo$([guid]::NewGuid().ToString('N').Substring(0, 8))"
New-Item -ItemType Directory -Path $raiz | Out-Null
$env:CEM_DESPLIEGUE_ESTADO = $raiz
. (Join-Path $PSScriptRoot 'compuertas.ps1')

$bien = 0; $mal = 0
function Comprobar($etiqueta, $condicion, $detalle) {
    if ($condicion) { Write-Host "  BIEN  $etiqueta" -ForegroundColor Green; $script:bien++ }
    else            { Write-Host "  MAL   $etiqueta -> $detalle" -ForegroundColor Red; $script:mal++ }
}
function Hora([string]$hhmmss) { $t = [timespan]$hhmmss; [datetime]::new(2026, 10, 14, $t.Hours, $t.Minutes, $t.Seconds, [DateTimeKind]::Utc) }

Write-Host "`n=== 1. Ventana 02:45-03:10 UTC ===" -ForegroundColor Cyan
foreach ($caso in @(@('02:44:59', $false), @('02:45:00', $true), @('03:00:00', $true), @('03:10:59', $true), @('03:11:00', $false), @('14:00:00', $false))) {
    $r = Test-CemVentanaProhibida -HoraUtc (Hora $caso[0])
    Comprobar "$($caso[0]) prohibida=$($caso[1])" ($r -eq $caso[1]) "dio $r"
}

$shaA = 'a' * 40; $shaB = 'b' * 40
Write-Host "`n=== 2. Constancia de ci.ps1 ===" -ForegroundColor Cyan
Comprobar 'sin constancia -> no aprobado' (-not (Test-CemCiAprobado -Sha $shaA)) 'aprobado'
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'falla' } | Out-Null
Comprobar 'constancia en rojo -> no aprobado' (-not (Test-CemCiAprobado -Sha $shaA)) 'aprobado'
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaB; resultado = 'ok' } | Out-Null
Comprobar 'constancia de otra SHA en el archivo -> no aprobado' (-not (Test-CemCiAprobado -Sha $shaA)) 'aprobado'
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok' } | Out-Null
Comprobar 'constancia en verde -> aprobado' (Test-CemCiAprobado -Sha $shaA) 'no aprobado'
Comprobar 'el verde de A no aprueba B' (-not (Test-CemCiAprobado -Sha $shaB)) 'aprobado'
$e = $null; try { Test-CemCiAprobado -Sha 'abc1234' | Out-Null } catch { $e = $_ }
Comprobar 'una SHA corta se rechaza' ($null -ne $e) 'aceptada'

Write-Host "`n=== 3. Constancia de staging verificado ===" -ForegroundColor Cyan
Comprobar 'sin constancia -> no verificado' (-not (Test-CemVerificadoStaging -Sha $shaA)) 'verificado'
Write-CemVerificadoStaging -Sha $shaA -Datos @{ sha = $shaA; entorno = 'staging'; verificacion = 'falla' } | Out-Null
Comprobar 'verificación en rojo -> no verificado' (-not (Test-CemVerificadoStaging -Sha $shaA)) 'verificado'
Write-CemVerificadoStaging -Sha $shaA -Datos @{ sha = $shaA; entorno = 'staging'; verificacion = 'ok' } | Out-Null
Comprobar 'verificación en verde -> verificado' (Test-CemVerificadoStaging -Sha $shaA) 'no verificado'

Write-Host "`n=== 3b. Registro de despliegue (plantilla) ===" -ForegroundColor Cyan
$plantilla = Join-Path $PSScriptRoot 'plantilla-registro-despliegue.md'
$campos = [regex]::Matches((Get-Content -Raw $plantilla), '\{\{([A-Z_0-9]+)\}\}') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
Comprobar 'la búsqueda de campos encuentra {{SHA}} (control de la búsqueda)' ($campos -contains 'SHA') ($campos -join ',')
$valores = @{}; foreach ($c in $campos) { $valores[$c] = "valor-$c" }
$ruta = Join-Path (Get-CemCarpetaEstado deploys) 'registro-prueba.md'
foreach ($ent in 'staging', 'produccion') { $valores['ENTORNO'] = $ent; Add-CemRegistroDespliegue -Plantilla $plantilla -Valores $valores -Ruta $ruta | Out-Null }
$texto = Get-Content -Raw $ruta
Comprobar 'no queda ningún {{CAMPO}}' ($texto -notmatch '\{\{') ($texto | Select-String '\{\{.*?\}\}' -AllMatches)
Comprobar 'una sola cabecera' (([regex]::Matches($texto, '^# Despliegue', 'Multiline')).Count -eq 1) 'cabecera repetida'
Comprobar 'una sección por entorno' ($texto -match '## staging' -and $texto -match '## produccion') 'faltan secciones'
Comprobar 'la marca interna no llega al registro' ($texto -notmatch [regex]::Escape($CemMarcaSeccion)) 'marca presente'
$sinUno = @{} + $valores; $sinUno.Remove('SHA')
$e = $null; try { Add-CemRegistroDespliegue -Plantilla $plantilla -Valores $sinUno -Ruta (Join-Path (Get-CemCarpetaEstado deploys) 'otro.md') | Out-Null } catch { $e = $_ }
Comprobar 'control: un campo sin valor hace fallar el registro' ($null -ne $e -and "$e" -match 'SHA') "$e"

Write-Host "`n=== 4. cd.ps1 se niega (sin credenciales en el proceso) ===" -ForegroundColor Cyan
$cd = Join-Path $PSScriptRoot 'cd.ps1'
$punta = "$(git -C (Split-Path -Parent $PSScriptRoot) rev-parse origin/main)".Trim()
function CorrerCd([string[]]$argumentos) {
    # Proceso hijo con las variables de Coolify y Access vacías: aunque una compuerta
    # fallara, no hay con qué llegar a la API.
    $vars = 'COOLIFY_BASE_URL', 'COOLIFY_API_TOKEN', 'CF_ACCESS_CLIENT_ID', 'CF_ACCESS_CLIENT_SECRET'
    $guardadas = @{}; foreach ($v in $vars) { $guardadas[$v] = [Environment]::GetEnvironmentVariable($v); [Environment]::SetEnvironmentVariable($v, $null) }
    try { $salida = & pwsh -NoProfile -File $cd @argumentos 2>&1 | Out-String; [pscustomobject]@{ Codigo = $LASTEXITCODE; Salida = $salida } }
    finally { foreach ($v in $vars) { [Environment]::SetEnvironmentVariable($v, $guardadas[$v]) } }
}
$r = CorrerCd @('-Entorno', 'produccion', '-Sha', $punta, '-Autorizacion', 'prueba')
Comprobar 'producción sin -Confirmar: se niega' ($r.Codigo -eq 1 -and $r.Salida -match 'exige -Confirmar') $r.Salida
$r = CorrerCd @('-Entorno', 'produccion', '-Sha', $punta, '-Confirmar', '-Autorizacion', 'prueba')
Comprobar 'producción sin staging verificado: se niega' ($r.Codigo -eq 1 -and $r.Salida -match 'no está registrada como desplegada y verificada en staging') $r.Salida
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-Autorizacion', 'prueba')
Comprobar 'staging sin ci.ps1 en verde: se niega' ($r.Codigo -eq 1 -and $r.Salida -match 'ci.ps1 no tiene un resultado verde') $r.Salida
Write-CemResultadoCi -Sha $punta -Resultado @{ sha = $punta; resultado = 'ok' } | Out-Null
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta)
Comprobar 'staging sin -Autorizacion: se niega' ($r.Codigo -eq 1 -and $r.Salida -match 'falta -Autorizacion') $r.Salida
# La constancia en verde pero sin imágenes (corrida con -SinSubirImagenes).
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-Autorizacion', 'prueba')
Comprobar 'staging con ci.ps1 en verde pero sin imágenes subidas: se niega' ($r.Codigo -eq 1 -and $r.Salida -match 'no tiene imágenes subidas' -and $r.Salida -notmatch 'faltan variables de entorno') $r.Salida
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-SoloVerificar')
Comprobar '-SoloVerificar sin imágenes en la constancia: se niega (no hay digest contra qué comparar)' ($r.Codigo -eq 1 -and $r.Salida -match 'no tiene imágenes subidas') $r.Salida
function ImagenesDe([string]$sha, [string]$digestBackend, [string]$digestFrontend) {
    [ordered]@{
        'cem-backend'  = [ordered]@{ referencia = "ghcr.io/githolan/cem-backend:$sha";  digest = $digestBackend;  plataforma = 'linux/arm64' }
        'cem-frontend' = [ordered]@{ referencia = "ghcr.io/githolan/cem-frontend:$sha"; digest = $digestFrontend; plataforma = 'linux/arm64' }
    }
}
$digB = 'sha256:' + ('1' * 64); $digF = 'sha256:' + ('2' * 64)
Write-CemResultadoCi -Sha $punta -Resultado @{ sha = $punta; resultado = 'ok'; imagenes = (ImagenesDe $punta $digB $digF) } | Out-Null
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-Autorizacion', 'prueba')
Comprobar 'control: con todo en regla (imágenes incluidas) llega a Coolify y falla por falta de credenciales' ($r.Codigo -ne 0 -and $r.Salida -match 'faltan variables de entorno' -and $r.Salida -match [regex]::Escape($digB)) $r.Salida

Write-Host "`n=== 5. Revisiones de Alembic de una SHA (git real, sin red) ===" -ForegroundColor Cyan
$repoLocal = Split-Path -Parent $PSScriptRoot
function ShaDe([string]$ref) { "$(git -C $repoLocal rev-parse --verify "$ref^{commit}")".Trim() }
# Commits fijos de main: 5b51570 agrega 0001; 9a0b4ee agrega 0002; la raíz no tiene migraciones.
$cad = Get-CemCadenaAlembicEnSha -Repo $repoLocal -Sha (ShaDe '5b51570')
Comprobar '5b51570 (migración 0001) -> cadena 0001' (($cad -join ',') -eq '0001') ($cad -join ',')
$cad = Get-CemCadenaAlembicEnSha -Repo $repoLocal -Sha (ShaDe '9a0b4ee')
Comprobar '9a0b4ee (cola) -> cadena 0001,0002' (($cad -join ',') -eq '0001,0002') ($cad -join ',')
$e = $null; try { Get-CemCadenaAlembicEnSha -Repo $repoLocal -Sha (ShaDe "$(git -C $repoLocal rev-list --max-parents=0 HEAD)") | Out-Null } catch { $e = $_ }
Comprobar 'control: el commit raíz (sin migraciones) falla' ($null -ne $e -and "$e" -match 'no hay migraciones') "$e"

function Rev([string]$r, $a) { [pscustomobject]@{ Revision = $r; Anterior = $a; Archivo = "$r.py" } }
$e = $null; try { Get-CemCadenaAlembic -Revisiones @((Rev '0001' $null), (Rev '0002' '0001'), (Rev '0003' '0001')) | Out-Null } catch { $e = $_ }
Comprobar 'dos cabezas (bifurcación) falla' ($null -ne $e -and "$e" -match 'bifurcan') "$e"
$e = $null; try { Get-CemCadenaAlembic -Revisiones @((Rev '0001' $null), (Rev '0002' '0001'), (Rev '0002' '0001')) | Out-Null } catch { $e = $_ }
Comprobar 'revisión repetida falla' ($null -ne $e -and "$e" -match 'repetida') "$e"
$e = $null; try { Get-CemCadenaAlembic -Revisiones @((Rev '0001' $null), (Rev '0003' '0002')) | Out-Null } catch { $e = $_ }
Comprobar 'revisión que no cuelga de la base falla' ($null -ne $e -and "$e" -match 'no cuelgan') "$e"
$cad = Get-CemCadenaAlembic -Revisiones @((Rev '0003' '0002'), (Rev '0001' $null), (Rev '0002' '0001'))
Comprobar 'el orden de los archivos no importa: 0001,0002,0003' (($cad -join ',') -eq '0001,0002,0003') ($cad -join ',')
$textoTupla = "revision = ""0004""`ndown_revision = (""0002"", ""0003"")`n"
$e = $null; try { ConvertFrom-CemMigracion -Texto $textoTupla -Archivo 'fusion.py' | Out-Null } catch { $e = $_ }
Comprobar 'una migración de fusión (tupla) falla' ($null -ne $e -and "$e" -match 'down_revision') "$e"

Write-Host "`n=== 6. Decisión de la copia previa ===" -ForegroundColor Cyan
$c3 = @('0001', '0002', '0003')
function Decidir([string]$version, [bool]$trabajos, [bool]$sin) { Get-CemDecisionCopiaPrevia -Cadena $c3 -VersionEntorno $version -HayTrabajos $trabajos -SinCopiaPrevia $sin }
$d = Decidir '0003' $true $false
Comprobar 'base en la cabeza -> sin-migracion' ($d.Accion -eq 'sin-migracion' -and $d.Pendientes.Count -eq 0) "$($d.Accion)"
$d = Decidir '0003' $false $false
Comprobar 'base en la cabeza y sin cem-trabajos -> sin-migracion (no hace falta copia)' ($d.Accion -eq 'sin-migracion') "$($d.Accion)"
$d = Decidir '0002' $true $false
Comprobar 'una migración nueva con cem-trabajos -> copiar 0003' ($d.Accion -eq 'copiar' -and ($d.Pendientes -join ',') -eq '0003') "$($d.Accion) $($d.Pendientes -join ',')"
$d = Decidir '0001' $true $false
Comprobar 'dos migraciones nuevas -> copiar 0002,0003' ($d.Accion -eq 'copiar' -and ($d.Pendientes -join ',') -eq '0002,0003') "$($d.Accion) $($d.Pendientes -join ',')"
$d = Decidir '0002' $false $false
Comprobar 'migración nueva sin cem-trabajos -> negar y nombra -SinCopiaPrevia' ($d.Accion -eq 'negar' -and $d.Motivo -match '-SinCopiaPrevia') "$($d.Accion) $($d.Motivo)"
$d = Decidir '0002' $false $true
Comprobar 'migración nueva sin cem-trabajos con -SinCopiaPrevia -> omitida' ($d.Accion -eq 'omitida' -and $d.Motivo -match 'no tiene cem-trabajos') "$($d.Accion) $($d.Motivo)"
$d = Decidir '0002' $true $true
Comprobar 'migración nueva con cem-trabajos y -SinCopiaPrevia -> omitida' ($d.Accion -eq 'omitida') "$($d.Accion)"
$e = $null; try { Decidir '0009' $true $false | Out-Null } catch { $e = $_ }
Comprobar 'base en una revisión que la SHA no conoce -> falla' ($null -ne $e -and "$e" -match "'0009'") "$e"
$e = $null; try { Decidir '' $true $false | Out-Null } catch { $e = $_ }
Comprobar 'alembic_version vacía -> falla' ($null -ne $e -and "$e" -match 'vacía') "$e"

Write-Host "`n=== 7. Resultado de la copia y fila del registro ===" -ForegroundColor Cyan
$suma = 'c' * 64
$salidaBuena = "aviso en stderr`n{""objetos"": [""diarias/2026-10-14/cem-20261014T150000Z.dump.age""], ""bytes"": 48622, ""sha256"": ""$suma"", ""tablas"": 10, ""filas"": 7}`n"
$copiaLeida = Read-CemResultadoCopia -Salida $salidaBuena
Comprobar 'lee la última línea JSON de la copia' ($copiaLeida.Sha256 -eq $suma -and $copiaLeida.Bytes -eq 48622 -and $copiaLeida.Objetos.Count -eq 1) ($copiaLeida | ConvertTo-Json -Compress)
foreach ($caso in @(
        @('sin salida', ''),
        @('salida que no es JSON', 'Traceback: ErrorCopia: la restauración no coincide'),
        @('sin objetos', "{""objetos"": [], ""bytes"": 1, ""sha256"": ""$suma"", ""tablas"": 1, ""filas"": 0}"),
        @('SHA-256 corto', "{""objetos"": [""x""], ""bytes"": 1, ""sha256"": ""abc"", ""tablas"": 1, ""filas"": 0}"),
        @('0 tablas', "{""objetos"": [""x""], ""bytes"": 1, ""sha256"": ""$suma"", ""tablas"": 0, ""filas"": 0}"))) {
    $e = $null; try { Read-CemResultadoCopia -Salida $caso[1] | Out-Null } catch { $e = $_ }
    Comprobar "control: copia con $($caso[0]) -> falla" ($null -ne $e) 'aceptada'
}
$texto = Format-CemCopiaPrevia -Decision (Decidir '0002' $true $false) -Copia $copiaLeida -Bucket 'cem-copias-seguridad-staging'
Comprobar 'registro con copia: objeto y SHA-256' ($texto -match [regex]::Escape('diarias/2026-10-14/cem-20261014T150000Z.dump.age') -and $texto -match $suma) $texto
$texto = Format-CemCopiaPrevia -Decision (Decidir '0003' $true $false)
Comprobar 'registro sin migración: lo dice' ($texto -match 'sin migraciones nuevas') $texto
$texto = Format-CemCopiaPrevia -Decision (Decidir '0002' $false $true)
Comprobar 'registro con -SinCopiaPrevia: NO HECHA' ($texto -match 'NO HECHA, por -SinCopiaPrevia') $texto
$e = $null; try { Format-CemCopiaPrevia -Decision (Decidir '0002' $true $false) | Out-Null } catch { $e = $_ }
Comprobar 'control: decisión copiar sin resultado de copia -> falla' ($null -ne $e) 'aceptada'
$e = $null; try { Format-CemCopiaPrevia -Decision (Decidir '0002' $false $false) | Out-Null } catch { $e = $_ }
Comprobar 'control: decisión negar no llega al registro' ($null -ne $e) 'aceptada'
Comprobar 'el programa de la copia no lleva comillas (pasa por ssh y sh)' ($CemProgramaCopiaPrevia -notmatch "[`"']") $CemProgramaCopiaPrevia

Write-Host "`n=== 8. cd.ps1: -SinCopiaPrevia es un parámetro ===" -ForegroundColor Cyan
$parametros = (Get-Command $cd).Parameters
Comprobar 'cd.ps1 acepta -SinCopiaPrevia (switch)' ($parametros.ContainsKey('SinCopiaPrevia') -and $parametros['SinCopiaPrevia'].SwitchParameter) ($parametros.Keys -join ',')
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-Autorizacion', 'prueba', '-SinCopiaPrevia')
Comprobar 'con -SinCopiaPrevia las demás compuertas siguen valiendo (llega a credenciales y falla)' ($r.Codigo -ne 0 -and $r.Salida -match 'faltan variables de entorno') $r.Salida

Write-Host "`n=== 9. Imágenes en ghcr.io: constancia, Coolify y contenedor ===" -ForegroundColor Cyan
. (Join-Path $PSScriptRoot 'deploy-map.ps1')
function Falla([scriptblock]$b) { try { & $b | Out-Null; $null } catch { "$_" } }

# 9a. Constancia de ci.ps1 con imágenes
$imgs = @('cem-backend', 'cem-frontend')
$d = Get-CemImagenesAprobadas -Sha $punta -Imagenes $imgs
Comprobar 'control: constancia con las dos imágenes -> devuelve sus digests' ($d['cem-backend'] -eq $digB -and $d['cem-frontend'] -eq $digF) ($d | ConvertTo-Json -Compress)
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok' } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia verde sin digests -> falla y nombra -SinSubirImagenes' ($e -match 'no tiene imágenes subidas' -and $e -match '-SinSubirImagenes') $e
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'falla'; imagenes = (ImagenesDe $shaA $digB $digF) } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia en rojo aunque tenga digests -> falla' ($e -match 'no tiene un resultado verde') $e
$sinFront = ImagenesDe $shaA $digB $digF; $sinFront.Remove('cem-frontend')
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok'; imagenes = $sinFront } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia sin cem-frontend -> falla y la nombra' ($e -match 'no tiene la imagen cem-frontend') $e
$d = Get-CemImagenesAprobadas -Sha $shaA -Imagenes @('cem-backend')
Comprobar 'control: la misma constancia sirve si el entorno solo pide cem-backend' ($d['cem-backend'] -eq $digB) ($d | ConvertTo-Json -Compress)
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok'; imagenes = (ImagenesDe $shaB $digB $digF) } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia con la imagen etiquetada con otra SHA -> falla' ($e -match 'se esperaba' -and $e -match $shaB) $e
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok'; imagenes = (ImagenesDe $shaA 'sha256:abc' $digF) } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia con un digest malformado -> falla' ($e -match 'digest inválido') $e
$amd = ImagenesDe $shaA $digB $digF; $amd['cem-backend'].plataforma = 'linux/amd64'
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok'; imagenes = $amd } | Out-Null
$e = Falla { Get-CemImagenesAprobadas -Sha $shaA -Imagenes $imgs }
Comprobar 'constancia con una imagen linux/amd64 -> falla' ($e -match 'linux/amd64') $e

# 9b. Token de ghcr.io: nunca aparece en el mensaje
$e = Falla { Assert-CemTokenRegistro -Token $null }
Comprobar 'sin CEM_GHCR_TOKEN -> falla y nombra la variable' ($e -match 'falta la variable de usuario CEM_GHCR_TOKEN') $e
$fino = 'github_pat_' + ('Z' * 30)
$e = Falla { Assert-CemTokenRegistro -Token $fino }
Comprobar 'token de grano fino -> falla' ($e -match 'no es un token clásico') $e
Comprobar 'control: el mensaje no contiene el token' ($e -notmatch 'Z{10}') $e
$e = Falla { Assert-CemTokenRegistro -Token ('xghp_' + ('Y' * 30)) }
Comprobar 'ghp_ en el medio no cuenta como clásico' ($e -match 'no es un token clásico' -and $e -notmatch 'Y{10}') $e
$e = Falla { Assert-CemTokenRegistro -Token ('ghp_' + ('X' * 36)) }
Comprobar 'control: un token clásico pasa' ($null -eq $e) $e

# 9c. Manifiesto de ghcr.io (salida de imagetools inspect --format '{{json .Manifest}}')
$digI = 'sha256:' + ('3' * 64)
function Manifiesto([string]$tipo, [string[]]$plataformas) {
    @{ mediaType = $tipo; digest = $digI; manifests = @($plataformas | ForEach-Object { $p = $_ -split '/'; @{ digest = 'sha256:' + ('4' * 64); platform = @{ os = $p[0]; architecture = $p[1] } } }) } | ConvertTo-Json -Depth 5
}
$indice = 'application/vnd.oci.image.index.v1+json'
$r = Read-CemManifiestoImagen -Json (Manifiesto $indice @('linux/arm64', 'unknown/unknown')) -Referencia 'x'
Comprobar 'control: índice con linux/arm64 y atestación -> devuelve el digest del índice' ($r -eq $digI) $r
$e = Falla { Read-CemManifiestoImagen -Json (Manifiesto $indice @('linux/amd64', 'unknown/unknown')) -Referencia 'x' }
Comprobar 'índice sin linux/arm64 -> falla' ($e -match 'no tiene linux/arm64') $e
$e = Falla { Read-CemManifiestoImagen -Json (Manifiesto 'application/vnd.oci.image.manifest.v1+json' @()) -Referencia 'x' }
Comprobar 'manifiesto suelto (no índice) -> falla' ($e -match 'se esperaba un índice') $e
$e = Falla { Read-CemManifiestoImagen -Json 'ERROR: unauthorized' -Referencia 'x' }
Comprobar 'salida que no es JSON (p. ej. sin permiso) -> falla' ($e -match 'no devolvió JSON') $e

# 9d. Aplicación en Coolify: tipo dockerimage e imagen de su función
$apps = @(Get-CemAplicaciones -Entorno staging)
$back = $apps | Where-Object Funcion -eq 'backend'; $front = $apps | Where-Object Funcion -eq 'frontend'
$plan = $apps | Where-Object Funcion -eq 'planificador'
function Leida([string]$tipo, [string]$imagen) { [pscustomobject]@{ build_pack = $tipo; docker_registry_image_name = $imagen; docker_registry_image_tag = $punta } }
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/githolan/cem-backend') -App $back
Comprobar 'control: backend dockerimage con cem-backend -> sin problemas' ($p.Count -eq 0) ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/githolan/cem-backend') -App $plan
Comprobar 'control: planificador dockerimage con cem-backend -> sin problemas' ($p.Count -eq 0) ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/githolan/cem-frontend') -App $front
Comprobar 'control: frontend dockerimage con cem-frontend -> sin problemas' ($p.Count -eq 0) ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerfile' $null) -App $back
Comprobar 'app que no es dockerimage (la construye Coolify) -> problema' ($p -match "tipo 'dockerfile'") ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/githolan/cem-backend') -App $front
Comprobar 'frontend apuntando a cem-backend -> problema' ($p -match 'se espera .ghcr.io/githolan/cem-frontend') ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/githolan/cem-frontend') -App $plan
Comprobar 'planificador apuntando a cem-frontend -> problema' ($p -match 'se espera .ghcr.io/githolan/cem-backend') ($p -join '; ')
$p = Get-CemProblemasAppCoolify -Leida (Leida 'dockerimage' 'ghcr.io/otro/cem-backend') -App $back
Comprobar 'backend con la imagen de otro dueño -> problema' ($p.Count -eq 1) ($p -join '; ')

# 9e. El mapa: cada app con la imagen de su función; un mapa con la imagen cambiada falla
foreach ($ent in $CemEntornos.Keys) {
    foreach ($a in Get-CemAplicaciones -Entorno $ent) {
        Comprobar "mapa $ent/$($a.Nombre): imagen $($a.Imagen) = la de su función" ($a.Imagen -eq $CemImagenPorFuncion[$a.Funcion] -and $a.Imagen) $a.Imagen
    }
}
$original = $CemEntornos.staging.Aplicaciones.frontend.Imagen
$CemEntornos.staging.Aplicaciones.frontend.Imagen = 'cem-backend'
$e = Falla { Get-CemAplicaciones -Entorno staging }
$CemEntornos.staging.Aplicaciones.frontend.Imagen = $original
Comprobar 'control: un mapa con el frontend en cem-backend falla al leerse' ($e -match 'cem-frontend-staging' -and $e -match "usa 'cem-frontend'") $e

# 9f. Contenedor en la VM frente a la constancia
$labelsOk = "{""$CemEtiquetaRevision"":""$punta"",""otra"":""x""}"
function Contenedor([string]$imagenConfig, [string]$labels, [string[]]$repoDigests, $app = $back, [string]$digest = $digB) {
    Get-CemProblemasContenedor -App $app -Sha $punta -Digest $digest -ImagenConfig $imagenConfig -Etiquetas $labels -RepoDigests (ConvertTo-Json @($repoDigests) -Compress)
}
$refB = "ghcr.io/githolan/cem-backend:$punta"
$p = Contenedor "$refB`n" $labelsOk @("ghcr.io/githolan/cem-backend@$digB")
Comprobar 'control: imagen, etiqueta y digest correctos -> sin problemas' ($p.Count -eq 0) ($p -join '; ')
$p = Contenedor $refB $labelsOk @("ghcr.io/githolan/cem-backend@sha256:$('9' * 64)")
Comprobar 'digest distinto al de la constancia -> problema' ($p.Count -eq 1 -and $p[0] -match 'no es ghcr.io/githolan/cem-backend@') ($p -join '; ')
$p = Contenedor $refB $labelsOk @("ghcr.io/githolan/cem-frontend@$digB")
Comprobar 'mismo digest pero de otra imagen -> problema' ($p.Count -eq 1) ($p -join '; ')
$p = Contenedor "ghcr.io/githolan/cem-backend:$shaB" $labelsOk @("ghcr.io/githolan/cem-backend@$digB")
Comprobar 'contenedor con la etiqueta de otra SHA -> problema' ($p.Count -eq 1 -and $p[0] -match 'corre') ($p -join '; ')
$p = Contenedor $refB "{""$CemEtiquetaRevision"":""$shaB""}" @("ghcr.io/githolan/cem-backend@$digB")
Comprobar 'etiqueta revision de otra SHA -> problema' ($p.Count -eq 1 -and $p[0] -match $CemEtiquetaRevision) ($p -join '; ')
$p = Contenedor $refB 'null' @("ghcr.io/githolan/cem-backend@$digB")
Comprobar 'contenedor sin etiquetas -> problema' ($p.Count -eq 1) ($p -join '; ')
$p = Contenedor $refB $labelsOk @()
Comprobar 'imagen sin RepoDigests (construida en la VM) -> problema' ($p.Count -eq 1) ($p -join '; ')
$p = Contenedor $refB $labelsOk @("ghcr.io/githolan/cem-backend@$digB") $front $digF
Comprobar 'frontend corriendo la imagen de backend -> problema' ($p.Count -ge 1 -and $p[0] -match 'cem-frontend') ($p -join '; ')

Write-Host "`n=== 10. Cobertura de PA en la constancia de ci.ps1 ===" -ForegroundColor Cyan
$jsonPa = '{"conjunto": "P0-piloto", "total": 3, "cubiertas": {"PA15": ["backend/tests/test_rls.py"], "PA02": ["backend/tests/test_reparto.py"]}, "faltan": ["PA04"], "fuera_del_conjunto": {"PA40": ["frontend/e2e/x.e2e.ts"]}}'
$c = Read-CemCoberturaPa -Json $jsonPa
Write-CemResultadoCi -Sha $shaA -Resultado @{ sha = $shaA; resultado = 'ok'; cobertura_pa = $c } | Out-Null
$leida = (Read-CemResultadoCi -Sha $shaA).cobertura_pa
Comprobar 'la constancia guarda PA15 cubierta con su archivo' ($leida.cubiertas.PA15 -contains 'backend/tests/test_rls.py') ($leida | ConvertTo-Json -Depth 5)
Comprobar 'la constancia guarda las faltantes' (@($leida.faltan) -contains 'PA04' -and $leida.cantidad_cubiertas -eq 2) ($leida | ConvertTo-Json -Depth 5)
Comprobar 'control: PA99 (inexistente) no figura como cubierta' ($null -eq $leida.cubiertas.PA99 -and $null -eq $leida.fuera_del_conjunto.PA99) ($leida | ConvertTo-Json -Depth 5)
Comprobar 'control: una faltante no figura como cubierta' ($null -eq $leida.cubiertas.PA04) ($leida | ConvertTo-Json -Depth 5)
Comprobar 'una constancia con faltantes sigue en verde' (Test-CemCiAprobado -Sha $shaA) 'no aprobado'
foreach ($caso in @(
        @('salida vacía', ''),
        @('texto que no es JSON', 'Traceback (most recent call last):'),
        @('una lista en vez de un objeto', '[1, 2]'),
        @('sin el campo faltan', '{"conjunto": "P0-piloto", "total": 1, "cubiertas": {"PA02": ["a"]}, "fuera_del_conjunto": {}}'),
        @('cubiertas + faltan != total', '{"conjunto": "P0-piloto", "total": 25, "cubiertas": {"PA02": ["a"]}, "faltan": [], "fuera_del_conjunto": {}}'),
        @('nombre de PA inválido', '{"conjunto": "P0-piloto", "total": 1, "cubiertas": {"PA2": ["a"]}, "faltan": [], "fuera_del_conjunto": {}}'))) {
    $e = $null; try { Read-CemCoberturaPa -Json $caso[1] | Out-Null } catch { $e = $_ }
    Comprobar "informe roto ($($caso[0])) -> falla" ($null -ne $e) 'aceptado'
}

Write-Host "`n=== 11. Proyecto de compose por corrida de ci.ps1 ===" -ForegroundColor Cyan
$a = New-CemIdCorrida -Sha7 'abc1234'
$b = New-CemIdCorrida -Sha7 'abc1234'
Comprobar 'el nombre del proyecto sigue el formato' ("$CemPrefijoProyectoCi$a" -match $CemFormatoProyectoCi) $a
Comprobar 'dos corridas del mismo commit tienen proyectos distintos' ($a -ne $b) "$a = $b"
Comprobar "lleva el PID de la corrida ($PID)" ($a -match "^abc1234-$PID-") $a
$e = $null; try { New-CemIdCorrida -Sha7 'ABC' | Out-Null } catch { $e = $_ }
Comprobar 'un sha7 inválido se niega' ($null -ne $e) 'aceptado'
$vivos = @(100, 200)
$esVivo = { param($p) $vivos -contains $p }
$proyectos = @('cem-ci-abc1234-100-aaaaaa', 'cem-ci-abc1234-300-bbbbbb', 'cem-ci-abc1234-300-bbbbbb',
               'cem-ci', 'cem-h3d', 'cem-ci-abc1234-x-cccccc', 'cem-ci-def5678-200-dddddd')
$abandonados = @(Get-CemProyectosCiAbandonados -Proyectos $proyectos -ProcesoVivo $esVivo)
Comprobar 'solo la corrida de un proceso muerto, una vez' (($abandonados -join ',') -eq 'cem-ci-abc1234-300-bbbbbb') ($abandonados -join ',')
Comprobar 'control: con todos vivos no hay abandonadas' (@(Get-CemProyectosCiAbandonados -Proyectos $proyectos -ProcesoVivo { $true }).Count -eq 0) 'hay'
Comprobar "control: el proceso de esta prueba ($PID) se ve vivo" (@(Get-CemProyectosCiAbandonados -Proyectos @("cem-ci-abc1234-$PID-eeeeee")).Count -eq 0) 'se tomó por abandonada'

Remove-Item Env:\CEM_DESPLIEGUE_ESTADO
if ((Split-Path -Leaf $raiz) -like "$prefijo*" -and $raiz.StartsWith([IO.Path]::GetTempPath())) { Remove-Item -Recurse -Force $raiz }

Write-Host ""
Write-Host "BIEN=$bien  MAL=$mal" -ForegroundColor $(if ($mal -eq 0) { 'Green' } else { 'Red' })
exit $(if ($mal -eq 0) { 0 } else { 1 })
