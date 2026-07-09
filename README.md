# 사내 업로드

사내 장애처리용 미니 파일 업로드 도구입니다. 브라우저에서 파일과 메모를 올리면 서버 PC에 저장하고, 다른 PC에서 붙여넣어 바로 다운로드할 수 있는 링크를 생성합니다.

## 실행 방법

Windows 서버 PC에서:

```bat
run.bat
```

실행 후 같은 PC에서는 아래 주소로 접속합니다.

```text
http://127.0.0.1:8000
```

다른 PC에서 접속하려면 서버 PC의 사내 IP를 사용합니다.

```text
http://서버PC-IP:8000
```

Windows 방화벽에서 TCP `8000` 포트가 막혀 있으면 다른 PC에서 접속하거나 다운로드할 수 없습니다.

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
```

- `BASE_URL`: 다운로드 링크 기준 주소입니다. 예: `http://10.10.10.25:8000`
- `STORAGE_ROOT`: 파일 저장 기준 폴더입니다. 상대경로면 프로젝트 폴더 기준입니다.
- `DELETE_ALLOWED_IPS`: 삭제 버튼과 삭제 요청을 허용할 접속 IP 목록입니다.
- `RECENT_LIMIT`: 화면에 표시할 최근 업로드 개수입니다.

`BASE_URL`이 비어 있으면 프로그램이 서버 PC의 사내 IP를 자동 감지해 링크를 만듭니다. 링크가 `localhost` 또는 `127.0.0.1`로 생성되면 다른 PC에서는 사용할 수 없으므로 화면에 경고가 표시됩니다.

## 사용 방식

1. 파일을 선택합니다.
2. 필요한 경우 저장 하위 폴더를 입력합니다.
3. 필요한 경우 메모를 입력합니다.
4. 업로드 후 생성된 다운로드 링크를 공유합니다.

저장 하위 폴더는 `STORAGE_ROOT` 아래만 허용합니다. `C:\temp` 같은 절대경로나 `..` 경로는 차단합니다.

같은 저장 위치에 같은 이름의 파일이 있으면 먼저 경고가 표시됩니다. 그래도 업로드하려면 파일을 다시 선택하고 `같은 이름이 있으면 ID를 붙여 저장`을 체크하세요.

## 기록과 삭제

업로드 기록은 `data/upload_log.csv`에 저장됩니다. CSV에는 업로드일시, 원본파일명, 저장파일명, 저장경로, 메모, 다운로드 링크가 남습니다.

삭제는 `DELETE_ALLOWED_IPS`에 등록된 IP에서 접속했을 때만 가능합니다. 삭제하면 서버에 저장된 파일과 CSV 기록이 함께 삭제됩니다.

## GitHub 이력관리와 릴리즈 문서

GitHub에 push하거나 Release를 준비하기 전에는 아래 파일을 함께 확인합니다.

- `README.md`: 실행 방법, 설정값, 방화벽, 업로드/다운로드/삭제 방법
- `RELEASE_NOTES.md`: 릴리즈 전 점검 기준과 배포 asset 정책
- `CHANGELOG.md`: 사용자 관점 변경사항
- `AGENTS.md`: Codex 작업 규칙과 문서 최신화 기준

실제 사내 IP, 서버 PC 이름, 계정, 비밀번호, 업로드 자료, 장애 메모, 고객 정보는 문서와 Git 커밋에 넣지 않습니다.

## 개발 검증

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pytest -q
```
