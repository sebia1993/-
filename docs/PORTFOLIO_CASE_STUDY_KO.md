# 네트워크 엔지니어 포트폴리오: 전송 관측과 결과 신뢰성

[README로 돌아가기](../README.md)

이 프로젝트에서 검토할 역량은 **비교 측정으로 조사 범위를 좁히는 능력, 측정 부하 제어, 장애 증거 보존, 신뢰 경계 설명**입니다. 처리량 향상이나 현장 장애 감소는 공개된 코드만으로 입증할 수 없으므로 성과 수치로 제시하지 않습니다.

## 문제에서 구현까지

| 운영 상황 | 설계 판단과 비용 | 구현 근거 | 확인할 회귀 테스트 |
|---|---|---|---|
| 같은 시간에 여러 고부하 측정을 실행하면 서로의 처리량을 낮춤 | HTTP와 TCP가 공유하는 gate로 측정 소유권을 하나로 제한. 동시 측정 편의보다 비교 가능성을 우선 | [NetworkMeasurementGate](../network_measurement.py) | [소유권·취소·lease·deadlock](../tests/test_network_measurement.py) |
| 결과 JSON 저장 뒤 CSV 이력 기록 전에 프로세스 중단 | intent marker와 재시작 재조정으로 두 저장물의 일관성을 회복. 상태가 충돌하면 새 작업을 차단하므로 운영자 조치가 필요할 수 있음 | [측정 transaction](../measurement_transactions.py), [atomic JSON 저장](../result_storage.py) | [JSON만 저장된 경우 복구·충돌 거부·반복 복구](../tests/test_measurement_transactions.py) |
| 업로드·삭제 중단으로 파일과 로그가 어긋남 | 작업 단계와 대상 경로를 marker에 남기고 복구 시 다시 검증 | [업로드 transaction](../upload_transactions.py), [앱 복구 연결](../app.py) | [파일 commit 후 로그 누락·경로 재사용·삭제 복구](../tests/test_upload_transaction_recovery.py) |
| 브라우저 접근과 TCP 클라이언트 권한을 혼동 | 웹은 무로그인+CSRF, 원격 TCP 등록은 1회용 token으로 분리. 웹 접근 제한은 방화벽·ACL에 의존 | [AccessSecurity](../access_security.py) | [원격 무로그인 접근·CSRF·등록 token 소비](../tests/test_access_security.py) |
| TCP 제어 요청 위조·재전송 | HMAC·timestamp·nonce로 제어 frame 검증. 이는 데이터 암호화가 아님 | [protocol 검증](../network_probe/protocol.py) | [서명 변조·만료·replay 거부](../tests/test_network_probe_protocol.py) |

## 합성 사례로 설명하는 조사 흐름

다음은 실행 결과나 고객 사례가 아닌 검토용 시나리오입니다.

1. 동일한 두 endpoint 사이에서 HTTP 처리량 저하를 관측했다고 가정합니다.
2. 다른 고부하 측정이 없는 상태에서 별도 TCP 경로와 비교합니다. 방향, stream 수, 시각과 측정 시간을 함께 기록합니다.
3. TCP가 상대적으로 양호하면 브라우저·HTTP 처리와 endpoint 부하를 추가 조사합니다. 둘 다 낮으면 공통 경로와 endpoint 자원을 함께 확인합니다.
4. 한 방향만 완료됐다면 `부분 완료` 결과만 사용합니다. 실패한 방향의 수치를 0이나 정상값으로 채우지 않습니다.
5. 수치와 함께 결과 저장 상태를 확인합니다. 충돌한 transaction의 결과는 정상 증거로 취급하지 않습니다.

HTTP/TCP가 서로 다른 포트·실행 경로를 쓰므로 방화벽 정책이나 endpoint 처리 차이도 결과에 영향을 줄 수 있습니다. 이 비교만으로 특정 케이블·스위치·RF 원인을 확정할 수 없습니다. [측정 모델](MEASUREMENT_MODEL.md)에 해석 범위를 정리했습니다.

## 장비 없이 핵심 설계 재현

저장소 루트의 Windows PowerShell에서 Python 3.11을 사용합니다. 의존성 설치에는 패키지 저장소 접근이 필요합니다. 아래 테스트는 임시 디렉터리, Flask test client와 로컬 socket을 사용하며 실제 장비 주소·계정이 필요하지 않습니다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-windows.lock
.\.venv\Scripts\python.exe -m pytest -q tests/test_access_security.py tests/test_network_measurement.py tests/test_network_probe_protocol.py tests/test_measurement_transactions.py tests/test_upload_transaction_recovery.py
```

읽을 결과는 종료 코드 0과 위 경계의 PASS/FAIL입니다. 선택한 회귀 테스트의 성공은 EXE 패키지 검증이나 실제 네트워크 처리량 검증을 의미하지 않습니다. 전체 검증은 [개발 가이드](../DEVELOPMENT.md)와 [PR Validation 구성](../.github/workflows/pr-validation.yml)을 따릅니다.

## 검토자가 확인할 증거

- **코드**: 표의 소유권·복구·인증 경계와 대응 테스트를 함께 읽습니다.
- **Windows CI**: [PR Validation](https://github.com/sebia1993/internal-network-transfer-diagnostics/actions/workflows/pr-validation.yml)의 commit SHA, `windows-verify` 결과와 패키지 검증 로그를 확인합니다.
- **지속 실행**: [Windows Stability Soak](https://github.com/sebia1993/internal-network-transfer-diagnostics/actions/workflows/stability-windows.yml)의 실행 시간·합성 workload·분석 판정을 확인합니다. 프로필이 존재한다는 사실과 완료된 실행 증거는 구분합니다.
- **배포**: [Releases](https://github.com/sebia1993/internal-network-transfer-diagnostics/releases)의 ZIP SHA-256과 ZIP 내부 manifest/SBOM을 확인합니다. Release의 소스와 현재 main은 다를 수 있습니다.

## 남는 제약과 다음 검증

웹에는 사용자 인증·권한 분리가 없고 루프백 요청은 CSRF 검증을 우회합니다. HMAC도 HTTP/TCP payload의 기밀성을 제공하지 않습니다. 따라서 네트워크 접근 통제와 데이터 취급 정책이 이 도구의 운영 전제입니다. [보안 모델](SECURITY_MODEL.md)을 먼저 확인해야 합니다.

다음 검증 단계는 허가된 Windows 환경에서 동일 경로의 HTTP/TCP 비교, 보안 제품 영향, 재시작 복구와 부분 완료 내보내기를 관찰하는 것입니다. 공개 자동 검증은 이 현장 확인을 대신하지 않습니다.
