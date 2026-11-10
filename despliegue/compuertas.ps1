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
#
# Imágenes (decisión del propietario, 15-10-2026): Coolify ya no construye en la
# VM. ci.ps1 construye cem-backend y cem-frontend para linux/arm64 en la PC, las
# sube a ghcr.io (privado) con la SHA como etiqueta y guarda el digest de cada una
# en la constancia; cd.ps1 solo despliega esos digests y los compara en la VM.
# ==============================================================================

# Repositorio de imágenes de CEM en ghcr.io (minúsculas: así las nombra ghcr.io).
$CemRegistroImagenes = 'ghcr.io/githolan'
$CemServidorRegistro = 'ghcr.io'
# Usuario de GitHub con el que ci.ps1 hace el login temporal para subir.
$CemUsuarioRegistro  = 'Githolan'
# Variable de usuario de Windows con el token clásico (write:packages) para subir.
$CemVariableTokenRegistro = 'CEM_GHCR_TOKEN'
$CemPrefijoTokenClasico   = 'ghp_'
# Plataforma de la VM (OCI Ampere A1).
$CemPlataformaImagenes = 'linux/arm64'
# Imagen -> Dockerfile, relativo a la raíz del commit (que es el contexto de construcción).
$CemDockerfiles = [ordered]@{
    'cem-backend'  = 'backend/Dockerfile'
    'cem-frontend' = 'frontend/Dockerfile'
}
# Etiqueta OCI con el commit, que cd.ps1 compara en el contenedor.
$CemEtiquetaRevision = 'org.opencontainers.image.revision'
$CemPatronDigest     = '^sha256:[0-9a-f]{64}$'
$CemTiposIndice      = @('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json')

$CemCarpetaEstadoPorDefecto = Join-Path $env:LOCALAPPDATA 'cem-despliegue'

# Ventana de actualización automática de Coolify (documento 13, §18.3, regla 7):
# no se despliega entre estas horas UTC, ambas incluidas.
$CemVentanaProhibidaDesde = [timespan]'02:45:00'
$CemVentanaProhibidaHasta = [timespan]'03:10:59'

# Identificador de una corrida de ci.ps1: nombra su proyecto de compose, su imagen del frontend
# y sus contenedores de e2e, así dos corridas a la vez no se pisan y cada una borra solo lo
# suyo. Lleva el PID del proceso para reconocer después una corrida cortada (proceso muerto).
$CemPrefijoProyectoCi = 'cem-ci-'
# El sufijo -p<k> es la parte k de las pruebas del backend, que corren a la vez.
$CemFormatoProyectoCi = '^cem-ci-(?<sha>[0-9a-f]{7})-(?<pid>\d+)-(?<azar>[0-9a-f]{6})(-p\d+)?$'

function New-CemIdCorrida {
    param([Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{7}$')][string]$Sha7,
          [int]$Proceso = $PID)
    "$Sha7-$Proceso-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
}

# $true si el proceso con ese PID existe.
$CemProcesoVivo = { param($p) [bool](Get-Process -Id $p -ErrorAction SilentlyContinue) }

# De los proyectos de compose dados, los de corridas de ci.ps1 cuyo proceso ya no existe. Nunca
# el de un proceso vivo (podría ser otra corrida en curso) ni un nombre con otro formato.
# Si el PID lo reusó otro proceso, la corrida no se toma por abandonada: queda, no se borra.
function Get-CemProyectosCiAbandonados {
    param([string[]]$Proyectos, [scriptblock]$ProcesoVivo = $CemProcesoVivo)
    foreach ($p in ($Proyectos | Sort-Object -Unique)) {
        $m = [regex]::Match($p, $CemFormatoProyectoCi)
        if ($m.Success -and -not (& $ProcesoVivo ([int]$m.Groups['pid'].Value))) { $p }
    }
}

# Copia LF del commit que hace ci.ps1, directamente dentro de la carpeta temporal del sistema.
# Lleva el PID de la corrida, como el proyecto de compose, para reconocer después la copia de una
# corrida cortada (TaskStop, cierre de la terminal), cuyo `finally` no llegó a borrarla. Las del
# formato viejo (cem-ci-<sha7>-<azar>, sin PID) no se reconocen: nunca se borran solas.
$CemPrefijoCopiaCi = 'cem-ci-copia-'
$CemFormatoCopiaCi = "^$([regex]::Escape($CemPrefijoCopiaCi))(?<sha>[0-9a-f]{7})-(?<pid>\d+)-(?<azar>[0-9a-f]{8})$"
# Dos corridas que arrancan a la vez verían las mismas copias abandonadas y sus Remove-Item se
# pisarían (uno falla y corta su corrida). La limpieza entera (listar y borrar) va bajo este mutex
# con nombre: la segunda corrida espera y, al listar, ya no ve lo que borró la primera. Un renombre
# a un nombre propio NO sirve de reclamo en Windows: medido el 10-11-2026, dos Directory.Move del
# mismo origen pueden terminar los dos sin error (MoveFileEx renombra por handle).
$CemMutexLimpiezaCopiasCi = 'Global\cem-ci-limpieza-copias'
# Espera máxima por el mutex; vencida, la corrida falla (no se limpia a ciegas).
$CemEsperaLimpiezaCopiasCi = [timespan]'00:10:00'
# Con -ConservarCopia la copia lleva otro prefijo, fuera del formato: no se borra nunca sola.
$CemPrefijoCopiaConservada = 'cem-ci-conservada-'

function New-CemCarpetaCopiaCi {
    param([Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{7}$')][string]$Sha7,
          [int]$Proceso = $PID, [switch]$Conservada)
    $prefijo = if ($Conservada) { $CemPrefijoCopiaConservada } else { $CemPrefijoCopiaCi }
    Join-Path ([IO.Path]::GetTempPath()) "$prefijo$Sha7-$Proceso-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
}

# $true solo si la carpeta está directamente dentro de la carpeta temporal del sistema y su nombre
# cumple exactamente $CemFormatoCopiaCi. Es el límite de todo borrado de copias.
function Test-CemCopiaCi {
    param([Parameter(Mandatory)][string]$Carpeta)
    $completa = [IO.Path]::GetFullPath($Carpeta)
    $tmp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
    ([IO.Path]::GetDirectoryName($completa) -eq $tmp) -and ([IO.Path]::GetFileName($completa) -cmatch $CemFormatoCopiaCi)
}

# De las carpetas dadas, las copias de corridas de ci.ps1 cuyo proceso ya no existe (ruta completa).
# Nunca la de un proceso vivo, ni una fuera de la carpeta temporal, ni un nombre con otro formato.
function Get-CemCopiasCiAbandonadas {
    param([string[]]$Carpetas, [scriptblock]$ProcesoVivo = $CemProcesoVivo)
    foreach ($c in ($Carpetas | Sort-Object -Unique)) {
        if (-not (Test-CemCopiaCi -Carpeta $c)) { continue }
        $m = [regex]::Match((Split-Path -Leaf $c), $CemFormatoCopiaCi)
        if (-not (& $ProcesoVivo ([int]$m.Groups['pid'].Value))) { [IO.Path]::GetFullPath($c) }
    }
}

# Borra las copias abandonadas, con el mutex de limpieza tomado: lista ($Listar devuelve rutas de
# carpetas; por defecto, las de la carpeta temporal con el prefijo), filtra con
# Get-CemCopiasCiAbandonadas, reafirma el límite y borra. Devuelve las rutas borradas. Una copia
# que una corrida cortada dejó a medio borrar conserva su nombre: la toma la corrida siguiente.
function Remove-CemCopiasCiAbandonadas {
    param([scriptblock]$Listar = { @(Get-ChildItem -Directory -LiteralPath ([IO.Path]::GetTempPath()) -Filter "$CemPrefijoCopiaCi*" | ForEach-Object FullName) },
          [scriptblock]$ProcesoVivo = $CemProcesoVivo)
    $mutex = [Threading.Mutex]::new($false, $CemMutexLimpiezaCopiasCi)
    try {
        try { $tomado = $mutex.WaitOne($CemEsperaLimpiezaCopiasCi) }
        # Lo tenía un proceso que murió sin soltarlo (corrida cortada a mitad de la limpieza): el
        # mutex pasa a este proceso; lo que quedó a medio borrar se vuelve a listar abajo.
        catch [Threading.AbandonedMutexException] { $tomado = $true }
        if (-not $tomado) { throw "la limpieza de copias de otra corrida no soltó $CemMutexLimpiezaCopiasCi en $CemEsperaLimpiezaCopiasCi" }
        try {
            foreach ($c in @(Get-CemCopiasCiAbandonadas -Carpetas (& $Listar) -ProcesoVivo $ProcesoVivo)) {
                if (-not (Test-CemCopiaCi -Carpeta $c)) { throw "no se borra $c`: no es una copia de ci.ps1 dentro de la carpeta temporal" }
                Remove-Item -LiteralPath $c -Recurse -Force
                $c
            }
        }
        finally { $mutex.ReleaseMutex() }
    }
    finally { $mutex.Dispose() }
}

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

# Regla del propietario (09-11-2026): las pruebas de un commit valen para otro con el MISMO
# árbol (`git rev-parse <sha>^{tree}`), p. ej. el commit de fusión de un PR al día con main, que
# tiene exactamente los archivos de la punta del PR. Devuelve la SHA de una constancia en verde
# que corrió las pruebas ella misma (etapa backend en ok, no heredada) sobre un commit con el
# árbol de $Sha, o $null. La imagen se construye igual para $Sha: su etiqueta y su label
# `revision` siguen siendo los de $Sha y cd.ps1 no cambia.
function Find-CemPruebasMismoArbol {
    param([Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][string]$Sha)
    Assert-CemShaCompleta $Sha
    $arbol = (git -C $Repo rev-parse --verify "$Sha^{tree}" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $arbol) { throw "no pude leer el árbol de $Sha" }
    $arbol = $arbol.Trim()
    $candidatas = @(Get-ChildItem -Path (Get-CemCarpetaEstado ci) -Filter '*.json' | ForEach-Object {
        $r = Get-Content -Raw $_.FullName | ConvertFrom-Json
        if ($r.sha -and $r.sha -ne $Sha -and $r.resultado -eq 'ok' -and $r.etapas.backend -eq 'ok' -and
            "$($r.sha)" -match '^[0-9a-f]{40}$' -and $_.BaseName -eq $r.sha) { $r.sha }
    })
    if (-not $candidatas) { return $null }
    # Un commit que ya no existe (rama borrada sin fusionar) sale como "missing", no corta.
    $lineas = @($candidatas | ForEach-Object { "$_^{tree}" } | git -C $Repo cat-file --batch-check)
    if ($LASTEXITCODE -ne 0 -or $lineas.Count -ne $candidatas.Count) { throw "git cat-file no leyó los árboles de las constancias" }
    for ($i = 0; $i -lt $candidatas.Count; $i++) {
        if ($lineas[$i] -eq "$arbol tree $(($lineas[$i] -split ' ')[2])") { return $candidatas[$i] }
    }
    $null
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

# --- Copia previa a la migración (documento 13, §18.3, regla 5) -----------------
# cd.ps1 compara la última revisión de Alembic de la SHA a desplegar con la que tiene
# la base del entorno (plataforma.alembic_version). Si el despliegue va a migrar,
# primero hace una copia verificada (§17) dentro de cem-trabajos del entorno.

$CemCarpetaMigraciones = 'backend/migrations/versions'
$CemPatronRevision     = '(?m)^revision\s*=\s*"([^"]+)"\s*$'
$CemPatronAnterior     = '(?m)^down_revision\s*=\s*(?:"([^"]+)"|None)\s*$'

# Lee revision y down_revision del texto de una migración. Falla si no hay
# exactamente una de cada una (p. ej. una fusión con tupla, que no se usa en CEM).
function ConvertFrom-CemMigracion {
    param([Parameter(Mandatory)][string]$Texto, [Parameter(Mandatory)][string]$Archivo)
    $rev = [regex]::Matches($Texto, $CemPatronRevision)
    $ant = [regex]::Matches($Texto, $CemPatronAnterior)
    if ($rev.Count -ne 1) { throw "${Archivo}: se esperaba una línea 'revision = ""…""', hay $($rev.Count)" }
    if ($ant.Count -ne 1) { throw "${Archivo}: se esperaba una línea 'down_revision = ""…""' o 'None', hay $($ant.Count)" }
    [pscustomobject]@{
        Revision = $rev[0].Groups[1].Value
        Anterior = if ($ant[0].Groups[1].Success) { $ant[0].Groups[1].Value } else { $null }
        Archivo  = $Archivo
    }
}

# Ordena las revisiones de la base a la cabeza. Exige una cadena lineal: una sola
# base, ninguna revisión repetida, ninguna con dos sucesoras y todas alcanzables.
function Get-CemCadenaAlembic {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Revisiones)
    if ($Revisiones.Count -eq 0) { throw 'no hay migraciones de Alembic' }
    $repetidas = $Revisiones | Group-Object Revision | Where-Object Count -gt 1
    if ($repetidas) { throw "revisión de Alembic repetida: $($repetidas.Name -join ', ')" }
    $bases = @($Revisiones | Where-Object { $null -eq $_.Anterior })
    if ($bases.Count -ne 1) { throw "se esperaba una sola migración base (down_revision = None), hay $($bases.Count)" }
    $ramas = $Revisiones | Where-Object { $null -ne $_.Anterior } | Group-Object Anterior | Where-Object Count -gt 1
    if ($ramas) { throw "las migraciones se bifurcan después de $($ramas.Name -join ', '): hay más de una cabeza" }
    $siguiente = @{}
    foreach ($r in $Revisiones) { if ($null -ne $r.Anterior) { $siguiente[$r.Anterior] = $r.Revision } }
    $cadena = [System.Collections.Generic.List[string]]::new()
    $actual = $bases[0].Revision
    while ($null -ne $actual) {
        $cadena.Add($actual)
        $actual = $siguiente[$actual]
    }
    if ($cadena.Count -ne $Revisiones.Count) {
        $sueltas = $Revisiones.Revision | Where-Object { $cadena -notcontains $_ }
        throw "migraciones que no cuelgan de la base: $($sueltas -join ', ')"
    }
    , $cadena.ToArray()
}

# Cadena de revisiones de Alembic tal como está en el commit (no en la carpeta de trabajo).
function Get-CemCadenaAlembicEnSha {
    param([Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][string]$Sha)
    Assert-CemShaCompleta $Sha
    $archivos = @(git -C $Repo ls-tree --name-only "$Sha" -- "$CemCarpetaMigraciones/" | Where-Object { $_ -like '*.py' })
    if ($LASTEXITCODE -ne 0) { throw "git ls-tree falló sobre $Sha" }
    $revisiones = foreach ($a in $archivos) {
        $texto = (git -C $Repo show "${Sha}:$a") -join "`n"
        if ($LASTEXITCODE -ne 0) { throw "git show ${Sha}:$a falló" }
        ConvertFrom-CemMigracion -Texto $texto -Archivo $a
    }
    Get-CemCadenaAlembic -Revisiones @($revisiones)
}

# Decide qué hacer con la copia previa. Acciones:
#   sin-migracion  la base ya está en la cabeza de la SHA: no se copia
#   copiar         hay migraciones pendientes y existe cem-trabajos en el entorno
#   omitida        hay migraciones pendientes y el propietario pidió -SinCopiaPrevia
#   negar          hay migraciones pendientes, no hay cem-trabajos y no hay -SinCopiaPrevia
# Falla si la versión de la base no es ninguna revisión de la SHA (la base está
# adelante, o en otra rama): ahí cd.ps1 no sabe qué haría el arranque.
function Get-CemDecisionCopiaPrevia {
    param(
        [Parameter(Mandatory)][string[]]$Cadena,
        [Parameter(Mandatory)][AllowEmptyString()][string]$VersionEntorno,
        [Parameter(Mandatory)][bool]$HayTrabajos,
        [Parameter(Mandatory)][bool]$SinCopiaPrevia
    )
    if (-not $VersionEntorno) { throw 'plataforma.alembic_version del entorno está vacía: no se puede saber qué migraciones faltan' }
    $cabeza = $Cadena[-1]
    $posicion = [array]::IndexOf($Cadena, $VersionEntorno)
    if ($posicion -lt 0) {
        throw "la base del entorno está en la revisión '$VersionEntorno', que no existe en la SHA a desplegar (revisiones: $($Cadena -join ', '))"
    }
    $pendientes = @($Cadena | Select-Object -Skip ($posicion + 1))
    $d = [ordered]@{ Accion = ''; Cabeza = $cabeza; VersionEntorno = $VersionEntorno; Pendientes = $pendientes; Motivo = '' }
    if ($pendientes.Count -eq 0) {
        $d.Accion = 'sin-migracion'
        $d.Motivo = "la base ya está en $cabeza, la última revisión de la SHA"
    } elseif ($SinCopiaPrevia) {
        $d.Accion = 'omitida'
        $d.Motivo = "migraciones pendientes $($pendientes -join ', ') (base en $VersionEntorno) y se pidió -SinCopiaPrevia$(if (-not $HayTrabajos) { '; el entorno no tiene cem-trabajos' })"
    } elseif ($HayTrabajos) {
        $d.Accion = 'copiar'
        $d.Motivo = "migraciones pendientes $($pendientes -join ', ') (base en $VersionEntorno)"
    } else {
        $d.Accion = 'negar'
        $d.Motivo = "migraciones pendientes $($pendientes -join ', ') (base en $VersionEntorno) y el entorno no tiene cem-trabajos para hacer la copia previa; desplegar sin copia exige -SinCopiaPrevia"
    }
    [pscustomobject]$d
}

# Programa Python que corre dentro de cem-trabajos: la misma copia_diaria de
# app/plataforma/copias.py (volcado, restauración y comparación, cifrado, subida y
# relectura), con la hora actual como ventana. Imprime el resumen en JSON en la
# última línea. Sin comillas: pasa intacto por ssh y por sh entre comillas simples.
$CemProgramaCopiaPrevia = 'import datetime, json; from app.plataforma import copias; ' +
    'v = datetime.datetime.now(datetime.UTC).replace(microsecond=0); ' +
    'print(json.dumps(copias.copia_diaria(copias.ConfigCopias.desde_entorno(), v)))'

# Lee el resumen que imprime la copia (última línea no vacía) y exige que describa
# una copia hecha: al menos un objeto, SHA-256 de 64 hexadecimales, bytes y tablas.
function Read-CemResultadoCopia {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Salida)
    $ultima = @($Salida -split "`r?`n" | Where-Object { $_.Trim() })
    if ($ultima.Count -eq 0) { throw 'la copia previa no imprimió nada' }
    $linea = $ultima[-1].Trim()
    try { $r = $linea | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "la última línea de la copia previa no es JSON: $linea" }
    $objetos = @($r.objetos | Where-Object { $_ })
    if ($objetos.Count -eq 0) { throw "la copia previa no informa ningún objeto subido: $linea" }
    if ("$($r.sha256)" -notmatch '^[0-9a-f]{64}$') { throw "la copia previa no informa un SHA-256 válido: $linea" }
    if (-not ($r.bytes -gt 0) -or -not ($r.tablas -gt 0)) { throw "la copia previa informa 0 bytes o 0 tablas: $linea" }
    [pscustomobject]@{ Objetos = $objetos; Sha256 = $r.sha256; Bytes = [long]$r.bytes; Tablas = [int]$r.tablas; Filas = [long]$r.filas }
}

# Texto de la fila "Copia previa" del registro de despliegue.
function Format-CemCopiaPrevia {
    param([Parameter(Mandatory)]$Decision, $Copia, [string]$Bucket)
    switch ($Decision.Accion) {
        'sin-migracion' { "No hizo falta: $($Decision.Motivo) (``$($Decision.Cabeza)``); sin migraciones nuevas" }
        'omitida'       { "**NO HECHA, por -SinCopiaPrevia**: $($Decision.Motivo)" }
        'copiar' {
            if ($null -eq $Copia) { throw 'la decisión era copiar y no hay resultado de la copia' }
            $tilde = '`'
            $objetos = ($Copia.Objetos | ForEach-Object { $tilde + $_ + $tilde }) -join ' y '
            $enBucket = if ($Bucket) { " en $tilde$Bucket$tilde" } else { '' }
            "Hecha y verificada antes de desplegar ($($Decision.Motivo)): $objetos$enBucket, " +
            "SHA-256 $tilde$($Copia.Sha256)$tilde, $($Copia.Bytes) bytes, $($Copia.Tablas) tablas y $($Copia.Filas) filas comparadas tras restaurar"
        }
        default { throw "decisión de copia previa sin registro posible: '$($Decision.Accion)'" }
    }
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

# --- Imágenes en ghcr.io (ci.ps1 las sube, cd.ps1 las despliega) ---------------

function Get-CemReferenciaImagen {
    param([Parameter(Mandatory)][string]$Imagen, [Parameter(Mandatory)][string]$Sha)
    if (-not $CemDockerfiles.Contains($Imagen)) { throw "imagen desconocida '$Imagen' (válidas: $($CemDockerfiles.Keys -join ', '))" }
    Assert-CemShaCompleta $Sha
    "$CemRegistroImagenes/${Imagen}:$Sha"
}

# Exige un token clásico de GitHub (los de grano fino no sirven para ghcr.io).
# Ningún mensaje lleva el token ni una parte de él.
function Assert-CemTokenRegistro {
    param([AllowEmptyString()][AllowNull()][string]$Token)
    if (-not $Token) {
        throw "falta la variable de usuario $CemVariableTokenRegistro (token clásico de GitHub con write:packages); no se pueden subir las imágenes"
    }
    if (-not $Token.StartsWith($CemPrefijoTokenClasico, [StringComparison]::Ordinal)) {
        throw "$CemVariableTokenRegistro no es un token clásico de GitHub (debe empezar con $CemPrefijoTokenClasico); ghcr.io no acepta los de grano fino"
    }
}

# Lee la salida de `python -m scripts.cobertura_pa --json` (documento 14, §11) y devuelve
# el objeto que ci.ps1 guarda en su constancia. Falla si no es JSON o le falta la forma
# esperada: un informe roto no puede pasar por "no hay PA cubiertas".
function Read-CemCoberturaPa {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json)
    try { $c = $Json | ConvertFrom-Json -AsHashtable -ErrorAction Stop }
    catch { throw "cobertura_pa no devolvió JSON: '$Json'" }
    if ($c -isnot [hashtable]) { throw "cobertura_pa no devolvió un objeto: '$Json'" }
    foreach ($campo in 'conjunto', 'total', 'cubiertas', 'faltan', 'fuera_del_conjunto') {
        if (-not $c.ContainsKey($campo)) { throw "cobertura_pa: falta el campo '$campo'" }
    }
    if ($c.cubiertas -isnot [hashtable] -or $c.fuera_del_conjunto -isnot [hashtable]) { throw 'cobertura_pa: cubiertas y fuera_del_conjunto tienen que ser objetos' }
    $pas = @($c.cubiertas.Keys) + @($c.faltan) + @($c.fuera_del_conjunto.Keys)
    $malas = @($pas | Where-Object { "$_" -notmatch '^PA\d{2}$' })
    if ($malas) { throw "cobertura_pa: nombres de PA inválidos: $($malas -join ', ')" }
    if ($c.cubiertas.Count + @($c.faltan).Count -ne $c.total) {
        throw "cobertura_pa: cubiertas ($($c.cubiertas.Count)) más faltan ($(@($c.faltan).Count)) no suman el total ($($c.total))"
    }
    [ordered]@{
        conjunto           = $c.conjunto
        total              = $c.total
        cantidad_cubiertas = $c.cubiertas.Count
        cubiertas          = $c.cubiertas
        faltan             = @($c.faltan)
        fuera_del_conjunto = $c.fuera_del_conjunto
    }
}

# Lee la salida de `docker buildx imagetools inspect <ref> --format '{{json .Manifest}}'`
# y devuelve el digest del índice. Exige un índice (Docker con el almacén de
# containerd sube índice + atestación) que contenga $CemPlataformaImagenes.
function Read-CemManifiestoImagen {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json, [Parameter(Mandatory)][string]$Referencia)
    try { $m = $Json | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "${Referencia}: la inspección del manifiesto no devolvió JSON" }
    if ($null -eq $m) { throw "${Referencia}: la inspección del manifiesto no devolvió nada" }
    if ("$($m.digest)" -notmatch $CemPatronDigest) { throw "${Referencia}: digest inválido '$($m.digest)'" }
    if ($CemTiposIndice -notcontains $m.mediaType) {
        throw "${Referencia}: se esperaba un índice de imágenes y es '$($m.mediaType)'; ¿cambió el almacén de imágenes de Docker?"
    }
    $plataformas = @($m.manifests | ForEach-Object { "$($_.platform.os)/$($_.platform.architecture)" })
    if ($plataformas -notcontains $CemPlataformaImagenes) {
        throw "${Referencia}: el manifiesto no tiene $CemPlataformaImagenes (tiene: $($plataformas -join ', '))"
    }
    $m.digest
}

# Digests que ci.ps1 subió para la SHA, uno por imagen pedida. Falla si la
# constancia no existe, no está en verde, no tiene imágenes (corrida con
# -SinSubirImagenes) o alguna imagen falta, es de otra SHA, de otra plataforma o
# tiene un digest inválido.
function Get-CemImagenesAprobadas {
    param([Parameter(Mandatory)][string]$Sha, [Parameter(Mandatory)][string[]]$Imagenes)
    if (-not (Test-CemCiAprobado -Sha $Sha)) { throw "ci.ps1 no tiene un resultado verde sobre $Sha" }
    $r = Read-CemResultadoCi -Sha $Sha
    if ($null -eq $r.imagenes) {
        throw "la constancia de ci.ps1 sobre $Sha no tiene imágenes subidas (¿se corrió con -SinSubirImagenes?); correr ci.ps1 sin ese parámetro"
    }
    $digests = [ordered]@{}
    foreach ($img in $Imagenes) {
        $d = $r.imagenes.$img
        if ($null -eq $d) { throw "la constancia de ci.ps1 sobre $Sha no tiene la imagen $img" }
        $esperada = Get-CemReferenciaImagen -Imagen $img -Sha $Sha
        if ($d.referencia -ne $esperada) { throw "la constancia dice que $img se subió como '$($d.referencia)', se esperaba '$esperada'" }
        if ($d.plataforma -ne $CemPlataformaImagenes) { throw "la constancia dice que $img es para '$($d.plataforma)', se esperaba $CemPlataformaImagenes" }
        if ("$($d.digest)" -notmatch $CemPatronDigest) { throw "la constancia tiene un digest inválido para ${img}: '$($d.digest)'" }
        $digests[$img] = $d.digest
    }
    $digests
}

# Problemas de una aplicación leída de Coolify (GET /applications/<uuid>) frente a
# lo que espera el mapa: tipo dockerimage e imagen de ghcr.io según su función.
function Get-CemProblemasAppCoolify {
    param([Parameter(Mandatory)]$Leida, [Parameter(Mandatory)]$App)
    $problemas = [System.Collections.Generic.List[string]]::new()
    if ($Leida.build_pack -ne 'dockerimage') {
        $problemas.Add("$($App.Nombre) es de tipo '$($Leida.build_pack)' en Coolify; se espera 'dockerimage' (Coolify ya no construye)")
    }
    $esperada = "$CemRegistroImagenes/$($App.Imagen)"
    if ($Leida.docker_registry_image_name -ne $esperada) {
        $problemas.Add("$($App.Nombre) usa la imagen '$($Leida.docker_registry_image_name)'; para la función $($App.Funcion) se espera '$esperada'")
    }
    , $problemas.ToArray()
}

# Problemas del contenedor en marcha frente a la imagen esperada. Recibe lo que
# devuelve la VM:
#   ImagenConfig  docker inspect -f '{{.Config.Image}}' <contenedor>
#   Etiquetas     docker inspect -f '{{json .Config.Labels}}' <contenedor>
#   RepoDigests   docker image inspect -f '{{json .RepoDigests}}' <id de la imagen>
function Get-CemProblemasContenedor {
    param(
        [Parameter(Mandatory)]$App, [Parameter(Mandatory)][string]$Sha, [Parameter(Mandatory)][string]$Digest,
        [Parameter(Mandatory)][AllowEmptyString()][string]$ImagenConfig,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Etiquetas,
        [Parameter(Mandatory)][AllowEmptyString()][string]$RepoDigests
    )
    $problemas = [System.Collections.Generic.List[string]]::new()
    $referencia = Get-CemReferenciaImagen -Imagen $App.Imagen -Sha $Sha
    if ($ImagenConfig.Trim() -ne $referencia) { $problemas.Add("$($App.Nombre): corre '$($ImagenConfig.Trim())', se espera '$referencia'") }
    $etq = $null
    $etiquetasJson = $true
    try { $etq = $Etiquetas | ConvertFrom-Json -ErrorAction Stop } catch { $etiquetasJson = $false; $problemas.Add("$($App.Nombre): las etiquetas del contenedor no son JSON") }
    # Sin etiquetas ('null') también es un problema: la imagen no es la que subió ci.ps1.
    if ($etiquetasJson -and ($null -eq $etq -or $etq.$CemEtiquetaRevision -ne $Sha)) {
        $problemas.Add("$($App.Nombre): la etiqueta $CemEtiquetaRevision es '$($etq.$CemEtiquetaRevision)', se espera $Sha")
    }
    $lista = $null
    try { $lista = @($RepoDigests | ConvertFrom-Json -ErrorAction Stop) } catch { $problemas.Add("$($App.Nombre): RepoDigests de la imagen no es JSON") }
    $conDigest = "$CemRegistroImagenes/$($App.Imagen)@$Digest"
    if ($null -ne $lista -and $lista -notcontains $conDigest) {
        $problemas.Add("$($App.Nombre): la imagen del contenedor no es $conDigest (RepoDigests: $($lista -join ', '))")
    }
    , $problemas.ToArray()
}
