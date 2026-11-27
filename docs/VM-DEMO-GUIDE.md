# VM 3대 기반 SSRF 시연 가이드

Docker 없이 VMware Workstation VM 3대(webhook / internal / kali)로 같은 SSRF 체인과 4계층
방어를 재현하기 위한 설정·실행·시연 절차다. 원본 시나리오와 코드는 [LAB.md](LAB.md),
D3FEND 4계층 매핑은 [explanation.md](../explanation.md)를 따른다. 여기서는 **Docker 네트워크
격리를 VM 네트워크(고정 IP + iptables)로 어떻게 재현했는지**와 **방법 A(레이어별 독립
검증 → 최종 종합) 시연 순서**만 다룬다.

## 0. VM 구성

| VM | 역할 | 고정 IP |
|---|---|---|
| webhook (Ubuntu 20.04) | SSRF 체인의 중심 서버 (webhook/server.py) | `192.168.40.10` |
| internal (Ubuntu 20.04, webhook의 풀 클론) | 사내 API (internal/server.py) | `192.168.40.20` |
| kali (Kali Linux) | 공격자: redirect fixture(receiver) + 공격 스크립트 | `192.168.40.30` |

세 VM 모두 **VMware Host-only 네트워크 1개(VMnet1, `192.168.40.0/24`)**에만 연결한다.
인터넷이 필요한 작업(패키지 설치 등)은 그때만 VM에 NAT 어댑터를 임시로 추가했다가 끝나면
제거한다 — Host-only 하나만 유지해야 iptables 격리 테스트(2계층)가 의미 있다.

## 1. 파일 배포

이 저장소의 [`vm-transfer/`](../vm-transfer/)에 역할별 zip이 들어있다. 인터넷이 되는
아무 PC(또는 호스트 OS)에서 저장소를 받아 각 VM으로 zip만 옮기면 된다.

```bash
git clone https://github.com/WebSecuriy/docker.git
cd docker
git checkout develop/final-cve-demo-20261123   # 또는 팀에서 쓰는 브랜치
ls vm-transfer/   # webhook-files.zip  internal-files.zip  kali-files.zip
```

각 zip을 VMware Tools 드래그&드롭(또는 클립보드)으로 해당 VM에 옮긴 뒤:

```bash
# 각 VM에서
mkdir -p ~/websec-lab && cd ~/websec-lab
unzip ~/Desktop/webhook-files.zip     # webhook VM
unzip ~/Desktop/internal-files.zip    # internal VM
unzip ~/Desktop/kali-files.zip        # kali VM
chmod +x *.sh
```

파일 구성:

- **webhook-files.zip**: `webhook/{server.py,transport.py,config.py,storage.py}`, `config/{accounts.json,url-exceptions.lab.local.json}`, `run-webhook.sh`, `layer2-toggle.sh`
- **internal-files.zip**: `internal/server.py`, `run-internal.sh`
- **kali-files.zip**: `fixtures/server.py`(receiver), `gen-certs.sh`, `run-receiver.sh`, `attack-mlflow.sh`, `attack-plane.sh`

코드 자체(webhook/internal/fixtures)는 표준 라이브러리만 쓰고 플랫폼에 무관해서 Docker용
원본과 동일하다. **Docker 전용**이던 것(compose 네트워크 분리, 내장 DNS)만 VM에서는
고정 IP + `/etc/hosts` + iptables로 재구현했다.

> 이미 Python 3.8(Ubuntu 20.04 기본)과의 호환성 문제 2건(`config.py`의 `X | None` 문법,
> `internal/server.py`의 `removeprefix()`)은 위 zip 안에 **미리 수정 반영**돼 있다. 팀원이
> 더 최신 Python을 쓰더라도 문제없이 동작한다.

## 2. 네트워크 사전 준비 (각 VM 공통)

### 2.1 고정 IP

Ubuntu 20.04는 보통 netplan을 쓰지만, 이미지에 따라 netplan이 NetworkManager에게
위임(`renderer: NetworkManager`)해놓고 정작 NetworkManager 설정(`/usr/lib/NetworkManager/conf.d/`)이
모든 유선 인터페이스를 `unmanaged`로 빼놓는 경우가 있었다(이번에 webhook VM에서 실제로 겪음).
아래 **networkd 직접 사용** 방식이 가장 확실하다.

```bash
ip a   # 인터페이스 이름 확인 (보통 ens33)

sudo tee /etc/netplan/01-network-manager-all.yaml > /dev/null <<'EOF'
network:
  version: 2
  renderer: networkd
  ethernets:
    ens33:
      dhcp4: no
      addresses: [192.168.40.X/24]   # webhook=.10, internal=.20, kali는 아래 nmcli 방식 참고
EOF

sudo systemctl disable --now NetworkManager
sudo systemctl enable --now systemd-networkd
sudo netplan apply
ip a   # inet 192.168.40.X/24 확인
```

Kali는 기본적으로 NetworkManager가 정상 동작하므로 `nmcli`로 더 간단히 고정할 수 있다:

```bash
nmcli connection show                 # 연결 이름 확인 (보통 "Wired connection 1")
sudo nmcli connection modify "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.40.30/24
sudo nmcli connection up "Wired connection 1"
```

### 2.2 /etc/hosts (세 VM 모두 동일하게)

```bash
sudo tee -a /etc/hosts > /dev/null <<'EOF'
192.168.40.10 webhook
192.168.40.20 internal
192.168.40.30 receiver kali
EOF
```

### 2.3 상호 핑 확인

```bash
ping -c 2 webhook
ping -c 2 internal
ping -c 2 kali
```

세 VM 모두 서로 핑이 가야 다음 단계로 진행한다.

## 3. 인증서 (webhook ↔ receiver TLS)

Kali에서:

```bash
cd ~/websec-lab
./gen-certs.sh 192.168.40.30     # 기본값도 .30 이므로 인자 생략 가능
```

`certs/ca.pem`을 webhook VM의 `~/websec-lab/certs/ca.pem` 자리로 복사한다(드래그&드롭 또는
`nano`로 내용 붙여넣기). `server.pem`/`server-key.pem`은 Kali에만 둔다.

## 4. 서버 실행

세 VM 각각 터미널을 하나씩 열어서(포그라운드로 켜두고 그대로 둔다):

```bash
# internal VM
cd ~/websec-lab && ./run-internal.sh

# webhook VM
cd ~/websec-lab && ./run-webhook.sh

# kali VM
cd ~/websec-lab && ./run-receiver.sh
```

실수로 `Ctrl+Z`를 눌러 멈춘 경우 `fg`로 복귀한다. "Address already in use" 에러가 나면
이전 프로세스가 포트를 쥐고 있는 것이므로 `jobs -l` → `kill -9 <PID>` (또는
`sudo fuser -k 8000/tcp` / `8443/tcp`) 후 재실행한다.

## 5. 시연 방법 A — 레이어별 독립 검증 + 최종 종합

**핵심 원칙**: `POLICY=strict`(1계층)는 HTTPS→HTTP 다운그레이드 리다이렉트 자체를 거부하므로,
이 공격 시나리오로 2·3·4계층을 각각 **단독으로** 확인하려면 그동안 1계층은 `baseline`으로
낮춰둔다(= "1계층이 우회/미적용됐다고 가정하고 그 다음 계층이 막아내는가"를 보는 구도).
마지막에 모든 계층을 운영값으로 되돌려 **1계층에서 바로 차단되는 최종 모습**을 보여주는 것으로
마무리한다.

`run-webhook.sh` 안의 값을 바꾼 뒤 `Ctrl+C` → `./run-webhook.sh`로 재시작해야 적용된다.

### 5.1 1계층 — URL/리다이렉트 검사 (단독)

```bash
# run-webhook.sh: POLICY=strict (나머지 기본값)
```
칼리에서:
```bash
./attack-mlflow.sh lab-alice-token
```
기대 결과: `"error": "HTTPS downgrade denied"`, `"outcome": "blocked"` — 리다이렉트가
HTTP로 떨어지는 순간 1계층이 차단.

### 5.2 2계층 — 네트워크 분리 (단독)

```bash
# run-webhook.sh: POLICY=baseline (RETURN_BODY 등은 기본 mask 유지해도 무방)
```
webhook VM에서 차단 적용:
```bash
sudo ./layer2-toggle.sh block
sudo ./layer2-toggle.sh status   # 규칙 걸린 것 확인
```
칼리에서:
```bash
./attack-mlflow.sh lab-alice-token
```
기대 결과: `"error": "timed out"`, `"state": "failed"` — 리다이렉트(1계층)는 통과했지만
`internal`로의 아웃바운드 자체가 iptables에 막혀 타임아웃.

확인 후 원복:
```bash
sudo ./layer2-toggle.sh unblock
```

### 5.3 3계층 — 접근 제어 (단독)

```bash
# run-webhook.sh: POLICY=baseline, layer2는 unblock 상태
```
칼리에서 토큰만 바꿔가며 3회 비교:
```bash
./attack-mlflow.sh lab-alice-token      # admin  -> 끝까지 성공, 데이터 유출
./attack-mlflow.sh lab-viewer-token     # 권한없음 -> 403, hook 등록 단계에서 중단
./attack-mlflow.sh invalid-token-xxxx   # 잘못된 토큰 -> 401, hook 등록 단계에서 중단
```
동일 스크립트·동일 흐름에서 토큰만 바꿔 "어디서 끊기는지"를 비교해 보여준다.

### 5.4 4계층 — 데이터 마스킹 (단독)

```bash
# run-webhook.sh: POLICY=baseline, RETURN_BODY=mask, STORE_BODY=mask, READ_BODY=mask
```
칼리에서:
```bash
./attack-mlflow.sh lab-alice-token
```
기대 결과: 리다이렉트도 통과하고 internal에도 도달하지만(`"outcome": "succeeded"`),
`result.body` 안의 `report_marker`/`contract_summary` 등 민감 정보가 평문이 아니라
`"[REDACTED]"` 형태로 마스킹되어 나옴 — 1~3계층이 전부 뚫렸다고 가정해도 4계층이
최종 유출을 막는다는 것을 보여준다.

> 참고: Plane 쪽(`attack-plane.sh`)은 `STORE_BODY`와 `READ_BODY`가 분리된 정책이라,
> `STORE_BODY=full`이어도 `READ_BODY=mask`면 로그 조회 시 `[REDACTED]`로 보이는
> 재미있는 디테일이 있다. 발표에서 "저장 정책과 조회 정책은 별개"라는 포인트로 쓸 수 있다.

### 5.5 최종 종합 — 운영(보호) 설정 전체 적용

```bash
# run-webhook.sh: POLICY=strict, RETURN_BODY=mask, STORE_BODY=mask, READ_BODY=mask
# (layer2-toggle.sh는 unblock 상태로 — 정상 운영 중에는 내부망 접근 자체는 열려 있어야 하므로)
```
칼리에서:
```bash
./attack-mlflow.sh lab-alice-token
```
기대 결과: 1계층(HTTPS 다운그레이드 검사)에서 즉시 차단. 발표에서 "실제로는 공격이
1계층에서 끝나지만, 혹시 1계층이 우회되더라도 2·3·4계층이 각각 독립적으로 추가 방어선을
구성한다"는 심층 방어(defense-in-depth) 결론으로 마무리한다.

## 6. 자주 발생한 문제 (트러블슈팅)

| 증상 | 원인 | 해결 |
|---|---|---|
| `ping` 안 됨, `ip a`에 IP가 DHCP로 랜덤하게 잡힘 | 고정 IP 설정이 재부팅/클론 과정에서 날아감 | 2.1절 netplan+networkd 절차 재실행 |
| `nmcli connection up` 시 `device is strictly unmanaged` | `/usr/lib/NetworkManager/conf.d/`가 유선 인터페이스를 전부 unmanaged 처리 | 2.1절처럼 networkd로 전환하거나, `/etc/NetworkManager/conf.d/10-globally-managed-devices.conf`에 `[keyfile]\nunmanaged-devices=`로 덮어쓰기 |
| `OSError: [Errno 98] Address already in use` | 이전 서버 프로세스가 `Ctrl+Z`로 멈춘 채 포트를 쥐고 있음 | `jobs -l` → `kill -9 <PID>`, 또는 `sudo fuser -k 8000/tcp`(webhook/internal) / `8443/tcp`(receiver) |
| `non-public address denied` 등록 실패 | `config/url-exceptions.lab.local.json`의 CIDR이 실제 receiver IP와 다름 | zip에 이미 `192.168.40.30/32`로 수정 반영됨 — VM IP 대역을 바꿨다면 이 파일도 맞춰 수정 후 webhook 재시작(예외 파일은 기동 시 1회만 읽음) |
| `[Errno 111] Connection refused` (attack → receiver) | 칼리의 `run-receiver.sh`가 안 떠 있거나 죽음 | 별도 터미널에서 `./run-receiver.sh` 재기동 |
| Plane 공격(`attack-plane.sh`) 결과가 `"state": "queued"/"sending"`로 안 끝남 | 비동기 큐가 누적된 hook 때문에 한 번에 여러 건 처리 중 (1건당 최대 `DELIVERY_TIMEOUT=3`초) | 10~20초 더 기다렸다가 같은 `run_id`로 재조회, 또는 레이어 테스트는 `attack-mlflow.sh`(동기식) 사용 권장 |
| 한글이 `□□□`로 깨져 보임 | 터미널에 CJK 폰트가 없음 | `sudo apt-get install -y fonts-noto-cjk locales && sudo locale-gen ko_KR.UTF-8 en_US.UTF-8 && sudo update-locale LANG=en_US.UTF-8` 후 터미널 재시작 (인터넷 필요 — 2.1절 전 NAT 어댑터 임시 추가) |
