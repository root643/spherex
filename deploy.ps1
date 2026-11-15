# Despliega el servidor SPHEREx en la maquina de Oracle Cloud con un solo comando.
# Uso (PowerShell, dentro de esta carpeta):
#     powershell -ExecutionPolicy Bypass -File .\deploy.ps1
# Opcional:  -Server 51.170.50.113  -User ubuntu  -Key .\ssh-key-2026-10-24.key
param(
    [string]$Server = "51.170.50.113",
    [string]$User = "",
    [string]$Key = (Join-Path $PSScriptRoot "ssh-key-2026-10-24.key")
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Key)) { throw "No encuentro la clave: $Key" }
foreach ($tool in "ssh", "scp", "tar") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Falta '$tool' (OpenSSH/tar vienen con Windows 10 y 11)." }
}

# OpenSSH en Windows rechaza claves legibles por otros usuarios
icacls $Key /inheritance:r /grant:r "${env:USERNAME}:R" | Out-Null

$opts = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15")

if (-not $User) {
    Write-Host "Buscando el usuario SSH..."
    foreach ($u in "ubuntu", "opc", "root") {
        $ErrorActionPreference = "Continue"
        & ssh @opts -o BatchMode=yes "$u@$Server" "true" 2>$null
        $ok = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = "Stop"
        if ($ok) { $User = $u; break }
    }
    if (-not $User) { throw "No pude entrar por SSH en $Server con esa clave (probe ubuntu, opc y root). Comprueba la IP, la clave y que el puerto 22 este abierto." }
}
Write-Host "Conectado como $User@$Server"

$tgz = Join-Path $env:TEMP "spherex-server.tgz"
& tar -czf $tgz -C $PSScriptRoot server
if ($LASTEXITCODE -ne 0) { throw "tar ha fallado" }

Write-Host "Subiendo el paquete..."
& scp @opts $tgz "${User}@${Server}:/tmp/spherex-server.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp ha fallado" }

Write-Host "Instalando en el servidor (unos minutos la primera vez)..."
$remote = "set -o pipefail; rm -rf /tmp/spherex-deploy && mkdir -p /tmp/spherex-deploy && tar -xzf /tmp/spherex-server.tgz -C /tmp/spherex-deploy && sudo bash /tmp/spherex-deploy/server/install.sh 2>&1 | tee /tmp/spherex-install.log"
& ssh @opts "$User@$Server" $remote
$code = $LASTEXITCODE
Remove-Item $tgz -ErrorAction SilentlyContinue

Write-Host ""
if ($code -eq 0) { Write-Host "Listo: http://$Server/" -ForegroundColor Green }
else { Write-Host "La instalacion termino con avisos. Copia la salida de arriba (tambien queda en /tmp/spherex-install.log del servidor)." -ForegroundColor Yellow }
