# 검증 기록

검증일: 2026-11-23 (Asia/Seoul)

환경: Windows Docker Desktop, Linux 컨테이너, Python 3.12-slim
범위: 수업용 합성 Internal API·수신 서버만 사용. 실제 MLflow/Plane 인스턴스는 설치하지 않음.

## 실행한 검증

```powershell
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --build verifier
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-cves.ps1
```

첫 명령은 HTTP/TLS/SQLite/권한/URL 검증을 포함한 **16개 테스트**를 통과했습니다. 두 번째 명령은 아래의 여섯 CVE 비교와 결정론적 DNS rebinding 비교를 완료했고, 원본 UUID별 결과는 무시되는 `artifacts/final-*.json`, 요약 결과는 [verification/final-scenario-2026-11-23.json](verification/final-scenario-2026-11-23.json)에 보관했습니다.

| 시나리오 | MLflow 내부 도달 | MLflow 본문 노출 | Plane 내부 도달 | Plane 로그 노출 | 결과 |
|---|---:|---:|---:|---:|---|
| baseline-vulnerable | 예 | 예 | 예 | 예 | 취약 흐름 확인 |
| reject-open | 아니오 | 아니오 | 아니오 | 아니오 | Redirect 거부 확인 |
| strict-open | 아니오 | 아니오 | 아니오 | 아니오 | hop/연결 검증 확인 |
| baseline-network-blocked | 아니오 | 아니오 | 아니오 | 아니오 | Webhook→Internal 격리 확인 |
| fixed-url-auth-mask | 아니오 | 아니오 | 아니오 | 아니오 | 수정 프로필·인증·본문 비노출 확인 |
| plane-log-filter | 아니오 | 아니오 | 예 | 아니오 | 내부 도달과 로그 필터가 별도 축임을 확인 |

Plane 로그 조회에서는 alpha workspace의 일반 member(`viewer`)와 다른 workspace admin(`bob`) 모두 `403 workspace permission denied`를 받았습니다.

MLflow 수정 프로필에서는 무인증 CVE 호출이 `401`, alpha member(`viewer`)와 다른 workspace admin(`bob`) 호출이 각각 `403`이었고, Alice 관리자 호출만 방어된 전달 결과를 조회했습니다.

DNS rebinding 비교에서는 등록 시 허용된 `127.0.0.1`과 전송 시 합성 Internal `127.0.0.2`를 의도적으로 다르게 해석했습니다. baseline은 Internal에 도달했고, strict는 차단했습니다. 이 결과는 외부 DNS나 실제 Internal 주소를 사용하지 않습니다.

## 일반 Webhook 회귀 검증

다음 명령은 CVE 모드를 끈 일반 API에서 `baseline`/`reject`/`strict`, open/blocked network, 본문 정책을 조합해 정상 `/webhook` 수신이 유지되는지 확인합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-matrix.ps1 -SkipBuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\export-results.ps1
```

이번 재실행은 **9개 조합 모두 통과**, 정상 수신 전달은 **18/18 성공**했습니다. 최신 결과는 [verification/docker-matrix.json](verification/docker-matrix.json)에 기록합니다. 실행 시 다음을 함께 확인합니다.

- 기존 `GET /health`와 `POST /webhook` 호환성
- 일반 Webhook의 MLflow식 동기/Plane식 비동기 정상 전달
- 직접 Internal URL 등록 차단
- 다른 workspace 및 일반 member의 설정·로그 접근 거부
- `RETURN_BODY`·`STORE_BODY`·`READ_BODY`가 Internal 요청 여부와 독립적으로 본문 노출만 제어함

## 증거의 의미와 한계

- Internal 도달은 `/observations?run_id=...` 기록으로만 확정합니다.
- 본문 노출은 `REPORT-<run_id>` 표식이 MLflow 즉시 응답 또는 Plane 권한 로그에 있는지로만 판정합니다.
- `connection_trace`와 `network_event_count`는 애플리케이션 연결 추적입니다. PCAP라고 주장하지 않습니다.
- 실제 VM의 방화벽·패킷 분석은 [../indications.md](../indications.md)의 제한된 실험망 절차로 별도 확인합니다.
