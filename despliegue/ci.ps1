# ==============================================================================
# Consorcio en Mano - integración local (documento 13, §18.2; plan H1, semana 2)
# ==============================================================================
# Corre la suite oficial completa sobre UN commit, desde una copia LF de ese
# commit (git archive con core.autocrlf=false), nunca desde la carpeta de trabajo:
#
#   0. copia: ningún .sh con CR (con CRLF la imagen no arranca: trampa de Windows
#      con core.autocrlf=true). La copia va a %TEMP%\cem-ci-copia-<sha7>-<pid>-<azar>
#      (compuertas.ps1) y se borra al final; al empezar se borran las de corridas
#      cortadas cuyo proceso ya no existe (las del formato viejo, sin PID, no se tocan)
#   1. backend: pytest en Linux contra PostgreSQL 18 y Redis 7 reales
#      (backend/pruebas/compose.yml, proyecto de compose propio de la corrida,
#      cem-ci-<sha7>-<pid>-<azar>: dos corridas a la vez no se pisan y cada una baja solo
#      lo suyo; al empezar se bajan los de corridas cortadas cuyo proceso ya no existe)
#   2. ruff check + ruff format --check, en el mismo contenedor
#   3. contratos: pytest en /src/contratos, en el mismo contenedor
#   3b. cobertura de PA: `python -m scripts.cobertura_pa --json` en el mismo
#      contenedor (documento 14, §11); las PA del P0-piloto cubiertas y faltantes
#      van a la constancia (campo cobertura_pa) y al .log. Que falten PA no es
#      rojo (sin --estricto); que el script falle o no devuelva el informe, sí
#   3c. seguridad (documento 13, §18.2): pip-audit sobre lo que instala la imagen de
#      cem-backend (con sus dependencias indirectas), pnpm audit --prod desde alta, y
#      gitleaks sobre el árbol y el historial reciente. Cualquier hallazgo es rojo; las
#      excepciones de gitleaks van, acotadas y comentadas, en .gitleaks.toml
#   4. frontend: pnpm install --frozen-lockfile, lint, test, build
#   5. imagen del frontend: docker build + frontend/scripts/probar-imagen.sh
#   6. extremo a extremo: pnpm e2e (frontend/scripts/probar-extremo-a-extremo.sh:
#      Playwright contra las imágenes de cem-frontend y cem-backend con Redis, en
#      una red de Docker propia); al final se borran sus contenedores y su red,
#      aunque falle, y se comprueba que no quede ninguno
#   7. imágenes para la VM: SOLO si todo lo anterior dio verde, construye
#      cem-backend y cem-frontend para linux/arm64 desde la misma copia LF, las
#      sube a ghcr.io/githolan/<imagen>:<sha> (privado), comprueba con
#      `docker buildx imagetools inspect` que el índice tiene linux/arm64 y que la
#      etiqueta y el digest dan el mismo manifiesto, y guarda el digest de cada
#      una en la constancia (cd.ps1 lo exige). El login es temporal: un
#      DOCKER_CONFIG propio en una carpeta temporal, con el token clásico de la
#      variable de usuario de Windows CEM_GHCR_TOKEN por stdin; al final siempre
#      `docker logout` y se borra esa carpeta. El token nunca se imprime.
#
# Código de salida 0 = todo verde. Distinto de 0 = alguna etapa falló.
# Deja el resultado en <estado>\ci\<sha>.json (ver compuertas.ps1), que cd.ps1
# exige antes de desplegar esa SHA, y la salida completa en <estado>\ci\<sha>.log.
#
# Uso (PowerShell 7, desde cualquier carpeta):
#   pwsh despliegue/ci.ps1                     prueba HEAD y sube sus imágenes
#   pwsh despliegue/ci.ps1 -Sha 49ac99f        prueba ese commit y sube sus imágenes
#   pwsh despliegue/ci.ps1 -SinSubirImagenes   corrida local: prueba y no sube nada
#                                              (la constancia queda sin imágenes y
#                                              cd.ps1 no despliega esa SHA)
#   pwsh despliegue/ci.ps1 -ConservarCopia     no borra la copia temporal (se llama
#                                              cem-ci-conservada-<sha7>-<pid>-<azar> y
#                                              ninguna corrida la borra después)
#   pwsh despliegue/ci.ps1 -SinHeredar         corre todas las etapas aunque haya pruebas heredadas
#   pwsh despliegue/ci.ps1 -PartesBackend 1    pytest del backend en una sola parte (por defecto 4
#                                              a la vez, cada una con su proyecto de compose
#                                              <proyecto>-p<k>; la suma de las partes tiene que dar
#                                              el total recolectado o la etapa es roja)
#
# Pruebas heredadas (regla del propietario, 09-11-2026): si otro commit con el MISMO árbol
# (`git rev-parse <sha>^{tree}`) ya tiene una constancia en verde que corrió las pruebas, las
# etapas 1 a 6 no se repiten (etapa 'pruebas-heredadas', campo pruebas_de de la constancia), salvo
# los secretos del historial, que dependen de la historia y no del árbol y corren siempre; y
# solo se construyen y suben las imágenes de esta SHA. Uso: `ci.ps1 -Sha <punta del PR>
# -SinSubirImagenes` antes de fusionar y `ci.ps1 -Sha <fusión>` después, con el PR al día.
# Requiere: git, Docker en marcha (con buildx y emulación arm64), pnpm 10 y Node 22,
# bash de Git for Windows (o $env:CEM_BASH con la ruta de otro bash que no sea el
# de WSL) y, para subir, CEM_GHCR_TOKEN como variable de usuario de Windows.
# ==============================================================================

param(
    [string]$Sha = 'HEAD',
    [switch]$ConservarCopia,
    [switch]$SinSubirImagenes,
    [switch]$SinHeredar,
    # Partes de las pruebas del backend que corren a la vez, cada una con sus servicios.
    [ValidateRange(1, 8)][int]$PartesBackend = 4
)

$ErrorActionPreference = 'Stop'
# Los comandos nativos escriben avisos en stderr: se decide por su código de salida.
$PSNativeCommandUseErrorActionPreference = $false

. (Join-Path $PSScriptRoot 'compuertas.ps1')

$ServicioPruebas   = 'cem-backend-pruebas'
$ServicioPostgres  = 'cem-postgres-pruebas'
$ComposeRelativo   = 'backend/pruebas/compose.yml'
$PruebaImagen      = 'frontend/scripts/probar-imagen.sh'
# Un .sh que se sabe que existe: si la búsqueda no lo encuentra, la búsqueda está mal.
$ShConocido        = 'frontend/scripts/probar-imagen.sh'
# Prefijo de los contenedores y la red de frontend/scripts/probar-extremo-a-extremo.sh.
$PrefijoE2e        = 'cem-frontend-e2e'
# Carpeta temporal del DOCKER_CONFIG del login a ghcr.io (se borra siempre).
$PrefijoDockerConfig = 'cem-ci-docker-config-'
# Herramientas de la etapa 3c, con versión fija.
$ImagenPython      = 'python:3.12-slim'
$VersionPipAudit   = '2.9.0'
$ImagenGitleaks    = 'zricethezav/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854'
$CommitsGitleaks   = 200
# Imagen -> referencia, digest y plataforma de lo subido en la etapa 7.
$imagenes = [ordered]@{}
# Informe de scripts/cobertura_pa.py (etapa 3b); va a la constancia.
$coberturaPa = $null

$repo = Split-Path -Parent $PSScriptRoot
$shaCompleta = (git -C $repo rev-parse --verify "$Sha^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $shaCompleta) { throw "no existe el commit '$Sha' en $repo" }
$shaCompleta = $shaCompleta.Trim()
Assert-CemShaCompleta $shaCompleta
$sha7 = $shaCompleta.Substring(0, 7)

$idCorrida = New-CemIdCorrida -Sha7 $sha7
$ProyectoCompose = "$CemPrefijoProyectoCi$idCorrida"
$ImagenFrontend = "cem-frontend-ci:$idCorrida"
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
    # git puede resolver a <Git>\cmd\git.exe o a <Git>\mingw64\bin\git.exe; el bash está en
    # <Git>\bin\bash.exe, uno o dos niveles más arriba según cuál.
    $git = (Get-Command git -ErrorAction Stop).Source
    $nivel1 = Split-Path -Parent (Split-Path -Parent $git)
    $candidatos = @((Join-Path $nivel1 'bin\bash.exe'), (Join-Path (Split-Path -Parent $nivel1) 'bin\bash.exe'))
    foreach ($bash in $candidatos) { if (Test-Path $bash) { return $bash } }
    throw "no encontré el bash de Git for Windows en $($candidatos -join ' ni en '); definí CEM_BASH"
}

# Etapa 1: pytest del backend en $PartesBackend partes a la vez (CEM_PRUEBAS_PARTE=k/n, ver
# tests/conftest.py), cada una en su proyecto de compose con sus servicios. Antes se cuenta lo
# recolectado sin servicios (--no-deps); la etapa es verde solo si todas las partes salen con 0 y
# sus pruebas suman exactamente ese total (una parte que pierde archivos no pasa por verde).
function Probar-BackendEnPartes {
    $f = Join-Path $copia $ComposeRelativo
    # Un commit anterior al reparto (su conftest.py no lee CEM_PRUEBAS_PARTE) correría la suite
    # entera en cada parte: se prueba en una sola, y se dice.
    $partes = $PartesBackend
    if (-not (Select-String -Path (Join-Path $copia 'backend/tests/conftest.py') -SimpleMatch 'CEM_PRUEBAS_PARTE' -Quiet)) {
        $partes = 1
        Escribir "  backend/tests/conftest.py de $sha7 no reparte por CEM_PRUEBAS_PARTE: va en 1 parte, no en $PartesBackend" 'Yellow'
    }
    $recoleccion = @(docker @composeArgs run --rm --build --no-deps $ServicioPruebas pytest --collect-only -q -p no:cacheprovider 2>&1 | ForEach-Object { "$_" })
    $recoleccion | Select-Object -Last 3 | Tee-Object -FilePath $log -Append | Out-Host
    $m = [regex]::Match(($recoleccion -join "`n"), '(?m)^(\d+) tests? collected')
    if ($LASTEXITCODE -ne 0 -or -not $m.Success) {
        $etapas['backend'] = 'falla (no se pudo contar lo recolectado)'
        Escribir '  backend: FALLA (no se pudo contar lo recolectado)' 'Red'
        return
    }
    $total = [int]$m.Groups[1].Value
    Escribir "  $total pruebas recolectadas; van en $partes partes"
    $trabajos = foreach ($k in 1..$partes) {
        $p = "$ProyectoCompose-p$k"
        $salida = Join-Path ([IO.Path]::GetTempPath()) "$p.log"
        Start-ThreadJob -ArgumentList $p, $f, $k, $partes, $ServicioPruebas, $salida -ScriptBlock {
            param($p, $f, $k, $n, $servicio, $salida)
            docker compose -p $p -f $f run --rm --build -e "CEM_PRUEBAS_PARTE=$k/$n" $servicio pytest -p no:cacheprovider *> $salida
            [pscustomobject]@{ Proyecto = $p; Parte = $k; Codigo = $LASTEXITCODE; Salida = $salida }
        }
    }
    $resultados = @($trabajos | Wait-Job | Receive-Job)
    $trabajos | Remove-Job
    $suma = 0
    foreach ($r in ($resultados | Sort-Object Parte)) {
        $texto = Get-Content -Raw $r.Salida
        "=== Parte $($r.Parte) de $partes ($($r.Proyecto)) ===`n$texto" | Out-File -Append -Encoding utf8 $log
        Remove-Item $r.Salida
        $resumen = [regex]::Matches($texto, '(?m)^=+ (.+) in [\d.]+s')
        $linea = if ($resumen.Count) { $resumen[$resumen.Count - 1].Groups[1].Value } else { 'sin resumen' }
        foreach ($c in [regex]::Matches($linea, '(\d+) (passed|skipped|failed|errors?|xfailed|xpassed)')) { $suma += [int]$c.Groups[1].Value }
        $color = if ($r.Codigo -eq 0) { 'Green' } else { 'Red' }
        Escribir "  parte $($r.Parte): código $($r.Codigo); $linea" $color
        if ($r.Codigo -ne 0) { $script:partesEnRojo += $r.Proyecto }
    }
    if ($partesEnRojo.Count) { $etapas['backend'] = "falla ($($partesEnRojo.Count) de $partes partes en rojo)" }
    elseif ($suma -ne $total) { $etapas['backend'] = "falla (las partes suman $suma pruebas y se recolectaron $total)" }
    else { $etapas['backend'] = 'ok' }
    $color = if ($etapas['backend'] -eq 'ok') { 'Green' } else { 'Red' }
    Escribir "  backend: $($etapas['backend']) ($suma de $total pruebas en $partes partes)" $color
}

function Test-TodoVerde { ($etapas.Count -gt 0) -and -not ($etapas.Values | Where-Object { $_ -ne 'ok' }) }

# Etapa 7: construye y sube cada imagen, verifica el índice y anota el digest en
# $imagenes. Cualquier error marca la etapa en rojo (y por lo tanto toda la
# corrida); el login temporal se deshace siempre.
function Subir-Imagenes {
    $carpeta = Join-Path ([IO.Path]::GetTempPath()) "$PrefijoDockerConfig$sha7-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $configAnterior = $env:DOCKER_CONFIG
    $configPropia = $false
    try {
        $token = [Environment]::GetEnvironmentVariable($CemVariableTokenRegistro, 'User')
        Assert-CemTokenRegistro -Token $token
        New-Item -ItemType Directory -Path $carpeta | Out-Null
        $env:DOCKER_CONFIG = $carpeta
        $configPropia = $true
        # El token va por stdin; la salida de docker login no lo contiene.
        $salidaLogin = $token | docker login $CemServidorRegistro -u $CemUsuarioRegistro --password-stdin 2>&1 | ForEach-Object { "$_" }
        $codigoLogin = $LASTEXITCODE
        Remove-Variable token
        $salidaLogin | Tee-Object -FilePath $log -Append | Out-Host
        if ($codigoLogin -ne 0) { throw "docker login $CemServidorRegistro falló (código $codigoLogin)" }
        foreach ($img in $CemDockerfiles.Keys) {
            $referencia = Get-CemReferenciaImagen -Imagen $img -Sha $shaCompleta
            Escribir "  $img -> $referencia ($CemPlataformaImagenes)"
            docker buildx build --platform $CemPlataformaImagenes -f (Join-Path $copia $CemDockerfiles[$img]) `
                -t $referencia --label "$CemEtiquetaRevision=$shaCompleta" --push $copia 2>&1 |
                ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "docker buildx build --push de $img falló (código $LASTEXITCODE)" }
            $porEtiqueta = (docker buildx imagetools inspect $referencia --format '{{json .Manifest}}' 2>&1 | ForEach-Object { "$_" }) -join "`n"
            if ($LASTEXITCODE -ne 0) { throw "docker buildx imagetools inspect $referencia falló: $porEtiqueta" }
            $digest = Read-CemManifiestoImagen -Json $porEtiqueta -Referencia $referencia
            # Control: el digest leído resuelve en ghcr.io al mismo manifiesto que la etiqueta.
            $conDigest = "$CemRegistroImagenes/$img@$digest"
            $porDigest = (docker buildx imagetools inspect $conDigest --format '{{json .Manifest}}' 2>&1 | ForEach-Object { "$_" }) -join "`n"
            if ($LASTEXITCODE -ne 0) { throw "docker buildx imagetools inspect $conDigest falló: $porDigest" }
            if ((Read-CemManifiestoImagen -Json $porDigest -Referencia $conDigest) -ne $digest) { throw "$conDigest no devuelve el digest $digest" }
            $script:imagenes[$img] = [ordered]@{ referencia = $referencia; digest = $digest; plataforma = $CemPlataformaImagenes }
            Escribir "  $img`: $digest, índice con $CemPlataformaImagenes" 'Green'
        }
        $script:etapas['imagenes-ghcr'] = 'ok'
        Escribir "  imagenes-ghcr: ok" 'Green'
    }
    catch {
        $script:etapas['imagenes-ghcr'] = "falla ($($_.Exception.Message))"
        $script:imagenes.Clear()
        Escribir "  imagenes-ghcr: FALLA ($($_.Exception.Message))" 'Red'
    }
    finally {
        if ($configPropia) {
            # Con el DOCKER_CONFIG temporal todavía puesto: borra la credencial de ahí.
            docker logout $CemServidorRegistro 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append | Out-Host
            if ($configAnterior) { $env:DOCKER_CONFIG = $configAnterior } else { Remove-Item Env:\DOCKER_CONFIG }
        }
        $esCarpeta = $carpeta.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath())) -and ((Split-Path -Leaf $carpeta) -like "$PrefijoDockerConfig$sha7-*")
        if ($esCarpeta -and (Test-Path $carpeta)) { Remove-Item -Recurse -Force $carpeta }
        if (Test-Path $carpeta) {
            $script:etapas['imagenes-ghcr'] = "falla (no se pudo borrar el DOCKER_CONFIG temporal $carpeta)"
            $script:imagenes.Clear()
            Escribir "  imagenes-ghcr: FALLA (quedó el DOCKER_CONFIG temporal $carpeta; borrarlo a mano)" 'Red'
        }
    }
}

# Etapa 3b: lista las PA del P0-piloto con prueba y las que faltan, sobre la misma copia
# y en el mismo contenedor. Sin --estricto: que falten PA no pone la corrida en rojo; que
# el script falle o devuelva algo que no es el informe, sí.
function Medir-CoberturaPa {
    try {
        # stdout trae solo el JSON; los avisos de compose (stderr) van al registro.
        $salida = (docker @composeArgs run --rm $ServicioPruebas python -m scripts.cobertura_pa --json 2>>$log) -join "`n"
        if ($LASTEXITCODE -ne 0) { throw "python -m scripts.cobertura_pa salió con código $LASTEXITCODE" }
        $script:coberturaPa = Read-CemCoberturaPa -Json $salida
        $c = $script:coberturaPa
        Escribir "  PA del $($c.conjunto) con prueba: $($c.cantidad_cubiertas) de $($c.total)"
        foreach ($pa in $c.cubiertas.Keys | Sort-Object) { Escribir "    $pa`: $($c.cubiertas[$pa] -join ', ')" }
        foreach ($pa in $c.fuera_del_conjunto.Keys | Sort-Object) { Escribir "    $pa (fuera del $($c.conjunto)): $($c.fuera_del_conjunto[$pa] -join ', ')" }
        Escribir "  Sin prueba: $(if ($c.faltan) { $c.faltan -join ', ' } else { 'ninguna' })" $(if ($c.faltan) { 'Yellow' } else { 'Green' })
        $script:etapas['cobertura-pa'] = 'ok'
        Escribir '  cobertura-pa: ok' 'Green'
    }
    catch {
        $script:coberturaPa = $null
        $script:etapas['cobertura-pa'] = "falla ($($_.Exception.Message))"
        Escribir "  cobertura-pa: FALLA ($($_.Exception.Message))" 'Red'
    }
}

function Guardar-Resultado {
    $ok = Test-TodoVerde
    $ruta = Write-CemResultadoCi -Sha $shaCompleta -Resultado @{
        sha        = $shaCompleta
        resultado  = if ($ok) { 'ok' } else { 'falla' }
        etapas     = $etapas
        # Sin imágenes ($null) si no se subieron: cd.ps1 se niega a desplegar esa SHA.
        imagenes   = if ($ok -and $imagenes.Count -gt 0) { $imagenes } else { $null }
        # PA del P0-piloto cubiertas y faltantes; $null si la etapa no llegó a correr o falló.
        cobertura_pa = $coberturaPa
        # Commit cuyas pruebas se heredaron por tener el mismo árbol, o $null si corrieron acá.
        pruebas_de = $pruebasDe
        fecha_utc  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        minutos    = [math]::Round(((Get-Date) - $inicio).TotalMinutes, 1)
        equipo     = $env:COMPUTERNAME
        registro   = $log
    }
    Escribir "`nResultado: $(if ($ok) { 'VERDE' } else { 'ROJO' }) sobre $shaCompleta" $(if ($ok) { 'Green' } else { 'Red' })
    Escribir "Constancia: $ruta"
    $ok
}

$copia = New-CemCarpetaCopiaCi -Sha7 $sha7 -Conservada:$ConservarCopia
$composeArgs = @('compose', '-p', $ProyectoCompose, '-f', (Join-Path $copia $ComposeRelativo))
$ok = $false
$pruebasDe = $null
$partesEnRojo = @()
$composeUsado = $false
$e2eUsado = $false
$sufijoE2e = "ci-$idCorrida"
$imagenFrontendUsada = $false
try {
    Escribir "CEM - integración local sobre $shaCompleta (corrida $idCorrida)" 'White'

    # Corridas cortadas antes de su limpieza (TaskStop, Ctrl+C): su proceso ya no existe y
    # sus contenedores quedaron. Se bajan por su nombre de proyecto; las de procesos vivos no.
    $proyectos = @(docker ps -a --filter "label=com.docker.compose.project" --format '{{.Label "com.docker.compose.project"}}')
    foreach ($abandonado in @(Get-CemProyectosCiAbandonados -Proyectos $proyectos)) {
        Escribir "  corrida cortada: se baja $abandonado" 'Yellow'
        # `down` no saca el contenedor de `compose run` que sigue andando (el de pytest), y ese
        # retiene la red: primero se sacan todos los contenedores del proyecto, por su etiqueta.
        $suyos = @(docker ps -aq --filter "label=com.docker.compose.project=$abandonado")
        if ($suyos) { docker rm -f @suyos 2>&1 | Out-File -Append -Encoding utf8 $log }
        docker compose -p $abandonado down --remove-orphans --rmi local 2>&1 | Out-File -Append -Encoding utf8 $log
        $quedan = @(docker ps -aq --filter "label=com.docker.compose.project=$abandonado")
        if ($quedan) { throw "la corrida cortada $abandonado dejó $($quedan.Count) contenedores" }
    }
    # Sus copias LF en la carpeta temporal también quedaron (el `finally` no corrió): se borran
    # las de procesos muertos. Las del formato viejo, sin PID, no se tocan.
    $candidatas = @(Get-ChildItem -Directory -LiteralPath ([IO.Path]::GetTempPath()) -Filter "$CemPrefijoCopiaCi*" | ForEach-Object FullName)
    $copiasAbandonadas = @(Get-CemCopiasCiAbandonadas -Carpetas $candidatas)
    foreach ($abandonada in $copiasAbandonadas) {
        if (-not (Test-CemCopiaCi -Carpeta $abandonada)) { throw "no se borra $abandonada`: no es una copia de ci.ps1 dentro de la carpeta temporal" }
        Remove-Item -LiteralPath $abandonada -Recurse -Force
    }
    Escribir "  copias de corridas cortadas borradas: $($copiasAbandonadas.Count) (de $($candidatas.Count) con el prefijo $CemPrefijoCopiaCi)" $(if ($copiasAbandonadas.Count) { 'Yellow' } else { 'Gray' })

    # --- 0. copia LF del commit -------------------------------------------------
    Etapa 'Copia LF del commit'
    New-Item -ItemType Directory -Path $copia | Out-Null
    $tar = "$copia.tar"
    git -C $repo -c core.autocrlf=false -c core.eol=lf archive --format=tar -o $tar $shaCompleta
    if ($LASTEXITCODE -ne 0) { throw "git archive falló" }
    # bsdtar de Windows por ruta completa: desde Git Bash el PATH pone primero el tar de Git, que lee 'C:' como host remoto.
    $tarWindows = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $tarWindows)) { throw "no existe el tar de Windows en $tarWindows" }
    & $tarWindows -xf $tar -C $copia
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

    # --- secretos en el historial: siempre, también con pruebas heredadas ------------
    # Depende de la historia de $shaCompleta, no de su árbol: dos commits con el mismo árbol
    # pueden tener historias distintas (un secreto agregado y después borrado).
    Etapa 'Secretos en el historial (gitleaks)'
    $gitComun = (Resolve-Path (git -C $repo rev-parse --path-format=absolute --git-common-dir)).Path
    Correr 'seguridad-secretos-historial' {
        docker run --rm -v "${copia}:/copia:ro" -v "${gitComun}:/repo.git:ro" $ImagenGitleaks git /repo.git -c /copia/.gitleaks.toml `
            --no-banner --redact=100 --log-opts="-n $CommitsGitleaks $shaCompleta"
    } | Out-Null

    # --- pruebas heredadas: otro commit con el mismo árbol ya pasó todo ------------
    $pruebasDe = if ($SinHeredar) { $null } else { Find-CemPruebasMismoArbol -Repo $repo -Sha $shaCompleta }
    if ($pruebasDe) {
        Etapa 'Pruebas heredadas (mismo árbol)'
        $etapas['pruebas-heredadas'] = 'ok'
        Escribir "  $sha7 tiene el mismo árbol que $pruebasDe, que pasó todas las etapas (constancia $(Join-Path (Get-CemCarpetaEstado ci) "$pruebasDe.json")); no se repiten (salvo los secretos del historial, que corrieron arriba)" 'Green'
    } else {
        # --- 1-3. backend, ruff y contratos en Linux ------------------------------
        Etapa "Backend (pytest, PostgreSQL 18 y Redis 7, en partes a la vez)"
        $composeUsado = $true
        Probar-BackendEnPartes
        Etapa 'ruff'
        Correr 'ruff' { docker @composeArgs run --rm $ServicioPruebas sh -c 'ruff check .. && ruff format --check ..' } | Out-Null
        Etapa 'Contratos (pytest)'
        Correr 'contratos' { docker @composeArgs run --rm $ServicioPruebas sh -c 'cd /src/contratos && pytest' } | Out-Null
        Etapa 'Cobertura de PA (documento 14, §11)'
        Medir-CoberturaPa

        # --- 3c. seguridad ----------------------------------------------------------
        Etapa 'Seguridad (pip-audit, pnpm audit, gitleaks del árbol)'
        $auditoriaPip = "python -m venv /entorno && /entorno/bin/pip install -q -r backend/requirements.prod.txt ./contratos && /entorno/bin/pip uninstall -q -y pip " +
            "&& pip install -q pip-audit==$VersionPipAudit && pip-audit --path /entorno/lib/python3.12/site-packages"
        Correr 'seguridad-dependencias-python' {
            docker run --rm -v "${copia}:/copia:ro" -w /tmp/auditoria $ImagenPython sh -c "cp -r /copia/backend /copia/contratos . && $auditoriaPip"
        } | Out-Null
        Push-Location (Join-Path $copia 'frontend')
        try { Correr 'seguridad-dependencias-frontend' { pnpm audit --prod --audit-level high } | Out-Null }
        finally { Pop-Location }
        Correr 'seguridad-secretos-arbol' {
            docker run --rm -v "${copia}:/copia:ro" $ImagenGitleaks dir /copia -c /copia/.gitleaks.toml --no-banner --redact=100
        } | Out-Null

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
        $imagenFrontendUsada = $true
        if (Correr 'frontend-imagen-build' { docker build -f (Join-Path $copia 'frontend/Dockerfile') -t $ImagenFrontend $copia }) {
            $bash = Get-BashDeGit
            $env:MSYS_NO_PATHCONV = '1'
            Push-Location $copia
            try { Correr 'frontend-imagen-prueba' { & $bash $PruebaImagen $ImagenFrontend } | Out-Null }
            finally { Pop-Location; Remove-Item Env:\MSYS_NO_PATHCONV }
        }

        # --- 6. extremo a extremo (Playwright contra las imágenes reales) ---------
        Etapa 'Extremo a extremo (pnpm e2e)'
        if ($etapas['frontend-install'] -ne 'ok') {
            $etapas['frontend-e2e'] = 'falla (sin pnpm install)'
            Escribir '  frontend-e2e: FALLA (no hay dependencias instaladas)' 'Red'
        } else {
            $e2eUsado = $true
            $rutaGit = Join-Path (Split-Path -Parent (Split-Path -Parent (Get-BashDeGit))) 'usr\bin'
            if (-not (Test-Path (Join-Path $rutaGit 'sh.exe'))) { throw "no encontré sh.exe de Git for Windows en $rutaGit" }
            $pathAnterior = $env:PATH
            # `pnpm e2e` llama a `sh`: el de Git for Windows (el bash.exe de System32 es WSL).
            $env:PATH = "$rutaGit;$pathAnterior"
            $env:CEM_E2E_SUFIJO = $sufijoE2e
            $env:CEM_E2E_SHA = $shaCompleta
            # La copia no tiene .git: la versión anterior del frontend (e2e/versiones.e2e.ts) se
            # construye desde el repositorio.
            $env:CEM_E2E_REPO = $repo
            Push-Location (Join-Path $copia 'frontend')
            try { Correr 'frontend-e2e' { pnpm e2e } | Out-Null }
            finally {
                Pop-Location
                $env:PATH = $pathAnterior
                Remove-Item Env:\CEM_E2E_SUFIJO, Env:\CEM_E2E_SHA, Env:\CEM_E2E_REPO
            }
        }
    }
}
catch {
    Escribir "ERROR: $($_.Exception.Message)" 'Red'
    $etapas['error'] = $_.Exception.Message
}
finally {
    # Solo si esta corrida levantó el proyecto: un `down` ajeno cortaría otra corrida.
    if ($composeUsado) {
        # Con el backend en rojo, las consultas de los dos lados de un deadlock solo quedan en el
        # registro de PostgreSQL, que el `down` borra: se copian antes (deadlock intermitente de
        # las pruebas de migración, tracker).
        foreach ($pRojo in $partesEnRojo) {
            # Cada deadlock con todo lo que su proceso escribe a continuación: el DETAIL lista las
            # consultas de los procesos (truncadas en track_activity_query_size) y el STATEMENT trae
            # entera la del abortado. Una línea nueva de otro proceso, o de otro nivel, lo cierra.
            $registro = @(docker compose -p $pRojo -f (Join-Path $copia $ComposeRelativo) logs --no-color --no-log-prefix $ServicioPostgres 2>&1 | ForEach-Object { "$_" })
            $leido = $LASTEXITCODE
            $lineas = @(); $pid_ = $null; $cuantos = 0
            foreach ($l in $registro) {
                $entrada = [regex]::Match($l, '^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ \S+ \[(\d+)\] (\w+):')
                if ($entrada.Success) {
                    if ($l -match 'ERROR:  deadlock detected') { $pid_ = $entrada.Groups[1].Value; $cuantos++ }
                    elseif ($pid_ -ne $entrada.Groups[1].Value -or
                            $entrada.Groups[2].Value -notin 'DETAIL', 'HINT', 'CONTEXT', 'STATEMENT') { $pid_ = $null }
                }
                if ($pid_) { $lineas += $l }
            }
            $titulo = if ($leido -eq 0) { "$cuantos deadlock(s)" } else { "no se pudo leer (código $leido)" }
            (@("=== Registro de $ServicioPostgres de $pRojo`: $titulo ===") + $lineas) |
                Tee-Object -FilePath $log -Append | Out-Host
        }
        $quedanTodos = 0
        foreach ($p in @($ProyectoCompose) + @(1..$PartesBackend | ForEach-Object { "$ProyectoCompose-p$_" })) {
            $args_ = @('compose', '-p', $p, '-f', (Join-Path $copia $ComposeRelativo))
            # Un `compose run` todavía vivo (corrida interrumpida) no lo saca `down`: va primero.
            $suyos = @(docker ps -aq --filter "label=com.docker.compose.project=$p")
            if ($suyos) { docker rm -f @suyos 2>&1 | Out-File -Append -Encoding utf8 $log }
            # --rmi local: también la imagen que compose construyó para este proyecto (lleva su nombre).
            docker @args_ down --remove-orphans --rmi local 2>&1 | Out-File -Append -Encoding utf8 $log
            $quedanTodos += @(docker ps -aq --filter "label=com.docker.compose.project=$p").Count
        }
        if ($quedanTodos) { $etapas['limpieza-compose'] = "falla (quedan $quedanTodos contenedores de $ProyectoCompose y sus partes)" }
        else { $etapas['limpieza-compose'] = 'ok'; Escribir "  limpieza-compose: sin contenedores de $ProyectoCompose ni de sus $PartesBackend partes" 'Green' }
    }
    if ($imagenFrontendUsada) { docker rmi $ImagenFrontend 2>&1 | Out-File -Append -Encoding utf8 $log }
    # El script de e2e borra lo suyo con un trap; si lo cortaron antes, se borra acá
    # por el sufijo de esta corrida (nunca lo de otra) y se exige que no quede nada.
    if ($e2eUsado) {
        $restos = @(docker ps -aq --filter "name=$PrefijoE2e.*-$sufijoE2e`$")
        if ($restos) { docker rm -f @restos 2>&1 | Out-File -Append -Encoding utf8 $log }
        $redes = @(docker network ls -q --filter "name=$PrefijoE2e-red-$sufijoE2e`$")
        if ($redes) { docker network rm @redes 2>&1 | Out-File -Append -Encoding utf8 $log }
        $quedan = @(docker ps -aq --filter "name=$PrefijoE2e.*-$sufijoE2e`$") + @(docker network ls -q --filter "name=$PrefijoE2e-red-$sufijoE2e`$")
        if ($quedan) { $etapas['limpieza-e2e'] = "falla (quedan $($quedan.Count) contenedores o redes)" }
        else { $etapas['limpieza-e2e'] = 'ok'; Escribir "  limpieza-e2e: sin contenedores ni redes de la corrida $sufijoE2e ($($restos.Count) contenedores y $($redes.Count) redes borrados acá)" 'Green' }
    }
    # --- 7. imágenes en ghcr.io (solo con todo lo anterior en verde) -----------
    Etapa "Imágenes $CemPlataformaImagenes en $CemRegistroImagenes"
    if ($SinSubirImagenes) {
        Escribir '  omitida por -SinSubirImagenes: la constancia queda sin imágenes y cd.ps1 no despliega esta SHA' 'Yellow'
    } elseif (-not (Test-TodoVerde)) {
        Escribir '  no se suben imágenes: hay etapas en rojo' 'Red'
    } else {
        Subir-Imagenes
    }
    $ok = Guardar-Resultado
    $esCopia = (Test-CemCopiaCi -Carpeta $copia) -and ((Split-Path -Leaf $copia) -like "$CemPrefijoCopiaCi$sha7-$PID-*")
    if ($ConservarCopia) { Escribir "Copia conservada en $copia" }
    elseif ($esCopia -and (Test-Path $copia)) { Remove-Item -Recurse -Force $copia }
}
exit $(if ($ok) { 0 } else { 1 })
