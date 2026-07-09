# Release Notes 운영 규칙

현재 앱 버전: `v0.1.0`

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 현재 GitHub Actions가 Windows 실행 ZIP을 만들고 GitHub Release에 업로드합니다. Release 본문은 이 파일과 `CHANGELOG.md` 기준으로 작성합니다.

## v0.1.0 - 2026-07-09

초기 사내 장애처리용 미니 업로드 도구입니다.

포함된 기능:

- Windows PC에서 `run.bat`로 실행하는 Python Flask 웹앱
- Python 설치 없이 실행할 수 있는 Windows EXE ZIP Release asset
- 파일 업로드, 선택 메모 입력, 저장 하위 폴더 지정
- `config.ini` 기반 `BASE_URL`, 포트, 저장 기준 폴더, 삭제 허용 IP 설정
- `BASE_URL` 우선 다운로드 링크 생성, 미설정 시 서버 PC IP 기반 링크 생성
- `localhost` 또는 `127.0.0.1` 링크가 생성될 때 다른 PC 사용 불가 경고 표시
- `/download/<upload_id>` 형식의 ID 기반 직접 다운로드 링크
- 같은 이름의 파일이 이미 있으면 먼저 경고하고, 사용자가 확인하면 ID를 붙여 저장
- 최근 50개 업로드 목록 표시
- 설정된 허용 IP에서만 파일과 CSV 기록 삭제
- `data/upload_log.csv` 기반 업로드 기록
- DB, 로그인, 권한관리, 수신자 지정, 만료일, 관리자 페이지 제외

## Release 전 문서 점검

GitHub에 push하거나 Release를 준비하기 전에 아래 문서를 함께 확인합니다.

- `README.md`: 설치, 실행, 설정, 방화벽, 업로드, 다운로드, 삭제, 제한사항
- `RELEASE_NOTES.md`: 릴리즈 설명 규칙과 현재 배포 기준
- `CHANGELOG.md`: 구현된 변경과 제외된 항목 구분

다음 항목이 바뀌면 문서도 같은 변경에 포함합니다.

1. 실행 방법 또는 Python 요구사항
2. `config.ini` 키와 기본값
3. 서버 포트, `BASE_URL`, IP 자동 감지 방식
4. 저장 폴더 정책과 허용 경로
5. CSV 필드 또는 기록 위치
6. 삭제 허용 IP와 삭제 동작
7. 업로드 중복 파일 처리
8. GitHub Release asset 또는 배포 ZIP 정책

## GitHub Release / Asset 계약

현재 GitHub Release는 태그 기준으로 생성하고, Windows 실행 ZIP은 GitHub Actions에서 빌드해 업로드합니다.

- 태그 형식: `v0.1.0`처럼 앱 버전과 맞춥니다.
- Release 제목: `v0.1.0 - 사내 업로드 Windows 실행 ZIP`
- Release 본문: 포함 기능, 제외 항목, 검증 명령, 실행 방법, asset 정책을 한국어로 적습니다.
- 직접 업로드하는 Release asset: `internal-upload_v0.1.0_windows.zip`
- SHA256 checksum은 Release 본문에 기록합니다.

GitHub가 자동으로 표시하는 `Source code (zip)` / `Source code (tar.gz)`는 tag 기준 소스 아카이브입니다. 일반 사용자는 `internal-upload_v0.1.0_windows.zip`을 다운로드합니다.

ZIP 내부 구조:

- `InternalUpload.exe`
- `start_internal_upload.cmd`
- `config.ini`
- `README_START_HERE_KO.txt`
- `README.md`, `RELEASE_NOTES.md`, `CHANGELOG.md`
- `data/upload_log.csv`
- `uploads/README_UPLOADS_KO.txt`

## 검증 기준

Release 또는 GitHub push 전에 다음 검증을 실행합니다.

```powershell
python -m compileall app.py tests
python -m pytest -q
```

macOS 작업 환경에서는 다음 명령을 사용합니다.

```bash
.venv/bin/python -m compileall app.py tests
.venv/bin/python -m pytest -q
```

Windows Release ZIP 검증은 GitHub Actions `windows-latest`에서 실행합니다.

```powershell
python -m compileall app.py tests tools
python -m pytest -q
pwsh -NoProfile -File .\tools\build_windows_release.ps1 -Version v0.1.0
InternalUpload.exe --smoke-check
python tools\verify_release_zip.py --zip dist\internal-upload_v0.1.0_windows.zip --version v0.1.0
```

## 작성하지 않을 내용

- 실제 사내 IP, 서버 PC 이름, 사용자 계정, 비밀번호
- 실제 장애자료 파일명, 메모, 업로드 CSV 기록
- 고객명, 사이트명, 내부망 식별자
- 아직 구현하지 않은 로그인, 만료일, 관리자 페이지
