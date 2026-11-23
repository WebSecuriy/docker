# 최종 CVE 모의·방어 실행 가이드

> 작성일: 2026-11-24 · 범위: 격리된 수업용 합성 데이터와 로컬 Docker 실험만 사용합니다.
>
> 이 저장소는 MLflow나 Plane 제품을 설치·배포하지 않습니다. 아래의 버전 이름은 **취약점 흐름을 비교하기 위한 경량 모의 프로필**입니다.

## 1. 최종 시연 흐름

```text
Kali의 HTTPS 수신/Redirect 서버
          │ 정상처럼 보이는 HTTPS 첫 URL
          ▼
Webhook 서버 ── Redirect 또는 DNS 재해석 ──► Internal 보고서 API
    │                                               │
    │ MLflow 모의: 동기 테스트 응답 본문               │ /observations에 run_id 기록
    │ Plane 모의: 비동기 delivery 로그 본문            │
    ▼                                               ▼
URL·IP·Redirect 검증 / 네트워크 분리 / 본문·로그·권한 방어
```

1. 정상 고객 Webhook이 MLflow식 동기 전송과 Plane식 비동기 전송 모두에서 성공함을 확인합니다.
2. `baseline`에서는 허용된 HTTPS 첫 URL이 Redirect한 뒤 합성 Internal API까지 도달하는 것을 확인합니다.
3. `reject`, `strict`, 그리고 `compose.block.yaml` 네트워크 분리를 각각 적용해 내부 도달이 멈추는 것을 비교합니다.
4. MLflow 모의 취약 프로필에서 응답 본문이 즉시 노출되는 것을 확인하고, 수정 프로필·권한·본문 마스킹으로 차단합니다.
5. Plane 모의 취약 프로필에서 비동기 전달 로그의 본문을 관리자 계정이 읽는 것을 확인하고, Redirect 거부·로그 필터·권한 통제로 차단합니다.

`run_id`는 모든 단계의 공통 상관관계 키입니다. Webhook 감사 로그, 수신 서버 관측, Internal `/observations`을 반드시 같은 `run_id`로 비교합니다.

## 2. 실제 제품 참고 범위와 모의 범위

| 대상 | 실제 공개 정보 | 이 저장소의 모의 범위 |
|---|---|---|
| MLflow CVE-2026-64849 | 영향 범위는 `>= 3.10.0, < 3.15.0`, 수정은 3.15.0입니다. 무인증 테스트 경로와 응답 본문 노출, Redirect/DNS 시간차가 핵심입니다. | `v3.13.0-vulnerable`: 명시적으로 승인된 로컬 실험에서 무인증 테스트와 본문 반환을 모의합니다. `v3.15.0-fixed`: `strict` 연결 검증을 강제하며, 로컬 Alice 관리자 권한·본문 마스킹을 비교합니다. |
| Plane CVE-2026-105636 | Plane `<= 1.3.1`은 최초 Webhook URL만 검증한 뒤 3xx Redirect 목적지를 재검증하지 않아, worker가 내부 주소로 이동하고 응답을 Webhook 로그에 저장할 수 있었습니다. 1.4.0에서 수정되었습니다. | `v1.3.1-vulnerable`: 허용된 HTTPS 첫 URL → 302 → 합성 Internal API → 비동기 로그 조회를 재현합니다. `v1.4.0-fixed`: Redirect를 거부합니다. 로그 필터와 workspace 관리자 권한도 별도 방어 축으로 제공합니다. |

공식 근거는 [MLflow 보안 권고](https://github.com/mlflow/mlflow/security/advisories/GHSA-7gwp-5pfp-969j), [Plane 보안 권고](https://github.com/makeplane/plane/security/advisories/GHSA-mq87-52pf-hm3h), [Plane 수정 커밋](https://github.com/makeplane/plane/commit/04622ce1188c4680951f0001e35efb342fe51615)과 [상세 참고표](docs/REFERENCES.md)를 참조합니다.

## 3. 실행 전제와 안전장치

- CVE 라우트는 기본적으로 존재하지 않습니다. `CVE_DEMO_MODE`와 `CVE_DEMO_ACK=local-lab-only`를 동시에 지정해야만 열립니다.
- 자동 시연은 `127.0.0.1`에만 Webhook 포트를 게시하고, 합성 `internal`, `receiver` 컨테이너만 대상으로 합니다.
- 직접 Internal URL 등록은 모든 프로필에서 음성 대조군으로 차단됩니다. 취약 흐름은 외부처럼 보이는 HTTPS 첫 URL의 **후속 Redirect**를 통해서만 모의합니다.
- 응답 본문과 보고서는 모두 `fictional: true`, `REPORT-<run_id>` 표식이 있는 가상 데이터입니다.
- TLS 인증서 검증과 호스트 이름 검증은 해제하지 않습니다. 테스트 CA와 URL 예외는 정확한 origin·CIDR만 허용합니다.

## 4. 주요 환경 변수

| 변수 | 용도 |
|---|---|
| `POLICY` | `baseline`, `reject`, `strict` 중 URL·Redirect 검증 정책 |
| `CVE_DEMO_MODE` | `none`, `mlflow-2026-64849`, `plane-2026-105636`, `all` |
| `MLFLOW_VERSION_PROFILE` | `v3.13.0-vulnerable` 또는 `v3.15.0-fixed` 모의 프로필 |
| `MLFLOW_CVE_AUTH_REQUIRED` | 수정 MLflow 비교 시 CVE 경로 Bearer 인증 요구 여부 |
| `PLANE_VERSION_PROFILE` | `v1.3.1-vulnerable` 또는 `v1.4.0-fixed` 모의 프로필 |
| `CVE_DEMO_PLANE_ORIGIN` | Plane 취약 시나리오가 허용하는 합성 HTTPS 첫 Redirect URL |
| `PLANE_LOG_FILTER` | `none`, `redact-sensitive`, `metadata-only`; Plane 본문을 **저장 전** 필터링 |
| `RETURN_BODY`, `STORE_BODY`, `READ_BODY` | MLflow 즉시 응답, 저장 본문, 재조회 본문에 각각 적용하는 `full`/`limit`/`mask`/`omit` 정책 |

`baseline`은 등록 시점의 URL만 검사하는 비교용 기준선입니다. `reject`는 Redirect를 거부합니다. `strict`는 매 hop의 URL·DNS 결과를 검사하고 선택한 숫자 IP에 고정 연결한 뒤 peer IP와 TLS 호스트 이름을 확인합니다.

## 5. 재현 명령

Docker Desktop이 실행된 PowerShell에서 저장소 최상위로 이동합니다.

```powershell
# 1) 단위·통합 검증 (CVE 프로필은 기본 비활성)
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --build verifier

# 2) 취약/수정 프로필, URL 정책, 네트워크 분리, 로그 필터를 한 번에 비교
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-cves.ps1

# 3) 일반 Webhook 호환성 9개 조합을 다시 확인하고 결과를 갱신
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-matrix.ps1 -SkipBuild
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\export-results.ps1
```

두 번째 명령은 여섯 결과를 `artifacts/final-*.json`에 만들고, 버전 관리되는 요약본을 [docs/verification/final-scenario-2026-11-24.json](docs/verification/final-scenario-2026-11-24.json)에 기록합니다.

| 케이스 | MLflow 내부 도달 / 본문 노출 | Plane 내부 도달 / 로그 노출 | 비교 목적 |
|---|---:|---:|---|
| `baseline-vulnerable` | 예 / 예 | 예 / 예 | 취약 기준선 |
| `reject-open` | 아니오 / 아니오 | 아니오 / 아니오 | Redirect 거부 |
| `strict-open` | 아니오 / 아니오 | 아니오 / 아니오 | hop별·연결 시점 검증 |
| `baseline-network-blocked` | 아니오 / 아니오 | 아니오 / 아니오 | Webhook→Internal 네트워크 분리 |
| `fixed-url-auth-mask` | 아니오 / 아니오 | 아니오 / 아니오 | 수정 프로필, MLflow 인증, 본문 마스킹 |
| `plane-log-filter` | 아니오 / 아니오 | 예 / 아니오 | 내부 도달과 Plane 로그 노출이 별개임을 증명 |

자동 스크립트는 결정론적 DNS rebinding 비교도 실행합니다. 등록 단계에는 허용된 `127.0.0.1`, 전송 단계에는 합성 Internal 서버 `127.0.0.2`가 해석되도록 resolver를 주입합니다. 결과는 baseline에서 도달, strict에서 차단이어야 합니다.

## 6. 결과 해석과 증거

- `internal_api_reached`: Internal 서버의 `GET /observations?run_id=<UUID>`가 해당 run을 기록했는지입니다. `source_ip`도 함께 남습니다.
- `report_content_exposed`: MLflow에서는 동기 테스트 응답, Plane에서는 권한 있는 Webhook 로그에 `REPORT-<run_id>`가 있는지입니다.
- `connection_trace`: Webhook transport가 남긴 DNS 해석·TCP 연결·TLS 확인·HTTP Redirect 이벤트입니다.
- `network_event_count`: 위 애플리케이션 연결 추적의 이벤트 수입니다.

`connection_trace`는 **PCAP가 아닙니다.** Docker 검증에서는 애플리케이션 수준 증거만 생성합니다. 실제 Kali/별도 VM 시연에서 패킷 증거가 필요하면 실험 전용 인터페이스와 실험용 IP만 대상으로 `tcpdump`를 실행하고, pcap 파일명과 `run_id`를 함께 기록합니다. 자세한 책임 분리는 [indications.md](indications.md)를 따릅니다.

## 7. API 빠른 참조

정상 API는 Bearer 인증을 사용합니다.

| 경로 | 의미 |
|---|---|
| `GET /health`, `POST /webhook` | 기존 통신 확인 호환 경로 |
| `POST /api/workspaces/{ws}/hooks` | 정상 Webhook 등록 |
| `POST /api/workspaces/{ws}/hooks/{id}/test` | MLflow식 동기 테스트 |
| `POST /api/workspaces/{ws}/events` | Plane식 비동기 이벤트 큐잉 |
| `GET /api/workspaces/{ws}/deliveries?run_id=...` | 일반 전달 이력 |
| `GET /api/workspaces/{ws}/runs/{run_id}` | 감사 로그·전달 이력 |
| `POST /api/2.0/mlflow/webhooks`, `POST /api/2.0/mlflow/webhooks/{id}/test` | 승인된 MLflow CVE 모의 전용 |
| `POST /api/cve/plane/workspaces/{ws}/webhooks` | 승인된 Plane CVE 모의 전용 |
| `GET /api/cve/plane/workspaces/{ws}/webhook-logs/{id}?run_id=...` | Plane CVE 모의 전달 로그; 해당 workspace admin 또는 내부 operator만 허용 |

전체 API 계약과 Internal/Kali 담당자의 구현·시연 순서는 [indications.md](indications.md)에 정리했습니다.

## 8. 알려진 한계

- 단일 Python worker와 SQLite를 사용합니다. Celery, Redis, MongoDB, 실제 제품 인증 체계나 재시도 보장은 모의하지 않습니다.
- `v*-fixed`는 해당 CVE의 핵심 방어 동작을 설명하기 위한 프로필이지, 실제 제품의 완전한 패치 구현이나 배포 권고가 아닙니다.
- DNS rebinding은 외부 DNS를 사용하지 않는 결정론적 실험입니다.
- URL·IP 검증과 네트워크 차단은 서로 대체하지 않습니다. 실제 VM에서는 방화벽 정책과 캡처 결과를 별도로 검증해야 합니다.
