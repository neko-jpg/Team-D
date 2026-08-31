$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvDirectory = Join-Path $root '.venv-rembg'
$pythonExecutable = Join-Path $venvDirectory 'Scripts\python.exe'
$requirements = Join-Path $root 'requirements-rembg.txt'
$modelCache = Join-Path $root '.cache\rembg'

if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    & python -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 venvの作成に失敗しました。" }
}

$pythonVersion = & $pythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
if ($pythonVersion -notmatch '^3\.11\.') {
    throw "Python 3.11が必要です。検出値: $pythonVersion"
}

& $pythonExecutable -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pipの更新に失敗しました。" }

& $pythonExecutable -m pip install --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw "rembgの依存関係の導入に失敗しました。" }

New-Item -ItemType Directory -Force -Path $modelCache | Out-Null
$env:REMBG_HOME = $modelCache
$rembgExecutable = Join-Path $venvDirectory 'Scripts\rembg.exe'
& $rembgExecutable d birefnet-general-lite
if ($LASTEXITCODE -ne 0) { throw "birefnet-general-liteのdownloadに失敗しました。" }

$modelPath = Join-Path $modelCache 'models\birefnet-general-lite\birefnet-general-lite.onnx'
if (-not (Test-Path -LiteralPath $modelPath)) {
    throw "モデルファイルが見つかりません: $modelPath"
}

Write-Output "Python=$pythonVersion"
Write-Output "rembg=$(& $pythonExecutable -c 'import rembg; print(rembg.__version__)')"
Write-Output "model=$modelPath"
