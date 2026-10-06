# Local openWakeWord bundle

이 디렉터리의 ONNX 파일은 openWakeWord v0.5.1 공식 GitHub release asset에서 받았다.

- `hey_jarvis_v0.1.onnx`
- `melspectrogram.onnx`
- `embedding_model.onnx`

`manifest.json`은 운영 시 확인할 SHA-256과 prediction key를 고정한다. loader는 URL을 받거나
모델을 다운로드하지 않으며, 세 파일의 경로·symlink·크기·hash를 모두 검증한다.

원본 프로젝트와 라이선스: https://github.com/dscripka/openWakeWord
