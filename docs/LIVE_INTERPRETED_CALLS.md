# Live interpreted calls

This subsystem is additive to the asynchronous AI interview. It does not read
or change questions, candidate responses, transcription artifacts, or scoring.
It creates one `LiveCallSession` for an existing scheduled `InterviewSession`
and, when present, links the session's `Evaluation`.

## Media design

The browser-to-browser camera stream and the original audio track use a direct
1:1 WebRTC peer connection. SDP and trickle ICE are relayed through Django
Channels/Redis. There is no SFU. Each browser also converts its microphone to
16 kHz, mono, signed 16-bit little-endian PCM and sends those chunks as binary
frames on the call WebSocket. The backend maintains one Azure Speech
Translation recognizer per speaker. Final translated phrases are synthesized
with Azure neural TTS and sent to the other participant as base64 MP3
`translated_audio` messages. Translation text is intentionally not sent to the
browser: this feature produces spoken interpretation, not captions.

Translated speech **replaces** original remote speech. The frontend must keep
the WebRTC remote audio track muted and play `translated_audio`; video remains
live. Playing original and translated speech together is confusing because the
translation trails the original, and it increases acoustic feedback into both
recognizers. A UI may expose the original track for diagnostics, but it must
not play both by default.

This is full duplex: both PCM streams and recognizers remain active
simultaneously. It is not magically zero-latency. Expected phrase-level latency
on a healthy connection is roughly 1.5–4 seconds (endpoint detection + STT/MT +
TTS + network/playback), and can be longer for hesitant speech or a distant
Azure region. Short endpointing lowers latency but produces fragmented prosody
and translation; longer endpointing improves sentence context but delays TTS.
Overlapping speech does not mix in the cloud—each microphone has an independent
pipeline—but loudspeaker bleed can still be recognized by the other mic. Use
`echoCancellation`, `noiseSuppression`, and `autoGainControl` media constraints;
headsets are strongly recommended. Browser AEC quality, accents, code-switching,
domain vocabulary, and unsupported language/voice pairs remain real quality
risks that must be acceptance-tested before locking the UI.

## REST contract

All paths are below `/api/v1/live-calls/`.

* `POST sessions/{interview_session_id}/join` creates or joins the linked call.
  Staff authenticate normally. A candidate supplies the existing session token
  in `X-Session-Token`, `?token=`, or JSON `token`. The response contains the
  participant role, a short-lived signed WebSocket ticket, ICE servers, audio
  policy, and PCM format.
* `GET sessions/{interview_session_id}/languages` returns call state and both
  preferences.
* `PUT sessions/{interview_session_id}/languages` accepts `input_language` and
  `output_language` BCP-47 values such as `ar-SA` and `en-US`. Changing either
  preference restarts the affected live pipelines.

The join window opens 15 minutes before `scheduled_start_at` by default. Closed
interview sessions cannot be joined.

## WebSocket contract

Connect to the returned `websocket_path` with `?ticket=...`. JSON client actions:

```json
{"action":"offer","data":{"type":"offer","sdp":"..."}}
{"action":"answer","data":{"type":"answer","sdp":"..."}}
{"action":"ice_candidate","data":{"candidate":"..."}}
{"action":"renegotiate","data":{}}
{"action":"ping"}
{"action":"end_call"}
```

The peer receives corresponding `event` messages. Presence changes produce
`peer_presence`; reconnect by obtaining a fresh join response/ticket, rebuilding
the peer connection, and sending a new offer. The persisted state moves through
`WAITING`, `ACTIVE`, `RECONNECTING`, and `ENDED`. Binary client frames are PCM;
server messages remain JSON. `translated_audio.audio` is base64 `audio/mpeg`.
Queue returned MP3 clips in order rather than playing them simultaneously.

## Production configuration

Required application settings:

```text
USE_REDIS_CHANNEL_LAYER=True
REDIS_URL=redis://...
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=...
WEBRTC_STUN_URLS=stun:turn.example.com:3478
WEBRTC_TURN_URLS=turn:turn.example.com:3478?transport=udp,turns:turn.example.com:5349?transport=tcp
WEBRTC_TURN_USERNAME=...
WEBRTC_TURN_CREDENTIAL=...
WEBRTC_TURN_SECRET=...
```

Run coturn on a dedicated Azure VM/container host with a stable public IP and
DNS name. Open inbound UDP/TCP 3478, TCP 5349, and the configured UDP relay range
(the compose example uses 49160–49200). Set coturn's `external-ip` to the public
IP when it is behind Azure NAT, use a real TLS certificate for `turns:`, restrict
the relay range in both coturn and the Network Security Group, and source
credentials from Key Vault. When `WEBRTC_TURN_SECRET` is set, join responses use
coturn REST-compatible HMAC-SHA1 credentials that expire after one hour; configure
the same secret with coturn's `static-auth-secret`. Static username/password
settings remain available for local development only, where the compose default
password is not production-safe.

Scale all ASGI instances against the same Redis channel layer. Translation runs
inside an ASGI worker today, so size worker concurrency for two recognizers per
call and monitor CPU, socket counts, Azure throttling, phrase latency, and
cancellation rates.

Production must serve `meritlense.asgi:application`, not the WSGI application.
The Docker image now uses Gunicorn's Uvicorn worker. The existing Azure VM
`gunicorn.service` must likewise point at the ASGI application with
`--worker-class uvicorn.workers.UvicornWorker`; a WSGI-only service cannot accept
WebSockets. Configure the reverse proxy/App Service WebSocket switch and use a
load-balancer idle timeout longer than the client's ping interval.

## Acceptance verification

Automated tests cover access separation, shared 1:1 room creation, ticket claims,
and independent language preferences. Production acceptance still requires two
real browsers on different networks, two genuinely different language pairs,
both users speaking over one another, audible translated MP3 in each direction,
and a forced network drop/reconnect. ICE connected by itself is not a pass.
Capture p50/p95 phrase-to-audio latency and evaluator-rated intelligibility for
the selected language pairs. This cannot be completed without deployed TURN,
Azure credentials, and the browser frontend.
