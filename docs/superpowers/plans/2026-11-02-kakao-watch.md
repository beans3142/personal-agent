# Temporary Kakao group replies

Goal: reply to new incoming messages in the owner-selected group until 23:00 KST on 2026-11-02. Kakao messages contain only reply text; Discord retains usage reporting.

Reuse the server Android connection and gateway lock. Poll the exact group UI without model calls. Compare overlapping message windows, exclude outgoing bubbles, and request one conversational response only when incoming text changes. Use no account, purchase, file or messaging tools in this conversational model call. Verify the same group again before sending. Store the observed window before generation so an uncertain send is never retried automatically.

- [x] Test room validation, incoming/outgoing classification, overlapping-window deduplication, expiry and uncertain-send recovery.
- [x] Add the bounded watcher and owner-only stop command to the existing gateway.
- [x] Run tests and independent review; deploy after current commands finish.
- [x] Establish a live baseline in the selected group, verify the runtime is polling, and record the exact expiry. Live incoming-message replies were subsequently verified; five delivery batches were recorded by 22:16 KST.

Limits: Android accessibility exposes no durable message ID. If the visible window loses all overlap, or a send is interrupted, pause and notify the owner rather than replaying old messages. Rapid bursts may be answered together. A disconnected app or changed group layout cannot be treated as successful monitoring.

Actual operation: baseline captured from the chosen five-person room. Polling and real incoming-message delivery were verified. End time is 2026-11-02T23:00:00+09:00. Owner Discord command `카톡 자동응답 중지` stops it early. Private state stores pending input/reply so uncertain sends can be reconciled without blind retries. No Kakao content or screenshots are automatically published to Discord.
