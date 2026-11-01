# personal-agent

내 PC에서 돌리는 개인 AI Agent다. Claude Code를 두뇌로 쓰고 Discord DM으로 대화한다. 추가 요금 없이 Claude 구독 범위 안에서 돌린다.

## 구조

```text
Discord 앱 <-> Discord 서버 <-> 공식 Discord 플러그인 <-> Claude Code <-> PC의 파일과 명령
```

이 레포에는 Agent 지침, 전용 설정, 실행 스크립트, 음성 변환 코드가 있다.

| 파일 | 역할 |
| --- | --- |
| `CLAUDE.md` | Agent 지침 |
| `.claude/settings.json` | 이 Agent만 쓰는 플러그인과 권한 |
| `tools/start-agent.ps1` | 실행하고, 꺼지면 다시 켠다 |
| `tools/transcribe.py` | 음성 파일을 글로 바꾼다 (faster-whisper, 로컬) |
| `tools/speak.py` | 글을 음성 파일로 바꾼다 (Windows 음성, 로컬) |

전역 Claude Code 설정과 상위 폴더 지침은 읽지 않는다. 다만 파일 접근까지 막는 격리는 아니다. Windows에서는 Claude Code 샌드박스가 지원되지 않으므로, 명령 실행은 Discord 버튼으로 승인받는다.

기억은 `data/memory`에 Markdown으로 저장한다. Obsidian으로 열 수 있고, Git에는 올라가지 않는다.

## 실행

필요한 것: Claude Code, Bun, Python, FFmpeg, `faster-whisper`, Discord 봇 토큰

토큰은 `~/.claude/channels/discord/.env`에 LF 줄바꿈으로 넣는다.

```text
DISCORD_BOT_TOKEN=발급받은_토큰
```

```powershell
claude plugin install discord@claude-plugins-official
.\tools\start-agent.ps1
```

테스트:

```powershell
python -m pytest tests -q
```

## 보안

- 토큰, 접근 설정, 기억, 대화 내용은 이 레포에 올리지 않는다.
- 봇을 쓸 수 있는 사용자는 `/discord:access`로 제한한다.
