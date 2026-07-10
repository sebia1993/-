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
    & $ExePath --probe-self-check

    Copy-Item $ExePath (Join-Path $PackageRoot "InternalUpload.exe")
    Copy-Item "config.ini" (Join-Path $PackageRoot "config.ini")
    Copy-Item "README.md" (Join-Path $PackageRoot "README.md")
    Copy-Item "RELEASE_NOTES.md" (Join-Path $PackageRoot "RELEASE_NOTES.md")
    Copy-Item "CHANGELOG.md" (Join-Path $PackageRoot "CHANGELOG.md")

    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "data"), (Join-Path $PackageRoot "data/network_check_results"), (Join-Path $PackageRoot "uploads") | Out-Null
    Copy-Item "data/upload_log.csv" (Join-Path $PackageRoot "data/upload_log.csv")
    Copy-Item "data/network_check_log.csv" (Join-Path $PackageRoot "data/network_check_log.csv")
    Copy-Item "data/network_check_session_log.csv" (Join-Path $PackageRoot "data/network_check_session_log.csv")
    Copy-Item "data/network_check_results/README_RESULTS_KO.txt" (Join-Path $PackageRoot "data/network_check_results/README_RESULTS_KO.txt")
    New-Item -ItemType Directory -Force -Path (Join-Path $PackageRoot "data/network_probe_results") | Out-Null
    Copy-Item "data/network_probe_log.csv" (Join-Path $PackageRoot "data/network_probe_log.csv")
    Copy-Item "data/network_probe_results/README_RESULTS_KO.txt" (Join-Path $PackageRoot "data/network_probe_results/README_RESULTS_KO.txt")
    "업로드 파일이 저장되는 폴더입니다. 운영 중 생성된 파일은 GitHub에 올리지 마세요." | Set-Content -Path (Join-Path $PackageRoot "uploads/README_UPLOADS_KO.txt") -Encoding UTF8

    @"
@echo off
chcp 65001 >nul
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
"@ | Set-Content -Path (Join-Path $PackageRoot "start_internal_upload.cmd") -Encoding UTF8

    @"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo TCP 정밀 측정 클라이언트를 시작합니다.
echo 서버 PC 이름 또는 사내 IP와 웹 포트를 입력하세요.
echo 예: 192.168.0.10:8000 또는 SERVER-PC:8000
echo.
set /p "SERVER_URL=서버 주소: "
if "%SERVER_URL%"=="" (
  echo 서버 주소가 비어 있습니다.
  pause
  exit /b 1
)
echo.
InternalUpload.exe --probe-client --server "%SERVER_URL%"
pause
"@ | Set-Content -Path (Join-Path $PackageRoot "start_tcp_probe_client.cmd") -Encoding UTF8

    @"
사내 업로드 $Version Windows 실행 ZIP

1. 이 ZIP 파일을 Windows 서버 PC의 원하는 폴더에 완전히 압축 해제합니다.
2. start_internal_upload.cmd를 더블클릭합니다.
3. 같은 PC에서는 http://127.0.0.1:8000 으로 접속합니다.
4. 다른 PC에서는 서버 PC의 사내 IP와 8000 포트로 접속합니다.

TCP 정밀 측정:
1. 서버 config.ini의 [network_probe] ENABLED=true를 설정합니다.
2. 서버 PC 방화벽에서 TCP 5201 포트를 허용합니다.
3. 측정 대상 PC에서 start_tcp_probe_client.cmd를 실행합니다.
4. 웹 화면의 TCP 정밀 측정에서 PC 이름과 IP를 선택합니다.

설정:
- config.ini에서 PORT, BASE_URL, STORAGE_ROOT, DELETE_ALLOWED_IPS와 TCP 측정 포트를 수정할 수 있습니다.
- Windows 방화벽에서 TCP 8000 포트가 막혀 있으면 다른 PC에서 접속할 수 없습니다.
- TCP 정밀 측정에는 TCP 5201 포트도 필요합니다.

주의:
- 코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.
- 실제 업로드 파일과 운영 CSV·JSON 기록은 GitHub에 올리지 마세요.
- TCP 정밀 측정은 Windows 클라이언트용이며 Android에서는 웹 HTTP 측정만 지원합니다.
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
- HTTP/1.1 브라우저 환경에서 안정적으로 동작하는 조각 단위 업로드 측정
- 평균/구간 속도 표시와 측정 취소 지원
- 중복 파일명 경고 후 ID를 붙여 저장
- 최근 50개 업로드 목록 표시
- 허용 IP에서만 파일과 CSV 기록 삭제
- ``data/upload_log.csv`` 기반 업로드 기록
- ``data/network_check_log.csv`` 기반 네트워크 체크 기록
- 3초 워밍 후 10초/30초를 측정하는 HTTP 지속 측정
- 1개/4개 HTTP 연결, 1초 구간 그래프, HTTP 응답시간, 취소 지원
- ``data/network_check_session_log.csv`` 요약과 세션별 JSON 상세 결과
- 별도 설치 없이 같은 ``InternalUpload.exe``를 사용하는 TCP 측정 클라이언트 모드
- TCP 업로드/다운로드/전체, 1개/4개 스트림, 3초 워밍업, 10초/30초 측정
- Windows TCP_INFO 기반 RTT, 최소 RTT, 혼잡 윈도우, 재전송 바이트 표시
- ``data/network_probe_log.csv`` 요약과 세션별 JSON 상세 결과

## 검증

- ``python -m compileall app.py network_sustained.py network_measurement.py network_probe tests tools`` 통과
- ``python -m pytest -q`` 통과
- ``InternalUpload.exe --smoke-check`` 통과
- ``InternalUpload.exe --probe-self-check`` 통과
- Windows ZIP 구조 검증 통과

## 제한사항

- 로그인, 권한관리, 수신자 지정, 만료일, 관리자 페이지는 포함하지 않습니다.
- DB를 사용하지 않습니다.
- 지속 측정은 브라우저 HTTP 전송 성능이며 TCP·UDP 정밀 측정이나 iperf 결과와는 다릅니다.
- TCP 정밀 측정은 자체 TCP 프로토콜이며 iperf 실행파일·라이브러리·호환 프로토콜을 사용하지 않습니다.
- UDP 정밀 측정과 Android 네이티브 TCP 클라이언트는 포함하지 않습니다.
- 코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.
- GitHub 기본 ``Source code (zip)`` / ``Source code (tar.gz)``는 소스 아카이브이며 일반 실행용 파일은 아닙니다.
"@ | Set-Content -Path $ReleaseNotesPath -Encoding UTF8

    Write-Host "Built $ZipPath"
    Write-Host "SHA256 $Hash"
}
finally {
    Pop-Location
}
