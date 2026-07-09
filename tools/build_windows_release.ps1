param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+\.\d+\.\d+$')]
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
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$ZipPath = Join-Path $DistRoot "$PackageName.zip"
$ShaPath = "$ZipPath.sha256"
$ReleaseNotesPath = Join-Path $DistRoot "release_notes_$Version.md"
$TemplatesPath = Join-Path $Root "templates"
$StaticPath = Join-Path $Root "static"

if (Test-Path $PackageRoot) { Remove-Item $PackageRoot -Recurse -Force }
if (Test-Path $PyInstallerDist) { Remove-Item $PyInstallerDist -Recurse -Force }
if (Test-Path $PyInstallerWork) { Remove-Item $PyInstallerWork -Recurse -Force }
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
if (Test-Path $ShaPath) { Remove-Item $ShaPath -Force }
if (Test-Path $ReleaseNotesPath) { Remove-Item $ReleaseNotesPath -Force }

New-Item -ItemType Directory -Force -Path $DistRoot, $BuildRoot, $PackageRoot, $PyInstallerDist, $PyInstallerWork | Out-Null

Push-Location $Root
try {
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --name InternalUpload `
        --distpath $PyInstallerDist `
        --workpath $PyInstallerWork `
        --specpath $BuildRoot `
        --add-data "${TemplatesPath};templates" `
        --add-data "${StaticPath};static" `
        app.py

    $ExePath = Join-Path $PyInstallerDist "InternalUpload.exe"
    if (-not (Test-Path $ExePath)) {
        throw "PyInstaller did not create $ExePath"
    }

    & $ExePath --smoke-check

    Copy-Item $ExePath (Join-Path $PackageRoot "InternalUpload.exe")
    Copy-Item "config.ini" (Join-Path $PackageRoot "config.ini")
    Copy-Item "README.md" (Join-Path $PackageRoot "README.md")
    Copy-Item "RELEASE_NOTES.md" (Join-Path $PackageRoot "RELEASE_NOTES.md")
    Copy-Item "CHANGELOG.md" (Join-Path $PackageRoot "CHANGELOG.md")

    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "data"), (Join-Path $PackageRoot "uploads") | Out-Null
    Copy-Item "data/upload_log.csv" (Join-Path $PackageRoot "data/upload_log.csv")
    Copy-Item "data/network_check_log.csv" (Join-Path $PackageRoot "data/network_check_log.csv")
    "업로드 파일이 저장되는 폴더입니다. 운영 중 생성된 파일은 GitHub에 올리지 마세요." | Set-Content -Path (Join-Path $PackageRoot "uploads/README_UPLOADS_KO.txt") -Encoding UTF8

    @"
@echo off
cd /d "%~dp0"
echo 사내 업로드 서버를 시작합니다.
echo.
echo 서버가 시작되면 브라우저에서 아래 주소로 접속하세요.
echo   http://127.0.0.1:8000
echo.
echo 다른 PC에서 접속하려면 config.ini의 BASE_URL 또는 서버 PC 사내 IP를 확인하세요.
echo 종료하려면 이 창에서 Ctrl+C를 누르세요.
echo.
InternalUpload.exe
pause
"@ | Set-Content -Path (Join-Path $PackageRoot "start_internal_upload.cmd") -Encoding ASCII

    @"
사내 업로드 $Version Windows 실행 ZIP

1. 이 ZIP 파일을 Windows 서버 PC의 원하는 폴더에 완전히 압축 해제합니다.
2. start_internal_upload.cmd를 더블클릭합니다.
3. 같은 PC에서는 http://127.0.0.1:8000 으로 접속합니다.
4. 다른 PC에서는 서버 PC의 사내 IP와 8000 포트로 접속합니다.

설정:
- config.ini에서 PORT, BASE_URL, STORAGE_ROOT, DELETE_ALLOWED_IPS를 수정할 수 있습니다.
- Windows 방화벽에서 TCP 8000 포트가 막혀 있으면 다른 PC에서 접속할 수 없습니다.

주의:
- 코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.
- 실제 업로드 파일과 운영 CSV 기록은 GitHub에 올리지 마세요.
"@ | Set-Content -Path (Join-Path $PackageRoot "README_START_HERE_KO.txt") -Encoding UTF8

    Compress-Archive -Path (Join-Path $PackageRoot "*") -DestinationPath $ZipPath -Force

    python tools/verify_release_zip.py --zip $ZipPath --version $Version

    $Hash = (Get-FileHash $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $PackageName.zip" | Set-Content -Path $ShaPath -Encoding ASCII

    @"
# $Version - 사내 업로드 Windows 실행 ZIP

사내 장애처리용 미니 파일 업로드 도구의 Windows 실행 ZIP 릴리즈입니다.

## 다운로드 파일

- $PackageName.zip
- SHA256: ``$Hash``

## 실행 방법

1. ZIP 파일을 Windows 서버 PC에 다운로드합니다.
2. 압축을 완전히 해제합니다.
3. ``start_internal_upload.cmd``를 더블클릭합니다.
4. 같은 PC에서는 ``http://127.0.0.1:8000``으로 접속합니다.
5. 다른 PC에서는 서버 PC의 사내 IP와 포트를 사용합니다.

## 포함 기능

- Python 설치 없이 실행되는 ``InternalUpload.exe``
- 파일 업로드, 선택 메모 입력, 저장 하위 폴더 지정
- ``config.ini`` 기반 ``BASE_URL``, 포트, 저장 기준 폴더, 삭제 허용 IP 설정
- ``/download/<upload_id>`` 형식의 직접 다운로드 링크
- 업로드/다운로드 전송 속도를 확인하는 네트워크 체크 모드
- 중복 파일명 경고 후 ID를 붙여 저장
- 최근 50개 업로드 목록 표시
- 허용 IP에서만 파일과 CSV 기록 삭제
- ``data/upload_log.csv`` 기반 업로드 기록
- ``data/network_check_log.csv`` 기반 네트워크 체크 기록

## 검증

- ``python -m compileall app.py tests tools`` 통과
- ``python -m pytest -q`` 통과
- ``InternalUpload.exe --smoke-check`` 통과
- Windows ZIP 구조 검증 통과

## 제한사항

- 로그인, 권한관리, 수신자 지정, 만료일, 관리자 페이지는 포함하지 않습니다.
- DB를 사용하지 않습니다.
- 코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.
- GitHub 기본 ``Source code (zip)`` / ``Source code (tar.gz)``는 소스 아카이브이며 일반 실행용 파일은 아닙니다.
"@ | Set-Content -Path $ReleaseNotesPath -Encoding UTF8

    Write-Host "Built $ZipPath"
    Write-Host "SHA256 $Hash"
}
finally {
    Pop-Location
}
