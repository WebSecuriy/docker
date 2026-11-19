# 실제 제품과 모의 구현의 구분

2026-11-20에 아래 태그의 공개 소스 원문을 확인했다. 제품 코드를 복사하거나 해당 제품의 전체 보안 동작을 재현하지 않고, 웹훅 응답이 호출자에게 전달되는 **경로**를 참고해 표준 라이브러리로 새로 작성했다. 두 모드는 서로 독립적이다.

| 참조 | 확인한 코드·기능 | 모의 서버에 반영한 범위 |
|---|---|---|
| MLflow v3.13.0 | [handlers.py의 `_test_webhook`](https://github.com/mlflow/mlflow/blob/v3.13.0/mlflow/server/handlers.py#L3385): 등록 웹훅 조회 후 테스트 결과를 API 응답으로 직렬화 | 등록된 웹훅에 동기 테스트 전송 후 상태·본문 즉시 반환 |
| MLflow v3.13.0 | [delivery.py의 `test_webhook`](https://github.com/mlflow/mlflow/blob/v3.13.0/mlflow/webhooks/delivery.py#L323): `response_status`, `response_body=response.text`, 성공·오류 결과 | 모의 API의 `result.status`, `result.body`, `outcome`, `error` |
| MLflow v3.13.0 | [delivery.py의 `_send_webhook_request`](https://github.com/mlflow/mlflow/blob/v3.13.0/mlflow/webhooks/delivery.py#L136): URL 검증, 요청 메타데이터·서명, requests 세션 전송 | 이벤트 전송 경로만 참고. 서명·세션·재시도·이벤트 스키마는 재현하지 않음 |
| Plane v1.3.1 | [webhook_task.py의 `webhook_activity`, `webhook_send_task`](https://github.com/makeplane/plane/blob/v1.3.1/apps/api/plane/bgtasks/webhook_task.py): 이벤트별 구독 필터 후 Celery 작업 등록, 전송, 재시도 | 구독 필터와 비동기 전달. SQLite 작업 테이블과 단일 Python 스레드로 대체 |
| Plane v1.3.1 | [같은 파일의 `save_webhook_log`](https://github.com/makeplane/plane/blob/v1.3.1/apps/api/plane/bgtasks/webhook_task.py#L96): 요청·상태·응답 헤더·`response_body` 저장, MongoDB 실패 시 DB 저장 | 전송 결과와 응답 본문을 SQLite에 저장. 별도 이력 API로 노출 |
| Plane v1.3.1 | [base.py의 `WebhookLogsEndpoint`](https://github.com/makeplane/plane/blob/v1.3.1/apps/api/plane/app/views/webhook/base.py#L121): 워크스페이스 ADMIN 권한, workspace와 webhook으로 조회 범위 한정 | 워크스페이스 admin 또는 내부 operator만 이력 조회. 다른 워크스페이스는 403 |

## URL 정책을 제품 동작으로 해석하지 않기

**두 참조 버전 모두 송신 시 URL 검증 코드를 포함한다.** MLflow는 `_send_webhook_request`에서 `_validate_webhook_url`을, Plane은 `webhook_send_task`에서 `validate_url(..., allowed_ips, allowed_hosts)`를 호출한다. 이 저장소의 `baseline`은 “최초 등록 시에만 주소 검사”라는 **의도적으로 단순화한 실험 조건**이다. 이 결과를 해당 제품 버전의 취약점 존재 또는 악용 가능성에 대한 증거로 사용하면 안 된다. 제품의 검증 구현 전체, 배포 설정, 프록시·네트워크·인증 확장은 이 실험의 검증 대상이 아니다.

`reject`와 `strict`, 본문 반환·저장·조회 정책은 비교 실험을 위해 이 프로젝트가 정의한 정책이다. 301/302/303의 POST→GET, 307/308의 메서드·본문 유지, 상대 Location 처리도 모의 송신기가 명시적으로 구현한다. 실제 제품의 모든 requests/urllib3 세부 동작을 보증하지 않는다.

## 의도한 단순화

- 인증: 실험 파일의 고정 Bearer 토큰. SQLite에는 SHA-256 해시로 저장한다. 로그인, OAuth, 비밀번호 관리, 토큰 발급·만료·HMAC 서명은 없다. 예제 토큰은 공개 가상 계정용이다.
- 권한: 일반 고객은 워크스페이스를 만들면 그 워크스페이스 admin이 된다. bootstrap에 admin/member를 정의한다. operator는 모든 워크스페이스를 관리한다. 실제 MLflow·Plane 권한 모델 전체와 동일하지 않다.
- 데이터: 모델 등록·이슈 변경은 고정된 가상 payload다. 학습·모델 레지스트리·이슈 관리 제품은 설치하지 않는다. 내부 보고서도 가상 운영 수치·계약 요약과 실행별 `REPORT-<run_id>`뿐이다.
- 저장: 제품 DB·MongoDB 대신 SQLite. schema migration·보존 기간·대규모 검색 기능은 없다.
- 비동기: Celery·Redis 대신 SQLite durable queue와 단일 worker 스레드. 자동 재시도는 없다. 기동 시 `sending`은 `interrupted`로 표시하고 자동 재전송하지 않는다. 미처리 `queued`는 다시 처리한다. exactly-once 전달은 제공하지 않는다.
- 전송: 쿠키, 고객 인증 헤더, 토큰, 프록시 환경변수는 전달하지 않는다. `X-Run-ID`, JSON 이벤트, 고정 User-Agent만 보낸다. `full`도 최대 응답 크기 제한을 받는다.
