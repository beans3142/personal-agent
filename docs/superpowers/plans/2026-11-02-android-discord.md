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
- [x] Verify owner Discord command -> model -> Android tool -> reply/screenshot, and restart persistence.
- [x] Update local handoff with verified results and exact remaining blockers, without credentials.

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
- Reviewer found and verified fixes for stale outbox images after a failed transit lookup, after-midnight timetable ordering, and final text-entry index races. Full suite: 155 passed.
- Restart persistence verified at 16:35 KST: restarted the ReDroid container and gateway, invoked Baemin through web Discord, and independently inspected the selected-address home screen. The registered delivery address persisted. The check did not register another address or order anything.

Verified update (2026-11-02, 21:27 KST):
- The owner completed KakaoTalk secondary-device phone authentication. Actual login and the Chats list were verified. Contacts, notifications and battery-optimization access were not granted.
- Initial self-chat attempts stopped without sending: the generic 8,000-character tool-result limit cut a roughly 16,000-character tablet UI tree before the right-pane message input. Screenshots showed the input while the model received only the left chat list. Android screen snapshots now bypass that generic string clipping; obsolete snapshots still compact before later model calls. Other tool-result limits are unchanged.
- The regression test places the active input after a large chat list. It failed with truncated invalid JSON before the fix. Full suite after the fix: 156 passed; independent review found no actionable correctness issues.
- After server deployment, a web Discord request sent exactly one test message to the owner's own chat. The actual me badge and new outgoing bubble were independently verified. No other person or group received a message. This verifies self-chat transmission, not arbitrary-recipient delivery, incoming-message monitoring or indefinite authentication.
- App-reopen persistence verified: force-stopped only KakaoTalk, then opened it through web Discord. The actual logged-in Chats list returned, with the test message preserved in the own-chat preview. No new message was sent. This check covers app-process restart, not a later Android reboot or account-session expiry.

Current Kakao behavior (2026-11-02, 22:17 KST):
- At the owner's explicit request, sent a contextual group reply and an introduction as daevis (대비스). Outgoing bubbles were independently verified. Subsequent owner-requested recipient sends were also verified.
- The owner subsequently removed usage suffixes from Kakao. Kakao now receives reply text only. Discord receives a fixed completion/failure status plus measured usage, without chat text or automatic Kakao screenshots. Sanitized status also replaces raw model/tool output in saved Kakao conversation history.
- Token counts use k units from 1,000 upward. New requests retain recent conversational messages and omit completed requests' tool payloads. One stored DM history was 33,803 characters versus 1,882 characters of conversation; guild history was 38,771 versus 4,010. These are character counts, not a controlled token-cost comparison.
- Explicit quoted personal-message requests have a native program path. It validates the recipient, enters the exact text, clicks once and confirms a newly appended matching outgoing bubble. Lookup failure can fall back before drafting; an uncertain send never retries automatically. Groups, ambiguous phrasing and non-BMP text use the general path.
- Live web Discord QA: an already-open own-chat send completed in 2.5 seconds, zero model calls and zero model tokens. A preceding lookup fallback used 7 calls, 50.0k input and 255 output tokens in 20.0 seconds. Do not claim every request uses the fast path.
- The server group watcher runs until 23:00 KST today. It polls without model calls and uses one restricted conversational model call only for new incoming messages. Actual automatic deliveries were verified in logs; one used 531 input / 116 output tokens, and a recovered batch used 744 / 73. Completion status and exact runtime expiry are in private server state.
- Deployment interrupted an in-flight automatic reply. The watcher paused instead of resending. The selected room, absence of any newly appended outgoing bubble, and unsent draft were inspected; two verified-unanswered messages were queued and processed with later incoming messages. Future updates must drain the watcher before restarting.
- Full suite: 175 passed. Independent review found and verified fixes for dropped incoming messages during generation, stop-command races, old-bubble false confirmation and private raw output retained in history.
