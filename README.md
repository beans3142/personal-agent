# personal-agent

내 PC에서 돌리는 개인 AI 에이전트 실험이다. 추가 요금 없이 Claude Pro 구독 범위 안에서 만든다.

## 현재 상태

이 레포에는 아직 실행 코드가 없다. 지금은 Claude Code에 공식 Discord 플러그인을 연결해 둔 상태다.

- 확인함: 봇 로그인, 사용자 등록
- 확인 못 함: Discord 메시지를 받아 Claude가 답하는 전체 흐름
- 없음: 음성 통화, 전용 기억, 자동 재시작, 예약 작업

## 실행

필요한 것: Claude Code, Bun, Discord 봇 토큰

```bash
claude plugin install discord@claude-plugins-official
claude --channels plugin:discord@claude-plugins-official
```

토큰은 `~/.claude/channels/discord/.env`에 넣는다. 줄바꿈은 LF여야 한다.

```text
DISCORD_BOT_TOKEN=발급받은_토큰
```

## 보안

- 토큰과 접근 설정은 이 레포에 올리지 않는다.
- 이 PC에서 파일 수정과 명령 실행이 가능한 구성이므로, 봇을 쓸 수 있는 사용자를 제한한다.
