# Docker 시연 증거 모음

이 디렉터리는 [../indications.md](../indications.md)의 최종 시연 순서 1~8에 맞춘 **실제 Docker 실행 결과의 읽기 쉬운 사본**입니다. JSON 문법에는 주석이 없으므로 각 파일의 `_comment`, `expected`, `key_observations` 필드가 주석 역할을 합니다.

| 단계 | 파일 | 무엇을 보여 주는가 |
|---:|---|---|
| 1 | `01-health-and-legacy-webhook.json` | 기존 `GET /health`, `POST /webhook` 호환성 |
| 2 | `02-normal-webhook-delivery.json` | 정상 receiver로의 MLflow식 동기·Plane식 비동기 전달 |
| 3 | `03-baseline-vulnerable.json` | 취약 기준선에서 Redirect 뒤 Internal 도달과 본문/로그 노출 |
| 4 | `04-reject-and-strict.json` | Redirect 거부와 hop별 strict 검증 비교 |
| 5 | `05-network-blocked.json` | `compose.block.yaml`과 동등한 Webhook→Internal 네트워크 차단 |
| 6 | `06-mlflow-fixed-auth-mask.json` | MLflow 401/403/strict·본문 마스킹과 Plane Redirect 거부 |
| 7 | `07-plane-log-filter-and-access.json` | Plane 저장 전 로그 필터와 workspace 로그 권한 통제 |
| 8 | `08-run-id-correlation-and-dns-rebinding.json` | Webhook·Kali receiver·Internal의 `run_id` 대조와 DNS rebinding 비교 |

## 현재 증적의 읽는 법

- 2~8단계는 바로 앞 Docker 실행에서 생성된 `artifacts/*.json`을 요약하며, 각 파일의 `source_artifacts.sha256`로 원본과 대조할 수 있습니다.
- 현재 1단계는 Docker 검증기에서 통과한 `test_legacy_contract_and_auth`와 전달 매트릭스의 실행 근거를 표시합니다. 이 export를 만든 셸은 Docker 엔진에 다시 연결할 권한이 없어 응답을 새로 요청하지 않았으므로 `capture_mode`이 `artifact-only`입니다.
- 다음 `capture-demonstration.ps1` 실행부터는 `check_lab.py`가 Webhook·Internal `/health`와 기존 `POST /webhook`의 실제 JSON 응답을 매트릭스 원본에 기록합니다. 그러면 같은 export가 자동으로 `artifact-recorded-docker`로 바뀌며, 1단계도 원본 응답을 직접 보존합니다.

## 재생성

Docker Desktop이 실행된 PowerShell에서 다음 한 명령으로 테스트, CVE 비교, 일반 전달 매트릭스, JSON export를 다시 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\capture-demonstration.ps1
```

이미 Docker 이미지를 빌드했다면 다음처럼 빌드를 생략할 수 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\capture-demonstration.ps1 -SkipBuild
```

각 JSON의 `source_artifacts`는 생성 시점의 ignored 원본 `artifacts/*.json` 경로와 SHA-256을 보존합니다. `observed`는 원본 전체를 무작정 복사하는 대신, 판정에 필요한 결과·관측·trace를 남긴 요약입니다. `connection_trace`는 HTTP client가 기록한 **애플리케이션 수준 연결 추적**이며 PCAP가 아닙니다.

실제 Kali/별도 VM에서 패킷 증거가 필요하면 [../indications.md](../indications.md)의 실습망 전용 `tcpdump` 절차를 사용하고, 이 Docker 결과와 혼동하지 않습니다.
