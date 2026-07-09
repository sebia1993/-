# Release Notes 운영 규칙

현재 앱 버전: `v0.2.1`

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 현재 GitHub Actions가 Windows 실행 ZIP을 만들고 GitHub Release에 업로드합니다. Release 본문은 이 파일과 `CHANGELOG.md` 기준으로 작성합니다.

## v0.2.1 - 2026-07-09

네트워크 체크 업로드 측정 안정화 릴리즈입니다.

수정된 문제:

- Edge/Chrome + HTTP/1.1 환경에서 업로드 측정이 `Failed to fetch`로 실패할 수 있던 문제를 수정
- 업로드 진행률이 10%에서 멈춘 것처럼 보일 수 있던 문제를 수정
- 브라우저 요청 스트리밍 대신 1MB 조각 단위 일반 POST 방식으로 변경
- 진행률을 서버가 받은 조각 기준으로 갱신하도록 조정
- 업로드 측정 실패 시 어느 단계에서 실패했는지 더 구체적으로 표시

## v0.2.0 - 2026-07-09

사내 업로드 도구에 네트워크 체크 모드를 추가했습니다.

추가된 기능:

- 상단 탭으로 `파일 업로드`와 `네트워크 체크` 모드를 분리
- 현재 PC와 서버 PC 사이의 업로드/다운로드 전송 속도 측정
- `10MB`, `50MB`, `100MB`, `500MB`, `1024MB` 테스트 크기 제공
- 업로드만, 다운로드만, 전체 측정 실행
- 측정 중 진행률과 현재 속도 표시
- 테스트 데이터는 파일로 저장하지 않고 스트리밍 후 폐기
- 네트워크 체크 결과를 `data/network_check_log.csv`에 별도로 기록
- 기존 `data/upload_log.csv` 업로드 기록과 속도 측정 기록 분리

제한사항:

- 인터넷 회선 속도 측정이 아니라 현재 PC와 사내 업로드 서버 PC 사이의 전송 속도 측정입니다.
- 1024MB 측정은 사내망과 서버 PC에 부하를 줄 수 있습니다.
- 스트리밍 업로드는 Windows 환경의 Edge/Chrome 기준으로 검증합니다.

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

- 태그 형식: `v0.2.1`처럼 앱 버전과 맞춥니다.
- Release 제목: `v0.2.1 - 사내 업로드 Windows 실행 ZIP`
- Release 본문: 포함 기능, 제외 항목, 검증 명령, 실행 방법, asset 정책을 한국어로 적습니다.
- 직접 업로드하는 Release asset: `internal-upload_v0.2.1_windows.zip`
- SHA256 checksum은 Release 본문에 기록합니다.

GitHub가 자동으로 표시하는 `Source code (zip)` / `Source code (tar.gz)`는 tag 기준 소스 아카이브입니다. 일반 사용자는 `internal-upload_v0.2.1_windows.zip`을 다운로드합니다.

ZIP 내부 구조:

- `InternalUpload.exe`
- `start_internal_upload.cmd`
- `config.ini`
- `README_START_HERE_KO.txt`
- `README.md`, `RELEASE_NOTES.md`, `CHANGELOG.md`
- `data/upload_log.csv`
- `data/network_check_log.csv`
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
pwsh -NoProfile -File .\tools\build_windows_release.ps1 -Version v0.2.1
InternalUpload.exe --smoke-check
python tools\verify_release_zip.py --zip dist\internal-upload_v0.2.1_windows.zip --version v0.2.1
```

## 작성하지 않을 내용

- 실제 사내 IP, 서버 PC 이름, 사용자 계정, 비밀번호
- 실제 장애자료 파일명, 메모, 업로드 CSV 기록
- 고객명, 사이트명, 내부망 식별자
- 아직 구현하지 않은 로그인, 만료일, 관리자 페이지
