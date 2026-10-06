# Windows Settings 페이지 독립 검증

`settings.open`은 사용자가 제공한 URI나 selector를 실행하지 않는다. typed plan의 `page_id`는 다음
고정 표의 네 값만 허용한다.

| page_id | 고정 URI |
|---|---|
| `display` | `ms-settings:display` |
| `sound` | `ms-settings:sound` |
| `network` | `ms-settings:network-status` |
| `bluetooth` | `ms-settings:bluetooth` |

이전의 `home` 매핑인 `ms-settings:`는 이미 열린 Settings의 마지막 페이지를 그대로 유지할 수 있어
home 도달을 증명하지 못한다. 검증 불가능한 성공을 보고하지 않기 위해 allowlist에서 제거했다.
임의 URI, query, fragment, shell argument와 planner 제공 UIA selector는 받을 수 없다.

운영 registry의 `settings` 항목은 `direct_launch=false`다. generic `process.launch`는
`SystemSettings.exe` 직접 실행을 거부한다. 전용 executor만 URI activation을 수행하며, 전후 process
snapshot에서 registry의 exact path와 현재 SHA-256에 맞는 runtime PID를 하나로 결합한다. 복수
runtime, activation 오류, 취소와 runtime 미발견은 실패다.

`SettingsPageVerifier`는 executor 결과를 그대로 믿지 않고 다음을 다시 확인한다.

- action ID, capability, target, page ID와 execution result의 정확한 결합
- 등록된 `SystemSettings.exe` runtime path/hash identity
- 페이지별 고정 AutomationId, control type과 class의 유일한 구조 anchor
- 3초 worker timeout과 최대 3회로 제한된 subprocess UIA 관찰 및 anchor visibility

성공 결과에는 `app_id`, `page_id`, `pid`, `page_profile_sha256`만 포함한다. UI name이나 화면 text는
감사 결과에 반환하지 않는다. 오류 원문도 stable failure code로 축약한다.

```powershell
python tools\settings_page_smoke.py
```

현재 Windows 11 actual smoke에서 display, sound, network와 bluetooth 네 페이지가 모두
`SETTINGS_PAGE_VERIFIED`를 반환했다. smoke는 Settings 내부 `CoreWindow`와 부모
`ApplicationFrameWindow` 관계로 검증된 frame만 닫으며, 종료 후 visible Settings frame 0개를
재확인했다.

AutomationId는 Windows Update 뒤 바뀔 수 있다. page anchor 존재는 페이지 도달 증거이지 특정 설정이
변경됐다는 증거가 아니다. profile 불일치는 fail-closed 처리하며 Windows Update 뒤 실제 구조를
다시 관찰하고 profile을 재검토해야 한다.
