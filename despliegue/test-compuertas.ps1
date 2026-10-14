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
$r = CorrerCd @('-Entorno', 'staging', '-Sha', $punta, '-Autorizacion', 'prueba')
Comprobar 'control: con todo en regla llega a Coolify y falla por falta de credenciales' ($r.Codigo -ne 0 -and $r.Salida -match 'faltan variables de entorno') $r.Salida

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

Remove-Item Env:\CEM_DESPLIEGUE_ESTADO
if ((Split-Path -Leaf $raiz) -like "$prefijo*" -and $raiz.StartsWith([IO.Path]::GetTempPath())) { Remove-Item -Recurse -Force $raiz }

Write-Host ""
Write-Host "BIEN=$bien  MAL=$mal" -ForegroundColor $(if ($mal -eq 0) { 'Green' } else { 'Red' })
exit $(if ($mal -eq 0) { 0 } else { 1 })
