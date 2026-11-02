# Android apps through the existing Discord agent

Goal: let the owner operate KakaoTalk and Baemin on the existing ARM server through Discord, and verify actual app availability before claiming support. Coupang Eats was excluded by the owner and its test installation was removed.

Architecture: retain Gateway, model connection, memory and scheduler. Add one Android tool adapter using UiAutomator2 over the existing local adb connection. Return a compact accessibility tree and a screenshot; require a fresh observation before actions. Keep credentials out of model history by accepting environment key names. Keep final purchase and security-consent actions behind the existing Discord approval callback. Do not create AWS resources or purchase anything during setup.

Acceptance:
- Google account setup completes on the existing Android installation.
- Each app is installed from its official store or a verified publisher source, then its actual launch/login result is recorded.
- The owner can ask Discord to open an app, inspect it, and perform a sequence of UI actions.
- Korean text entry works without a separate keyboard with access to credentials.
- The gateway survives a service restart, and only one bot gateway runs.
- Report unavailable services and outstanding user authentication precisely; a unit test is not evidence of app compatibility.

Execution:
- [x] Read latest transcript, HANDOFF, current source; establish baseline (110 tests).
- [x] Add failing tests for UI redaction, stale observations, credential references and consequential actions.
- [x] Implement Android adapter and connect tools to Gateway; run focused and full tests.
- [x] Expose allowlisted app launch in the existing owner portal for actual setup and inspection.
- [x] Install dependencies on ARM, verify Android service and app installation.
- [x] Move the gateway runtime and existing state to ARM, retaining a rollback copy and stopping the old gateway.
- [ ] Verify owner Discord command -> model -> Android tool -> reply/screenshot, and restart persistence.
- [ ] Update local handoff with verified results and exact remaining blockers, without credentials.

Live investigation: launching Baemin triggered a native crash in the ARM Vulkan software renderer (`vulkan.lvp.so`, llvmpipe workers), followed by SurfaceFlinger/SystemUI failures. LP_NUM_THREADS in LXC environment did not reach Android services. The Android Mesa property `debug.mesa.lp.num.threads=0` was persisted in waydroid_base.prop and waydroid.cfg before a full restart; no llvmpipe worker threads were present afterward. A subsequent Discord request opened Baemin and returned its actual notification-permission screen and screenshot. Continued app flows remain under test.
