# 탭

[English](tabs.md) · **한국어** · [← README](../README.ko.md)

모든 탭은 같은 방식으로 동작합니다. 위에는 실행 목록이 최신순으로(최근 20개, git은 50개), 아래에는 고른
실행의 출력이 나옵니다. 탭 이름 옆에는 최근 상태가 붙습니다. `✓` `✗`, 실행 중이면 스피너, 떠 있는 서버는
`●`입니다. 새 실행이 들어오면 열린 deck이 그 탭으로 넘어가고, 새 줄은 탭 색으로 시작해 서서히 돌아옵니다.

실행 기록은 설명이 아니라 핵심 명령으로 표시됩니다. 에이전트에게 호출 설명을 `<kind>: <command>` 형식으로
쓰게 하고, 그 밖의 경우는 셸 토큰을 읽어 명령만 남깁니다.

---

## agents

**보여 주는 것**: 맨 위에 에이전트 자신, 그 아래에 지금 일하는 Claude Code 서브 에이전트(백그라운드와
워크플로 에이전트 포함). 에이전트마다 두 줄입니다.

```
⠼ add refresh tokens to the auth middleware      2m 16s
    Bash npm test                           ▂▃▇▅  ▁▃
⠼ Map the auth tests                               40s
    Read src/auth/refresh.test.ts            ▁▂  ▁▅▅▆
```

첫 줄은 이름과 일한 시간, 둘째 줄은 지금 하는 일(실행 중인 도구, 없으면 마지막으로 한 말, 또는
`waiting for you`)과 최근 2분의 활동 그래프입니다. 하나를 고르면 전체가 보입니다. 상태와 분당 토큰 수,
모델과 effort, 컨텍스트 사용량, 토큰, 종류별 도구 사용, 할 일 목록, 지금 실행 중인 도구, 마지막으로 한 말,
받은 요청입니다.

**나타나는 때**: 패널에서 세션이 시작될 때, 그리고 에이전트가 서브 에이전트에게 일을 맡길 때.
[zoetrope](https://github.com/furkankly/zoetrope)처럼 세션의 대화 기록 파일에서 읽습니다.

## jobs

**보여 주는 것**: 테스트도 빌드도 아니면서 오래 걸리는 명령들. 대기 루프, 설치, timeout을 길게 준
스크립트 같은 것들입니다. 무엇을 하려는지(호출 설명), timeout 대비 얼마나 돌았는지(`15s of 2m`),
나오는 대로의 출력을 보여 줍니다. 끝나면 출력과 종료 코드를 남기고, 실패는 빨간색으로 표시합니다.

**나타나는 때**: 그 밖의 명령이 10초를 넘겼을 때. 더 짧은 명령은 나타나지 않습니다. 출력은 Claude Code가
그 호출의 출력을 쓰는 파일에서 읽으므로 호출 자체는 바꾸지 않습니다.

## servers

**보여 주는 것**: 이 프로젝트를 위해 떠 있는 개발 서버와 각 서버의 포트, 로그(서버가 쓰는 파일이나 서버가
출력하는 herdr 패널). 로그는 맨 아래를 따라가고, 스크롤하면 `esc`를 누를 때까지 멈춰 있습니다. `o`로
브라우저에서 엽니다.

**나타나는 때**: 프로젝트 안의 프로세스, 또는 같은 herdr 워크스페이스의 패널에서 시작된 프로세스가 포트를
열 때. `npm run dev`, `next dev`, `vite`, `anvil`, `uvicorn`, `flask run`, … 명령은 그대로 두고 탭만
앞으로 가져옵니다.

## build · test

**보여 주는 것**: 실행마다 셸에서 보던 색 그대로의 실시간 출력, 실행 위치, 걸린 시간, 종료 코드.

**나타나는 때**: 에이전트가 실행할 때.

- 테스트: `npm/pnpm/yarn test[:x]`, `vitest`, `jest`, `pytest`, `go test`, `cargo test`, `bun test`,
  `deno test`, `forge test`, `make`/`just test`, `./test.sh` 같은 스크립트
- 빌드: `npm/pnpm/yarn run build[:x]`, `tsc`, `next build`, `vite build`, `go build`,
  `cargo build`/`check`, `forge build`, `make`/`just build`, `./build.sh` 같은 스크립트

단순한 호출(`[cd DIR &&] [VAR=val] [time|timeout N|env] [npx|pnpm|…] RUNNER [args]`, 여기에 `2>&1`과
`| tail/head -n N` 하나까지)은 deck 안에서 실행되고, 에이전트는 여전히 일반 텍스트와 실제 종료 코드를
받습니다. 체인이나 파이프 안에 있으면(`git status; npm test | grep FAIL`) 쓴 그대로 실행되고, 끝난 뒤
나타납니다.

## git

**보여 주는 것**: 저장소를 바꾼 명령 전부. 단계마다 한 줄(`git add · git commit "Fix it" · git push`),
실행된 브랜치, 남긴 커밋을 보여 주고, 커밋은 메시지 전체를 보여 줍니다. 목록 테두리에는 지금의 저장소
상태가 나옵니다.

```
╭─ runs · 12 ─────────────── main ↑2 · 3 changed ─╮
```

브랜치, 푸시하지 않은 커밋(`↑`)과 받지 않은 커밋(`↓`), 커밋하지 않은 파일 수(없으면 `clean`)입니다.

**나타나는 때**: `git`이 들어 있는 명령이 실행되어 무언가를 바꿨을 때. `git status`, `git log` 같은 조회는
제외합니다.

## deploy

**보여 주는 것**: 배포마다 출력과 종료 코드.

**나타나는 때**: 실제 배포가 실행됐을 때. `vercel`, `railway up`, `netlify deploy`, `fly deploy`,
`wrangler deploy`, `firebase deploy`, `gcloud run deploy`, `terraform apply`, `kubectl apply`,
`helm upgrade`, `forge script --broadcast` 등([전체 목록](../bin/classify)). `vercel ls`, `fly status`
같은 조회는 제외합니다.
