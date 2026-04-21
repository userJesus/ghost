/* ============================================================
 * response.js — compact-mode response popup logic
 *
 * Extracted from web/response.html inline <script> block.
 * This is a STANDALONE pywebview window (not an Alpine component),
 * so it uses plain vanilla JS. The Python side calls the global
 * window.setMessages(messages) to update the rendered conversation.
 *
 * Loaded via <script src="js/response.js"></script> in response.html.
 * Byte-identical to the pre-extraction inline content.
 * ============================================================ */
        marked.setOptions({ breaks: true, gfm: true });

        let _ttsVoice = null;
        let _autoSpeak = false;
        let _speakingIdx = -1;
        let _lastMessagesCount = 0;

        function _initTTS() {
            if (!('speechSynthesis' in window)) return;
            const pick = () => {
                const voices = window.speechSynthesis.getVoices() || [];
                if (!voices.length) return;
                // Prefer neural/natural voices (muito melhor que SAPI padrão)
                _ttsVoice =
                    voices.find(v => /pt[-_]BR/i.test(v.lang) && /(natural|neural|online|francisca|antonio|thalita|heloís)/i.test(v.name)) ||
                    voices.find(v => /^pt/i.test(v.lang) && /(natural|neural|online)/i.test(v.name)) ||
                    voices.find(v => /pt[-_]BR/i.test(v.lang)) ||
                    voices.find(v => /^pt/i.test(v.lang)) ||
                    voices[0];
            };
            pick();
            window.speechSynthesis.onvoiceschanged = pick;
        }
        _initTTS();

        // ===== Global tooltip manager =====
        (function() {
            let tipEl = null, showTimer = null, currentEl = null;
            function getTip() {
                if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'gtooltip'; document.body.appendChild(tipEl); }
                return tipEl;
            }
            function show(el) {
                const text = el.getAttribute('data-tooltip');
                if (!text) return;
                const below = el.getAttribute('data-tooltip-pos') === 'below';
                const tip = getTip();
                tip.textContent = text;
                tip.style.left = '-9999px'; tip.style.top = '-9999px';
                tip.classList.add('visible');
                const rect = el.getBoundingClientRect();
                const tRect = tip.getBoundingClientRect();
                let left = rect.left + rect.width / 2 - tRect.width / 2;
                let top = below ? rect.bottom + 8 : rect.top - tRect.height - 8;
                left = Math.max(6, Math.min(left, window.innerWidth - tRect.width - 6));
                top = Math.max(6, Math.min(top, window.innerHeight - tRect.height - 6));
                tip.style.left = left + 'px'; tip.style.top = top + 'px';
            }
            function hide() { if (tipEl) tipEl.classList.remove('visible'); currentEl = null; clearTimeout(showTimer); }
            document.addEventListener('mouseover', (e) => {
                const el = e.target.closest('[data-tooltip]');
                if (!el) return;
                const text = el.getAttribute('data-tooltip');
                if (!text) { hide(); return; }
                if (el === currentEl) return;
                currentEl = el;
                clearTimeout(showTimer);
                showTimer = setTimeout(() => { if (currentEl === el) show(el); }, 400);
            });
            document.addEventListener('mouseout', (e) => {
                if (!currentEl) return;
                if (e.relatedTarget && currentEl.contains(e.relatedTarget)) return;
                const el = e.target.closest('[data-tooltip]');
                if (el && el !== currentEl) return;
                hide();
            });
            window.addEventListener('scroll', hide, true);
            window.addEventListener('resize', hide);
            document.addEventListener('mousedown', hide);
        })();

        try {
            _autoSpeak = localStorage.getItem('ghost_auto_speak') === '1';
            const btn = document.getElementById('autoSpeakBtn');
            if (btn && _autoSpeak) btn.classList.add('active-tts');
        } catch (e) {}

        // escapeHTML() and stripMarkdown() now come from web/shared/common.js
        // (loaded in <head>). Identical behavior; single source of truth.

        let _ttsAudio = null;

        function _speakText(text, idx) {
            // Browser speechSynthesis instantâneo (sem OpenAI round-trip)
            if (!('speechSynthesis' in window)) return false;
            window.speechSynthesis.cancel();
            const utter = new SpeechSynthesisUtterance(text);
            utter.lang = 'pt-BR';
            utter.rate = 1.0;
            utter.pitch = 1.0;
            if (_ttsVoice) utter.voice = _ttsVoice;
            utter.onend = () => {
                _speakingIdx = -1;
                _updateStopBtn();
                _refreshSpeakButtons();
            };
            utter.onerror = () => {
                _speakingIdx = -1;
                _updateStopBtn();
                _refreshSpeakButtons();
            };
            window.speechSynthesis.speak(utter);
            return true;
        }

        function _updateStopBtn() {
            const stopBtn = document.getElementById('stopBtn');
            if (!stopBtn) return;
            stopBtn.style.display = _speakingIdx >= 0 ? 'flex' : 'none';
        }

        function _refreshSpeakButtons() {
            document.querySelectorAll('.speak-btn').forEach(btn => {
                const idx = parseInt(btn.dataset.idx, 10);
                if (idx === _speakingIdx) {
                    btn.classList.add('speaking');
                    btn.innerHTML = `<span class="speak-bars"><span></span><span></span><span></span><span></span></span><span>Parar</span>`;
                } else {
                    btn.classList.remove('speaking');
                    btn.innerHTML = `<svg viewBox="0 0 14 14" fill="none"><path d="M5 4.5H3a1 1 0 00-1 1v3a1 1 0 001 1h2l3 2.5v-10L5 4.5z" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M10 5.2c.8.7.8 2.9 0 3.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg><span>Ouvir</span>`;
                }
            });
        }

        window.copyPopupMsg = async function(idx, btn) {
            try {
                const msgEl = btn.closest('.msg');
                const body = msgEl?.querySelector('.msg-assistant-body');
                const text = (body?.innerText || body?.textContent || '').trim();
                if (!text) return;
                await navigator.clipboard.writeText(text);
                const span = btn.querySelector('span');
                const orig = span.textContent;
                btn.classList.add('flash');
                span.textContent = 'Copiado';
                setTimeout(() => {
                    btn.classList.remove('flash');
                    span.textContent = orig;
                }, 1500);
            } catch (e) { console.warn('copy failed', e); }
        };

        window.copyPopupSel = async function(idx, btn) {
            try {
                const sel = window.getSelection();
                const selected = sel ? sel.toString().trim() : '';
                const span = btn.querySelector('span');
                if (!selected) {
                    const orig = span.textContent;
                    span.textContent = 'Arraste no texto primeiro';
                    setTimeout(() => { span.textContent = orig; }, 1500);
                    return;
                }
                const msgEl = btn.closest('.msg');
                if (msgEl && sel.rangeCount > 0) {
                    if (!msgEl.contains(sel.getRangeAt(0).commonAncestorContainer)) {
                        const orig = span.textContent;
                        span.textContent = 'Selecione dentro da resposta';
                        setTimeout(() => { span.textContent = orig; }, 1500);
                        return;
                    }
                }
                await navigator.clipboard.writeText(selected);
                const orig = span.textContent;
                btn.classList.add('flash');
                span.textContent = 'Trecho copiado';
                setTimeout(() => {
                    btn.classList.remove('flash');
                    span.textContent = orig;
                }, 1500);
            } catch (e) { console.warn('copy sel failed', e); }
        };

        window.branchFromPopup = async function(idx) {
            try {
                if (!window.pywebview?.api?.branch_main_conversation) {
                    console.warn('branch api not available');
                    return;
                }
                await window.pywebview.api.branch_main_conversation(idx);
                // O main window vai sair do compact + atualizar a conversa.
                // Fecha o popup pois o usuário verá o novo chat no main.
                try { window.closePopup && window.closePopup(); } catch (e) {}
            } catch (e) {
                console.warn('branch popup failed', e);
            }
        };

        window.toggleSpeakMsg = async function(idx, rawText) {
            if (_speakingIdx === idx) {
                if (_ttsAudio) { _ttsAudio.pause(); _ttsAudio = null; }
                window.speechSynthesis.cancel();
                _speakingIdx = -1;
                _updateStopBtn();
                _refreshSpeakButtons();
                return;
            }
            const text = stripMarkdown(rawText);
            if (!text) return;
            _speakingIdx = idx;
            _updateStopBtn();
            _refreshSpeakButtons();
            await _speakText(text, idx);
        };

        window.toggleAutoSpeak = function() {
            _autoSpeak = !_autoSpeak;
            try { localStorage.setItem('ghost_auto_speak', _autoSpeak ? '1' : '0'); } catch(e) {}
            const btn = document.getElementById('autoSpeakBtn');
            if (btn) btn.classList.toggle('active-tts', _autoSpeak);
            if (!_autoSpeak && _speakingIdx >= 0) {
                window.speechSynthesis.cancel();
                _speakingIdx = -1;
                _updateStopBtn();
                _refreshSpeakButtons();
            }
        };

        window.stopSpeaking = function() {
            if (_ttsAudio) { _ttsAudio.pause(); _ttsAudio = null; }
            window.speechSynthesis.cancel();
            _speakingIdx = -1;
            _updateStopBtn();
            _refreshSpeakButtons();
        };

        window.setPopupTitle = function(title) {
            const el = document.getElementById('popupTitle');
            if (!el) return;
            const t = (title || '').trim();
            // Trunca pra caber no header
            const shown = t.length > 40 ? t.slice(0, 37) + '…' : t;
            el.textContent = shown || 'Conversa';
        };

        window.setMessages = function(messages) {
            const chat = document.getElementById('chat');
            if (!messages || !messages.length) {
                chat.innerHTML = `<div class="empty"><div class="empty-icon pixel-ghost"></div><div>Nenhuma interação ainda</div><div style="font-size:11px;opacity:0.7">Digite algo na barra inferior</div></div>`;
                _lastMessagesCount = 0;
                return;
            }

            // Detect new completed assistant message for auto-speak
            const prevCount = _lastMessagesCount;
            _lastMessagesCount = messages.length;
            const last = messages[messages.length - 1];
            const newFinished = _autoSpeak
                && messages.length >= prevCount
                && last && last.role === 'assistant'
                && !last.loading
                && last.text
                && (window._lastSpokenText !== last.text);
            if (newFinished) {
                window._lastSpokenText = last.text;
                const idx = messages.length - 1;
                const text = stripMarkdown(last.text);
                if (text) {
                    _speakingIdx = idx;
                    _updateStopBtn();
                    _speakText(text, idx);
                }
            }

            const html = messages.map((m, idx) => {
                if (m.role === 'user') {
                    const img = m.image
                        ? `<img src="${m.image}" alt="">`
                        : '';
                    const transcriptBlock = m.transcript
                        ? `<div class="msg-transcript">
                             <div class="msg-transcript-head">
                                <svg viewBox="0 0 12 12" fill="none"><path d="M2 4h1.5l2.5-2v8L3.5 8H2a0.8 0.8 0 01-.8-0.8V4.8A0.8 0.8 0 012 4z" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/></svg>
                                <span>Trecho de áudio</span>
                             </div>
                             <div class="msg-transcript-body">${escapeHTML(m.transcript)}</div>
                           </div>`
                        : '';
                    return `<div class="msg"><div class="msg-user"><div class="msg-user-bubble">${img}${transcriptBlock}${escapeHTML(m.text || '')}</div></div></div>`;
                } else {
                    const isLoading = m.loading;
                    const needsKey = m.needsApiKey;
                    const bodyHtml = isLoading
                        ? '<div class="loading-dots"><span></span><span></span><span></span></div>'
                        : (needsKey
                            ? '<div style="padding:12px 14px;border-radius:10px;background:rgba(97,219,180,0.08);border:1px solid rgba(97,219,180,0.25);"><div style="font-weight:600;margin-bottom:4px;">🔑 Chave da OpenAI não configurada</div><div style="font-size:12px;opacity:0.85;line-height:1.5;">Volte ao Ghost em modo normal para configurar a chave nas opções.</div></div>'
                            : marked.parse(m.text || ''));
                    const hasText = !isLoading && !needsKey && m.text;
                    const actions = hasText
                        ? `<div class="msg-actions">
                             <button class="speak-btn" data-idx="${idx}" data-tooltip="Ler em voz alta" onclick='toggleSpeakMsg(${idx}, ${JSON.stringify(m.text)})'>
                               <svg viewBox="0 0 14 14" fill="none"><path d="M5 4.5H3a1 1 0 00-1 1v3a1 1 0 001 1h2l3 2.5v-10L5 4.5z" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/><path d="M10 5.2c.8.7.8 2.9 0 3.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/></svg>
                               <span>Ouvir</span>
                             </button>
                             <button class="msg-action-btn" data-idx="${idx}" data-tooltip="Copiar resposta inteira" onclick='copyPopupMsg(${idx}, this)'>
                               <svg viewBox="0 0 14 14" fill="none"><rect x="3" y="3" width="7" height="9" rx="1" stroke="currentColor" stroke-width="1.2"/><path d="M5 3V2a1 1 0 011-1h5a1 1 0 011 1v8a1 1 0 01-1 1h-1" stroke="currentColor" stroke-width="1.2"/></svg>
                               <span>Copiar</span>
                             </button>
                             <button class="msg-action-btn" data-idx="${idx}" data-tooltip="Arraste no texto pra selecionar, depois clique aqui" onmousedown="event.preventDefault()" onclick='copyPopupSel(${idx}, this)'>
                               <svg viewBox="0 0 14 14" fill="none"><path d="M3 3h8M3 6h8M3 9h5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><rect x="7" y="7" width="5" height="5" rx="0.5" stroke="currentColor" stroke-width="1.2"/></svg>
                               <span>Copiar trecho</span>
                             </button>
                             <button class="msg-action-btn" data-idx="${idx}" data-tooltip="Abrir novo chat com contexto desta conversa" onclick='branchFromPopup(${idx})'>
                               <svg viewBox="0 0 14 14" fill="none"><circle cx="3.5" cy="3" r="1.5" stroke="currentColor" stroke-width="1.2"/><circle cx="3.5" cy="11" r="1.5" stroke="currentColor" stroke-width="1.2"/><circle cx="10.5" cy="7" r="1.5" stroke="currentColor" stroke-width="1.2"/><path d="M3.5 4.5v5M5 3c2 0 4 1 5.5 3M5 11c2 0 4-1 5.5-3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
                               <span>Branch</span>
                             </button>
                           </div>`
                        : '';
                    return `<div class="msg"><div class="msg-assistant"><div class="msg-assistant-head"><div class="avatar pixel-ghost-mini"></div><span class="tag">ASSISTANT</span>${actions}</div><div class="msg-assistant-body">${bodyHtml}</div></div></div>`;
                }
            }).join('');
            chat.innerHTML = html;
            _injectCodeCopyButtons(chat);
            _refreshSpeakButtons();
            chat.scrollTop = chat.scrollHeight;
        };

        function _injectCodeCopyButtons(root) {
            // Adiciona botão "Copiar" em cada <pre> de code
            root.querySelectorAll('pre').forEach(pre => {
                if (pre.querySelector('.code-copy-popup-btn')) return;
                const btn = document.createElement('button');
                btn.className = 'code-copy-popup-btn';
                btn.innerHTML = '<svg viewBox="0 0 12 12" fill="none" width="10" height="10"><rect x="3" y="3" width="6" height="8" rx="0.8" stroke="currentColor" stroke-width="1.2"/><path d="M4.5 3V2a1 1 0 011-1h4a1 1 0 011 1v7a1 1 0 01-1 1h-1" stroke="currentColor" stroke-width="1.2"/></svg><span>Copiar</span>';
                btn.setAttribute('data-tooltip', 'Copiar código');
                btn.onclick = () => {
                    const code = pre.querySelector('code');
                    const text = (code?.textContent || code?.innerText || '').trim();
                    if (!text) return;
                    navigator.clipboard.writeText(text).then(() => {
                        const label = btn.querySelector('span');
                        const orig = label.textContent;
                        label.textContent = 'Copiado';
                        btn.classList.add('copied');
                        setTimeout(() => {
                            label.textContent = orig;
                            btn.classList.remove('copied');
                        }, 1400);
                    });
                };
                pre.appendChild(btn);

                // Lang badge via classe "language-X" no code
                const code = pre.querySelector('code');
                if (code) {
                    const lang = (code.className.match(/language-([a-z0-9+\-]+)/i) || [])[1];
                    if (lang) pre.setAttribute('data-lang', lang);
                }
            });
        }

        window.closePopup = function() {
            if (window.pywebview && window.pywebview.api) {
                window.pywebview.api.hide_response_popup();
            }
        };
