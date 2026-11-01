# personal-agent

OpenClaw 같은 개인 AI Agent를 직접 만드는 프로젝트다. 서버에서 24시간 돌고, Discord 메시지 한 번으로 여러 도구를 순서대로 써서 일을 끝낸다. 모델은 ChatGPT 구독 로그인(Codex OAuth)으로 부르며 API 키를 쓰지 않는다.

## 구조

```text
Discord DM -> gateway -> agent loop <-> model (Codex OAuth)
                 |            |
             scheduler      tools -> memory, schedule, web, mail, map
```

| 파일 | 역할 |
| --- | --- |
| `agent/gateway.py` | Discord 연결, 소유자 확인, 승인 버튼, 예약 실행 |
| `agent/loop.py` | 모델 호출과 도구 실행 반복, 승인, 단계 한도 |
| `agent/codex.py` | 구독 모델 호출과 토큰 갱신 |
| `agent/tools.py` | 도구 모음. 페이지 전체 대신 필요한 데이터만 모델에 준다 |
| `agent/memory.py` | 장기 기억과 대화 기록 (SQLite) |
| `agent/scheduler.py` | 알림과 예약 작업 |
| `agent/login.py` | ChatGPT 계정 로그인 |

## 구현 상태

동작: 다단계 작업, 웹 검색과 읽기, 장기 기억, 예약.

코드만 있고 실제 계정으로 시험 전: 네이버 메일(`NAVER_ID`, `NAVER_APP_PASSWORD`), 장소 검색(`KAKAO_REST_KEY`).

미구현: 카카오톡 발송, 네이버 웹 로그인, 개인 인증서버, KTX 예약, 배달 주문, 전화, 음성 호출.

## 실행

Python 3.12에서 돌린다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m agent.login
```

`.env`에 `DISCORD_BOT_TOKEN`, `OWNER_ID`, `AGENT_MODEL`을 넣고 `deploy/personal-agent.service`로 실행한다.

테스트:

```bash
python -m pytest tests -q
```

## 보안

- 토큰, 기억, 대화 기록은 `data/`와 `.env`에 두며 Git에 올리지 않는다.
- 소유자 DM만 처리하고, 승인이 필요한 도구는 Discord 버튼으로 확인한다.
- 웹 도구는 사설 주소와 클라우드 메타데이터 주소에 접속하지 않는다.
