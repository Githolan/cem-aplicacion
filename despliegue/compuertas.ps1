# ==============================================================================
# Consorcio en Mano - compuertas compartidas por ci.ps1 y cd.ps1
# ==============================================================================
# Estado local (fuera del repositorio): resultado de ci.ps1 por SHA, SHAs
# verificadas en staging y registros de despliegue generados. Carpeta:
#   $env:CEM_DESPLIEGUE_ESTADO   si está definida
#   %LOCALAPPDATA%\cem-despliegue   si no
#   ci\<sha>.json                 resultado de ci.ps1 sobre esa SHA
#   verificado-staging\<sha>.json SHA desplegada y verificada en staging por cd.ps1
#   deploys\AAAA-MM-DD-<sha7>.md  registro de despliegue (§18.6), para copiar al repo CEM
# ==============================================================================

$CemCarpetaEstadoPorDefecto = Join-Path $env:LOCALAPPDATA 'cem-despliegue'

# Ventana de actualización automática de Coolify (documento 13, §18.3, regla 7):
# no se despliega entre estas horas UTC, ambas incluidas.
$CemVentanaProhibidaDesde = [timespan]'02:45:00'
$CemVentanaProhibidaHasta = [timespan]'03:10:59'

function Get-CemCarpetaEstado {
    param([ValidateSet('', 'ci', 'verificado-staging', 'deploys')][string]$Sub = '')
    $raiz = if ($env:CEM_DESPLIEGUE_ESTADO) { $env:CEM_DESPLIEGUE_ESTADO } else { $CemCarpetaEstadoPorDefecto }
    $ruta = if ($Sub) { Join-Path $raiz $Sub } else { $raiz }
    New-Item -ItemType Directory -Force -Path $ruta | Out-Null
    $ruta
}

# $true si la hora UTC dada cae en la ventana prohibida.
function Test-CemVentanaProhibida {
    param([Parameter(Mandatory)][datetime]$HoraUtc)
    $t = $HoraUtc.TimeOfDay
    ($t -ge $CemVentanaProhibidaDesde) -and ($t -le $CemVentanaProhibidaHasta)
}

function Assert-CemShaCompleta {
    param([string]$Sha)
    if ($Sha -notmatch '^[0-9a-f]{40}$') { throw "se esperaba una SHA completa de 40 caracteres, llegó '$Sha'" }
}

function Write-CemResultadoCi {
    param([Parameter(Mandatory)][string]$Sha, [Parameter(Mandatory)][hashtable]$Resultado)
    Assert-CemShaCompleta $Sha
    $ruta = Join-Path (Get-CemCarpetaEstado ci) "$Sha.json"
    $Resultado | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 $ruta
    $ruta
}

# Devuelve el resultado de ci.ps1 para la SHA, o $null si no hay.
function Read-CemResultadoCi {
    param([Parameter(Mandatory)][string]$Sha)
    Assert-CemShaCompleta $Sha
    $ruta = Join-Path (Get-CemCarpetaEstado ci) "$Sha.json"
    if (-not (Test-Path $ruta)) { return $null }
    Get-Content -Raw $ruta | ConvertFrom-Json
}

# $true solo si ci.ps1 terminó en verde sobre exactamente esa SHA.
function Test-CemCiAprobado {
    param([Parameter(Mandatory)][string]$Sha)
    $r = Read-CemResultadoCi -Sha $Sha
    ($null -ne $r) -and ($r.sha -eq $Sha) -and ($r.resultado -eq 'ok')
}

function Write-CemVerificadoStaging {
    param([Parameter(Mandatory)][string]$Sha, [Parameter(Mandatory)][hashtable]$Datos)
    Assert-CemShaCompleta $Sha
    $ruta = Join-Path (Get-CemCarpetaEstado verificado-staging) "$Sha.json"
    $Datos | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 $ruta
    $ruta
}

# Completa la plantilla del registro de despliegue (§18.6). La plantilla tiene una
# cabecera y, después de $CemMarcaSeccion, la sección de un entorno. Si el archivo
# no existe se escribe la cabecera; la sección del entorno se agrega siempre al
# final. Falla si queda algún {{CAMPO}} sin completar.
$CemMarcaSeccion = '<!-- seccion-por-entorno -->'
function Add-CemRegistroDespliegue {
    param([Parameter(Mandatory)][string]$Plantilla, [Parameter(Mandatory)][hashtable]$Valores, [Parameter(Mandatory)][string]$Ruta)
    $partes = (Get-Content -Raw -Encoding utf8 $Plantilla) -split [regex]::Escape($CemMarcaSeccion), 2
    if ($partes.Count -ne 2) { throw "la plantilla $Plantilla no tiene la marca $CemMarcaSeccion" }
    function Completar([string]$texto) {
        foreach ($k in $Valores.Keys) { $texto = $texto.Replace("{{$k}}", "$($Valores[$k])") }
        if ($texto -match '\{\{[A-Z_0-9]+\}\}') { throw "la plantilla tiene un campo sin completar: $($Matches[0])" }
        $texto
    }
    $cabecera = (Completar $partes[0]).TrimEnd() + "`n"
    $seccion = "`n" + (Completar $partes[1]).Trim() + "`n"
    if (-not (Test-Path $Ruta)) { $cabecera | Out-File -Encoding utf8NoBOM -NoNewline $Ruta }
    $seccion | Out-File -Encoding utf8NoBOM -NoNewline -Append $Ruta
    $Ruta
}

# $true solo si cd.ps1 desplegó y verificó esa SHA en staging.
function Test-CemVerificadoStaging {
    param([Parameter(Mandatory)][string]$Sha)
    Assert-CemShaCompleta $Sha
    $ruta = Join-Path (Get-CemCarpetaEstado verificado-staging) "$Sha.json"
    if (-not (Test-Path $ruta)) { return $false }
    $d = Get-Content -Raw $ruta | ConvertFrom-Json
    ($d.sha -eq $Sha) -and ($d.entorno -eq 'staging') -and ($d.verificacion -eq 'ok')
}
