# Local application registry

`apps.json` is generated for this Windows installation. Each entry pins an absolute executable path,
SHA-256, and Authenticode signer subject. JAVIS re-checks these values when loading the registry and
again immediately before process launch.

An entry may additionally pin `runtime_executable`, `runtime_sha256`, and `runtime_publisher` when a
signed launcher hands off to a different process image. The launcher is held and verified for process
creation; only the exact runtime identity may receive a UIA session. Runtime paths are never globbed or
discovered automatically.

Windows Update can legitimately replace a system executable. A hash mismatch must not be bypassed:
inspect the new file, confirm its Authenticode status and publisher, then deliberately regenerate the
entry. Do not accept a path, hash, or publisher proposed by planner or untrusted document content.

Production startup should use `AppRegistryLoader(require_publisher=True)`. This file grants only the
ability to launch the exact executable without arguments; it does not grant UIA access automatically.
The launched PID must separately pass process identity verification and session authorization.

`windows_hello_helper.json` separately pins the validly Microsoft-signed system .NET host and every
managed file in the locally built Windows Hello helper directory. `load_windows_hello_verifier` resolves
managed artifacts under an explicit absolute project root, rejects symlinks, traversal, hash mismatch,
duplicates, and extra directory entries, then invokes the fixed entrypoint through that host. Regenerate
this manifest only with `native/WindowsHelloHelper/build.ps1`; never accept paths or digests from the
planner.

`product_host.json` contains only non-secret startup settings and project-relative manifest paths. It is
validated together with an explicitly supplied, user-only ACL-protected NIM secret file and the complete
doctor report before any microphone or model runtime may start. Never add an API key to this JSON.
