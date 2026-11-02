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

Live investigation: Waydroid repeatedly crashed in the ARM software renderer. Disabling Mesa worker threads, switching EGL to Mesa, and testing the official April 2026 vendor image did not establish stable app use. Do not repeat these as untried fixes or describe a transient terms screen as compatibility proof.

Resume state (2026-11-02, 15:43 KST):
- Runtime is now official ReDroid 13, pinned to image digest `sha256:41e5f0c1ff27a4a474c474e5595168cedf6c40fc5dd102c5617f48c80f511e9e`, on the existing ARM server. Container `personal-agent-android` uses guest rendering, `/home/ubuntu/redroid-data`, localhost-only ADB `127.0.0.1:15555`, and restart policy `unless-stopped`. Docker and personal-agent services are enabled. No new EC2 instance was created.
- `ANDROID_SERIAL=127.0.0.1:15555` is configured in the server environment. Portal and gateway both use it. Waydroid boot service `android.service` is disabled; old data and original vendor image backup are retained.
- KakaoTalk and Baemin APK splits were copied unchanged from official Play installations on Waydroid and installed successfully in ReDroid. ReDroid has no Google Play services. App login and service compatibility still need verification. No Coupang app is installed.
- Web Discord owner DM successfully opened Baemin, read the actual terms, selected only the required location-based terms, and pressed Get Started. Screenshot verified required checked, marketing unchecked, and marketing refusal processed. Further onboarding is in progress; no order was placed.
- Live QA found the model sometimes passed an array position instead of a node id, clicking launcher apps. `android_click` now requires an exact expected label as well as the node id; mismatches fail before input. Observations expose package, enabled/clickable flags, and checkbox state. Regression and full suite: 130 passed.
- Remaining acceptance: finish Baemin onboarding/login and harmless Korean search, Kakao secondary-device login, and actual Android restart persistence. The original x86 gateway is stopped; its EC2 instance remains running as a rollback source. Model OAuth lifetime is finite; do not claim indefinite unattended operation.

Discord access update (2026-11-02, 15:51 KST):
- Only `OWNER_ID` can invoke the model, whether in DM or by directly mentioning the bot in a guild channel. Approval buttons also enforce the owner id.
- Owner requested public replies after trying the initial DM-routing version. Current behavior: add hourglass reaction while queued/processing, return the answer and outbox to the originating channel, replace hourglass with checkmark after reply delivery or cross on an execution/delivery exception. No textual DM-routing acknowledgement is sent.
- Guild histories use `guild:<channel_id>` and do not share the owner DM history. Stored facts are not automatically injected into public-channel prompts. Consequential action approvals remain owner-only DMs.
- Guild/message intents are enabled. No member/presence intent or administrator permission was added. The bot must already be present and have channel visibility/send permissions; reactions additionally need permission.
- Live web Discord QA in the existing server channel verified the exact response `MENTION_QA_OK` in that channel and a checkmark on the originating owner message. Nonowner rejection, missing mentions, DM behavior, reaction-denied fallback, and exception reactions have regression tests.
- Android setup was interrupted for this Discord configuration request. Baemin is at address setup; the owner supplied an address in the Codex chat (do not copy it into this public repository). Kakao is at secondary-device login; credential input returned ValueError and still needs diagnosis. Do not claim either app login is complete.
