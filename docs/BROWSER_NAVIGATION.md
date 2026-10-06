# 제한된 HTTPS 브라우저 탐색

`browser.navigate`는 등록·서명·SHA-256 검증된 Chrome 또는 Edge에서 허용된 HTTPS 주소 하나를
새 창으로 연다. 일반 브라우저 자동화나 임의 명령행 실행 기능이 아니다.

## 신뢰 경계

- 허용 host는 `config/product_host.json`의 `browser_allowed_hosts`에 소문자 ASCII DNS 이름으로
  명시한다. 하위 도메인은 자동 허용하지 않는다.
- URL은 최대 2,048자이며 HTTPS만 허용한다. userinfo, IP literal, 비기본 port, fragment,
  제어문자, 역슬래시와 비 ASCII URL은 실행 전에 거부한다.
- action은 `browser_id`, `url`만 받고 target은 `browser:<browser_id>:<host>`와 정확히 일치해야
  한다. postcondition은 인자 없는 `browser.navigation.verified` 하나로 고정한다.
- 실행 파일은 열린 deny-write/delete lease에서 경로와 SHA-256을 다시 확인한다. 실행 명령은
  정확한 executable, 고정 `--new-window`, 검증된 URL 세 항목뿐이다.
- 실행 전 창 집합과 비교해 새 창 하나만 안정적으로 식별한다. 0개·복수·교체 후보는 모두
  fail-closed다.
- 결과와 verifier에는 URL, path, query를 남기지 않는다. browser ID, host, URL SHA-256,
  PID/HWND와 process creation time만 결합한다.
- 독립 verifier가 현재 PID image/hash, creation time과 PID/HWND/window class를 다시 읽는다.
  브라우저 내부 페이지 내용이나 로그인 상태를 성공 근거로 사용하지 않는다.

## 현재 허용 범위와 한계

기본 배포 설정은 NVIDIA 개발자 문서, Python 문서, GitHub, Microsoft Learn과 Google의 정확한
host만 허용한다. redirect 이후 최종 URL이나 페이지 로드 완료를 브라우저 외부에서 읽지 않으므로,
검증 결과는 “요청 URL을 등록 브라우저의 새 창에 전달했고 그 창의 process identity가 유지됨”을
뜻한다. 로그인, 폼 제출, 다운로드, 페이지 내용 추출은 이 capability 범위 밖이다.

2026-10-01 실제 Windows smoke에서 Edge는 새 창 후보를 하나로 증명하지 못해
`BROWSER_WINDOW_HANDOFF_FAILED`로 fail-closed됐다. 같은 exact-host 요청을 Chrome으로 실행한 결과
`BROWSER_NAVIGATION_VERIFIED`, URL digest 결합과 exact HWND `WM_CLOSE` 정리가 PASS했다. 페이지
내용, 로그인 상태나 title은 수집하지 않았다.
