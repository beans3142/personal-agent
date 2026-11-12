# 설치와 설정

Python 3.12, Ubuntu 서버 기준이다.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m agent.login
```

`.env`에 `DISCORD_BOT_TOKEN`, `OWNER_ID`, `HERMES_PYTHON`을 넣고 `deploy/personal-agent-hermes.service`로 실행한다. Hermes 쪽 MCP 설정 예시는 `deploy/mcp-hermes.example.yaml`에 있다. 전원 봇은 별도 상시 서버에서 `python -m agent.power_bot`으로 돌리고, 토큰과 소유자 ID는 AWS Parameter Store에서 읽는다.

테스트:

```bash
python -m pytest tests -q
```
