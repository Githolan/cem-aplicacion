# ==============================================================================
# Consorcio en Mano - cliente mínimo de la API de Coolify y del acceso por SSH
# ==============================================================================
# Credenciales SOLO por nombre de variable de entorno; ningún valor se imprime,
# se escribe en disco ni aparece en un mensaje de error:
#   COOLIFY_BASE_URL         p. ej. la URL del panel (detrás de Cloudflare Access)
#   COOLIFY_API_TOKEN        token de la API de Coolify (Bearer)
#   CF_ACCESS_CLIENT_ID      token de servicio de Cloudflare Access
#   CF_ACCESS_CLIENT_SECRET  ídem
# Opcional:
#   CEM_SSH_DESTINO          destino de ssh hacia la VM (por defecto 'oci', el alias de ~/.ssh/config)
#
# Lo cargan (dot-source) cd.ps1 y test-deploy-map.ps1.
# ==============================================================================

$CemUserAgent           = 'cem-despliegue/1.0 (+https://github.com/Githolan/cem-aplicacion/tree/main/despliegue)'
$CemVariablesCoolify    = @('COOLIFY_BASE_URL', 'COOLIFY_API_TOKEN', 'CF_ACCESS_CLIENT_ID', 'CF_ACCESS_CLIENT_SECRET')
$CemSshDestinoPorDefecto = 'oci'
$CemSegundosHttp        = 30

# Falla si falta alguna variable; nombra solo las que faltan, nunca un valor.
function Assert-CemCredenciales {
    $faltan = $CemVariablesCoolify | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
    if ($faltan) { throw "faltan variables de entorno: $($faltan -join ', ')" }
}

function Get-CemEncabezadosCoolify {
    param([string]$TokenCoolify = $env:COOLIFY_API_TOKEN)
    @{
        'CF-Access-Client-Id'     = $env:CF_ACCESS_CLIENT_ID
        'CF-Access-Client-Secret' = $env:CF_ACCESS_CLIENT_SECRET
        'Authorization'           = "Bearer $TokenCoolify"
        'Accept'                  = 'application/json'
        'User-Agent'              = $CemUserAgent
    }
}

# Llama a /api/v1<Ruta>. Devuelve el JSON ya convertido. Ante un código >= 300 o un
# cuerpo que no es JSON (p. ej. la página de inicio de sesión de Access con 200)
# lanza un error con el método, la ruta y el código; del cuerpo solo se toma el
# campo "message" de Coolify, que no lleva secretos.
function Invoke-CemCoolify {
    param(
        [Parameter(Mandatory)][ValidateSet('GET', 'POST', 'PATCH')][string]$Metodo,
        [Parameter(Mandatory)][string]$Ruta,
        $Cuerpo,
        [string]$TokenCoolify = $env:COOLIFY_API_TOKEN
    )
    $base = $env:COOLIFY_BASE_URL.TrimEnd('/')
    $json = if ($null -ne $Cuerpo) { $Cuerpo | ConvertTo-Json -Depth 6 -Compress } else { $null }
    $r = Invoke-WebRequest -Method $Metodo -Uri "$base/api/v1$Ruta" -Headers (Get-CemEncabezadosCoolify -TokenCoolify $TokenCoolify) `
        -ContentType 'application/json' -Body $json -SkipHttpErrorCheck -TimeoutSec $CemSegundosHttp -MaximumRedirection 0 `
        -ErrorAction Stop
    $tipo = "$($r.Headers['Content-Type'])"
    $datos = $null
    if ($r.Content -and $tipo -match 'application/json') { $datos = $r.Content | ConvertFrom-Json }
    if ($r.StatusCode -ge 300) {
        $msj = if ($datos -and $datos.message) { ": $($datos.message)" } else { '' }
        $e = [System.Exception]::new("$Metodo $Ruta -> HTTP $($r.StatusCode)$msj")
        $e.Data['CodigoHttp'] = [int]$r.StatusCode
        throw $e
    }
    if ($r.Content -and $tipo -notmatch 'application/json') {
        throw "$Metodo $Ruta -> HTTP $($r.StatusCode) pero el cuerpo no es JSON ($tipo): ¿respondió Cloudflare Access en lugar de Coolify?"
    }
    $datos
}

# Lee una variable de ejecución (no de vista previa) de una aplicación. Devuelve
# $null si no existe; falla si hay más de una.
function Get-CemVariableApp {
    param([Parameter(Mandatory)][string]$Uuid, [Parameter(Mandatory)][string]$Clave)
    $hay = @((Invoke-CemCoolify GET "/applications/$Uuid/envs") | ForEach-Object { $_ } |
        Where-Object { $_.key -eq $Clave -and -not $_.is_preview })
    if ($hay.Count -gt 1) { throw "$Clave aparece $($hay.Count) veces en $Uuid" }
    if ($hay.Count -eq 1) { $hay[0].value } else { $null }
}

# Crea o actualiza una variable NO secreta y verifica la lectura. Solo para valores
# públicos (la SHA del commit); el valor aparece en los mensajes de error.
function Set-CemVariablePublicaApp {
    param([Parameter(Mandatory)][string]$Uuid, [Parameter(Mandatory)][string]$Clave, [Parameter(Mandatory)][string]$Valor)
    $cuerpo = @{ key = $Clave; value = $Valor; is_literal = $true; is_preview = $false }
    if ($null -ne (Get-CemVariableApp -Uuid $Uuid -Clave $Clave)) {
        Invoke-CemCoolify PATCH "/applications/$Uuid/envs" $cuerpo | Out-Null
    } else {
        Invoke-CemCoolify POST "/applications/$Uuid/envs" $cuerpo | Out-Null
    }
    $leida = Get-CemVariableApp -Uuid $Uuid -Clave $Clave
    if ($leida -ne $Valor) { throw "$Clave no quedó en '$Valor' en $Uuid (leído: '$leida')" }
}

# Corre un comando en la VM por ssh, sin pedir nada interactivo. Devuelve la salida
# como texto; falla si ssh sale con código distinto de 0.
function Invoke-CemSsh {
    param([Parameter(Mandatory)][string]$Comando)
    $destino = if ($env:CEM_SSH_DESTINO) { $env:CEM_SSH_DESTINO } else { $CemSshDestinoPorDefecto }
    $salida = & ssh -o BatchMode=yes -o ConnectTimeout=15 $destino $Comando 2>&1
    $codigo = $LASTEXITCODE
    $texto = ($salida | Out-String).TrimEnd()
    if ($codigo -ne 0) { throw "ssh $destino salió con código $codigo`: $texto" }
    $texto
}
