# 사내 업로드

사내 장애처리용 미니 파일 업로드 도구입니다. 브라우저에서 파일과 메모를 올리면 서버 PC에 저장하고, 다른 PC에서 붙여넣어 바로 다운로드할 수 있는 링크를 생성합니다.

## 실행 방법

일반 사용자는 GitHub Release에서 Windows 실행 ZIP을 받습니다.

1. `internal-upload_v0.4.0-rc.1_windows.zip`을 다운로드합니다.
2. Windows 서버 PC의 원하는 폴더에 ZIP을 완전히 압축 해제합니다.
3. `start_internal_upload.cmd`를 더블클릭합니다.
4. 같은 PC에서는 아래 주소로 접속합니다.

```text
http://127.0.0.1:8000
```

다른 PC에서 접속하려면 서버 PC의 사내 IP를 사용합니다.

```text
http://서버PC-IP:8000
```

Windows 방화벽에서 TCP `8000` 포트가 막혀 있으면 다른 PC에서 접속하거나 다운로드할 수 없습니다.

코드서명하지 않은 EXE이므로 Windows SmartScreen 경고가 표시될 수 있습니다.

## 소스에서 실행

Windows 서버 PC에서:

```bat
run.bat
```

소스에서 실행하는 방식은 Python이 설치된 개발/운영 PC용입니다. Python 없이 실행하려면 Release ZIP을 사용하세요.

## 설정

`config.ini`에서 운영 값을 수정합니다.

```ini
[app]
HOST=0.0.0.0
PORT=8000
BASE_URL=
STORAGE_ROOT=uploads
DELETE_ALLOWED_IPS=127.0.0.1,::1
RECENT_LIMIT=50

[network_probe]
ENABLED=false
PORT=5201
```

- `BASE_URL`: 다운로드 링크 기준 주소입니다. 예: `http://10.10.10.25:8000`
- `STORAGE_ROOT`: 파일 저장 기준 폴더입니다. 상대경로면 프로젝트 폴더 기준입니다.
- `DELETE_ALLOWED_IPS`: 삭제 버튼과 삭제 요청을 허용할 접속 IP 목록입니다.
- `RECENT_LIMIT`: 화면에 표시할 최근 업로드 개수입니다.
- `network_probe.ENABLED`: Windows TCP 정밀 측정 서버를 켭니다. 기본값은 `false`입니다.
- `network_probe.PORT`: TCP 측정 데이터 포트입니다. 기본값은 `5201`이며 웹 `PORT`와 달라야 합니다.

`BASE_URL`이 비어 있으면 프로그램이 서버 PC의 사내 IP를 자동 감지해 링크를 만듭니다. 링크가 `localhost` 또는 `127.0.0.1`로 생성되면 다른 PC에서는 사용할 수 없으므로 화면에 경고가 표시됩니다.

## 사용 방식

1. 파일을 선택합니다.
2. 필요한 경우 저장 하위 폴더를 입력합니다.
3. 필요한 경우 메모를 입력합니다.
4. 업로드 후 생성된 다운로드 링크를 공유합니다.

저장 하위 폴더는 `STORAGE_ROOT` 아래만 허용합니다. `C:\temp` 같은 절대경로나 `..` 경로는 차단합니다.

같은 저장 위치에 같은 이름의 파일이 있으면 먼저 경고가 표시됩니다. 그래도 업로드하려면 파일을 다시 선택하고 `같은 이름이 있으면 ID를 붙여 저장`을 체크하세요.

## 네트워크 체크

상단 `네트워크 체크` 탭에서 현재 PC와 서버 PC 사이의 업로드/다운로드 전송 속도를 확인할 수 있습니다.

측정 방식은 세 가지입니다.

### 간편 측정

- `10MB`, `50MB`, `100MB`, `500MB`, `1024MB` 중 테스트 크기를 선택합니다.
- 업로드, 다운로드, 전체 측정을 실행합니다.
- 진행률, 평균 속도, 구간 속도를 `Mbps`와 `MB/s`로 표시합니다.
- 업로드는 HTTP/1.1에서 안정적으로 동작하는 1MB 일반 POST 조각을 사용합니다.
- `1024MB` 측정은 시작 전에 부하 확인창을 표시합니다.

### 지속 측정

- 별도 클라이언트 프로그램 설치 없이 Windows 11 Edge/Chrome과 Android Chrome에서 실행합니다.
- 각 방향마다 3초 워밍 후 10초 또는 30초를 본 측정합니다.
- HTTP 연결 1개 또는 4개를 선택해 단일 연결과 다중 연결 합산 처리량을 확인합니다.
- 평균, 중앙값, 최소, 최대, 변동률, 1초 구간 그래프와 HTTP 응답시간을 표시합니다.
- 서버 전체에서 한 번에 하나의 지속 측정만 허용합니다.
- 30초 또는 4개 연결은 시작 전에 부하 확인창을 표시합니다.
- 완료된 세션의 상세 JSON 결과를 현재 측정 PC에서 받을 수 있습니다.

### TCP 정밀 측정

TCP 정밀 측정은 Windows 서버와 Windows 클라이언트의 동일한 `InternalUpload.exe` 사이에서 별도 TCP 연결을 만들어 처리량을 측정합니다. 브라우저는 측정 시작·취소와 결과 표시만 담당합니다.

서버 PC에서:

1. `config.ini`의 `[network_probe] ENABLED=true`로 변경합니다.
2. Windows 방화벽에서 TCP `5201` 포트를 허용합니다.
3. `start_internal_upload.cmd`를 실행합니다.

측정 대상 Windows PC에서:

1. 같은 Release ZIP을 압축 해제합니다.
2. `start_tcp_probe_client.cmd`를 실행합니다.
3. 서버 PC 이름 또는 사내 IP와 웹 포트를 입력합니다. 예: `SERVER-PC:8000`
4. 웹 화면에서 표시되는 `PC 이름 · 접속 IP`를 선택합니다.

지원 범위:

- 업로드, 다운로드, 전체 순차 측정
- 1개 또는 4개 TCP 스트림
- 방향별 3초 워밍업 후 10초 또는 30초 본 측정
- 송신·수신 처리량과 1초 구간 그래프
- Windows TCP_INFO가 제공될 때 RTT, 최소 RTT, 혼잡 윈도우, 재전송 바이트
- 측정 취소, 연결 끊김 감지, 서버 전체 동시 네트워크 측정 1건 제한

TCP 클라이언트는 서버로만 연결하므로 클라이언트 PC의 인바운드 포트를 열 필요가 없습니다. 상세 TCP 통계를 조회할 수 없는 환경에서는 값을 추정하지 않고 `값 없음`으로 표시합니다.

간편·지속 측정은 브라우저와 Flask 서버 사이의 HTTP 응용 전송 성능입니다. TCP 정밀 측정은 브라우저를 데이터 경로에서 제외하지만 자체 프로토콜이므로 iperf 클라이언트·서버와 호환되지 않습니다. 모든 측정값에는 단말 CPU, NIC, Wi-Fi와 서버 PC 성능이 함께 반영됩니다. 설계 대상은 1Gbps 이하 사내망입니다.

테스트 데이터는 서버에 파일로 저장하지 않고 측정 후 폐기합니다. `1024MB` 측정은 사내망과 서버 PC에 부하를 줄 수 있으므로 장애 상황에서 필요할 때만 사용하세요.

## 기록과 삭제

업로드 기록은 `data/upload_log.csv`에 저장됩니다. CSV에는 업로드일시, 원본파일명, 저장파일명, 저장경로, 메모, 다운로드 링크가 남습니다.

간편 네트워크 체크는 `data/network_check_log.csv`에 저장됩니다. 지속 측정 요약은 `data/network_check_session_log.csv`, 상세 결과는 `data/network_check_results/<session_id>.json`에 저장됩니다. TCP 정밀 측정은 `data/network_probe_log.csv`와 `data/network_probe_results/<session_id>.json`에 저장됩니다. 운영 CSV와 JSON은 GitHub에 올리지 마세요.

삭제는 `DELETE_ALLOWED_IPS`에 등록된 IP에서 접속했을 때만 가능합니다. 삭제하면 서버에 저장된 파일과 CSV 기록이 함께 삭제됩니다.

## GitHub 이력관리와 릴리즈 문서

GitHub에 push하거나 Release를 준비하기 전에는 아래 파일을 함께 확인합니다.

- `README.md`: 실행 방법, 설정값, 방화벽, 업로드/다운로드/삭제 방법
- `RELEASE_NOTES.md`: 릴리즈 전 점검 기준과 배포 asset 정책
- `CHANGELOG.md`: 사용자 관점 변경사항
- `AGENTS.md`: Codex 작업 규칙과 문서 최신화 기준

현재 시험용 Release asset은 `internal-upload_v0.4.0-rc.1_windows.zip`과 SHA256 파일입니다. `rc.1`은 Windows 두 PC 실기 검증 전 사전 릴리즈입니다. GitHub가 자동으로 표시하는 `Source code (zip)` / `Source code (tar.gz)`는 소스 아카이브이며 일반 실행용 ZIP이 아닙니다.

실제 사내 IP, 서버 PC 이름, 계정, 비밀번호, 업로드 자료, 장애 메모, 고객 정보는 문서와 Git 커밋에 넣지 않습니다.

## 개발 검증

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pytest -q
```

전체 소스 검사:

```bat
.venv\Scripts\python -m compileall app.py network_sustained.py network_measurement.py network_probe tests tools
```

실행파일 자체 점검:

```bat
InternalUpload.exe --smoke-check
InternalUpload.exe --probe-self-check
```

UDP 손실·지터 측정과 Android 네이티브 TCP 클라이언트는 현재 포함되지 않습니다.
