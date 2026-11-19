# 검증 기록

검증일: 2026-11-20 (Asia/Seoul). Windows Docker Desktop의 Linux 컨테이너, 공식 `python:3.12-slim` 이미지(Python 3.12.16)에서 수행했다. 외부 MLflow·Plane 서버는 설치하지 않았다. 제품 자체의 취약점 유무를 평가한 결과가 아니다.

재현 절차는 [LAB.md](LAB.md), 실행별 run_id·상태·실제 연결 IP·redirect·오류와 원본 JSON의 SHA-256은 [docker-matrix.json](verification/docker-matrix.json)에 기록한다. 전체 원본에는 수신 서버·내부 API·webhook 감사 로그를 포함하며 `artifacts/*.json`에 보관한다. 로컬 생성 원본은 버전 관리에서 제외한다.

## HTTP·TLS·SQLite 통합 테스트

```powershell
docker compose -f compose.yaml -f compose.lab.yaml --profile test run --rm --no-deps verifier
```

[test_lab.py](../tests/test_lab.py)의 12개 테스트 메서드와 내부 subtest 조합을 실행한다. 실제 loopback HTTP/HTTPS 서버와 SQLite를 사용하고, DNS 변경 테스트에만 resolver를 주입한다.

검증 범위:

- 기존 `/health` 200 JSON·`/webhook` 202 JSON 및 입력 검증 유지
- 고정 토큰 인증, 소유 워크스페이스 생성·admin 부여, member/operator 구분, 타 공간 설정·삭제·로그 거부, run 소유권, 웹훅 CRUD
- 정책 3종에서 두 모드의 정상 전달·redirect 경로, 직접 내부 URL 등록 거부
- 반환·저장·조회 단계별 mask/omit/8바이트 limit: 본문 노출을 제한해도 내부 요청은 도달
- 신뢰 CA HTTPS 정상, 비신뢰 인증서·호스트 이름 불일치 거부, HTTPS downgrade 거부
- 등록 후 DNS가 내부 주소로 바뀌는 baseline 도달 실험, strict 거부, 검사→연결 사이 추가 DNS 조회 없음
- 혼합 public/private DNS, IPv4 정수·축약형, mapped IPv6, loopback·link-local·multicast, 포트 0·잘못된 scheme·userinfo 등 거부
- 301/302/303 GET 전환, 307/308 POST 유지, 상대 Location, redirect loop, 최대 응답 크기, 503 실패 기록
- SQLite 재개 시 sending→interrupted, queued 유지, 모드별 API 독립 활성화

## Docker 네트워크·정책 비교

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test-matrix.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/export-results.ps1
```

9개 조합마다 MLflow/Plane 각각 정상·redirect 전송 4건, 내부 URL 직접 등록 거부, 다른 워크스페이스 계정의 설정·로그 접근 거부를 확인한다. **총 전송 36건, 정상 전송 18/18건 성공**. 표의 노출은 MLflow 즉시 반환 / Plane 별도 이력 조회 순서다.

| 정책 | 보호망 | RETURN / STORE / READ | 내부 redirect 도달(두 모드) | 보고서 노출 MLflow / Plane | 정상 전달 |
|---|---|---|---|---|---|
| baseline | 연결 | full / full / full | 예 / 예 | 예 / 예 | 2/2 |
| baseline | 분리 | full / full / full | 아니오 / 아니오 | 아니오 / 아니오 | 2/2 |
| reject | 연결 | full / full / full | 아니오 / 아니오 | 아니오 / 아니오 | 2/2 |
| reject | 분리 | full / full / full | 아니오 / 아니오 | 아니오 / 아니오 | 2/2 |
| strict | 연결 | full / full / full | 아니오 / 아니오 | 아니오 / 아니오 | 2/2 |
| strict | 분리 | full / full / full | 아니오 / 아니오 | 아니오 / 아니오 | 2/2 |
| baseline | 연결 | mask / full / full | 예 / 예 | 아니오 / 예 | 2/2 |
| baseline | 연결 | full / mask / full | 예 / 예 | 예 / 아니오 | 2/2 |
| baseline | 연결 | full / full / mask | 예 / 예 | 예 / 아니오 | 2/2 |

실험의 정상 목적지는 테스트 CA를 검증하는 `https://receiver:8443`이다. 예외는 receiver의 현재 IP 한 개 `/32`다. redirect는 내부 컨테이너의 **숫자 IP**로 향해 네트워크 분리 결과가 DNS 이름 해석 실패에 의존하지 않도록 했다. 내부 `/observations`와 receiver `/observations`는 관측용 verifier가 독립적으로 조회한다.

strict의 Docker HTTPS→HTTP redirect는 downgrade 거부로 먼저 차단된다. 실제 HTTP redirect 이후 사설 IP 판정과 연결 pinning은 별도 통합 테스트에서 검증했다. reject는 redirect 거부 외에 최초 목적지 검증·pinning도 포함한다. 따라서 이 표만으로 단일 방어 요소의 성능 효과를 추정하지 않는다.

RETURN_BODY=mask는 **즉시 반환 경로**만 막는다. STORE/READ가 full이면 MLflow 모드의 별도 이력에도 원문이 남을 수 있다. 이력까지 막으려면 STORE/READ도 제한한다. marker 포함 여부는 이 가상 보고서의 완전한 실행별 식별자 노출 관측치이며 모든 부분 정보 유출을 탐지하지 않는다. 특히 limit에서 보고서 앞부분이 남을 수 있다.

## 실행 환경 확인과 한계

- 두 기본 컨테이너의 UID=10001, `/data` 소유 UID=10001, 쓰기 가능 여부를 실제 컨테이너에서 확인한다. named volume과 read-only 루트 구성을 사용한다.
- 기존 probe의 기본 연결 PASS와 보호망 분리 시 blocked PASS, 두 상태에서 공개 health·webhook 호환성을 확인한다. matrix 뒤에는 원래 환경변수 및 기본 네트워크 구성을 복구한다.
- 성공률 100%는 작은 fixture 표본이다. 부하·장기 운영·장애 복구·실제 인터넷 수신자에 대한 보증은 아니다.
- 실제 Kali VM, Windows 방화벽 경로, 별도 Linux VM 두 대의 배포·방화벽은 이 세션에서 실행하지 않았다. 동일 black-box 스크립트와 이전 절차를 제공했으며 각 환경에서 추가 확인해야 한다.
