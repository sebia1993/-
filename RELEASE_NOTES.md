# Release Notes 운영 규칙

현재 앱 버전: `v0.4.0-rc.3`

이 파일은 저장소에 커밋하는 릴리즈 준비 점검 문서입니다. 현재 GitHub Actions가 Windows 실행 ZIP을 만들고 GitHub Release에 업로드합니다. Release 본문은 이 파일과 `CHANGELOG.md` 기준으로 작성합니다.

## v0.4.0-rc.3 - 2026-07-14

웹 포트 충돌 시 설정 파일을 직접 편집하지 않아도 되도록 시작 절차를 개선한 사전 릴리즈입니다.

추가·개선된 내용:

- 설정 웹 포트가 사용 중이면 다음 99개 포트를 순서대로 검사해 가장 가까운 빈 포트 제안
- Enter 또는 `Y` 승인 후 실제 서버 바인딩에 성공한 경우에만 `config.ini`의 `PORT` 저장
- `N`, 입력 불가, 후보 포트 고갈과 최종 바인딩 실패 시 기존 설정 보존
- 동일한 InternalUpload 서버의 `/api/health` 응답을 확인해 중복 실행 대신 기존 주소 안내
- `BASE_URL`이 기존 웹 포트를 사용할 때만 새 포트로 동기화하고 별도 포트는 유지
- Windows 방화벽 인바운드 허용 상태 확인과 관리자용 수동 명령 안내
- 실제 실행 포트를 콘솔에 표시하고 새 포트에서 받은 TCP 클라이언트 ZIP에 해당 포트를 자동 포함

운영 방식과 제한:

- 방화벽 규칙과 관리자 권한은 자동으로 변경하지 않습니다.
- TCP 정밀 측정 포트 `5201`은 웹 포트 충돌 처리 대상이 아닙니다.
- 포트가 바뀌기 전에 받은 자동 연결 클라이언트 ZIP은 웹 화면에서 다시 받아야 합니다.
- 기존 `v0.4.0-rc.2` 릴리즈와 파일은 보존합니다.

## v0.4.0-rc.2 - 2026-07-10

TCP 측정 클라이언트 배포를 단순화한 사전 릴리즈입니다.

추가·개선된 내용:

- TCP 정밀 측정 화면에서 `Windows 클라이언트 ZIP 받기` 제공
- 브라우저가 접속한 서버 PC 이름 또는 IPv4와 웹 포트를 자동 포함
- `localhost`와 `127.0.0.1` 접속 시 감지된 사내 IPv4와 설정 웹 포트로 대체
- 압축 해제 후 주소 입력 없이 실행되는 자동 연결 `start_tcp_probe_client.cmd`
- 클라이언트 ZIP을 EXE, CMD, 한국어 안내문 세 파일로 제한하고 설정·토큰·세션 정보 제외
- Host 값 검증과 배치 명령 삽입 방지, 소스 실행·비활성 서버·EXE 누락 시 다운로드 차단
- `--probe-self-check`에서 실행 중인 Windows EXE의 클라이언트 ZIP 생성과 내부 구조 검증
- SHA256 파일을 LF 줄바꿈으로 생성해 Windows, macOS와 Linux 검증 명령에서 공통 사용

운영 방식과 제한:

- 서버 IP 또는 웹 포트가 바뀌면 클라이언트 ZIP을 다시 받습니다.
- 전체 Release ZIP의 주소 입력형 `start_tcp_probe_client.cmd`는 수동 대체 수단으로 유지합니다.
- 같은 스위치의 Windows 11 PC 두 대에서 iperf 3회 중앙값 대비 ±10%를 확인한 뒤 정식 `v0.4.0`을 게시합니다.
- UDP와 Android 네이티브 TCP 클라이언트는 포함하지 않습니다.

## v0.4.0-rc.1 - 2026-07-10

Windows 자체 TCP 정밀 측정을 추가한 사전 릴리즈입니다.

추가된 내용:

- 동일한 `InternalUpload.exe`의 TCP 서버 및 `--probe-client` 대기형 클라이언트 모드
- 클라이언트 주소 입력용 `start_tcp_probe_client.cmd`
- TCP 업로드·다운로드·전체, 1개·4개 스트림, 3초 워밍업, 10초·30초 측정
- 수신·송신 처리량, 1초 그래프, RTT, 최소 RTT, CWND, 재전송 바이트
- 자동 임시 토큰과 접속 IP 결합, 취소·연결 중단·만료 처리
- HTTP 간편·지속·TCP 측정의 서버 전체 동시 실행 1건 제한
- `data/network_probe_log.csv` 요약과 `data/network_probe_results/` 세션별 JSON

사전 릴리즈 제한:

- GitHub Actions Windows loopback 및 EXE 자체 점검 후 게시합니다.
- 같은 스위치의 Windows 11 PC 두 대에서 iperf 3회 중앙값 대비 ±10%를 확인한 뒤 정식 `v0.4.0`을 게시합니다.
- iperf는 비교 검증에만 사용하고 프로그램에 포함하거나 호출하지 않습니다.
- UDP와 Android 네이티브 TCP 클라이언트는 포함하지 않습니다.

## v0.3.0 - 2026-07-10

설치 없는 HTTP 지속 측정과 결과 기록을 추가한 릴리즈입니다.

추가된 내용:

- 기존 크기 기준 간편 측정과 별도의 HTTP 지속 측정 화면
- 방향별 3초 워밍 후 10초 또는 30초 본 측정
- HTTP 연결 1개/4개, 업로드/다운로드/전체 측정
- 평균·중앙·최소·최대·변동률, HTTP 응답시간, 1초 구간 그래프
- 서버 전체 동시 측정 1건 제한과 측정 취소·만료 처리
- `data/network_check_session_log.csv` 요약과 `data/network_check_results/` 세션별 JSON
- Windows 11 Edge/Chrome과 Android Chrome 반응형 화면
- 업로드 CSV 기록 실패 시 새로 생긴 고아 파일을 정리하는 안정성 개선

제한사항:

- 지속 측정은 브라우저 HTTP 응용 전송 성능입니다.
- TCP 재전송·CWND나 UDP 손실·지터를 측정하는 iperf 대체 기능은 포함하지 않습니다.
- `v0.4.0` TCP와 `v0.5.0` UDP 정밀 측정은 후속 계획입니다.

## v0.2.2 - 2026-07-09

네트워크 체크 표시와 운영 안전장치 개선 릴리즈입니다.

개선된 내용:

- 진행 중 속도를 `평균 속도`와 `구간 속도`로 분리
- 속도 값을 `Mbps`와 `MB/s`로 함께 표시
- 측정 중 취소 버튼 추가
- `1024MB` 측정 시작 전 확인창 추가
- 취소 후 다시 측정할 수 있도록 버튼 상태 복구

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

- 태그 형식: 사전 릴리즈는 `v0.4.0-rc.3`, 정식 릴리즈는 `v0.4.0`처럼 관리합니다.
- Release 제목: `v0.4.0-rc.3 - 사내 업로드 Windows 실행 ZIP`
- Release 본문: 포함 기능, 제외 항목, 검증 명령, 실행 방법, asset 정책을 한국어로 적습니다.
- 직접 업로드하는 Release asset: `internal-upload_v0.4.0-rc.3_windows.zip`, `.zip.sha256`
- SHA256 checksum은 별도 asset과 Release 본문에 기록합니다.

GitHub가 자동으로 표시하는 `Source code (zip)` / `Source code (tar.gz)`는 tag 기준 소스 아카이브입니다. 시험 사용자는 `internal-upload_v0.4.0-rc.3_windows.zip`을 다운로드합니다.

ZIP 내부 구조:

- `InternalUpload.exe`
- `start_internal_upload.cmd`
- `start_tcp_probe_client.cmd`
- `config.ini`
- `README_START_HERE_KO.txt`
- `README.md`, `RELEASE_NOTES.md`, `CHANGELOG.md`
- `data/upload_log.csv`
- `data/network_check_log.csv`
- `data/network_check_session_log.csv`
- `data/network_check_results/README_RESULTS_KO.txt`
- `data/network_probe_log.csv`
- `data/network_probe_results/README_RESULTS_KO.txt`
- `uploads/README_UPLOADS_KO.txt`

## 검증 기준

Release 또는 GitHub push 전에 다음 검증을 실행합니다.

```powershell
python -m compileall app.py startup_ports.py network_sustained.py network_measurement.py network_probe tests tools
python -m pytest -q
```

macOS 작업 환경에서는 다음 명령을 사용합니다.

```bash
.venv/bin/python -m compileall app.py startup_ports.py network_sustained.py network_measurement.py network_probe tests tools
.venv/bin/python -m pytest -q
```

Windows Release ZIP 검증은 GitHub Actions `windows-latest`에서 실행합니다.

```powershell
python -m compileall app.py startup_ports.py network_sustained.py network_measurement.py network_probe tests tools
python -m pytest -q
pwsh -NoProfile -File .\tools\build_windows_release.ps1 -Version v0.4.0-rc.3
InternalUpload.exe --smoke-check
InternalUpload.exe --probe-self-check
python tools\verify_release_zip.py --zip dist\internal-upload_v0.4.0-rc.3_windows.zip --version v0.4.0-rc.3
```

## 작성하지 않을 내용

- 실제 사내 IP, 서버 PC 이름, 사용자 계정, 비밀번호
- 실제 장애자료 파일명, 메모, 업로드 CSV 기록
- 고객명, 사이트명, 내부망 식별자
- 아직 구현하지 않은 로그인, 만료일, 관리자 페이지
