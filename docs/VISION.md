# Local-only Vision fallback 경계

`VisionClickExecutor`는 UIA로 해결할 수 없는 앱별 작업을 위한 제한된 fallback 계약이다. 범용
`click(x, y)` capability를 Planner에 노출하지 않는다. 실행 가능한 semantic capability마다 로컬
코드가 `VisionTargetProfile`을 등록해야 하며 profile은 정확한 app ID, 신뢰된 target cue와 최소
confidence를 고정한다. 현재 기본 registry에는 Vision 실행 capability가 없으므로 실제 입력은
활성화되지 않는다.

## 실행 전제

1. action argument는 `process_id` 하나이고 `target_identity`는 같은 `process:<pid>`여야 한다.
2. PID는 검증된 launch session에 속하고 profile의 app ID와 일치해야 한다. capture·재캡처·입력은
   동일 foreground HWND에도 결합돼 같은 프로세스의 다른 창 전환을 거부한다.
3. grounder는 `local_only=True`여야 한다. 화면을 외부 서비스에 보내는 구현은 거부한다.
4. capture는 해당 창의 최대 4096×2160 BGRA pixel만 메모리에 반환한다. 전체 데스크톱, 파일 저장,
   창 제목·화면 텍스트의 audit 기록은 허용하지 않는다.
5. grounding proposal은 action ID와 capture SHA-256에 결합되고, profile confidence 및 이미지 내부
   좌표 조건을 모두 통과해야 한다.
6. 입력 직전에 창을 다시 캡처한다. 크기나 pixel hash가 바뀌었으면 클릭하지 않고
   `SCREEN_CHANGED_BEFORE_INPUT`으로 끝낸다. 창이 이동했지만 내용이 같으면 새 origin을 사용한다.
7. session을 다시 검증하고 STOP을 다시 확인한 뒤 한 번만 클릭한다. input backend 예외는 부작용이
   불명확하므로 자동 재시도하지 않는다.

모든 capture는 `SelectiveCaptureGate`의 one-shot permit를 소비한다. broker가 같은 action의
retry-safe UIA pre-input failure를 확인한 뒤에만 GROUNDING, PRE_INPUT, VERIFICATION을 하나씩
발급한다. gate가 없거나 UIA 증거가 없으면 Vision adapter 자체를 호출하지 않는다.

로컬 deterministic grounding 경로인 `ExactTemplateVisionGrounder`는 최대 scan pixel 수를 제한하고
정확한 BGRA template이 화면에 한 번만 나타날 때 중앙 좌표를 반환한다. 0개·복수 일치 또는 scan
상한 초과는 모두 실패한다. template은 `.bgra` 파일의 상대 경로, geometry, SHA-256을 strict JSON
manifest에 고정하며 duplicate key, traversal, symlink, 크기·hash 불일치를 거부한다.

성공 결과에는 PID와 입력 전 pixel hash만 남으며 image bytes, cue, 좌표 또는 화면 텍스트는 남기지
않는다. `VisionWindowChangedPredicate`는 별도 capture backend로 승인된 창을 다시 읽어 input 전
hash와 달라졌는지 확인한다. 이 변화 검사는 최소 증거일 뿐 의미적 성공을 완전히 증명하지 않으므로,
실제 앱 profile은 가능한 경우 앱별 UIA/API postcondition을 추가해야 한다.

## 현재 한계

Windows foreground client GDI capture와 foreground HWND 재확인 mouse backend 코드는 구현됐고,
pywin32 API를 모사한 테스트에서 top-down BGRA 정규화, PID/HWND 결합과 GDI handle cleanup을
검증했다. 추가로 JAVIS가 생성한 일회성 합성 Tk client 전체만 template로 사용해 실제 GDI capture,
hash-pinned template load, exact grounding, 동일 PID/HWND mouse click, 화면 변경 postcondition을
end-to-end로 성공 검증했다. 원래 cursor 위치를 복원하고 임시 image/template는 즉시 폐기한다.

기본 registry에는 Vision capability가 없고 실제 앱 profile도 활성화하지 않았다. 사용자 화면은
캡처하지 않았으며 exact template 외 학습 기반 local vision model, 실제 앱별 의미적 postcondition,
DPI·다중 모니터 실기기 보정과 장시간 안정성은 여전히 미검증이다. 외부 또는 학습 기반 grounder
실행 시간 제한은 향후 격리 worker에서 강제해야 한다. 합성 smoke는 다음처럼 반복할 수 있다.

```powershell
.\.venv\Scripts\python.exe tools\vision_smoke.py
```
