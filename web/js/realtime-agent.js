/* Ghost Realtime Voice Agent — BETA
 *
 * Establishes a WebRTC connection to OpenAI's Realtime API using an
 * ephemeral token minted by the Python side. The agent has voice I/O
 * (speaks AND listens) and can call Ghost actions through tool calls
 * that are routed to pywebview.api.* methods.
 *
 * Architecture:
 *   Python (api.py) -> mints ephemeral token via /v1/realtime/client_secrets
 *        |
 *        v
 *   Browser (this file) -> POST /v1/realtime?model=...  with SDP offer
 *        | (RTCPeerConnection + data channel)
 *        v
 *   OpenAI returns audio stream + JSON events over data channel.
 *   Tool-call events are dispatched to pywebview.api.* and their
 *   return values sent back as function_call_output events.
 *
 * Exposes window.GhostRealtime with start/stop/status.
 */
(function () {
    "use strict";

    // GA WebRTC endpoint. The older `/v1/realtime` was beta and rejects
    // client secrets minted via `/v1/realtime/client_secrets` (which is GA).
    // See https://platform.openai.com/docs/guides/realtime
    const REALTIME_URL = "https://api.openai.com/v1/realtime/calls";

    // Look up the Alpine app state on the main window. Window-state tools
    // (dock/maximize/restore) MUST go through Alpine methods instead of the
    // raw API: the Alpine methods toggle `docked` / `maximizedMode` flags
    // that drive the UI (e.g. the Ghost SVG shown when docked, the sidebar
    // labels in maximized mode). Going straight to pywebview.api moves the
    // Win32 window but leaves the Alpine state inconsistent — result is a
    // blank 56x56 box with no ghost logo when the agent docks.
    function getAlpineApp() {
        try {
            return window.Alpine?.$data(document.body) || null;
        } catch (_) {
            return null;
        }
    }

    // Map tool-names (as declared in the Python tool catalog) to either
    // a direct pywebview.api method name OR a custom async handler. Keeping
    // the mapping in JS lets us compose multiple API calls for high-level
    // tools (analyze_screen = capture + analyze) without new Python code.
    const TOOL_DISPATCH = {
        take_screenshot: async () => {
            // Route through Alpine so pendingCapture is set and the user
            // sees the thumbnail in the composer — same visual flow as
            // clicking the capture button manually.
            const app = getAlpineApp();
            if (app?.captureFullscreen) {
                await app.captureFullscreen();
                return {
                    ok: true,
                    message: "Screenshot capturada e visível no chat",
                    width: app.pendingCapture?.width,
                };
            }
            return pywebview.api.capture_fullscreen();
        },
        capture_region: async () => {
            const app = getAlpineApp();
            if (app?.captureArea) { await app.captureArea(); return { ok: true }; }
            return pywebview.api.capture_area();
        },
        scroll_capture: async (args) => {
            const app = getAlpineApp();
            if (app?.captureScroll) {
                if (args.monitor_index) app.selectedMonitor = args.monitor_index;
                if (args.max_scrolls)   app.maxScrolls = args.max_scrolls;
                await app.captureScroll();
                return { ok: true };
            }
            return pywebview.api.capture_with_scroll(
                args.monitor_index ?? 1,
                args.max_scrolls ?? 20,
            );
        },
        analyze_screen: async (args) => {
            // Capture visibly (updates UI) + then analyze with the preset.
            // The analyze call returns plain text — model narrates it back.
            const app = getAlpineApp();
            if (app?.captureFullscreen) {
                await app.captureFullscreen();
            } else {
                const shot = await pywebview.api.capture_fullscreen();
                if (shot?.error) return shot;
            }
            const preset = args.preset || "Descrever livremente";
            const extra = args.extra_text || "";
            return pywebview.api.analyze_last_capture(preset, extra);
        },
        // "Minimize" docks the Ghost to a 56x56 icon on the screen edge
        // rather than fully hiding it — when the agent is active, hiding
        // the window also hides the floating orb UI, leaving the user
        // with audio-only feedback and no way to bring the UI back except
        // Ctrl+Shift+G. Docking keeps a clickable presence on screen.
        minimize_window: async () => {
            const app = getAlpineApp();
            if (app?.dockToEdge) { await app.dockToEdge(); return { ok: true }; }
            return pywebview.api.minimize_to_edge();
        },
        // Explicit "make Ghost completely invisible" — only used when the
        // user asks for it explicitly (prompt in the tool description
        // instructs the model to warn about Ctrl+Shift+G first).
        hide_window: async () => pywebview.api.hide_app(),
        maximize_window: async () => {
            const app = getAlpineApp();
            if (app?.enterMaximized) { await app.enterMaximized(); return { ok: true }; }
            return pywebview.api.enter_maximized();
        },
        exit_maximized: async () => {
            const app = getAlpineApp();
            if (app?.exitMaximized) { await app.exitMaximized(); return { ok: true }; }
            return pywebview.api.exit_maximized();
        },
        dock_to_edge: async () => {
            const app = getAlpineApp();
            if (app?.dockToEdge) { await app.dockToEdge(); return { ok: true }; }
            return pywebview.api.minimize_to_edge();
        },
        restore_from_edge: async () => {
            const app = getAlpineApp();
            if (app?.expandFromEdge) { await app.expandFromEdge(); return { ok: true }; }
            return pywebview.api.restore_from_edge();
        },
        list_windows: async () => {
            const wins = await pywebview.api.list_windows();
            return { windows: (wins || []).slice(0, 20) };
        },
        list_monitors: async () => {
            const mons = await pywebview.api.get_monitors();
            return { monitors: mons || [] };
        },
        read_clipboard: async () => pywebview.api.read_clipboard(),
        open_url: async (args) => pywebview.api.open_url(args.url || ""),
        toggle_watch: async (args) => {
            // Alpine toggleWatch() flips the flag; if current state already
            // matches the requested one, do nothing so we don't toggle off
            // by accident.
            const app = getAlpineApp();
            if (app?.toggleWatch) {
                if (!!args.enabled !== !!app.watchEnabled) {
                    await app.toggleWatch();
                }
                return { ok: true, enabled: !!app.watchEnabled };
            }
            return pywebview.api.toggle_watch(!!args.enabled, args.interval ?? 3.0);
        },
        start_window_drag: async () => pywebview.api.start_window_drag(),
        set_capture_visibility: async (args) => {
            // Same guard as toggle_watch — Alpine method toggles, so we
            // only call it if the desired state differs.
            const app = getAlpineApp();
            if (app?.toggleCaptureVisibility) {
                if (!!args.visible !== !!app.captureVisible) {
                    await app.toggleCaptureVisibility();
                }
                return { ok: true, visible: !!app.captureVisible };
            }
            return pywebview.api.set_capture_visibility(!!args.visible);
        },
    };

    // ─── Internal session state ──────────────────────────────────
    let pc = null;            // RTCPeerConnection
    let dc = null;            // data channel (events)
    let localStream = null;   // mic MediaStream
    let remoteAudioEl = null; // <audio> playback element for model voice
    let active = false;
    let listeners = new Set(); // state-change listeners

    function emit(event, payload = {}) {
        for (const fn of listeners) {
            try { fn({ type: event, ...payload }); } catch (_) {}
        }
    }

    function ensureAudioElement() {
        if (remoteAudioEl) return remoteAudioEl;
        remoteAudioEl = document.createElement("audio");
        remoteAudioEl.autoplay = true;
        remoteAudioEl.playsInline = true;
        remoteAudioEl.style.display = "none";
        document.body.appendChild(remoteAudioEl);
        return remoteAudioEl;
    }

    async function handleToolCall(callId, name, argsRaw) {
        const fn = TOOL_DISPATCH[name];
        if (!fn) {
            sendFunctionOutput(callId, JSON.stringify({
                error: `Tool desconhecida: ${name}`,
            }));
            return;
        }

        let args = {};
        try {
            args = argsRaw ? JSON.parse(argsRaw) : {};
        } catch (e) {
            console.warn("[realtime] bad tool args:", argsRaw, e);
        }

        emit("tool_call_start", { name, args });
        let result;
        try {
            result = await fn(args);
        } catch (e) {
            result = { error: String(e?.message || e) };
        }
        emit("tool_call_done", { name, result });

        // Some pywebview results are bulky (base64 thumbnails). Trim those
        // before sending back — the model only needs the semantic result,
        // and shipping ~600KB of base64 per tool call burns tokens and
        // adds latency to the model's next reply.
        const trimmed = trimToolOutput(result);
        sendFunctionOutput(callId, JSON.stringify(trimmed));

        // Ask the model to generate its next response (speaks the outcome).
        dc?.send(JSON.stringify({ type: "response.create" }));
    }

    function trimToolOutput(r) {
        if (!r || typeof r !== "object") return r;
        const out = {};
        for (const k of Object.keys(r)) {
            const v = r[k];
            if (typeof v === "string" && v.length > 400) {
                // Likely a data URL or long text — summarize size.
                out[k] = `<truncated: ${v.length} chars>`;
            } else {
                out[k] = v;
            }
        }
        return out;
    }

    function sendFunctionOutput(callId, outputJson) {
        if (!dc || dc.readyState !== "open") {
            console.warn("[realtime] data channel not open, dropping tool output");
            return;
        }
        dc.send(JSON.stringify({
            type: "conversation.item.create",
            item: {
                type: "function_call_output",
                call_id: callId,
                output: outputJson,
            },
        }));
    }

    function handleServerEvent(evt) {
        // Known event types we care about — everything else is fine to ignore.
        switch (evt.type) {
            case "session.created":
            case "session.updated":
                emit("session_ready");
                break;

            case "input_audio_buffer.speech_started":
                emit("user_speech_start");
                break;

            case "input_audio_buffer.speech_stopped":
                emit("user_speech_stop");
                break;

            case "response.audio.delta":
                emit("assistant_speaking");
                break;

            case "response.output_item.done":
            case "response.done":
                emit("assistant_done");
                break;

            case "response.function_call_arguments.done": {
                // Final tool-call fire: { call_id, name, arguments }
                const callId = evt.call_id;
                const name = evt.name;
                const args = evt.arguments || "{}";
                handleToolCall(callId, name, args);
                break;
            }

            case "error": {
                console.error("[realtime] server error:", evt);
                const msg = evt.error?.message || String(evt);
                const param = evt.error?.param ? ` (param=${evt.error.param})` : "";
                pyLog("error", "server_event_error", msg + param);
                emit("error", { message: msg });
                break;
            }

            default:
                // Other events (rate_limits, transcript deltas, etc.)
                // are interesting for debugging only.
                // console.debug("[realtime]", evt.type, evt);
                break;
        }
    }

    function pyLog(level, stage, detail) {
        try {
            pywebview?.api?.realtime_log?.(
                level, stage, String(detail).slice(0, 800),
            );
        } catch (_) {}
    }

    async function start() {
        if (active) return { ok: true, already: true };

        try {
            // 1. Ask Python for an ephemeral token + tool catalog
            emit("connecting");
            pyLog("info", "mint_start", "calling realtime_create_session");
            const cfg = await pywebview.api.realtime_create_session();
            if (!cfg || cfg.error) {
                const msg = cfg?.error || "Falha ao criar sessão";
                pyLog("error", "mint_failed", msg);
                emit("error", { message: msg });
                return { error: msg };
            }
            pyLog("info", "mint_ok", `model=${cfg.model} tools=${cfg.tools?.length || 0}`);

            // 2. Grab mic
            pyLog("info", "mic_request", "navigator.mediaDevices.getUserMedia");
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                    },
                });
            } catch (mediaErr) {
                const detail = `${mediaErr?.name || "Error"}: ${mediaErr?.message || mediaErr}`;
                pyLog("error", "mic_denied", detail);
                throw new Error(`Acesso ao microfone negado: ${detail}`);
            }
            pyLog("info", "mic_ok", `tracks=${localStream.getTracks().length}`);

            // 3. Build RTCPeerConnection
            pc = new RTCPeerConnection();

            // Outbound: mic
            localStream.getTracks().forEach((t) => pc.addTrack(t, localStream));

            // Inbound: model voice
            const audio = ensureAudioElement();
            pc.ontrack = (e) => {
                if (e.streams && e.streams[0]) audio.srcObject = e.streams[0];
            };

            // Events data channel
            dc = pc.createDataChannel("oai-events");
            dc.addEventListener("open", () => {
                // Push the tool catalog + final instructions into the session.
                // The ephemeral token already carried instructions+tools at
                // mint time, but session.update is the canonical spot to
                // configure VAD, transcription, and tool_choice.
                //
                // Schema follows the GA realtime API (not the old beta):
                //   - session.type MUST be "realtime" (required on every update)
                //   - turn_detection lives under audio.input (not top-level)
                //   - transcription lives under audio.input (not top-level)
                // Sending the beta shape here returns
                // "Missing required parameter 'session.type'" as an error.
                dc.send(JSON.stringify({
                    type: "session.update",
                    session: {
                        type: "realtime",
                        instructions: cfg.instructions,
                        tools: cfg.tools,
                        tool_choice: "auto",
                        audio: {
                            input: {
                                turn_detection: {
                                    type: "server_vad",
                                    threshold: 0.5,
                                    prefix_padding_ms: 300,
                                    silence_duration_ms: 600,
                                },
                                transcription: { model: "whisper-1" },
                            },
                            output: {
                                voice: cfg.voice,
                            },
                        },
                    },
                }));
            });
            dc.addEventListener("message", (e) => {
                try { handleServerEvent(JSON.parse(e.data)); }
                catch (_) {}
            });
            dc.addEventListener("close", () => {
                emit("disconnected");
                cleanup();
            });
            dc.addEventListener("error", (e) => {
                console.error("[realtime] dc error:", e);
            });

            pc.onconnectionstatechange = () => {
                emit("pc_state", { state: pc.connectionState });
                if (pc.connectionState === "failed" ||
                    pc.connectionState === "disconnected" ||
                    pc.connectionState === "closed") {
                    cleanup();
                }
            };

            // 4. SDP handshake — POST offer to OpenAI with ephemeral token
            pyLog("info", "sdp_create", "createOffer + setLocalDescription");
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            const sdpUrl = `${REALTIME_URL}?model=${encodeURIComponent(cfg.model)}`;
            pyLog("info", "sdp_post", `POST ${sdpUrl}`);
            let sdpResp;
            try {
                sdpResp = await fetch(sdpUrl, {
                    method: "POST",
                    body: offer.sdp,
                    headers: {
                        Authorization: `Bearer ${cfg.token}`,
                        "Content-Type": "application/sdp",
                    },
                });
            } catch (fetchErr) {
                const detail = `${fetchErr?.name || "FetchError"}: ${fetchErr?.message || fetchErr}`;
                pyLog("error", "sdp_fetch_failed", detail);
                throw new Error(`Falha de rede no handshake WebRTC: ${detail}`);
            }
            if (!sdpResp.ok) {
                const body = await sdpResp.text().catch(() => "");
                pyLog("error", "sdp_http_error", `${sdpResp.status}: ${body.slice(0, 200)}`);
                throw new Error(`SDP HTTP ${sdpResp.status}: ${body.slice(0, 200)}`);
            }
            const answerSdp = await sdpResp.text();
            pyLog("info", "sdp_answer", `got ${answerSdp.length} bytes`);
            try {
                await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
            } catch (sdpErr) {
                const detail = `${sdpErr?.name || "Error"}: ${sdpErr?.message || sdpErr}`;
                pyLog("error", "sdp_set_remote_failed", detail);
                throw new Error(`SDP answer inválido: ${detail}`);
            }

            active = true;
            pyLog("info", "connected", `model=${cfg.model} voice=${cfg.voice}`);
            emit("connected", { model: cfg.model, voice: cfg.voice });
            return { ok: true };
        } catch (e) {
            console.error("[realtime] start failed:", e);
            pyLog("error", "start_failed", e?.message || e);
            cleanup();
            const msg = String(e?.message || e);
            emit("error", { message: msg });
            return { error: msg };
        }
    }

    function cleanup() {
        active = false;
        try { dc?.close(); } catch (_) {}
        dc = null;
        try { pc?.getSenders?.().forEach((s) => s.track && s.track.stop()); } catch (_) {}
        try { pc?.close(); } catch (_) {}
        pc = null;
        try { localStream?.getTracks().forEach((t) => t.stop()); } catch (_) {}
        localStream = null;
        if (remoteAudioEl) {
            try { remoteAudioEl.srcObject = null; } catch (_) {}
        }
        try { pywebview?.api?.realtime_end_session?.(); } catch (_) {}
    }

    async function stop() {
        cleanup();
        emit("disconnected");
        return { ok: true };
    }

    function onStateChange(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
    }

    function status() {
        return {
            active,
            connection: pc?.connectionState || "closed",
            channel: dc?.readyState || "closed",
        };
    }

    window.GhostRealtime = { start, stop, onStateChange, status };
})();
