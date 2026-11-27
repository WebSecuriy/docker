# 예측랩 Webhook SSRF 모의·방어 실험

가상의 B2B 수요예측 기업 **예측랩**을 배경으로, 고객사가 등록한 Webhook URL로 모델 등록·이슈 변경 이벤트를 보내는 경량 Python 서버입니다. 수업용 합성 Internal 보고서 API를 대상으로 Redirect와 DNS rebinding이 일으킬 수 있는 SSRF 흐름, 응답/로그 노출, 그리고 단계별 방어를 Docker에서 비교합니다.

> 이 저장소는 실제 MLflow·Plane 제품을 설치하거나 공격하지 않습니다. 공개된 흐름을 참고한 **격리된 합성 데이터 전용 모의 구현**입니다. 실제 서비스, cloud metadata, 실제 고객 데이터나 자격 증명을 대상으로 사용하면 안 됩니다.

## 빠른 문서 안내

| 문서 | 용도 |
|---|---|
| [explanation.md](explanation.md) | CVE 모의 범위, 실행 명령, 방어 해석, 알려진 한계 |
| [indications.md](indications.md) | Internal·Kali 담당자의 API 계약, TLS/방화벽 조건, 최종 시연 순서 |
| [demonstration/](demonstration/README.md) | Docker에서 실제 생성한 1~8단계 주석 포함 JSON 증거 |
| [docs/LAB.md](docs/LAB.md) | 일반 API, 설정, VM 이전, 상세 운영 제약 |
| [docs/VM-DEMO-GUIDE.md](docs/VM-DEMO-GUIDE.md) | VM 3대(webhook/internal/kali) 설정·배포·레이어별 시연 절차 |
| [docs/REFERENCES.md](docs/REFERENCES.md) | 실제 제품 참고 범위와 모의 구현의 경계 |
| [docs/VERIFICATION.md](docs/VERIFICATION.md) | Docker 검증 결과와 해석 주의사항 |
| [final-scenario JSON](docs/verification/final-scenario-2026-11-24.json) | CVE 비교 6개 케이스의 영구 요약 |
| [docker-matrix JSON](docs/verification/docker-matrix.json) | 일반 Webhook 정책·네트워크·본문 정책 회귀 결과 |

## 1. 현재 구현 상태

2026-11-24 기준 Python 표준 라이브러리, SQLite, 단일 worker, Docker Compose로 구현·검증했습니다.

| 항목 | 완료 상태 |
|---|---|
| 기존 호환성 | `GET /health`, `POST /webhook` 유지 |
| 일반 Webhook | MLflow식 동기 테스트와 Plane식 비동기 전송·이력 조회 |
| URL 방어 | `baseline` / `reject` / `strict`, TLS·hostname 검증 유지 |
| 네트워크 방어 | `compose.block.yaml`으로 Webhook → Internal 경로 분리 비교 |
| MLflow 모의 | CVE-2026-64849 취약/수정 비교, 동기 본문 노출·권한·마스킹 |
| Plane 모의 | CVE-2026-105636 취약/수정 비교, 비동기 로그·필터·권한 통제 |
| 관측 | `run_id`, 최초 URL, Redirect, DNS/연결 trace, Internal source IP |
| 영속성 | 쓰기 가능한 `/data`의 SQLite, 컨테이너 UID/GID `10001:10001` |

현재 검토 대상은 [PR #4](https://github.com/WebSecuriy/docker/pull/4)이며, Internal·Kali 담당자의 연동 기준은 [Issue #5](https://github.com/WebSecuriy/docker/issues/5)와 `indications.md`입니다.

## 2. 세 노드와 데이터 흐름

```text
Kali receiver / redirect
  ├─ POST /webhook: 정상 수신
  └─ /redirect: 합성 Internal 보고서 URL로 30x 응답
               │
               ▼
Webhook 서버
  ├─ 일반 관리 API, MLflow식 동기 테스트, Plane식 비동기 worker
  ├─ URL·IP·Redirect·TLS 정책 및 본문/로그/권한 방어
  └─ X-Run-ID, 연결 trace, 감사 로그
               │
               ▼
Internal 보고서 API
  ├─ 합성 REPORT-<run_id> 보고서
  └─ /observations?run_id=<UUID>로 도달·source IP 관측
```

모든 실행에는 새 UUID `run_id`를 사용합니다. 동일 `run_id`가 Webhook 결과, Kali receiver 관측, Internal 관측에 모두 나타나야 합니다. `REPORT-<run_id>`는 가상 보고서의 노출 여부를 판단하는 표식일 뿐 실제 민감 정보가 아닙니다.

## 3. 실제 제품 참고와 모의 프로필

| 참고 흐름 | 취약 모의 프로필 | 수정 모의 프로필 | 이 저장소가 비교하는 결과 |
|---|---|---|---|
| MLflow CVE-2026-64849 | `v3.13.0-vulnerable`: 승인된 로컬 실험에서 무인증 동기 테스트와 응답 본문 반환 | `v3.15.0-fixed`: strict 연결 검증, Alice 관리자 권한, 응답 본문 마스킹 | Internal 도달 여부와 동기 응답 노출 여부 |
| Plane CVE-2026-105636 | `v1.3.1-vulnerable`: HTTPS 첫 URL → 302 → Internal API → 비동기 Webhook 로그 조회 | `v1.4.0-fixed`: Redirect 거부 | Internal 도달 여부와 저장·조회 로그 노출 여부 |

MLflow는 [공식 보안 권고](https://github.com/mlflow/mlflow/security/advisories/GHSA-7gwp-5pfp-969j), Plane은 [공식 보안 권고](https://github.com/makeplane/plane/security/advisories/GHSA-mq87-52pf-hm3h)와 [수정 커밋](https://github.com/makeplane/plane/commit/04622ce1188c4680951f0001e35efb342fe51615)을 참고했습니다. 버전 이름은 실제 바이너리를 설치했다는 뜻이 아니며, 자세한 경계는 [docs/REFERENCES.md](docs/REFERENCES.md)에 있습니다.

CVE 전용 API는 기본적으로 꺼져 있습니다. 격리된 실험에서만 다음 두 환경 변수를 함께 지정해야 활성화됩니다.

```text
CVE_DEMO_MODE=all
CVE_DEMO_ACK=local-lab-only
```

Plane CVE 모의에는 정확한 HTTPS 첫 Redirect URL도 필요합니다.

```text
CVE_DEMO_PLANE_ORIGIN=https://<lab-receiver>/redirect?status=302
```

직접 Internal URL 등록은 모든 프로필의 음성 대조군으로 거부됩니다. 취약 흐름은 허용된 HTTPS 첫 URL 뒤의 Redirect 또는 결정론적 DNS rebinding을 통해서만 모의합니다.

## 4. 방어 축

| 축 | 설정/구현 | 의미 |
|---|---|---|
| URL·Redirect 정책 | `POLICY=baseline|reject|strict` | baseline은 비교용 최초 URL 검사만, reject는 Redirect 거부, strict는 hop별 검사·IP 고정 연결·peer/TLS 확인 |
| 네트워크 분리 | `compose.block.yaml` | URL 정책과 별개로 Webhook → Internal egress를 차단 |
| MLflow 응답 보호 | `MLFLOW_CVE_AUTH_REQUIRED`, `RETURN_BODY` | 수정 비교에서 무인증 401, viewer/bob 403, Alice admin만 허용; 응답 본문은 mask/omit/limit 가능 |
| Plane 로그 보호 | `PLANE_LOG_FILTER`, `STORE_BODY`, `READ_BODY` | 로그 저장 전 합성 민감 필드 필터링과 조회 시 본문 정책 적용 |
| Plane 로그 권한 | workspace admin 또는 internal operator | 같은 workspace 일반 member와 다른 workspace 계정은 403 |

`connection_trace`는 DNS 해석, TCP 연결, TLS, Redirect를 기록하는 **애플리케이션 수준 연결 추적**입니다. PCAP가 아닙니다. 실제 Kali/VM 패킷 증거가 필요하면 `indications.md`의 실습망 전용 캡처 절차를 따릅니다.

## 5. API 빠른 참조

### 기존·일반 API

| 경로 | 목적 |
|---|---|
| `GET /health` | Webhook 상태 확인 |
| `POST /webhook` | 기존 연결 확인; `202 accepted` |
| `POST /api/workspaces/{ws}/hooks` | 정상 Webhook 등록 |
| `POST /api/workspaces/{ws}/hooks/{id}/test` | MLflow식 동기 테스트 |
| `POST /api/workspaces/{ws}/events` | Plane식 비동기 이벤트 등록 |
| `GET /api/workspaces/{ws}/deliveries?run_id=...` | 전달 이력·저장 결과 조회 |
| `GET /api/workspaces/{ws}/runs/{run_id}` | 감사 로그·전달 기록 조회 |

정상 관리 API에는 `Authorization: Bearer <token>`이 필요합니다. 기본 합성 계정은 `alice`(alpha admin), `viewer`(alpha member), `bob`(beta admin), `operator`(내부 운영자)입니다.

### CVE 모의 전용 API

| 경로 | 목적 |
|---|---|
| `POST /api/2.0/mlflow/webhooks` | MLflow CVE 모의 Webhook 등록 |
| `POST /api/2.0/mlflow/webhooks/{id}/test` | MLflow CVE 모의 동기 테스트 |
| `POST /api/cve/plane/workspaces/{ws}/webhooks` | Plane CVE 모의 Webhook 등록 |
| `GET /api/cve/plane/workspaces/{ws}/webhook-logs/{id}?run_id=...` | Plane CVE 모의 전달 로그 조회 |

Internal 기준 API는 `GET /health`, `GET|POST /reports/alpha`, `GET /observations?run_id=<UUID>`입니다. 관측 `requests` 원소는 `[run_id, method, path, created, source_ip]`이며, 관측 조회 자체는 보고서 도달 기록을 새로 만들지 않습니다.

## 6. Docker 빠른 시작

Windows PowerShell에서 저장소 최상위로 이동합니다.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose build
docker compose up -d --no-build --wait
docker compose exec -T webhook python probe.py
```

Docker fixture와 테스트를 포함한 최종 시연 증거는 다음 명령으로 다시 생성합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\capture-demonstration.ps1
```

이미 이미지를 빌드한 경우:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\capture-demonstration.ps1 -SkipBuild
```

이 명령은 통합 테스트, CVE 비교, 일반 Webhook 매트릭스, 결과 export를 순서대로 실행하고 `demonstration/01`~`08` JSON을 갱신합니다.

## 7. 최종 8단계 시연과 증거

| 단계 | Docker 결과 파일 | 확인 내용 |
|---:|---|---|
| 1 | [01](demonstration/01-health-and-legacy-webhook.json) | 기존 `/health`, `/webhook` 호환성 |
| 2 | [02](demonstration/02-normal-webhook-delivery.json) | 정상 receiver로 MLflow식·Plane식 전달 |
| 3 | [03](demonstration/03-baseline-vulnerable.json) | baseline Redirect 후 Internal 도달과 MLflow 본문/Plane 로그 노출 |
| 4 | [04](demonstration/04-reject-and-strict.json) | reject·strict Redirect 방어 |
| 5 | [05](demonstration/05-network-blocked.json) | Webhook → Internal 네트워크 분리 |
| 6 | [06](demonstration/06-mlflow-fixed-auth-mask.json) | MLflow 401/403/strict·마스킹, Plane Redirect 거부 |
| 7 | [07](demonstration/07-plane-log-filter-and-access.json) | Plane 로그 저장 전 필터와 권한 통제 |
| 8 | [08](demonstration/08-run-id-correlation-and-dns-rebinding.json) | 세 노드 `run_id` 대조와 DNS rebinding baseline/strict 비교 |

각 JSON은 `_comment`, `expected`, `observed`, `key_observations`, `source_artifacts`를 포함합니다. JSON 자체에는 문법상 주석을 넣을 수 없으므로 이 필드들이 핵심 로그의 설명 역할을 합니다.

## 8. 실제 Docker 검증 결과

| 검증 | 결과 |
|---|---|
| HTTP/TLS/SQLite/권한/URL 통합 테스트 | 16개 통과 |
| 일반 Webhook 정책·네트워크·본문 매트릭스 | 9개 조합 통과, 정상 전달 18/18 성공 |
| `baseline-vulnerable` | MLflow·Plane 모두 Internal 도달 및 합성 표식 노출 |
| `reject-open`, `strict-open` | Internal 미도달·표식 미노출 |
| `baseline-network-blocked` | 네트워크 분리로 Internal 미도달 |
| `fixed-url-auth-mask` | MLflow 무인증 401, viewer/bob 403, strict·마스킹; Plane Redirect 거부 |
| `plane-log-filter` | Internal 도달은 유지 가능하지만 Plane 로그 표식 미노출, viewer/bob 403 |
| DNS rebinding | baseline은 합성 재바인딩 대상 도달, strict는 차단 |

세부 원본과 SHA-256은 [docs/verification/final-scenario-2026-11-24.json](docs/verification/final-scenario-2026-11-24.json), 일반 회귀 결과는 [docs/verification/docker-matrix.json](docs/verification/docker-matrix.json)에 있습니다.

## 9. Internal·Kali 담당자 인계

상세 구현 계약은 [indications.md](indications.md)가 기준입니다.

- **Internal 담당자**: 합성 `/reports/alpha`·`/observations` 계약, source IP 기록, Kali 직접 접근 차단, Webhook→Internal 방화벽 비교를 구현합니다.
- **Kali 담당자**: 검증 가능한 TLS 수신 `/webhook`, 실험 전용 `/redirect`, 수신 관측, 정확한 CA/SAN/origin/IP 정보를 제공합니다. Internal을 직접 호출해 증거를 만들지 않습니다.
- **모두**: IP·port·CA·보고서 URL은 환경 변수/설정 파일로 주입하고, 코드에 고정하지 않습니다. 실제 VM으로 옮긴 뒤에는 Docker fixture 통과와 별도로 같은 시연을 재실행합니다.

## 10. 알려진 제한과 운영 주의

- SQLite와 단일 worker를 사용하며 Celery/Redis/MongoDB, 실제 제품 인증/재시도/보존 정책은 모의하지 않습니다.
- `v*-fixed`는 각 CVE의 핵심 방어 방향을 설명하는 프로필이며 실제 제품의 완전한 패치 검증이나 배포 권고가 아닙니다.
- DNS rebinding은 외부 DNS가 아닌 결정론적 resolver 실험입니다.
- URL·IP 검증, 네트워크 egress 제한, 응답/로그 노출 제한, 권한 통제는 서로 대체하지 않습니다. 실제 VM에서는 각 방어를 함께 적용하고 재검증해야 합니다.
- 모든 시연은 합성 데이터와 격리된 실습 환경에서만 실행합니다.
