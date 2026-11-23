# 실제 제품 참고와 모의 구현의 경계

이 프로젝트는 교육용 경량 모의 서버입니다. MLflow나 Plane 패키지·데이터베이스·worker를 설치하거나 실제 제품의 보안성을 시험하지 않습니다.

| 참고 대상 | 제품에서 참고한 흐름 | 이 저장소가 모사한 최소 동작 | 의도적으로 생략한 것 |
|---|---|---|---|
| [MLflow v3.13.0 handler](https://github.com/mlflow/mlflow/blob/v3.13.0/mlflow/server/handlers.py) 및 [delivery](https://github.com/mlflow/mlflow/blob/v3.13.0/mlflow/webhooks/delivery.py) | Webhook 등록, 테스트 전송, 상태·응답 본문을 테스트 결과로 반환 | 등록된 hook에 동기 전송 후 `response_status`, `response_body`, `run_id`를 즉시 반환 | Tracking server, experiment/model registry, OAuth, 실제 작업 큐 |
| [MLflow CVE-2026-64849 보안 권고](https://github.com/mlflow/mlflow/security/advisories/GHSA-7gwp-5pfp-969j) | 무인증 테스트 경로의 SSRF와 응답 본문 노출, Redirect/DNS 시간차 | 명시적 승인 아래의 route-shaped CVE API, Redirect·DNS 비교, 본문 정책 | 실제 제품 바이너리, 실제 취약 환경 제공, 실제 인증 기본값 주장 |
| [Plane v1.3.1 webhook task](https://github.com/makeplane/plane/blob/v1.3.1/apps/api/plane/bgtasks/webhook_task.py) | 이벤트 전달 worker와 Webhook 전달 결과·응답 본문 로그 | SQLite queue + single worker, 비동기 delivery history | Celery, Redis, MongoDB, 재시도·보존 정책 |
| [Plane CVE-2026-105636 보안 권고](https://github.com/makeplane/plane/security/advisories/GHSA-mq87-52pf-hm3h), [수정 커밋](https://github.com/makeplane/plane/commit/04622ce1188c4680951f0001e35efb342fe51615) | `<=1.3.1`에서 최초 URL 검사 뒤 Redirect 목적지 미재검증, 내부 응답이 Webhook 로그에 저장; 1.4.0 수정 | 외부처럼 보이는 HTTPS 첫 URL → 302 → 합성 Internal API → admin 로그 조회 | 전체 Plane API/권한 체계, 실제 데이터·네트워크 |

## 이 프로젝트의 정책은 제품 동작과 다를 수 있습니다

- `baseline`은 첫 등록 URL만 검사하는 **의도적인 실험 기준선**입니다. 제품의 모든 릴리스가 이 정책을 사용했다는 뜻이 아닙니다.
- `reject`와 `strict`는 비교 실험용 방어 정책입니다. `strict`는 각 hop의 DNS/IP 검사, 숫자 IP 연결 고정, peer IP, TLS 호스트 검증을 수행합니다.
- `v3.15.0-fixed`와 `v1.4.0-fixed`은 버전 이름을 붙인 모의 프로필입니다. 전자는 strict 전달·선택적 로컬 Bearer 인증을, 후자는 Redirect 거부를 적용합니다.
- `PLANE_LOG_FILTER`는 합성 보고서의 `report_marker`, `contract_summary`, `model_operations`만 좁게 처리하는 교육용 필터입니다. 범용 DLP가 아닙니다.
- 자동 결과의 `connection_trace`는 HTTP client가 기록한 애플리케이션 수준 이벤트이며 PCAP가 아닙니다.

실행 절차는 [../explanation.md](../explanation.md), 노드별 인계 사항은 [../indications.md](../indications.md)를 참조합니다.
