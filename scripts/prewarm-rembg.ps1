$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvDirectory = Join-Path $root '.venv-rembg'
$pythonExecutable = Join-Path $venvDirectory 'Scripts\python.exe'
$rembgExecutable = Join-Path $venvDirectory 'Scripts\rembg.exe'
$modelCache = Join-Path $root '.cache\rembg'
$fixture = Join-Path $root 'fixtures\garment\front.png'
$outputDirectory = Join-Path $root 'tmp'
$prewarmOutput = Join-Path $outputDirectory 'prewarm-mask.png'

foreach ($requiredPath in @($pythonExecutable, $rembgExecutable, $fixture)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "必要なファイルがありません: $requiredPath。先に .\scripts\setup-rembg.ps1 を実行してください。"
    }
}

$env:REMBG_HOME = $modelCache
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$serverStartedHere = $false
$portConnection = Get-NetTCPConnection -LocalPort 7000 -ErrorAction SilentlyContinue
if ($null -eq $portConnection) {
    $stdout = Join-Path $outputDirectory 'rembg.stdout.log'
    $stderr = Join-Path $outputDirectory 'rembg.stderr.log'
    $server = Start-Process -FilePath $rembgExecutable -ArgumentList @('s', '--host', '127.0.0.1', '--port', '7000', '--log_level', 'warning', '--threads', '1', '--no-ui') -WorkingDirectory $root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $serverStartedHere = $true
    Write-Output "Started rembg PID=$($server.Id)"
}

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    $portConnection = Get-NetTCPConnection -LocalPort 7000 -ErrorAction SilentlyContinue
    if ($null -ne $portConnection) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    throw 'rembgが127.0.0.1:7000で起動しませんでした。'
}

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw 'curl.exeが必要です。'
}

& curl.exe --fail-with-body --silent --show-error --max-time 120 -X POST 'http://127.0.0.1:7000/api/remove' -F "file=@$fixture;type=image/png" -F 'model=birefnet-general-lite' -F 'om=true' -o $prewarmOutput -w "HTTP_STATUS=%{http_code}`nCONTENT_TYPE=%{content_type}`nBYTES=%{size_download}`n"
if ($LASTEXITCODE -ne 0) {
    throw "mask-only prewarm requestに失敗しました。exit=$LASTEXITCODE"
}

$metadata = & $pythonExecutable -c "from PIL import Image; import sys; im=Image.open(sys.argv[1]); print(f'FORMAT={im.format} SIZE={im.size[0]}x{im.size[1]} MODE={im.mode} BBOX={im.getbbox()}')" $prewarmOutput
if ($LASTEXITCODE -ne 0) { throw 'prewarm maskのPNG検証に失敗しました。' }

Write-Output "PREWARM_OUTPUT=$prewarmOutput"
Write-Output $metadata
if ($serverStartedHere) {
    Write-Output 'rembgはprewarm後も127.0.0.1:7000で稼働しています。'
} else {
    Write-Output '既存の127.0.0.1:7000 rembgを使用しました。'
}
