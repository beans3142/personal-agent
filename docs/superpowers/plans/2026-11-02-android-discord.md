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

Verified update (2026-11-02, 16:32 KST):
- Replies include measured processing seconds (excluding queue time), summed reported input/output tokens, model-call and tool-call counts, and returned model id. Missing usage is labelled unavailable; a requested-model fallback is explicitly labelled. Live single-call QA returned 1.5s, 1,683 input / 10 output tokens, and gpt-5.6-luna.
- Transit uses Naver's own place autocomplete instead of the previously failing OSM geocode dependency. It selects distinct fast-arrival, low-transfer and low-walking alternatives (up to three), reads route details and a source-labelled scheduled timetable, and attaches one overview map. Detailed panels are removed before capture and animations disabled. Live Discord QA verified alternatives, arrival/boarding times and fast-transfer/exit positions. This is scheduled travel information, not a guarantee of actual arrival.
- Owner requested concise Discord output: recommendation + 2–3 lines per alternative; no station-by-station lists, local image Markdown or repeated map images. Message splitting now preserves short lines such as the metrics footer.
- Android plain text and credential-reference input are separate tools. Search keyboard submission is available for a freshly observed focused input. Anonymous clickable containers with labelled children are omitted; exact node identity is revalidated across unrelated node-index shifts and rotating banners. Consequential labels and permission grants remain protected. Final native text entry resolves the same node identity again instead of reusing an index.
- Obsolete Android UI trees are replaced with a superseded marker before later model calls. Three repeated action errors stop the loop; step-limit/repeated-error replies use a failure reaction, not a success reaction. Token totals from different tasks are not a controlled cost benchmark.
- Baemin delivery address was actually registered and its selected home screen verified. Live Discord search for Burger King then showed Delivery 3 and three named stores. No cart/order/payment occurred. Do not publish the owner's address in this repository. Account login/checkout has not been verified; browsing works as a guest.
- Kakao credentials were entered successfully using environment references. Login reached the secondary-device registration code on the existing phone; code expired while waiting. The owner was asked to prepare the phone before a fresh code is generated. Kakao messaging is not yet operational.
- Reviewer found and verified fixes for stale outbox images after a failed transit lookup, after-midnight timetable ordering, and final text-entry index races. Full suite: 155 passed. Android restart/persistence verification is being performed separately.
