# 예측랩 웹훅 실험 안내

이 서버는 학부 실험용 모의 구현이다. 제품 참조 범위는 [REFERENCES.md](REFERENCES.md), 실제 수행 결과는 [VERIFICATION.md](VERIFICATION.md)에 있다. 기존 수신·통신 실습은 [README](../README.md)를 따른다.

## 구성과 실행

기본 [compose.yaml](../compose.yaml)은 webhook과 internal 두 서비스만 시작한다. `webhook`은 edge와 protected, `internal`은 protected에만 연결한다. internal 포트는 호스트에 게시하지 않는다. [compose.block.yaml](../compose.block.yaml)은 webhook을 edge에만 연결한다. 기존 `/health`, `/webhook`, 내부 `/health` 응답은 유지했다.

추가 DB·큐·Python 패키지가 필요 없다. Python 3.12 이상에서 실행한다. [webhook/Dockerfile](../webhook/Dockerfile), [internal/Dockerfile](../internal/Dockerfile)은 `/data`를 UID/GID 10001에 할당한다. Compose는 루트 파일시스템을 읽기 전용으로 두고 각 DB 디렉터리만 named volume으로 쓴다. SQLite WAL 파일도 같은 디렉터리에 생성한다.

```powershell
Copy-Item .env.example .env  # .env가 없는 경우에만 실행
docker compose build
docker compose up -d --no-build --wait
docker compose exec -T webhook python probe.py
```

Kali 수신 서버 없이도 반복할 수 있도록 [fixtures/server.py](../fixtures/server.py)와 [compose.lab.yaml](../compose.lab.yaml)을 제공한다. 이 옵션의 receiver는 Kali 역할을 대신하는 **테스트 fixture**다. 테스트 CA·개인키는 공개 실험용이며 [인증서 설명](../tests/certs/README.md)에 재생성 절차가 있다.

```powershell
docker compose -f compose.yaml -f compose.lab.yaml --profile test build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prepare-lab.ps1
docker compose -f compose.yaml -f compose.lab.yaml up -d --no-build --wait
```

준비 스크립트는 internal·receiver를 먼저 시작하고 receiver의 실제 IP를 조회해 [config/](../config/)에 무시되는 `url-exceptions.lab.local.json`을 생성한다. 예외는 `https://receiver:8443`와 **현재 receiver IP 하나의 /32**로 한정한다. webhook은 그 파일을 읽는다. 컨테이너를 삭제·재생성해서 IP가 바뀌면 준비 스크립트를 다시 실행한다. checked-in 정책 파일은 빈 목록이며 사설망 전체를 허용하지 않는다.

## 계정과 권한

[accounts.json](../config/accounts.json)은 실험 계정이다. 기동 시 읽고 토큰 해시를 동기화한다. 멤버십은 최초 bootstrap 시 삽입된다. 운영용 계정 관리 기능이 없으므로 별도의 실험 DB와 구성 파일을 사용한다.

| 계정 / 예제 Bearer 토큰 | 역할 |
|---|---|
| alice / `lab-alice-token` | alpha 관리자 |
| bob / `lab-bob-token` | beta 관리자 |
| viewer / `lab-viewer-token` | alpha 일반 멤버: alpha 웹훅 조회만 가능 |
| customer / `lab-customer-token` | 초기 소속 없음. 워크스페이스 생성 후 해당 공간 관리자 |
| operator / `lab-operator-token` | 내부 운영자: 모든 워크스페이스 설정·로그 접근 |

다른 워크스페이스의 웹훅 설정·송신·이력 요청은 403이다. 객체 ID만 아는 것으로 접근할 수 없으며 모든 조회에 workspace 조건을 넣는다. 일반 멤버는 웹훅 수정·테스트·이벤트 발생·이력 조회 권한이 없다. 인증 없는 관리 요청은 401이다. 통신 확인용 `/health`·`/webhook`만 인증 없이 유지한다.

## API

관리 API에는 `Authorization: Bearer <token>`과 JSON `Content-Type`을 사용한다. `ws`는 workspace ID다. 등록·수정·테스트·이벤트 요청은 선택적으로 UUID `run_id`를 받는다. 생략하면 생성한다. 같은 실험의 등록·송신에는 같은 UUID를 넣고, 새 실험에는 새 UUID를 사용한다. run은 한 워크스페이스에만 속한다.

| 메서드·경로 | 용도 |
|---|---|
| `GET /api/me` | 계정·멤버십 |
| `GET /api/workspaces` | 권한 있는 워크스페이스 목록 |
| `POST /api/workspaces` | `{"name":"가상 고객사"}`로 생성, 생성자가 admin |
| `GET /api/workspaces/{ws}/hooks` | 웹훅 목록, 최대 100개 |
| `POST /api/workspaces/{ws}/hooks` | `name`, `url`, `events`, 선택 `run_id`로 등록 |
| `GET /api/workspaces/{ws}/hooks/{id}` | 웹훅 조회 |
| `PUT /api/workspaces/{ws}/hooks/{id}` | name·url·events 전체 갱신, URL 재검증 |
| `DELETE /api/workspaces/{ws}/hooks/{id}` | 삭제, 기존 이력은 보존 |
| `POST /api/workspaces/{ws}/hooks/{id}/test` | MLflow 참고 모드. `event` 기본 `model.registered`, 동기 전송 결과 반환 |
| `POST /api/workspaces/{ws}/events` | Plane 참고 모드. `event` 기본 `issue.updated`, 구독 웹훅마다 작업 생성, 202와 delivery IDs 반환 |
| `GET /api/workspaces/{ws}/deliveries?run_id={uuid}&offset=0` | 별도 이력 조회. 100개씩 offset 사용 |
| `GET /api/workspaces/{ws}/runs/{uuid}` | 실행별 이력과 감사 로그(최대 1000개) |

구독 이벤트는 `model.registered`, `issue.updated`다. 테스트도 구독한 이벤트만 보낸다. 송신 결과 API 자체의 200은 테스트 수행 결과이며, 원격 성공은 `result.outcome=succeeded`와 `result.status`로 판단한다. 원격 2xx만 성공으로 분류한다. Plane의 `queued`·`sending`은 완료 상태가 아니다. 성공·실패·차단은 `succeeded`·`failed`·`blocked`, 재시작 때 중단된 작업은 `interrupted`다.

Kali Bash 호출 예시(실제 Windows 주소·수신 URL로 치환):

```bash
export BASE='http://<Windows-VMnet1-IP>:18080'
export TOKEN='lab-alice-token'
export RUN_ID="$(cat /proc/sys/kernel/random/uuid)"
export RECEIVER_URL='https://<Kali-CA-SAN-host>:8443/webhook'
curl --noproxy '*' -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data "{\"name\":\"모델 알림\",\"url\":\"$RECEIVER_URL\",\"events\":[\"model.registered\",\"issue.updated\"],\"run_id\":\"$RUN_ID\"}" \
  "$BASE/api/workspaces/alpha/hooks"
# 등록 응답의 id를 사용한다.
export HOOK_ID='<등록 응답 id>'
curl --noproxy '*' -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data "{\"run_id\":\"$RUN_ID\"}" "$BASE/api/workspaces/alpha/hooks/$HOOK_ID/test"
curl --noproxy '*' -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data "{\"event\":\"issue.updated\",\"run_id\":\"$RUN_ID\"}" "$BASE/api/workspaces/alpha/events"
curl --noproxy '*' -sS -H "Authorization: Bearer $TOKEN" "$BASE/api/workspaces/alpha/runs/$RUN_ID"
```

각 모드를 별도로 실험하려면 서로 다른 run_id를 사용한다. `ENABLED_MODES=mlflow` 또는 `plane`으로 한 모드만 켤 수 있다. 기본은 둘 다 지원하되 동기/비동기 API와 작업은 독립적이다.

## 검증·본문 정책

| 환경변수 | 의미 / 기본값 |
|---|---|
| `POLICY` | `baseline`, `reject`, `strict` 중 선택. 기본 strict |
| `ENABLED_MODES` | `mlflow,plane` |
| `RETURN_BODY` | MLflow 즉시 응답의 본문 정책. 기본 mask |
| `STORE_BODY` | 두 모드 이력에 저장하는 본문 정책. 기본 mask |
| `READ_BODY` | 이력 조회 시 추가로 적용할 본문 정책. 기본 mask |
| `BODY_LIMIT` | limit의 UTF-8 바이트 수. 기본 1024 |
| `MAX_RESPONSE_BYTES` | full을 포함해 읽는 최대 응답 바이트. 기본 65536, 최대 1 MiB |
| `DELIVERY_TIMEOUT`, `MAX_REDIRECTS` | 소켓 작업 timeout 기본 3초, redirect 기본 5회 |
| `ALLOW_HTTP` | 기본 true. 격리 실험의 내부 HTTP API용. false이면 HTTPS만 허용 |
| `CA_FILE` | 추가 신뢰 CA PEM 경로. 빈 값은 시스템 CA. 검증 비활성화 옵션은 없음 |
| `URL_EXCEPTIONS_FILE` | 사설 정상 수신자 origin·CIDR 예외 JSON. 기본 파일은 빈 목록 |
| `BOOTSTRAP_FILE`, `DB_PATH` | 계정 파일·쓰기 가능한 SQLite 경로 |
| `INTERNAL_HEALTH_URL` | 기존 probe가 확인할 주소 |
| `REPORT_API_URL`, `RECEIVER_URL` | fixture의 고정 redirect 목적지, 검증 실행기의 정상 수신 origin |
| `BIND_HOST`, `PORT` | VM Python 직접 실행 시 수신 주소·포트. 기본 0.0.0.0:8000 |

- **baseline**: 등록·갱신 시에만 DNS의 모든 주소를 검사한다. 전송 시와 redirect 이후에는 새로 해석한 주소를 검사하지 않는다. 연결 IP·redirect·상태는 관측한다. 등록 후 DNS 변경과 redirect 우회의 비교용 조건이다.
- **reject**: 등록 및 송신 시 최초 주소를 검사하고 검사한 IP에 연결한다. Location을 가진 301/302/303/307/308을 따라가지 않는다. 이 정책은 최초 연결의 pinning도 포함하므로 baseline 대비 차이가 redirect 거부 하나뿐은 아니다.
- **strict**: 모든 redirect 단계에서 DNS의 모든 A/AAAA 결과를 검사하고, 허용 IP에 직접 연결한다. 연결 시 호스트 이름을 다시 해석하지 않으며 peer IP도 확인한다. HTTPS는 원래 호스트를 SNI·인증서 이름 검증에 사용한다. HTTPS→HTTP downgrade도 거부한다.

IP 판단은 비공개·loopback·link-local·예약·multicast·unspecified 주소와 비공개 IPv4-mapped IPv6를 거부한다. 예외는 **scheme + 정규화한 hostname + port**와 CIDR 모두 일치해야 한다. 허용·차단 주소가 섞인 DNS 응답은 전체 거부한다. 쿠키·API Bearer 토큰·고객 인증 헤더·환경 프록시는 송신에 사용하지 않는다. HTTPS 검증은 모든 정책에서 유지한다. 허용 IP가 여러 개면 같은 DNS 검사 결과 내에서 연결을 재시도한다.

본문 정책은 `full`(최대 응답 크기 내 원문), `limit`(앞 BODY_LIMIT 바이트), `mask`(`[REDACTED]`), `omit`(null)이다. 저장 시 버린 내용은 조회 정책을 full로 바꿔도 복원되지 않는다. RETURN_BODY만 막으면 이력에는 남을 수 있고 READ_BODY만 막으면 DB에는 남을 수 있다. **limit는 앞부분의 정보 유출을 막지 않는다.** 본문 제한을 내부 API 접근 차단으로 판단하면 안 된다.

Compose 설정을 변경하면 `up -d`로 컨테이너를 재생성한다. worker 작업은 생성 시 검증·저장 정책을 snapshot으로 보관하고 조회 정책은 조회 시점 설정을 사용한다. CA 파일은 같은 경로에 유지해야 한다.

### Kali의 정상 사설 HTTPS 수신자

수신 origin에 한해 실제 Kali IP `/32`(IPv6이면 `/128`)를 예외로 둔다. 내부 보고서 IP나 전체 사설망을 예외로 넣지 않는다. 예:

```json
[{"origin":"https://kali-receiver.lab:8443","cidrs":["192.168.241.50/32"]}]
```

이는 예시 주소이며 실제 주소는 설정 파일에서 지정한다. Docker 안에서 hostname이 해석돼야 하며 인증서 SAN도 그 hostname과 일치해야 한다. IP URL을 쓰면 인증서에 해당 IP SAN이 필요하다. CA 공개 인증서를 [config/](../config/)에 두고 `.env`에서 `CA_FILE=/config/<CA파일>`을 지정한다. 기본 구성의 `/config/url-exceptions.json`에 origin 예외를 저장한다. lab override는 자체 생성 예외 파일과 테스트 CA를 사용하므로 Kali 실험에서는 기본 Compose를 사용한다.

Kali에서 Python 3.12와 별도로 생성한 인증서로 fixture를 띄운다. 다음 주소·경로는 환경에 맞춰 입력한다.

```bash
export REPORT_API_URL='http://<내부-VM-IP>:8000/reports/alpha'
python3 fixtures/server.py --port 8443 --target "$REPORT_API_URL" \
  --cert /path/to/receiver.pem --key /path/to/receiver-key.pem
```

`/webhook`은 정상 수신, `/redirect`는 설정한 내부 API로 303 이동한다. `?status=307` 등으로 상태를 바꿀 수 있다. `/relative`, `/loop`, `/large`, `/fail`도 제공한다. X-Run-ID를 내부 API까지 유지하고, redirect URL에도 run_id를 넣는다. HTTPS 수신 뒤 HTTP 내부 API로 이동하는 strict 실험은 downgrade에서 먼저 차단된다. HTTP 수신을 사용한 단위·통합 테스트에서는 사설 목적지 검사에서 차단되는 경우도 검증한다.

## 반복 검증과 관측

```powershell
# 독립 HTTP/TLS/SQLite 통합 테스트
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier
# 3개 정책 × 2개 네트워크 + 반환·저장·조회 마스킹 비교
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-matrix.ps1
```

matrix는 내부 컨테이너의 **숫자 IP**를 redirect 주소로 설정하므로 DNS가 없어지는 것만으로 차단됐다고 판정하지 않는다. 검증 컨테이너는 관측용으로 두 망에 연결하지만 webhook의 망은 조건별로 변경한다. 각 조합마다 두 모드의 정상 전송·redirect 전송, 내부 URL 직접 등록 거부, 타 워크스페이스 설정·이력 거부를 확인한다. 종료 시 원래 환경변수와 기본 두 서비스 연결 설정을 복구하며 DB 볼륨은 유지한다. fixture는 남으므로 실험 종료 시 아래처럼 함께 정지한다.

```powershell
docker compose -f compose.yaml -f compose.lab.yaml down
```

결과 JSON은 [artifacts/](../artifacts/)에 생성된다(버전 관리 제외). 결과에는 run_id, 최초 URL, hop별 DNS/선택 IP/연결 IP, redirect, 판정, HTTP 상태, 지연, 오류, 서버 감사 로그, 수신·내부 관측 결과를 포함한다. DB·콘솔 감사 로그에는 원문 응답을 별도로 출력하지 않는다.

`internal_api_reached`는 **내부 서버의 `/observations?run_id=...` 기록**으로 판정한다. `report_content_exposed`는 MLflow 즉시 응답 또는 Plane 권한 있는 로그 조회 결과에 실행별 보고서 marker가 포함됐는지로 판정한다. marker 검사는 지정된 가상 보고서의 관측 방법이며 임의 민감 정보 탐지기가 아니다. 전송 경로만 보고 내부 도달을 추측하지 않는다. webhook 감사 로그의 내부 도달 값은 자체 확인이 불가능하므로 unknown으로 남긴다.

```powershell
docker compose logs --tail 100 webhook internal
docker compose exec -T internal python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8000/observations?run_id=<UUID>').read().decode())"
docker compose exec -T webhook python -c "import os; print(os.getuid(), os.stat('/data').st_uid, os.access('/data', os.W_OK))"
```

보호망 분리 시에도 Kali→webhook `/health`·`/webhook`과 정상 수신 전송은 살아 있어야 한다. 기존 `probe.py --expect blocked`도 별도로 실행할 수 있다.

## 별도 Linux VM으로 이전

이미지는 각각 `docker save`/`docker load`로 이전하거나 각 VM에서 같은 소스로 빌드한다. MLflow·Plane·DB·큐를 추가 설치하지 않는다. 예시의 디렉터리는 운영자가 고른 실제 경로로 치환한다.

1. webhook VM에는 계정·URL 예외·CA 파일과 환경 파일을 복사한다. internal VM에는 내부 이미지와 `/data`용 볼륨을 준비한다. 기존 DB를 옮기려면 서비스를 정지하고 DB와 WAL/SHM을 함께 백업하거나 SQLite backup을 사용한다.
2. webhook 환경 파일에 `DB_PATH=/data/webhook.sqlite3`, `BOOTSTRAP_FILE=/config/accounts.json`, `URL_EXCEPTIONS_FILE=/config/url-exceptions.json`, CA·정책·본문 옵션을 넣는다. `/data`는 UID 10001이 쓸 수 있어야 한다. named volume은 이미지의 소유권을 이어받고, bind mount는 호스트에서 소유권을 준비한다.
3. 두 VM의 **필요한 사설 인터페이스만** 게시하고 Kali와 webhook의 수신·송신 경로를 확인한다. container 간 DNS 이름 `internal` 대신 내부 VM의 환경별 URL을 사용한다.

```bash
# webhook VM: 변수는 실제 로컬 설정값으로 지정
docker run -d --name yechuk-webhook --user 10001:10001 --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --env-file "$WEBHOOK_ENV_FILE" -p "$WEBHOOK_BIND_IP:$WEBHOOK_HOST_PORT:8000" \
  -v webhook-data:/data -v "$CONFIG_DIR:/config:ro" webhook-connectivity-lab/webhook:local
# internal VM
docker run -d --name yechuk-internal --user 10001:10001 --read-only \
  --cap-drop ALL --security-opt no-new-privileges -e INTERNAL_DB_PATH=/data/internal.sqlite3 \
  -p "$INTERNAL_BIND_IP:$INTERNAL_HOST_PORT:8000" -v internal-data:/data webhook-connectivity-lab/internal:local
```

컨테이너 없이도 저장소 루트에서 환경변수를 지정하고 `python3 webhook/server.py`, `python3 internal/server.py`를 각각 실행할 수 있다. IP·포트·URL·CA를 소스 수정 없이 바꾼다.

같은 [check_lab.py](../scripts/check_lab.py)를 Kali 또는 관측용 VM에서 실행한다. 이 실행기는 receiver와 내부 관측 API에 접근할 수 있어야 한다. webhook만 내부 API에 접근하지 못하도록 방화벽을 제한하고 관측 실행기는 별도 허용한다. Docker 컨테이너의 Linux 호스트 방화벽은 FORWARD/DOCKER-USER 구간, 직접 실행한 서버는 OUTPUT 구간의 송신 제어를 적용해야 한다. 규칙·주소와 판정 결과를 실험 기록에 남긴다.

```bash
python3 scripts/check_lab.py --base "$WEBHOOK_BASE_URL" --receiver "$RECEIVER_ORIGIN" \
  --internal "$REPORT_API_URL" --observer "$INTERNAL_OBSERVER_ORIGIN" --ca "$CA_PATH" \
  --policy baseline --network open --return-body full --store-body full --read-body full \
  --output artifacts/vm-baseline-open.json
```

방화벽 제한을 적용한 뒤 `--network blocked`로 같은 테스트를 실행한다. reject·strict 및 본문 제한 조건은 서버 환경을 바꿔 재기동하고 실행기에도 같은 값을 전달한다. API 접근이 안 되거나 관측 서버가 죽으면 차단 성공으로 처리하지 않고 테스트가 실패한다. **실제 Kali·별도 VM 실행은 각 환경에서 이 절차로 추가 확인해야 한다.**

## 알려진 제한

- stdlib HTTP 서버와 고정 실험 토큰은 작은 격리 실험용이다. 공개 서비스 운영, 다중 프로세스·다중 replica·대량 작업 처리용 구현이 아니다.
- SQLite는 단일 프로세스를 전제로 한다. 프로세스가 여러 개면 기동 시 sending 복구 판정이 충돌할 수 있다. 자동 재시도·작업 보존 기간·계정 CRUD·DB migration은 없다.
- DNS resolver 지연은 소켓 timeout의 적용 대상이 아니며, timeout은 전체 실행의 절대 시간 제한이 아니다. 느린 응답의 전체 deadline·호출량 제한은 미구현이다.
- TLS/실제 연결 검증은 애플리케이션이 관측한 peer까지의 보장이다. 관리자 예외로 허용한 주소·NAT·라우팅·외부 프록시 뒤 목적지를 의미적으로 보증하지 않는다. VM 방화벽 결과는 별도 검증이 필요하다.
- `/observations`는 내부/fixture의 실험 관측 API로 인증이 없다. 외부에 게시하지 않으며 실제 제품 기능이 아니다. 보고서는 가상 데이터다.
- 감사 로그는 설정 URL·Location을 기록하므로 URL에는 실제 비밀번호나 토큰을 넣지 않는다. 본문 마스킹은 URL·헤더·임의 문자열의 DLP 기능이 아니다.
- 기본 API 수신은 기존 HTTP를 유지한다. HTTPS가 필요한 VM 환경에서는 별도 TLS 종단 구성을 사용한다. 송신 HTTPS 인증서·호스트 이름 검증은 항상 켜져 있다.
