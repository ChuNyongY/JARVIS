# 전경 컨텍스트 계약

`javis foreground-context`는 현재 전경 창의 구조적 식별 정보만 읽는다. 수집 결과는
`HWND`, `PID`, 등록된 `app_id` 여부, 검증 시각으로 제한한다. 창 제목, 문서명, UI 텍스트,
화면 이미지, 명령줄, 실행 파일 경로는 수집하거나 출력하지 않는다.

등록 앱 판별은 프로세스 실행 파일의 이름만 비교하지 않는다. 운영 registry를 먼저 엄격하게
검증한 뒤, 전경 PID의 실제 실행 파일이 등록된 runtime 경로와 정확히 같고 현재 SHA-256도
일치할 때만 `identity_verified=true`로 표시한다. 둘 이상의 등록과 일치하면 모호한 상태로
간주해 실패한다. 일치 항목이 없으면 PID와 HWND만 포함한 미등록 상태를 반환한다.

```powershell
python -m javis_core.cli foreground-context config/apps.json --require-publisher
```

이 결과는 사용자 경험을 위한 최소 컨텍스트일 뿐 authorization artifact가 아니다. 작업 실행은
별도로 typed plan, capability policy, 승인 토큰, 검증된 process session과 postcondition 검증을
통과해야 한다. 특히 PID는 재사용될 수 있으므로 foreground snapshot만으로 UIA 변경 대상을
승인해서는 안 된다.

`StableForegroundContextCollector`는 변경 작업 직전의 보조 판정에 사용할 수 있다. 기본값은
50 ms 간격의 3개 연속 관찰이며 HWND, PID, app ID, 검증 여부가 모두 같아야 한다. 등록·검증된
앱만 허용하고, 중간 포커스 변경·수집 오류·비단조 clock·전체 timeout을 모두 실패로 처리한다.
샘플 수는 2~10개, 간격은 0~500 ms, 전체 제한은 최대 5초로 고정된다. 가장 최근 snapshot을
반환하지만 이 안정성 검사 역시 승인 토큰이나 process creation time에 결합된 session을 대신하지
않는다.

Planner에 제공할 때는 `capture_planner_context()`를 사용한다. 이 경계는 안정 표본의 등록 신원이
확인된 경우에만 `{"foreground_app_id": "..."}` 한 필드를 만든다. HWND, PID, 관찰 시각과 경로는
로컬 수집기 밖으로 내보내지 않는다. 이 값은 계획 제안의 trusted context일 뿐이며 실행 권한이나
대상 결합으로 해석하지 않는다.

상시 runtime에서는 polling 대신 제한된 WinEvent 구독으로 수집 시점을 정할 수 있다. 이벤트는
foreground/focus의 PID와 root HWND만 포함하며, 정확한 event snapshot과 collector 결과가 일치해야
한다. overflow와 순서 역행은 실패로 닫힌다. 상세 계약은 `docs/CONTEXT_EVENTS.md`에 있다.
