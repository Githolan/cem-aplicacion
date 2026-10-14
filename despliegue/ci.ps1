# ==============================================================================
# Consorcio en Mano - integración local (documento 13, §18.2; plan H1, semana 2)
# ==============================================================================
# Corre la suite oficial completa sobre UN commit, desde una copia LF de ese
# commit (git archive con core.autocrlf=false), nunca desde la carpeta de trabajo:
#
#   0. copia: ningún .sh con CR (con CRLF la imagen no arranca: trampa de Windows
#      con core.autocrlf=true)
#   1. backend: pytest en Linux contra PostgreSQL 18 y Redis 7 reales
#      (backend/pruebas/compose.yml, proyecto de compose propio 'cem-ci')
#   2. ruff check + ruff format --check, en el mismo contenedor
#   3. contratos: pytest en /src/contratos, en el mismo contenedor
#   4. frontend: pnpm install --frozen-lockfile, lint, test, build
#   5. imagen del frontend: docker build + frontend/scripts/probar-imagen.sh
#
# Código de salida 0 = todo verde. Distinto de 0 = alguna etapa falló.
# Deja el resultado en <estado>\ci\<sha>.json (ver compuertas.ps1), que cd.ps1
# exige antes de desplegar esa SHA, y la salida completa en <estado>\ci\<sha>.log.
#
# Uso (PowerShell 7, desde cualquier carpeta):
#   pwsh despliegue/ci.ps1                     prueba HEAD
#   pwsh despliegue/ci.ps1 -Sha 49ac99f        prueba ese commit
#   pwsh despliegue/ci.ps1 -ConservarCopia     no borra la copia temporal
# Requiere: git, Docker en marcha, pnpm 10 y Node 22, bash de Git for Windows
# (o $env:CEM_BASH con la ruta de otro bash que no sea el de WSL).
# ==============================================================================

param(
    [string]$Sha = 'HEAD',
    [switch]$ConservarCopia
)

$ErrorActionPreference = 'Stop'
# Los comandos nativos escriben avisos en stderr: se decide por su código de salida.
$PSNativeCommandUseErrorActionPreference = $false

. (Join-Path $PSScriptRoot 'compuertas.ps1')

$ProyectoCompose   = 'cem-ci'
$ServicioPruebas   = 'cem-backend-pruebas'
$ImagenFrontend    = 'cem-frontend-ci'
$ComposeRelativo   = 'backend/pruebas/compose.yml'
$PruebaImagen      = 'frontend/scripts/probar-imagen.sh'
$PrefijoCopia      = 'cem-ci-'
# Un .sh que se sabe que existe: si la búsqueda no lo encuentra, la búsqueda está mal.
$ShConocido        = 'frontend/scripts/probar-imagen.sh'

$repo = Split-Path -Parent $PSScriptRoot
$shaCompleta = (git -C $repo rev-parse --verify "$Sha^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $shaCompleta) { throw "no existe el commit '$Sha' en $repo" }
$shaCompleta = $shaCompleta.Trim()
Assert-CemShaCompleta $shaCompleta
$sha7 = $shaCompleta.Substring(0, 7)

$log = Join-Path (Get-CemCarpetaEstado ci) "$shaCompleta.log"
"ci.ps1 sobre $shaCompleta, $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" | Out-File -Encoding utf8 $log

$etapas = [ordered]@{}
$inicio = Get-Date

function Escribir([string]$m, [string]$color = 'White') {
    Write-Host $m -ForegroundColor $color
    $m | Out-File -Append -Encoding utf8 $log
}
function Etapa([string]$nombre) { Escribir "`n=== $nombre ===" 'Cyan' }

# Corre un comando nativo, copia su salida al registro y anota el resultado de la etapa.
function Correr([string]$etapa, [scriptblock]$comando) {
    & $comando 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append | Out-Host
    $codigo = $LASTEXITCODE
    $script:etapas[$etapa] = if ($codigo -eq 0) { 'ok' } else { "falla (código $codigo)" }
    if ($codigo -eq 0) { Escribir "  $etapa`: ok" 'Green' } else { Escribir "  $etapa`: FALLA (código $codigo)" 'Red' }
    $codigo -eq 0
}

function Get-BashDeGit {
    if ($env:CEM_BASH) { return $env:CEM_BASH }
    $git = (Get-Command git -ErrorAction Stop).Source
    $raizGit = Split-Path -Parent (Split-Path -Parent $git)
    $bash = Join-Path $raizGit 'bin\bash.exe'
    if (-not (Test-Path $bash)) { throw "no encontré el bash de Git for Windows en $bash; definí CEM_BASH" }
    $bash
}

function Guardar-Resultado {
    $ok = ($etapas.Count -gt 0) -and -not ($etapas.Values | Where-Object { $_ -ne 'ok' })
    $ruta = Write-CemResultadoCi -Sha $shaCompleta -Resultado @{
        sha        = $shaCompleta
        resultado  = if ($ok) { 'ok' } else { 'falla' }
        etapas     = $etapas
        fecha_utc  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        minutos    = [math]::Round(((Get-Date) - $inicio).TotalMinutes, 1)
        equipo     = $env:COMPUTERNAME
        registro   = $log
    }
    Escribir "`nResultado: $(if ($ok) { 'VERDE' } else { 'ROJO' }) sobre $shaCompleta" $(if ($ok) { 'Green' } else { 'Red' })
    Escribir "Constancia: $ruta"
    $ok
}

$copia = Join-Path ([IO.Path]::GetTempPath()) "$PrefijoCopia$sha7-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$composeArgs = @('compose', '-p', $ProyectoCompose, '-f', (Join-Path $copia $ComposeRelativo))
$ok = $false
$composeUsado = $false
try {
    Escribir "CEM - integración local sobre $shaCompleta" 'White'

    # --- 0. copia LF del commit -------------------------------------------------
    Etapa 'Copia LF del commit'
    New-Item -ItemType Directory -Path $copia | Out-Null
    $tar = "$copia.tar"
    git -C $repo -c core.autocrlf=false -c core.eol=lf archive --format=tar -o $tar $shaCompleta
    if ($LASTEXITCODE -ne 0) { throw "git archive falló" }
    tar -xf $tar -C $copia
    if ($LASTEXITCODE -ne 0) { throw "tar no pudo extraer la copia" }
    Remove-Item $tar
    $shs = @(Get-ChildItem -Path $copia -Recurse -File -Filter '*.sh')
    $relativos = $shs | ForEach-Object { [IO.Path]::GetRelativePath($copia, $_.FullName).Replace('\', '/') }
    if ($relativos -notcontains $ShConocido) { throw "la búsqueda de .sh no encontró ${ShConocido}: la comprobación de CR no sería válida" }
    $conCr = $shs | Where-Object { ([IO.File]::ReadAllBytes($_.FullName)) -contains 13 }
    if ($conCr) {
        $etapas['copia-lf'] = 'falla (CR en .sh)'
        $conCr | ForEach-Object { Escribir "  CR en $([IO.Path]::GetRelativePath($copia, $_.FullName))" 'Red' }
        throw "hay archivos .sh con CR en el commit $sha7; la imagen no arrancaría"
    }
    $etapas['copia-lf'] = 'ok'
    Escribir "  copia en $copia; $($shs.Count) archivos .sh sin CR" 'Green'

    # --- 1-3. backend, ruff y contratos en Linux ------------------------------
    Etapa 'Backend (pytest, PostgreSQL 18 y Redis 7)'
    $composeUsado = $true
    Correr 'backend' { docker @composeArgs run --rm --build $ServicioPruebas } | Out-Null
    Etapa 'ruff'
    Correr 'ruff' { docker @composeArgs run --rm $ServicioPruebas sh -c 'ruff check .. && ruff format --check ..' } | Out-Null
    Etapa 'Contratos (pytest)'
    Correr 'contratos' { docker @composeArgs run --rm $ServicioPruebas sh -c 'cd /src/contratos && pytest' } | Out-Null

    # --- 4. frontend ------------------------------------------------------------
    Etapa 'Frontend (pnpm)'
    Push-Location (Join-Path $copia 'frontend')
    try {
        if (Correr 'frontend-install' { pnpm install --frozen-lockfile }) {
            Correr 'frontend-lint' { pnpm lint } | Out-Null
            Correr 'frontend-test' { pnpm test } | Out-Null
            Correr 'frontend-build' { pnpm build } | Out-Null
        }
    } finally { Pop-Location }

    # --- 5. imagen del frontend -----------------------------------------------
    Etapa 'Imagen del frontend'
    if (Correr 'frontend-imagen-build' { docker build -f (Join-Path $copia 'frontend/Dockerfile') -t $ImagenFrontend $copia }) {
        $bash = Get-BashDeGit
        $env:MSYS_NO_PATHCONV = '1'
        Push-Location $copia
        try { Correr 'frontend-imagen-prueba' { & $bash $PruebaImagen $ImagenFrontend } | Out-Null }
        finally { Pop-Location; Remove-Item Env:\MSYS_NO_PATHCONV }
    }
}
catch {
    Escribir "ERROR: $($_.Exception.Message)" 'Red'
    $etapas['error'] = $_.Exception.Message
}
finally {
    # Solo si esta corrida levantó el proyecto: un `down` ajeno cortaría otra corrida.
    if ($composeUsado) {
        docker @composeArgs down --remove-orphans 2>&1 | Out-File -Append -Encoding utf8 $log
    }
    $ok = Guardar-Resultado
    $tmp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $esCopia = $copia.StartsWith($tmp) -and ((Split-Path -Leaf $copia) -like "$PrefijoCopia$sha7-*")
    if ($ConservarCopia) { Escribir "Copia conservada en $copia" }
    elseif ($esCopia -and (Test-Path $copia)) { Remove-Item -Recurse -Force $copia }
}
exit $(if ($ok) { 0 } else { 1 })
