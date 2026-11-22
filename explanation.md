# CVE 재현 점검 및 실행 가이드

작성일: 2026-11-23
대상: `CVE-2026-64849`(MLflow), `CVE-2026-30242`(Plane)

## 1. 범위와 안전 조건

이 저장소는 취약한 MLflow 또는 Plane 바이너리를 설치·공격하는 프로젝트가 아니다. 두 공개 권고문에서 확인되는 **웹훅 SSRF와 응답 본문 노출의 핵심 흐름을, 가상 보고서 API와 `REPORT-<run_id>` marker만으로 재현하는 교육용 모의 서버**다.

- 실제 클라우드 metadata 주소, 실제 고객 URL, 실제 MLflow/Plane 인스턴스는 사용하지 않는다.
- CVE 데모는 기본값이 `none`이며, `CVE_DEMO_ACK=local-lab-only`라는 명시적 확인값 없이는 서버가 기동하지 않는다.
- Plane 데모의 직접 등록 대상은 `CVE_DEMO_PLANE_TARGET`와 정확히 같은 가상 보고서 URL 하나로 제한된다.
- 실행기 [scripts/demo-cves.ps1](scripts/demo-cves.ps1)는 webhook 호스트 공개를 `127.0.0.1`로 고정하고, 종료 시 원래 환경값으로 컨테이너를 다시 만든다.

따라서 아래 결과는 **이 모의 구현의 재현 결과**다. 실제 제품의 설치 상태·배포 설정·인증·네트워크가 취약하다는 증거로 사용하면 안 된다.

## 2. 공개 권고문과 재현 목표

| 항목 | 공개 권고문에서 확인한 사실 | 이 저장소의 재현 목표 |
| --- | --- | --- |
| MLflow `CVE-2026-64849` | GitHub 권고문은 MLflow `>= 3.10.0, < 3.15.0`을 영향 범위, `3.15.0`을 수정 버전으로 명시한다. 기본 인증 없는 Tracking Server의 동기 `POST /api/2.0/mlflow/webhooks/{id}/test`가 원래 URL만 검사한 뒤 Redirect를 따라가고, 새 DNS 해석/연결을 pin하지 않아 내부 응답 상태·본문을 호출자에게 반환한 흐름이다. | 직접 내부 URL은 거부하면서, 허용된 HTTPS 첫 홉의 `302` Redirect가 가상 내부 보고서로 향하게 한다. 무인증 모의 `/test` 호출의 `result.response_body`에서 marker를 확인한다. |
| Plane `CVE-2026-30242` | GitHub 권고문은 Plane `<= 1.2.1`에서 workspace ADMIN이 loopback 외의 사설/metadata 주소를 직접 웹훅 URL로 등록할 수 있었고, 비동기 전달 결과 본문이 로그에 저장·재조회된다고 설명한다. 수정 버전은 `1.2.3`이다. | workspace ADMIN이 Docker 보호망의 가상 보고서 URL을 **직접** 등록한다. 이벤트 후 비동기 delivery/log에서 marker를 확인하고, 다른 workspace 계정은 로그 조회가 403인지 확인한다. |

근거: [MLflow 공식 GitHub Security Advisory](https://github.com/mlflow/mlflow/security/advisories/GHSA-7gwp-5pfp-969j), [MLflow 수정 커밋](https://github.com/mlflow/mlflow/commit/ba949522477cbd5915aa55d29b0cfad7d5ddf939), [Plane 공식 GitHub Security Advisory](https://github.com/makeplane/plane/security/advisories/GHSA-fpx8-73gf-7x73), [Plane v1.2.3 릴리스](https://github.com/makeplane/plane/releases/tag/v1.2.3).

중요: 기존 참고 대상인 Plane v1.3.1은 `1.2.3` 이후 버전이다. 따라서 v1.3.1 참고 모드는 전달·이력 구조를 이해하기 위한 것이며, CVE-2026-30242의 취약 버전 자체가 아니다.

## 3. 총점검 결과와 이번 보완

기존 구현에는 이미 다음 공통 기능이 있었다.

- [webhook/server.py](webhook/server.py)의 MLflow 참고 모드: 동기 전송 후 상태·본문을 즉시 반환한다.
- [webhook/server.py](webhook/server.py)의 Plane 참고 모드: SQLite queue와 단일 worker로 비동기 전송하고, 권한 있는 workspace ADMIN만 이력을 본다.
- [webhook/transport.py](webhook/transport.py)의 `baseline`/`reject`/`strict`: Redirect, DNS 재해석, 실제 연결 IP, TLS 인증서·호스트 이름을 비교 관찰한다.
- [fixtures/server.py](fixtures/server.py)의 고정 Redirect와 [internal/server.py](internal/server.py)의 `REPORT-<run_id>` 및 `/observations` 관측 API.

하지만 공통 baseline만으로는 두 CVE의 등록 당시 특징을 분명히 구분할 수 없었다. 이번에 다음을 추가했다.

| 보완 | 구현 |
| --- | --- |
| MLflow 전용 재현 경로 | `CVE_DEMO_MODE=mlflow-2026-64849` 또는 `all`일 때만, 무인증 `POST /api/2.0/mlflow/webhooks`와 `POST /api/2.0/mlflow/webhooks/{id}/test` 모의 경로를 연다. 첫 홉 HTTPS 검사와 원래 URL의 재검증 뒤, baseline에서는 Redirect 목적지를 재검증하지 않은 별도 연결을 수행한다. 결과는 `success`, `response_status`, `response_body`를 포함한다. |
| Plane 전용 재현 경로 | `CVE_DEMO_MODE=plane-2026-30242` 또는 `all`일 때만, ADMIN용 `POST /api/cve/plane/workspaces/{workspace}/webhooks`를 연다. baseline에서는 Plane v1.2.1의 **loopback만 거부**한 등록 검사를 모사하되, 실제 전송 대상은 환경변수로 지정한 가상 내부 보고서 URL 하나에만 고정한다. |
| 방어 비교 | `strict`/`reject`에서는 Plane 전용 등록 경로도 표준 공개 목적지 검증을 사용한다. MLflow 전용 경로는 Redirect 다음 hop에서 차단된다. |
| 반복 가능한 증거 | [scripts/cve_demo.py](scripts/cve_demo.py)가 두 시나리오와 권한 거부를 assert하고 `artifacts/cve-baseline.json`, `artifacts/cve-strict.json`에 run_id·전송 결과·내부 관측을 남긴다. |
| 회귀 테스트 | [tests/test_lab.py](tests/test_lab.py)에 MLflow의 무인증 Redirect read-back, Plane의 직접 사설 등록/비동기 로그/타 workspace 403, strict 차단을 추가했다. |

일부러 단순화한 부분도 남아 있다.

- 실제 MLflow/Plane의 ORM, proto, 인증 플러그인, Celery/MongoDB, 모든 이벤트 enum과 REST 세부 경로는 복제하지 않는다.
- MLflow 경로는 실제 취약 API의 형태를 참고하지만, `run_id`는 이 실험의 상관관계를 위한 추가 필드다.
- Plane 경로는 실제 제품 URI를 복제한다고 주장하지 않으며, 권고문의 핵심인 ADMIN 권한·직접 사설 등록·비동기 로그 read-back만 모사한다.
- DNS rebinding은 [tests/test_lab.py](tests/test_lab.py)의 단위 테스트로 확인한다. 사람이 수행하는 Docker 시연은 더 결정적인 HTTPS Redirect 흐름을 기본으로 한다.

## 4. 가장 간단한 실행 방법

사전 조건은 Docker Desktop의 **Linux containers 엔진이 실행 중**이고 PowerShell 현재 위치가 저장소 최상위인 것이다. `.env`가 없다면 한 번만 생성한다.

```powershell
Copy-Item .env.example .env   # .env가 아직 없을 때만
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-cves.ps1
```

이미지를 빌드한 상태라면 다음처럼 빌드를 건너뛸 수 있다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-cves.ps1 -SkipBuild
```

실행기는 다음 순서로 동작한다.

1. `receiver`의 현재 Docker IP만 허용하는 임시 HTTPS 예외 파일을 만든다.
2. `baseline + full/full/full`로 MLflow와 Plane의 취약 흐름을 실행한다.
3. `strict + full/full/full`로 같은 흐름이 차단되는지 실행한다.
4. 실행별 JSON 증거를 `artifacts/`에 쓰고, 환경변수와 컨테이너를 원래의 안전 설정으로 복구한다.

결과를 빠르게 확인하려면 다음을 실행한다.

```powershell
Get-Content .\artifacts\cve-baseline.json -Raw
Get-Content .\artifacts\cve-strict.json -Raw
docker compose logs --tail 100 webhook internal receiver
```

## 5. 수동 실행 절차

실행기가 하는 일을 분리해 보려면 아래 명령을 사용한다. 이 블록은 로컬 Docker fixture만 대상으로 한다.

```powershell
$env:WEBHOOK_BIND_IP = '127.0.0.1'
$env:ENABLED_MODES = 'mlflow,plane'
$env:RETURN_BODY = 'full'
$env:STORE_BODY = 'full'
$env:READ_BODY = 'full'
$env:ALLOW_HTTP = 'true'
$env:REPORT_API_URL = 'http://internal:8000/reports/alpha'
$env:CVE_DEMO_MODE = 'all'
$env:CVE_DEMO_ACK = 'local-lab-only'
$env:CVE_DEMO_PLANE_TARGET = $env:REPORT_API_URL

docker compose -f compose.yaml -f compose.lab.yaml --profile test build
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-lab.ps1

# 취약 조건
$env:POLICY = 'baseline'
docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait internal receiver webhook
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier `
  python scripts/cve_demo.py --policy baseline --output /artifacts/cve-baseline.json

# 방어 조건
$env:POLICY = 'strict'
docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait internal receiver webhook
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier `
  python scripts/cve_demo.py --policy strict --output /artifacts/cve-strict.json
```

`prepare-lab.ps1`은 receiver가 재생성되어 IP가 바뀔 때마다 다시 실행해야 한다. 생성되는 `config/url-exceptions.lab.local.json`은 Git에서 제외되어 있고, `https://receiver:8443`의 현재 `/32` 주소 하나만 허용한다.

수동 실행 뒤에는 `CVE_DEMO_MODE=none`, `CVE_DEMO_ACK=`로 되돌리고 webhook 컨테이너를 재생성한다. 자동 실행기는 이 정리를 자동으로 한다.

```powershell
$env:CVE_DEMO_MODE = 'none'
$env:CVE_DEMO_ACK = ''
$env:CVE_DEMO_PLANE_TARGET = ''
$env:POLICY = 'strict'
docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait webhook internal receiver
```

## 6. 성공 판정표

| 조건 | MLflow 모의 결과 | Plane 모의 결과 | 내부 관측 |
| --- | --- | --- | --- |
| baseline | 직접 내부 첫 홉 등록은 400, HTTPS Redirect 등록 후 **무인증** `/test`의 `response_body`에 `REPORT-<run_id>` | ADMIN의 직접 내부 URL 등록 201, 이벤트 202 후 같은 workspace 로그 본문에 marker | 두 모드 모두 `internal_api_reached: true` |
| strict | HTTPS 첫 홉은 통과해도 Redirect 다음 hop이 blocked, marker 없음 | 직접 내부 URL 등록 400, delivery가 만들어지지 않음 | 두 모드 모두 `false` |
| Plane 타 workspace 계정 | 해당 없음 | 다른 workspace의 `deliveries?run_id=...` 조회는 403 | 조회가 보고서 도달 기록을 만들지 않음 |

`internal_api_reached`와 `report_content_exposed`는 반드시 구분한다. 본문을 mask/omit/limit해도 baseline에서 내부 요청 자체는 이미 발생할 수 있다. 본문 제한은 유출 read-back 완화일 뿐, SSRF 전송 차단의 대체 수단이 아니다.

## 7. 추가 회귀 검증

이번 변경에서 다음 검증을 완료했다.

- `docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --build verifier`: 15개 테스트 통과.
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo-cves.ps1`: baseline/strict CVE 모의 시연과 두 JSON 증거 생성 성공.
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-matrix.ps1 -SkipBuild`: 기존 일반 정책 매트릭스 9개 조합 통과. 출력 확인이 필요한 `baseline-read-mask` 조건은 최신 이미지로 별도 재실행해 갱신했다.
- `git diff --check`: 통과.

다음 명령은 이후 변경 후에도 같은 회귀 검증을 다시 수행하는 방법이다.

```powershell
# HTTP/TLS/SQLite 단위 및 통합 회귀 테스트
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --build verifier

# 기존 일반 정책·네트워크·본문 9개 조합
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-matrix.ps1 -SkipBuild
```

실행 시 `dockerDesktopLinuxEngine` 연결 오류가 나오면 Docker Desktop을 시작한 뒤 위 명령을 다시 실행한다.

## 8. 발표 때 제시할 최소 증거

각 조건에서 다음 네 가지를 같은 `run_id`로 제시하면 충분하다.

1. `artifacts/cve-*.json`의 등록 결과·test/delivery 로그·정책 판정.
2. `internal_observation`의 `internal_api_reached`와 요청 경로.
3. MLflow의 `result.response_body` 또는 Plane의 `delivery_log.result.body`에 있는 `REPORT-<run_id>` 여부.
4. `docker compose logs`의 webhook·receiver·internal JSON 로그.

실제 보고서, 토큰, cloud metadata, 타인의 주소를 입력하지 말고, 이 저장소의 가상 보고서 marker만 증거로 사용한다.
