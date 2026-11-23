# Internal 서버·Kali 공격/수신 서버 연동 지침

이 문서는 새 Webhook 기준 브랜치 `develop/final-cve-demo-20261123`와 함께 읽습니다. 목표는 실제 공격 도구를 만드는 것이 아니라, 수업용 격리 환경에서 `run_id`로 증거를 대조하며 CVE 흐름과 방어 효과를 시연하는 것입니다.

## 1. 세 노드의 역할

| 담당 | 해야 할 일 | 하지 말아야 할 일 |
|---|---|---|
| Webhook 서버 | URL·IP·Redirect 정책, MLflow식 동기 반환, Plane식 비동기 로그, 권한·본문 방어, 공통 `run_id` 제공 | 실제 MLflow/Plane 설치나 실서비스 대상 전송 |
| Internal 서버 | 합성 보고서 API, 접근 관측, Webhook에서 온 연결의 최소 메타데이터 기록, 방화벽 정책 | 실제 비밀·계약 데이터 제공, Kali의 직접 접근 허용 |
| Kali 서버 | TLS가 유효한 정상 수신과 실험용 302 Redirect, 수신 관측, 선택적 실험망 PCAP | 공용 인터넷·클라우드 메타데이터·타인 시스템 대상으로 스캔/공격 |

## 2. 모두 지켜야 할 공통 계약

- 모든 시연에 새 UUID `run_id`를 하나 만들고 `X-Run-ID` 또는 JSON `run_id`로 끝까지 전달합니다.
- 보고서 본문에는 반드시 `fictional: true`와 `REPORT-<run_id>`를 넣습니다. 이것만을 노출 여부 판정 표식으로 사용합니다.
- 어떤 노드도 비밀, 사용자 데이터, 실제 API 키를 로그/본문에 넣지 않습니다.
- Webhook이 외부로 전송할 HTTPS 수신 주소는 테스트 CA가 신뢰하는 인증서와 정확히 일치하는 DNS SAN을 가져야 합니다. 검증을 끄거나 insecure 우회를 사용하지 않습니다.
- `baseline`은 의도적으로 비교를 위해 약하게 만든 정책입니다. 실제 운영 설정처럼 쓰지 않습니다.

## 3. Internal 서버 담당자 지침

### 구현할 API

| 경로 | 요구 동작 |
|---|---|
| `GET /health` | `200` 및 간단한 상태 JSON |
| `GET` 또는 `POST /reports/alpha` | 가상 보고서 JSON 반환. `REPORT-<run_id>`, `fictional: true`, 가상 `contract_summary`·`model_operations` 포함 |
| `GET /observations?run_id=<UUID>` | 해당 요청이 Internal까지 도달했는지, method/path/created/source_ip를 반환 |

현재 저장소의 `internal/server.py`가 이 계약의 기준 구현입니다. 별도 VM으로 옮길 때에도 경로와 `run_id` 의미를 바꾸지 마세요.

### 네트워크·로그 정책

1. Internal API는 인터넷·Kali에서 직접 접근할 수 없게 합니다. 허용 대상은 Webhook 서버의 명시된 egress IP/네트워크와 관측용 관리 경로뿐입니다.
2. 차단 비교에서는 Webhook→Internal TCP 연결도 막아야 합니다. Docker에서는 `compose.block.yaml`이 이 조건을 모의합니다.
3. 관측 로그에는 `run_id`, UTC 시간, source IP, method, path, 결과 코드만 남기고 본문·인증정보는 남기지 않습니다.
4. 방화벽 변경 전후에 같은 `run_id`가 아니라 **새** `run_id`로 실험합니다. 이전 관측이 결과를 오염시키지 않게 합니다.

### Webhook과의 확인 경로

```text
Webhook 전송 → Internal /reports/alpha
Internal 기록 → GET /observations?run_id=<UUID>
Webhook 결과 → GET /api/workspaces/alpha/runs/<UUID>
```

정상 결과와 방어 결과는 모두 Internal `/observations`의 `internal_api_reached`를 기준으로 판단합니다. Webhook 감사 로그만으로 내부 도달을 추정하지 않습니다.

## 4. Kali 수신/공격 시뮬레이터 담당자 지침

### 구현할 HTTPS 수신 서버

| 경로 | 요구 동작 |
|---|---|
| `GET /health` | Kali receiver 상태 확인 |
| `POST /webhook` | 정상 webhook 수신 및 `X-Run-ID` 기록 |
| `POST` 또는 `GET /redirect?status=302` | 실험에서만 `Location: <Internal 보고서 URL>?run_id=<UUID>`를 반환. `run_id`를 보존 |
| `GET /observations?run_id=<UUID>` | receiver/redirect 수신 기록 조회 |

Webhook 서버가 등록하는 첫 URL은 반드시 Kali의 HTTPS origin이어야 합니다. 예: `https://kali-receiver.lab:8443/redirect?status=302`. 이 origin의 정확한 hostname, port, 현재 IP/CIDR, CA PEM 경로를 Webhook 담당자에게 전달해야 합니다.

### TLS와 URL 예외

1. 인증서 SAN에 등록 URL hostname을 넣습니다. IP URL을 쓴다면 해당 IP도 SAN에 있어야 합니다.
2. Webhook의 `URL_EXCEPTIONS_FILE`에는 전체 사설망이 아니라 정확한 `scheme + hostname + port + Kali IP/32`만 예외로 추가합니다.
3. `CVE_DEMO_PLANE_ORIGIN`은 위 HTTPS Redirect URL과 정확히 같아야 합니다. 다른 URL이나 Internal URL을 넣지 않습니다.
4. Redirect 타깃은 수업용 Internal 보고서 API뿐이어야 합니다. 공용 주소, cloud metadata 주소, 실제 사내 주소는 금지입니다.

### 선택적 패킷 증거

Webhook transport가 남기는 `connection_trace`는 패킷 캡처가 아닙니다. 실제 패킷 분석을 제출해야 한다면 Kali의 **실험 전용 인터페이스**에서 아래처럼 Webhook·Internal의 실험용 IP만 대상으로 캡처하세요.

```bash
RUN_ID='<새 UUID>'
IFACE='<실험용 인터페이스>'
WEBHOOK_IP='<Webhook VM IP>'
INTERNAL_IP='<Internal VM IP>'
sudo tcpdump -ni "$IFACE" -w "lab-${RUN_ID}.pcap" \
  "tcp and (host $WEBHOOK_IP or host $INTERNAL_IP)"
```

시연 뒤에는 `pcap` 파일명, 캡처 시작·종료 시각, `run_id`, 방화벽 상태만 제출합니다. 패킷 본문이나 인증 헤더는 공유하지 않습니다. 담당자는 허가된 격리망 외의 트래픽을 수집하지 않습니다.

## 5. Webhook API를 호출할 때 알아야 할 것

정상 관리 API에는 `Authorization: Bearer <token>`이 필요합니다. 기본 실험 계정은 `alice`(alpha admin), `viewer`(alpha member), `bob`(beta admin)입니다.

| 목적 | 호출 |
|---|---|
| 기존 호환 확인 | `GET /health`, `POST /webhook` |
| 정상 Webhook 등록 | `POST /api/workspaces/alpha/hooks` |
| MLflow식 동기 시험 | `POST /api/workspaces/alpha/hooks/{hook_id}/test` |
| Plane식 비동기 이벤트 | `POST /api/workspaces/alpha/events` |
| 일반 전달 이력 | `GET /api/workspaces/alpha/deliveries?run_id=<UUID>` |
| 공통 감사 기록 | `GET /api/workspaces/alpha/runs/<UUID>` |
| MLflow CVE 모의 | `POST /api/2.0/mlflow/webhooks`, `POST /api/2.0/mlflow/webhooks/{id}/test` |
| Plane CVE 모의 등록 | `POST /api/cve/plane/workspaces/alpha/webhooks` |
| Plane CVE 로그 조회 | `GET /api/cve/plane/workspaces/alpha/webhook-logs/{hook_id}?run_id=<UUID>` |

CVE 경로는 `CVE_DEMO_MODE`와 `CVE_DEMO_ACK=local-lab-only`가 지정된 격리 환경에서만 사용합니다. 일반 API의 URL 검증 규칙은 CVE 모드가 켜져도 약화되지 않습니다.

## 6. 최종 시연 순서

1. Docker 또는 각 VM에서 `/health`와 정상 `/webhook` 수신을 먼저 확인합니다.
2. 정상 Webhook 하나를 등록하고 MLflow식 테스트와 Plane식 이벤트 모두가 Kali `/webhook`에 도달하는지 확인합니다.
3. `baseline-vulnerable`: Kali `/redirect` → Internal 보고서 API. Internal observation, MLflow 응답 본문, Plane admin 로그를 같은 `run_id`로 대조합니다.
4. `reject-open`, `strict-open`: Internal observation이 없고 Redirect/정책 판정이 기록됐는지 확인합니다.
5. `baseline-network-blocked`: URL 정책은 baseline으로 두되 Webhook→Internal 방화벽만 차단합니다. 정상 Kali 수신은 유지되어야 합니다. 필요한 경우 이 단계에서 pcap과 application trace를 함께 보관합니다.
6. `fixed-url-auth-mask`: MLflow 수정 프로필에서 무인증 CVE 호출이 401인지, alpha member/다른 workspace admin 호출이 403인지, Alice 관리자 호출도 Internal에 닿지 않고 본문 표식이 없는지 확인합니다. Plane 수정 프로필은 Redirect를 거부해야 합니다.
7. `plane-log-filter`: Internal 도달은 유지되더라도 Plane 저장 로그에 `REPORT-<run_id>`가 없음을 확인합니다. `viewer`와 `bob`의 로그 조회는 403이어야 합니다.
8. 각 단계 종료 뒤 Webhook `/runs`, Kali observation, Internal observation, 방화벽/pcap 요약을 같은 `run_id`로 묶습니다.

## 7. 인계 체크리스트

- [ ] 각 노드의 IP·port·DNS name·CA 경로가 환경 파일에만 있고 소스에 고정되지 않았다.
- [ ] Kali 인증서 SAN과 Webhook URL이 일치한다.
- [ ] Internal이 Kali에서 직접 접근되지 않는다.
- [ ] `run_id`가 Webhook, Kali, Internal 로그에 모두 남는다.
- [ ] 정상 수신은 방어 정책별로 여전히 성공한다.
- [ ] Plane 로그는 alpha admin 또는 내부 operator만 조회할 수 있고 viewer/bob은 403이다.
- [ ] 저장 전 로그 필터가 켜진 경우 표식이 데이터베이스에 남지 않는다.
- [ ] pcap을 썼다면 대상이 실험망·실험 IP만임을 확인했다.
