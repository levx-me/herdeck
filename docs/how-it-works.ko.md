# 동작 방식

[English](how-it-works.md) · **한국어** · [← README](../README.ko.md)

```mermaid
flowchart LR
  A[Claude Code<br/>Bash tool] -- PreToolUse --> H[bin/hook]
  H -- test / build:<br/>rewrite to --> R[bin/run]
  H -- anything else --> J[bin/job]
  A -- PostToolUse(Failure) --> C[bin/record]
  R -- bin/ptyrun --> S[(TMPDIR/steading/&lt;agent&gt;/)]
  J --> S
  C --> S
  R & C -- bin/pane --follow --> V[bin/viewer.py<br/>the deck]
  V -- reads --> S
  V -- bin/servers --> L[lsof]
  V -- transcripts --> T[(~/.claude/projects)]
```

- `bin/classify`는 명령줄을 셸 토큰 단위로 읽습니다. 무엇을 실행하는지(test, build, deploy, git 쓰기),
  보여 줄 핵심 명령(`cd x; env -u A pnpm test | tail` → `pnpm test`), 실시간으로 실행해도 되는지를
  판단합니다. 두 훅이 모두 이것을 씁니다.
- `bin/hook`은 Bash 호출이 무엇인지 판단합니다. 테스트와 빌드는 `bin/run`으로 바꿔 실행하고(승인됨,
  아래 참고), 개발 서버는 열린 deck을 `servers`로 바꾸기만 합니다. 나머지는 job으로 기록하고,
  명령이 시작되면 `bin/job`이 Claude Code가 그 출력을 쓰는 파일을 찾아 연결합니다.
- `bin/run`은 명령을 의사 터미널(`bin/ptyrun`)에서 실행하고, 실행 기록을
  `$TMPDIR/steading/<agent pane>/<kind>/runs/<n>/`에 쓴 뒤, 일반 텍스트 출력과 종료 코드를 돌려줍니다.
- `bin/record`는 Claude가 실행한 뒤 git 명령, 배포, `bin/run`을 거치지 않은 테스트와 빌드를 기록하고,
  job이 어떻게 끝났는지 남깁니다.
- `bin/open`은 Claude Code 세션에 단순한 명령으로 실행하라는 안내를 추가합니다. 세션이 새 패널에서 다시
  열렸거나 `/tmp`가 비워져 deck이 비어 있으면, `bin/restore`가 세션 대화 기록을 `bin/record`에 다시
  넣어 채웁니다.
- `bin/pane`은 에이전트마다 deck을 한 번 열고 어느 탭을 보여 줄지 알려 줍니다. 실행에서 부를 때
  (`--follow`)는 이미 열린 deck의 탭만 바꿉니다.
- `bin/deck`은 herdr 쪽입니다. `toggle`은 단축키용, `auto`는 herdr의 `pane.agent_detected` 이벤트용,
  `flip`은 `L`용입니다.
- `bin/viewer.py`는 그 실행 기록 디렉터리로 deck을 그리고, 파일이 수정되면 스스로 다시 시작합니다.

## 설정

| 변수 | 기본값 | 용도 |
|---|---|---|
| `STEADING_SOURCE` | `levx-me/steading` | `bin/install-claude`가 Claude Code 마켓플레이스를 추가할 위치(경로 또는 `owner/repo`) |
| `TMPDIR` | `/tmp` | 실행 기록(`$TMPDIR/steading`)의 상위 디렉터리 |

deck에서 실행되는 명령에는 `CI=1`(watch 모드 끔)과 `FORCE_COLOR=1`, `CLICOLOR_FORCE=1`,
`PY_COLORS=1`, `CARGO_TERM_COLOR=always`가 설정됩니다.

## 보안 참고

- deck으로 넘긴 테스트와 빌드는 Claude Code에 **승인됨**(`permissionDecision: "allow"`)으로 돌려주므로
  권한을 묻지 않습니다. deny와 ask 규칙은 그대로 적용됩니다. `npm test`와 `npm run build`는
  `package.json`에 적힌 것을 그대로 실행합니다. 자동 승인을 원하지 않으면 `bin/hook`의 패턴에서 빼세요.
- git 명령, 배포, job은 Steading이 바꾸거나 승인하지 않습니다.
- 실행 로그는 `$TMPDIR`에만 남고 컴퓨터 밖으로 나가지 않습니다.

## 개발

```sh
git clone git@github.com:levx-me/steading.git && cd steading
claude plugin marketplace add "$PWD" && claude plugin install steading@steading
herdr plugin link "$PWD"
bun test
```

체크아웃한 저장소로 플러그인을 설치하면 `bun test` 자체도 deck에서 실행됩니다. deck의 모양이 바뀌면
README 미리보기를 다시 만드세요: `python3 docs/demo.py > docs/demo.svg`.
