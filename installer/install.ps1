# Installation des composants de Book Translator (lancé par l'installateur).
# Chaque étape est sautée si le composant est déjà là : relancer l'installateur
# répare ou met à jour sans tout retélécharger.
param(
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [string]$Lang = "ko",
    [string]$Model = "translategemma:12b"
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Host.UI.RawUI.WindowTitle = "Book Translator - Setup"

$PY_VERSION   = "3.12.10"
$PY_URL       = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip"
$GETPIP_URL   = "https://bootstrap.pypa.io/get-pip.py"
$OLLAMA_URL   = "https://ollama.com/download/OllamaSetup.exe"
$TESS_URL     = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
$TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main"
$TESS_LANGS   = @("osd","eng","fra","kor","spa","deu","ita","por","nld","rus","jpn","chi_sim","ara")
$TESS_DIR     = "C:\Program Files\Tesseract-OCR"

$T = @{
  ko = @{ s1="1/5  Python 설치"; s2="2/5  Python 라이브러리 설치 (몇 분 걸릴 수 있습니다)"; s3="3/5  Tesseract OCR 및 언어 데이터 설치";
          s4="4/5  Ollama 설치"; s5="5/5  AI 모델 다운로드: {0} (용량이 커서 시간이 걸립니다)"; skip="이미 설치됨"; nomodel="모델 다운로드 건너뜀";
          ok="설치가 끝났습니다. 바탕화면의 Book Translator 아이콘으로 실행하세요."; err="설치 중 오류:"; enter="Enter 키를 누르면 닫힙니다";
          dl="다운로드:"; wait="Ollama 시작을 기다리는 중…" }
  en = @{ s1="1/5  Installing Python"; s2="2/5  Installing Python libraries (may take a few minutes)"; s3="3/5  Installing Tesseract OCR and language data";
          s4="4/5  Installing Ollama"; s5="5/5  Downloading the AI model: {0} (large, please wait)"; skip="already installed"; nomodel="model download skipped";
          ok="Installation complete. Start it with the Book Translator icon on your desktop."; err="Error during installation:"; enter="Press Enter to close";
          dl="Downloading:"; wait="Waiting for Ollama to start…" }
  fr = @{ s1="1/5  Installation de Python"; s2="2/5  Installation des bibliothèques Python (quelques minutes)"; s3="3/5  Installation de Tesseract OCR et des langues";
          s4="4/5  Installation d'Ollama"; s5="5/5  Téléchargement du modèle IA : {0} (gros fichier, patience)"; skip="déjà installé"; nomodel="téléchargement du modèle sauté";
          ok="Installation terminée. Lance le programme avec l'icône Book Translator du bureau."; err="Erreur pendant l'installation :"; enter="Appuie sur Entrée pour fermer";
          dl="Téléchargement :"; wait="Attente du démarrage d'Ollama…" }
}
if (-not $T.ContainsKey($Lang)) { $Lang = "en" }
$M = $T[$Lang]

function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "   $msg" -ForegroundColor DarkGray }

function Get-File($url, $out) {
    Info "$($M.dl) $url"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    for ($i = 1; $i -le 3; $i++) {
        if ($curl) {
            & curl.exe -L --fail --retry 3 --progress-bar -o $out $url
            if ($LASTEXITCODE -eq 0) { return }
        } else {
            try {
                $ProgressPreference = "SilentlyContinue"
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out
                return
            } catch {}
        }
        Start-Sleep -Seconds (3 * $i)
    }
    throw "download failed: $url"
}

function Find-Ollama {
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "C:\Program Files\Ollama\ollama.exe")) {
        if (Test-Path $p) { return $p }
    }
    $c = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    return $null
}

function Test-Port($port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $port); $c.Close(); return $true }
    catch { return $false }
}

$tmp = Join-Path $InstallDir "setup\downloads"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    # ---------- 1. Python portable (dans le dossier du programme) ----------
    Step $M.s1
    $pyDir = Join-Path $InstallDir "python"
    $py = Join-Path $pyDir "python.exe"
    if (Test-Path $py) { Info $M.skip } else {
        $zip = Join-Path $tmp "python.zip"
        Get-File $PY_URL $zip
        Expand-Archive -Path $zip -DestinationPath $pyDir -Force
        # active « site » pour que pip et les bibliothèques fonctionnent
        $pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
        (Get-Content $pth.FullName) -replace "^#\s*import site", "import site" | Set-Content $pth.FullName -Encoding ASCII
        $getpip = Join-Path $tmp "get-pip.py"
        Get-File $GETPIP_URL $getpip
        & $py $getpip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw "get-pip" }
    }

    # ---------- 2. Bibliothèques ----------
    Step $M.s2
    & $py -m pip install --no-warn-script-location --disable-pip-version-check -r (Join-Path $InstallDir "app\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install" }

    # ---------- 3. Tesseract + langues ----------
    Step $M.s3
    if (Test-Path "$TESS_DIR\tesseract.exe") { Info $M.skip } else {
        $setup = Join-Path $tmp "tesseract-setup.exe"
        Get-File $TESS_URL $setup
        Start-Process -FilePath $setup -ArgumentList "/S" -Wait
        if (-not (Test-Path "$TESS_DIR\tesseract.exe")) { throw "Tesseract" }
    }
    foreach ($code in $TESS_LANGS) {
        $f = "$TESS_DIR\tessdata\$code.traineddata"
        if (-not (Test-Path $f)) { Get-File "$TESSDATA_URL/$code.traineddata" $f }
    }

    # ---------- 4. Ollama ----------
    Step $M.s4
    $ollama = Find-Ollama
    if ($ollama) { Info $M.skip } else {
        $setup = Join-Path $tmp "OllamaSetup.exe"
        Get-File $OLLAMA_URL $setup
        Start-Process -FilePath $setup -ArgumentList "/VERYSILENT","/NORESTART","/SUPPRESSMSGBOXES" -Wait
        $ollama = Find-Ollama
        if (-not $ollama) { throw "Ollama" }
    }
    if (-not (Test-Port 11434)) {
        Info $M.wait
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 60 -and -not (Test-Port 11434); $i++) { Start-Sleep -Seconds 1 }
    }

    # ---------- 5. Modèle IA ----------
    if ($Model -eq "none") { Step $M.nomodel } else {
        Step ($M.s5 -f $Model)
        & $ollama pull $Model
        if ($LASTEXITCODE -ne 0) { & $ollama pull $Model }     # 2e essai (coupure réseau)
        if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model" }
    }

    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host $M.ok -ForegroundColor Green
    Start-Sleep -Seconds 4
    exit 0
}
catch {
    Write-Host ""
    Write-Host "$($M.err) $_" -ForegroundColor Red
    Read-Host $M.enter | Out-Null
    exit 1
}
