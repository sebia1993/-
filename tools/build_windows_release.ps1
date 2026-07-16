param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+\.\d+\.\d+(?:-rc\.\d+)?$')]
    [string]$Version
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DistRoot = Join-Path $Root "dist"
$BuildRoot = Join-Path $Root "build"
$PackageName = "internal-upload_${Version}_windows"
$PackageRoot = Join-Path $DistRoot $PackageName
$PyInstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$ServerWork = Join-Path $BuildRoot "server-work"
$ClientWork = Join-Path $BuildRoot "client-work"
$ZipPath = Join-Path $DistRoot "$PackageName.zip"
$ShaPath = "$ZipPath.sha256"
$ReleaseNotesPath = Join-Path $DistRoot "release_notes_$Version.md"
$ServerVersionInfo = Join-Path $BuildRoot "server-version.txt"
$ClientVersionInfo = Join-Path $BuildRoot "client-version.txt"
$TemplatesPath = Join-Path $Root "templates"
$StaticPath = Join-Path $Root "static"

foreach ($Path in @($PackageRoot, $PyInstallerDist, $ServerWork, $ClientWork, $ZipPath, $ShaPath, $ReleaseNotesPath)) {
    if (Test-Path $Path) { Remove-Item $Path -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $DistRoot, $BuildRoot, $PackageRoot, $PyInstallerDist, $ServerWork, $ClientWork | Out-Null

Push-Location $Root
try {
    $SourceVersion = (python -c "from app_version import APP_VERSION; print(APP_VERSION)").Trim()
    if ($SourceVersion -ne $Version) {
        throw "Source APP_VERSION $SourceVersion does not match requested release $Version"
    }
    $WorktreeStatus = git status --porcelain --untracked-files=all
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Git worktree state" }
    if ($WorktreeStatus) {
        throw "Release builds require a clean Git worktree so security_manifest.json matches the source commit"
    }
    $SourceCommit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { (git rev-parse HEAD).Trim() }

    python tools/generate_windows_version_info.py `
        --version $Version `
        --product-name "Internal Upload Server" `
        --description "Internal file upload and network measurement server" `
        --filename "InternalUploadServer.exe" `
        --output $ServerVersionInfo
    python tools/generate_windows_version_info.py `
        --version $Version `
        --product-name "Network Probe Client" `
        --description "Internal TCP network measurement client" `
        --filename "NetworkProbeClient.exe" `
        --output $ClientVersionInfo

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --noupx `
        --name InternalUploadServer `
        --version-file $ServerVersionInfo `
        --distpath $PyInstallerDist `
        --workpath $ServerWork `
        --specpath $BuildRoot `
        --add-data "${TemplatesPath};templates" `
        --add-data "${StaticPath};static" `
        app.py

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --noupx `
        --name NetworkProbeClient `
        --version-file $ClientVersionInfo `
        --distpath $PyInstallerDist `
        --workpath $ClientWork `
        --specpath $BuildRoot `
        probe_client.py

    $ServerBundle = Join-Path $PyInstallerDist "InternalUploadServer"
    $ClientBundle = Join-Path $PyInstallerDist "NetworkProbeClient"
    $ServerExe = Join-Path $ServerBundle "InternalUploadServer.exe"
    $ClientExe = Join-Path $ClientBundle "NetworkProbeClient.exe"
    if (-not (Test-Path $ServerExe)) { throw "PyInstaller did not create $ServerExe" }
    if (-not (Test-Path $ClientExe)) { throw "PyInstaller did not create $ClientExe" }

    Copy-Item (Join-Path $ServerBundle "*") $PackageRoot -Recurse
    $ClientTemplate = Join-Path $PackageRoot "client-template"
    New-Item -ItemType Directory -Force -Path $ClientTemplate | Out-Null
    Copy-Item (Join-Path $ClientBundle "*") $ClientTemplate -Recurse

    Copy-Item "config.ini" (Join-Path $PackageRoot "config.ini")
    Copy-Item "README.md" (Join-Path $PackageRoot "README.md")
    Copy-Item "RELEASE_NOTES.md" (Join-Path $PackageRoot "RELEASE_NOTES.md")
    Copy-Item "CHANGELOG.md" (Join-Path $PackageRoot "CHANGELOG.md")

    New-Item -ItemType Directory -Force -Path `
        (Join-Path $PackageRoot "data"), `
        (Join-Path $PackageRoot "data/network_check_results"), `
        (Join-Path $PackageRoot "data/network_probe_results"), `
        (Join-Path $PackageRoot "uploads") | Out-Null
    Copy-Item "data/upload_log.csv" (Join-Path $PackageRoot "data/upload_log.csv")
    Copy-Item "data/network_check_log.csv" (Join-Path $PackageRoot "data/network_check_log.csv")
    Copy-Item "data/network_check_session_log.csv" (Join-Path $PackageRoot "data/network_check_session_log.csv")
    Copy-Item "data/network_check_results/README_RESULTS_KO.txt" (Join-Path $PackageRoot "data/network_check_results/README_RESULTS_KO.txt")
    Copy-Item "data/network_probe_log.csv" (Join-Path $PackageRoot "data/network_probe_log.csv")
    Copy-Item "data/network_probe_results/README_RESULTS_KO.txt" (Join-Path $PackageRoot "data/network_probe_results/README_RESULTS_KO.txt")
    "업로드 파일이 저장되는 폴더입니다. 운영 중 생성된 파일은 GitHub에 올리지 마세요." | Set-Content -Path (Join-Path $PackageRoot "uploads/README_UPLOADS_KO.txt") -Encoding UTF8

    @"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 사내 업로드 서버를 시작합니다.
echo.
echo 서버가 시작되면 콘솔에 실제 접속 주소가 표시됩니다.
echo 웹 또는 TCP 측정 포트가 사용 중이면 빈 포트로 변경할지 물어봅니다.
echo 승인된 포트는 config.ini에 자동 저장됩니다.
echo Windows 방화벽은 자동 조회하거나 변경하지 않습니다.
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo.
InternalUploadServer.exe
pause
"@ | Set-Content -Path (Join-Path $PackageRoot "start_internal_upload.cmd") -Encoding UTF8

    @"
사내 업로드 $Version Windows 포터블 폴더 ZIP

서버 실행:
1. ZIP을 Windows 서버 PC의 원하는 폴더에 완전히 압축 해제합니다.
2. start_internal_upload.cmd를 더블클릭합니다.
3. 콘솔에 표시된 실제 접속 주소를 브라우저에서 엽니다. 기본 웹 포트는 8000입니다.
4. 포트 충돌 시 프로그램이 제안한 빈 포트를 승인하면 config.ini에 저장됩니다.
5. Windows 방화벽은 자동 조회하거나 변경하지 않습니다. 다른 PC 접속이 실패하면 표시된 포트를 확인하세요.

TCP 전송 성능 측정:
1. TCP 측정 서버는 기본으로 함께 시작됩니다. 기본 포트는 5201입니다.
2. 웹 화면의 TCP 전송 성능 측정에서 Windows 클라이언트 ZIP을 받습니다.
3. 측정 PC에서 ZIP 전체를 압축 해제하고 NetworkProbeClient.exe를 실행합니다.
4. 웹 화면에서 자동 등록된 PC를 선택해 측정합니다. 클라이언트 콘솔은 측정 중 열어 두세요.
5. 서버 IP 또는 웹 포트가 바뀌면 클라이언트 ZIP을 다시 받습니다.

보안 정보:
- 서버와 클라이언트는 기능이 분리된 별도 실행 파일입니다.
- 서버 시작 과정에서 PowerShell을 실행하지 않습니다.
- 실행파일, 스크립트, 매크로 문서와 디스크 이미지는 업로드할 수 없습니다.
- 압축파일 내부 검사와 파일 크기 제한은 적용하지 않습니다.
- 코드서명은 적용하지 않았습니다. SECURITY_REVIEW_KO.md와 SHA256SUMS.txt를 확인하세요.
"@ | Set-Content -Path (Join-Path $PackageRoot "README_START_HERE_KO.txt") -Encoding UTF8

    $PackagedServerExe = Join-Path $PackageRoot "InternalUploadServer.exe"
    $PackagedClientExe = Join-Path $ClientTemplate "NetworkProbeClient.exe"
    & $PackagedServerExe --smoke-check
    if ($LASTEXITCODE -ne 0) { throw "Server smoke check failed" }
    & $PackagedServerExe --probe-self-check
    if ($LASTEXITCODE -ne 0) { throw "Server probe self-check failed" }
    & $PackagedClientExe --self-check
    if ($LASTEXITCODE -ne 0) { throw "Client self-check failed" }

    python tools/generate_security_artifacts.py `
        --root $PackageRoot `
        --version $Version `
        --source-commit $SourceCommit `
        --requirements-lock (Join-Path $Root "requirements-windows.lock")

    Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $ZipPath -Force
    python tools/verify_release_zip.py --zip $ZipPath --version $Version

    $Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText($ShaPath, "$Hash  $PackageName.zip`n", [System.Text.Encoding]::ASCII)
    if ([System.IO.File]::ReadAllBytes($ShaPath) -contains 13) {
        throw "SHA256 file must use LF line endings"
    }

    @"
# $Version - 사내 업로드 Windows 보안 구조 개선 사전 릴리즈

## 주요 변경

- PyInstaller one-file 대신 임시 자체 압축 해제가 없는 포터블 onedir 구조
- 서버 전용 ``InternalUploadServer.exe``와 TCP 전용 ``NetworkProbeClient.exe`` 분리
- 서버 시작 시 PowerShell과 ``ExecutionPolicy Bypass`` 실행 제거
- 웹 클라이언트 ZIP에 서버 EXE와 CMD를 넣지 않고 고정 클라이언트 해시와 JSON 자동 연결 설정 제공
- 실행파일, 스크립트, 매크로 문서와 디스크 이미지 업로드 차단
- PE 버전 정보, 파일별 SHA256, CycloneDX SBOM과 보안 검토 문서 포함
- 고정된 Windows Python 의존성 해시로 빌드

## 실행

1. ``$PackageName.zip``을 완전히 압축 해제합니다.
2. ``start_internal_upload.cmd``를 실행합니다.
3. TCP 측정 PC에서는 서버 웹 화면에서 클라이언트 ZIP을 받고 ``NetworkProbeClient.exe``를 실행합니다.

## 보안상 제한

- 코드서명은 적용하지 않았으므로 보안 제품 경고가 완전히 사라지는 것을 보장하지 않습니다.
- 사내망 전체 무인증 접근, 파일 크기 무제한, 압축파일 내부 미검사와 TCP 장기 폴링은 유지됩니다.
- Windows 방화벽은 자동 조회하거나 변경하지 않습니다.

SHA256: ``$Hash``
"@ | Set-Content -Path $ReleaseNotesPath -Encoding UTF8

    Write-Host "Built $ZipPath"
    Write-Host "SHA256 $Hash"
}
finally {
    Pop-Location
}
