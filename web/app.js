function ghostApp() {
    return {
        // State
        presets: ['Responder pergunta'],
        monitors: [],
        selectedPreset: 'Responder pergunta',
        selectedMonitor: 1,
        captureMode: 'tela',
        maxScrolls: 40,
        inputText: '',
        messages: [],
        busy: false,
        ready: false,
        watchEnabled: false,
        meetingRunning: false,
        meetingProcessing: false,
        meetingElapsed: '00:00',
        meetingStatusText: '',
        meetingModalOpen: false,
        meetingTarget: '',
        availableWindows: [],
        _meetingTimer: null,
        statusMessage: '',
        _blurTimeout: null,
        docked: false,
        compactMode: false,
        kbdCapture: false,
        captureVisible: false,
        settingsModalOpen: false,
        settings: { has_openai_key: false, masked_key: "", openai_model: "", available_models: [] },
        appInfo: { version: "", author: "", authorGithub: "", authorLinkedin: "", repoUrl: "", releasesUrl: "" },
        updateInfo: { hasUpdate: false, current: "", latest: "", releaseUrl: "", releaseNotes: "" },
        updateBannerDismissed: false,
        openaiKeyInput: "",
        savingKey: false,
        settingsError: "",
        settingsSuccess: "",
        keyPermissions: null,
        keyWarnings: [],
        speakingIdx: -1,
        autoSpeak: false,
        _ttsVoice: null,
        voiceRecording: false,      // ativo enquanto grava
        voiceSource: '',            // 'mic' | 'system' atualmente gravando
        voiceTranscribing: false,   // ativo enquanto o Whisper processa
        voiceElapsed: '0:00',
        _voiceTimer: null,
        pendingCapture: null,       // { thumbnail, label } — imagem capturada aguardando pergunta do usuário
        systemTranscript: '',       // contexto transcrito do áudio do sistema
                                    // (aparece acima do input pra usuário
                                    // combinar com a pergunta dele)
        copiedIdx: -1,              // idx da msg onde "Copiado" apareceu (flash)
        copiedSelIdx: -1,           // idx da msg onde "Trecho copiado" apareceu
        selectModeIdx: -1,          // idx da msg em modo de seleção ativo
        modelSaveMsg: '',           // msg de confirmação ao trocar modelo
        shortcutsModalOpen: false,  // modal de atalhos do sistema
        closeConfirmOpen: false,    // modal de confirmação ao clicar X
        // ===== Histórico =====
        currentConvId: '',          // id da conversa atual
        currentConvTitle: '',       // título dinâmico (usado no popup também)
        _titledAtMsgCount: 0,       // quantas msgs a conversa tinha no último refresh de título
        historyModalOpen: false,
        historyList: [],
        _savingHistory: false,
        // ===== Streaming =====
        _currentStreamId: '',
        streamingEnabled: true,     // feature flag (pode desativar via config)
        // ===== Drag-and-drop =====
        droppedFiles: [],           // arquivos pendentes {kind, filename, content?/data_url?}
        isDragOver: false,
        // ===== Live Q&A meeting =====
        meetingQuestion: '',
        meetingQaAsking: false,
        // ===== Sensitive info =====
        sensitiveWarning: null,     // { types: [...], pending: { question, ctx } }
        currency: 'USD',            // moeda de exibição (além do USD base)
        exchangeRate: 1.0,          // taxa atual USD → currency
        exchangeRateDate: '',       // data da cotação (ex: 2026-04-17)
        exchangeLoading: false,
        currencyOpen: false,        // controla o dropdown custom
        _currencySupport: [
            { code: 'USD', label: 'Dólar (USD)',     symbol: '$'   },
            { code: 'BRL', label: 'Real (BRL)',      symbol: 'R$'  },
            { code: 'EUR', label: 'Euro (EUR)',      symbol: '€'   },
            { code: 'GBP', label: 'Libra (GBP)',     symbol: '£'   },
            { code: 'JPY', label: 'Iene (JPY)',      symbol: '¥'   },
            { code: 'ARS', label: 'Peso Arg. (ARS)', symbol: 'AR$' },
            { code: 'MXN', label: 'Peso Mex. (MXN)', symbol: 'MX$' },
        ],
        presetOpen: false,
        captureModeOpen: false,
        monitorOpen: false,
        captureModes: [
            { value: 'tela', icon: '📷', label: 'Tela inteira' },
            { value: 'area', icon: '✂', label: 'Selecionar área' },
            { value: 'scroll', icon: '📜', label: 'Rolagem de página' },
        ],

        // Computed
        get latestAssistantMessage() {
            for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].role === 'assistant') return this.messages[i];
            }
            return null;
        },
        get captureIcon() {
            return { tela: '◳', area: '▱', scroll: '⬇' }[this.captureMode];
        },
        get captureModeLabel() {
            const m = this.captureModes.find(c => c.value === this.captureMode);
            return m ? m.label : '';
        },
        get captureModeIcon() {
            const m = this.captureModes.find(c => c.value === this.captureMode);
            return m ? m.icon : '';
        },
        get selectedMonitorLabel() {
            const m = this.monitors.find(x => x.index === this.selectedMonitor);
            return m ? m.label : `Monitor ${this.selectedMonitor}`;
        },
        get modelsByTier() {
            const all = this.settings?.available_models || [];
            const groups = { economy: [], balanced: [], flagship: [] };
            all.forEach(m => { if (groups[m.tier]) groups[m.tier].push(m); });
            return [
                { tier: 'economy',  label: 'Mais baratos',      icon: '💸', models: groups.economy },
                { tier: 'balanced', label: 'Custo-benefício',   icon: '⚖',  models: groups.balanced },
                { tier: 'flagship', label: 'Melhor qualidade',  icon: '💎', models: groups.flagship },
            ].filter(g => g.models.length > 0);
        },
        get currencySymbol() {
            const c = this._currencySupport.find(x => x.code === this.currency);
            return c ? c.symbol : this.currency;
        },
        get exchangeRateDateBR() {
            // Converte YYYY-MM-DD → DD/MM/YYYY
            if (!this.exchangeRateDate) return '';
            const parts = this.exchangeRateDate.split('-');
            if (parts.length !== 3) return this.exchangeRateDate;
            return `${parts[2]}/${parts[1]}/${parts[0]}`;
        },
        get exchangeRateShort() {
            // Formato exibido no pill: "R$ 4,98" (2 decimais, vírgula BR)
            if (!this.exchangeRate || this.currency === 'USD') return '';
            const val = this.exchangeRate.toFixed(this.exchangeRate < 1 ? 4 : 2).replace('.', ',');
            return `${this.currencySymbol} ${val}`;
        },
        formatUsd(cost) {
            // Sempre em USD, formato consistente
            return '$' + cost.toFixed(cost < 0.1 ? 3 : 2);
        },
        formatConverted(cost) {
            // Se moeda é USD, retorna string vazia (não duplica)
            if (this.currency === 'USD') return '';
            const converted = cost * (this.exchangeRate || 1);
            const dec = converted < 0.1 ? 3 : 2;
            return this.currencySymbol + converted.toFixed(dec).replace('.', ',');
        },
        closeAllDropdowns() {
            this.presetOpen = false;
            this.captureModeOpen = false;
            this.monitorOpen = false;
        },
        get captureTooltip() {
            return {
                tela: 'Capturar tela inteira',
                area: 'Selecionar área da tela',
                scroll: 'Capturar página com scroll automático',
            }[this.captureMode];
        },
        get meetingLabel() {
            if (this.meetingProcessing) return 'Processando…';
            if (this.meetingRunning) return 'Encerrar reunião';
            return 'Iniciar reunião';
        },
        get meetingTooltip() {
            if (this.meetingProcessing) return 'Gerando transcrição + resumo no desktop';
            if (this.meetingRunning) return 'Parar de gravar e gerar documento';
            return 'Iniciar gravação de reunião (áudio + screenshots + resumo ao final)';
        },

        // --- Init ---
        async init() {
            try {
                marked.setOptions({ breaks: true, gfm: true });
                this.setStatus('Carregando...');

                // Expose a global key handler so Python can inject keystrokes via evaluate_js
                window.ghostKey = (payload) => this.handleGlobalKey(payload);

                await this._waitForApi();

                // get_settings com retry — o bridge pywebview às vezes não está
                // 100% pronto no primeiro tick mesmo após _waitForApi, então
                // tentamos até 5x com pausa curta antes de desistir.
                let settingsOk = false;
                for (let attempt = 0; attempt < 5; attempt++) {
                    try {
                        const r = await window.pywebview.api.get_settings();
                        if (r && typeof r.has_openai_key === 'boolean') {
                            this.settings = r;
                            settingsOk = true;
                            break;
                        }
                    } catch (e) {
                        console.warn('[init] get_settings falhou (tentativa ' + (attempt + 1) + '):', e);
                    }
                    await new Promise(r => setTimeout(r, 150));
                }
                if (!settingsOk) {
                    console.error('[init] get_settings nunca retornou válido — assumindo sem chave');
                }

                // Auto-open settings modal on first run if no API key configured
                if (!this.settings.has_openai_key) {
                    this.settingsModalOpen = true;
                }

                // Load app metadata (version, author) + kick off update check.
                try {
                    const info = await window.pywebview.api.get_app_info();
                    if (info && !info.error) this.appInfo = info;
                } catch (_) { /* offline or bridge hiccup */ }
                this._scheduleUpdateCheck();

                try {
                    this.presets = await window.pywebview.api.get_presets();
                    this.monitors = await window.pywebview.api.get_monitors();
                } catch (e) {
                    this.setStatus('Falha ao carregar config');
                }

                await this.$nextTick();

                if (this.presets.length) this.selectedPreset = this.presets[0];
                if (this.monitors.length) this.selectedMonitor = this.monitors[0].index;

                await this.$nextTick();

                const savedVis = localStorage.getItem('ghost_capture_visible') === '1';
                if (savedVis) {
                    try {
                        await window.pywebview.api.set_capture_visibility(true);
                        this.captureVisible = true;
                    } catch (e) {}
                }

                this.autoSpeak = localStorage.getItem('ghost_auto_speak') === '1';
                try { this._initTTS(); } catch (e) { console.error('[TTS init]', e); }

                // Setup streaming handlers globais + drag-drop
                this._setupStreamingGlobals();
                this._setupDragAndDrop();

                // Carrega moeda preferida (faz fetch da taxa se não for USD)
                const savedCur = localStorage.getItem('ghost_currency');
                if (savedCur && savedCur !== 'USD') {
                    this.setCurrency(savedCur).catch(() => {});
                }

                this.ready = true;
                this.setStatus('Pronto');
            } catch (err) {
                console.error('[init FATAL]', err);
                this.ready = true;
                this.setStatus('Erro no init: ' + (err?.message || err));
                // Remove x-cloak manualmente pra garantir que UI apareça
                document.body.removeAttribute('x-cloak');
            }
        },

        // --- Voice input (mic/system → Whisper → input field) ---
        async toggleVoice(source) {
            // Mesmo source clicado 2x = para. Source diferente enquanto outro grava = troca.
            if (this.voiceRecording) {
                if (this.voiceSource === source) {
                    await this._stopVoiceAndTranscribe();
                } else {
                    // Troca de fonte: cancela atual e inicia a nova
                    await this._cancelVoice();
                    await this._startVoice(source);
                }
                return;
            }
            await this._startVoice(source);
        },

        async _startVoice(source) {
            if (this.voiceRecording || this.voiceTranscribing) return;
            try {
                const r = await window.pywebview.api.voice_start(source);
                if (!r || r.error) {
                    this.setStatus('Erro: ' + (r?.error || 'falha ao iniciar gravação'));
                    return;
                }
                this.voiceRecording = true;
                this.voiceSource = source;
                this.voiceElapsed = '0:00';
                const t0 = Date.now();
                this._voiceTimer = setInterval(() => {
                    const s = Math.floor((Date.now() - t0) / 1000);
                    this.voiceElapsed = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
                }, 500);
                this.setStatus(source === 'mic' ? 'Gravando voz...' : 'Gravando áudio do sistema...');
            } catch (e) {
                this.setStatus('Erro ao iniciar: ' + (e?.message || e));
            }
        },

        async _stopVoiceAndTranscribe() {
            if (!this.voiceRecording) return;
            const source = this.voiceSource;
            clearInterval(this._voiceTimer);
            this._voiceTimer = null;
            this.voiceRecording = false;
            this.voiceTranscribing = true;
            this.setStatus('Transcrevendo...');
            try {
                const r = await window.pywebview.api.voice_stop_and_transcribe();
                if (!r || r.error) {
                    this.setStatus('Erro: ' + (r?.error || 'transcrição falhou'));
                    return;
                }
                const text = (r.text || '').trim();
                if (!text) {
                    this.setStatus('Nada foi ouvido');
                    return;
                }

                if (source === 'system') {
                    // Áudio do sistema é CONTEXTO — aparece num painel separado
                    // acima do input pra usuário combinar com pergunta dele
                    if (this.systemTranscript.trim()) {
                        // Concatena transcrições sucessivas
                        this.systemTranscript = this.systemTranscript.trim() + ' ' + text;
                    } else {
                        this.systemTranscript = text;
                    }
                    this.setStatus('Contexto pronto — digite sua pergunta');
                } else {
                    // Mic = pergunta do usuário, vai direto pro input
                    if (this.inputText.trim()) {
                        this.inputText = this.inputText.trim() + ' ' + text;
                    } else {
                        this.inputText = text;
                    }
                    this.setStatus('Pronto — revise e envie');
                }
            } catch (e) {
                this.setStatus('Erro: ' + (e?.message || e));
            } finally {
                this.voiceTranscribing = false;
                this.voiceSource = '';
            }
        },

        clearSystemTranscript() {
            this.systemTranscript = '';
        },

        _isApiKeyError(err) {
            if (!err) return false;
            const s = String(err).toLowerCase();
            return /api.?key|chave|unauthorized|não configurada|not configured|401/.test(s);
        },

        openSettingsFromNotice() {
            this.settingsModalOpen = true;
            this.loadSettings();
        },

        get apiKeyMissing() {
            return !this.settings?.has_openai_key;
        },
        get apiLockTooltip() {
            return 'Configure sua chave OpenAI primeiro (⚙)';
        },

        async setCurrency(code) {
            this.currency = code;
            this.currencyOpen = false;
            localStorage.setItem('ghost_currency', code);
            if (code === 'USD') {
                this.exchangeRate = 1.0;
                this.exchangeRateDate = '';
                return;
            }
            this.exchangeLoading = true;
            try {
                // frankfurter.dev — API pública de câmbio baseada em taxas
                // do Banco Central Europeu (ECB), atualizada diariamente.
                const r = await fetch(`https://api.frankfurter.dev/v1/latest?from=USD&to=${code}`,
                                       { cache: 'no-store' });
                if (!r.ok) throw new Error('HTTP ' + r.status);
                const data = await r.json();
                this.exchangeRate = (data?.rates?.[code]) || 1.0;
                this.exchangeRateDate = data?.date || '';
                console.log(`[currency] 1 USD = ${this.exchangeRate} ${code} (cotação ${this.exchangeRateDate})`);
            } catch (e) {
                console.warn('[currency] fetch falhou, usando 1.0:', e);
                this.exchangeRate = 1.0;
                this.exchangeRateDate = '';
            } finally {
                this.exchangeLoading = false;
            }
        },

        async selectModel(modelId) {
            this.modelSaveMsg = '';
            try {
                const r = await window.pywebview.api.set_openai_model(modelId);
                if (!r || r.error) {
                    this.modelSaveMsg = 'Erro: ' + (r?.error || 'falha ao salvar');
                    return;
                }
                this.settings.openai_model = r.openai_model;
                this.modelSaveMsg = 'Modelo atualizado: ' + r.openai_model;
                setTimeout(() => { this.modelSaveMsg = ''; }, 2000);
            } catch (e) {
                this.modelSaveMsg = 'Erro: ' + (e?.message || e);
            }
        },

        // --- Branch: resume o contexto até a mensagem selecionada e abre novo chat ---
        async branchFromMessage(idx) {
            console.log('[branch] clicked idx=', idx, 'msgs=', this.messages.length);
            if (idx < 0 || idx >= this.messages.length) {
                console.warn('[branch] idx inválido');
                return;
            }
            if (this.busy) {
                this.setStatus('Aguarde a resposta atual terminar');
                return;
            }
            try {
                // 1. Persiste a conversa atual antes de trocar
                if (this.messages.length > 0) {
                    try { await this._persistHistory(); } catch (e) {}
                }

                // 2. Serializa mensagens até idx (inclusive) pra enviar pro resumo
                const contextMsgs = this.messages.slice(0, idx + 1)
                    .filter(m => (m.text || '').trim() || m.transcript)
                    .map(m => ({
                        role: m.role,
                        text: m.transcript
                            ? `[trecho transcrito] ${m.transcript}\n\n${m.text || ''}`
                            : (m.text || ''),
                    }));

                if (!contextMsgs.length) {
                    this.setStatus('Nada pra resumir');
                    return;
                }

                // 3. Estado de loading — mostra card visível enquanto IA resume
                this.busy = true;
                const origMessages = this.messages;
                this.messages = [{
                    role: 'assistant',
                    text:
                        '**🌿 Criando branch da conversa…**\n\n' +
                        '*Estou resumindo o contexto das mensagens anteriores ' +
                        'para que o novo chat comece com tudo que você precisa. ' +
                        'Aguarde um instante…*',
                    loading: false,
                    isBranchSummary: true,
                    streaming: true,  // usa o pulse do streaming pra dar vida
                }];
                this.setStatus('Resumindo contexto…');
                await this.$nextTick();

                // 4. Chama o resumidor no Python
                let summary = '';
                try {
                    const r = await window.pywebview.api.branch_summarize(contextMsgs);
                    if (r?.error) {
                        // Reverte e mostra erro
                        this.messages = origMessages;
                        this.busy = false;
                        this.setStatus('Erro ao resumir: ' + r.error);
                        return;
                    }
                    summary = r?.summary || '';
                } catch (e) {
                    this.messages = origMessages;
                    this.busy = false;
                    this.setStatus('Erro ao resumir: ' + (e?.message || e));
                    return;
                }

                // 5. Gera novo ID
                let newId = '';
                try {
                    const r = await window.pywebview.api.history_new_id();
                    newId = (r && r.id) || ('conv-' + Date.now());
                } catch (e) {
                    newId = 'conv-' + Date.now();
                }

                // 6. Monta a mensagem-aviso com o resumo formatado
                const noticeText =
                    '**🌿 Continuação de conversa anterior**\n\n' +
                    '*Resumo do contexto até aqui:*\n\n' +
                    summary +
                    '\n\n---\n\n' +
                    '*Continue sua pergunta abaixo — tenho esse contexto em mente.*';

                const newMsgs = [{
                    role: 'assistant',
                    text: noticeText,
                    isBranchSummary: true,
                }];

                // 7. Troca para nova conversa
                this.currentConvId = newId;
                this.currentConvTitle = '';
                this.messages = newMsgs;
                this.selectModeIdx = -1;
                this.copiedIdx = -1;
                this.copiedSelIdx = -1;
                this.speakingIdx = -1;
                this.busy = false;
                this._titleRequested = false;
                this._titledAtMsgCount = 0;
                this._syncPopupTitle();

                // 8. Reseta o histórico do servidor e injeta o resumo como
                // mensagem de sistema — assim as próximas respostas têm contexto.
                try {
                    await window.pywebview.api.branch_reset_history(summary);
                } catch (e) { console.warn('[branch] reset history falhou:', e); }

                // 9. Persiste a nova conv no histórico local
                try {
                    await window.pywebview.api.history_save(newId, newMsgs);
                } catch (e) {}

                this.setStatus('✨ Novo chat criado com resumo do contexto');
                await this.$nextTick();
            } catch (e) {
                console.error('[branch] erro:', e);
                this.busy = false;
                this.setStatus('Erro ao criar branch: ' + (e?.message || e));
            }
        },

        // --- Copiar resposta ---
        async copyAssistantMessage(idx, event) {
            const msg = this.messages[idx];
            if (!msg || msg.role !== 'assistant' || !msg.text) return;
            try {
                // Copia texto plano (sem markdown tags) usando o body renderizado
                let text = msg.text;
                try {
                    const btn = event?.currentTarget;
                    const msgRoot = btn?.closest('.msg');
                    const body = msgRoot?.querySelector('.msg-assistant-body');
                    if (body) text = body.innerText || body.textContent || msg.text;
                } catch (e) {}
                await navigator.clipboard.writeText(text.trim());
                this.copiedIdx = idx;
                setTimeout(() => { if (this.copiedIdx === idx) this.copiedIdx = -1; }, 1500);
            } catch (e) {
                this.setStatus('Erro ao copiar: ' + (e?.message || e));
            }
        },

        async toggleSelectMode(idx, event) {
            // 1º click: entra em modo de seleção (usuário arrasta pra marcar trecho)
            // 2º click: copia o trecho selecionado e sai do modo
            if (this.selectModeIdx !== idx) {
                this.selectModeIdx = idx;
                this.setStatus('Arraste no texto pra selecionar o trecho, depois clique em "Copiar seleção"');
                return;
            }

            // Já está em modo seleção → tenta copiar
            try {
                const sel = window.getSelection();
                const selected = sel ? sel.toString().trim() : '';
                if (!selected) {
                    this.setStatus('Arraste no texto pra selecionar algo primeiro');
                    return;
                }
                const btn = event?.currentTarget;
                const msgRoot = btn?.closest('.msg');
                if (msgRoot && sel.rangeCount > 0) {
                    const range = sel.getRangeAt(0);
                    if (!msgRoot.contains(range.commonAncestorContainer)) {
                        this.setStatus('A seleção está fora dessa resposta');
                        return;
                    }
                }
                await navigator.clipboard.writeText(selected);
                this.copiedSelIdx = idx;
                this.selectModeIdx = -1;
                sel?.removeAllRanges();
                setTimeout(() => { if (this.copiedSelIdx === idx) this.copiedSelIdx = -1; }, 1500);
            } catch (e) {
                this.setStatus('Erro ao copiar: ' + (e?.message || e));
            }
        },

        async _cancelVoice() {
            clearInterval(this._voiceTimer);
            this._voiceTimer = null;
            this.voiceRecording = false;
            this.voiceSource = '';
            try { await window.pywebview.api.voice_cancel(); } catch (e) {}
        },

        // --- TTS (text-to-speech) ---
        _initTTS() {
            if (!('speechSynthesis' in window)) return;
            const pick = () => {
                const voices = window.speechSynthesis.getVoices() || [];
                if (!voices.length) return;

                // DEBUG: listar todas vozes disponíveis no console + na window
                const ptVoices = voices.filter(v => /^pt/i.test(v.lang));
                console.log('[TTS] Todas vozes pt:', ptVoices.map(v => `${v.name} (${v.lang})`).join(' | '));
                window.__ghostVoices = voices;

                // Preferir vozes Natural/Neural/Online (qualidade humana)
                // Evitar explicitamente Maria/Daniel (SAPI, extremamente robóticas)
                const isRobotic = (name) => /(maria|daniel)\b/i.test(name) && !/natural|neural|online/i.test(name);

                this._ttsVoice =
                    voices.find(v => /pt[-_]BR/i.test(v.lang) && /natural|neural/i.test(v.name)) ||
                    voices.find(v => /pt[-_]BR/i.test(v.lang) && /online/i.test(v.name)) ||
                    voices.find(v => /pt[-_]BR/i.test(v.lang) && /francisca|antonio|thalita|heloís/i.test(v.name)) ||
                    voices.find(v => /^pt/i.test(v.lang) && /natural|neural|online/i.test(v.name)) ||
                    voices.find(v => /pt[-_]BR/i.test(v.lang) && !isRobotic(v.name)) ||
                    voices.find(v => /^pt/i.test(v.lang) && !isRobotic(v.name)) ||
                    voices.find(v => /pt[-_]BR/i.test(v.lang)) ||
                    voices.find(v => /^pt/i.test(v.lang)) ||
                    voices[0];

                if (this._ttsVoice) {
                    const isNeural = /natural|neural|online/i.test(this._ttsVoice.name);
                    console.log('[TTS] Voz escolhida:', this._ttsVoice.name, this._ttsVoice.lang,
                                isNeural ? '✓ NEURAL' : '⚠ SAPI (pode soar robótica)');
                    if (!isNeural) {
                        console.warn('[TTS] Sistema não tem vozes Neural pt-BR instaladas. ' +
                                     'Instale em: Windows Settings → Hora e Idioma → Fala → Adicionar vozes → pt-BR (Natural)');
                    }
                }
            };
            pick();
            window.speechSynthesis.onvoiceschanged = pick;
        },

        _stripMarkdown(md) {
            if (!md) return '';
            try {
                const html = marked.parse(md);
                const div = document.createElement('div');
                div.innerHTML = html;
                // Skip code blocks to avoid reading syntax out loud
                div.querySelectorAll('pre, code').forEach(el => {
                    el.replaceWith(document.createTextNode(' código omitido. '));
                });
                return (div.textContent || '').replace(/\s+/g, ' ').trim();
            } catch (e) {
                return md.replace(/[*_`#>\-\[\]()]/g, ' ').replace(/\s+/g, ' ').trim();
            }
        },

        async _speakText(text, idx) {
            // Browser speechSynthesis = INSTANTÂNEO (sem round-trip de rede)
            // OpenAI TTS foi removido por causar ~1-2s de latência que o usuário
            // rejeitou. A voz vai ser a melhor Natural/Neural disponível no sistema
            // (see _initTTS).
            return this._speakWithBrowser(text);
        },

        _speakWithBrowser(text) {
            if (!('speechSynthesis' in window)) return false;
            window.speechSynthesis.cancel();
            const utter = new SpeechSynthesisUtterance(text);
            utter.lang = 'pt-BR';
            utter.rate = 1.0;
            utter.pitch = 1.0;
            utter.volume = 1.0;
            if (this._ttsVoice) utter.voice = this._ttsVoice;
            utter.onend = () => { this.speakingIdx = -1; };
            utter.onerror = () => { this.speakingIdx = -1; };
            window.speechSynthesis.speak(utter);
            return true;
        },

        async _speakWithOpenAI(text, idx) {
            try {
                // Stop any ongoing audio
                if (this._ttsAudio) {
                    this._ttsAudio.pause();
                    this._ttsAudio = null;
                }
                window.speechSynthesis?.cancel();

                const t0 = performance.now();
                const r = await window.pywebview.api.openai_tts(text);
                const dt = Math.round(performance.now() - t0);
                console.log('[TTS OpenAI] API respondeu em', dt, 'ms', r?.ok ? 'OK' : 'FALHOU', r?.error || '');
                if (!r || !r.ok || !r.audio_url) {
                    if (r?.error) this.setStatus('TTS erro: ' + r.error);
                    return false;
                }

                const audio = new Audio(r.audio_url);
                this._ttsAudio = audio;
                audio.onended = () => {
                    if (this._ttsAudio === audio) this._ttsAudio = null;
                    this.speakingIdx = -1;
                };
                audio.onerror = (e) => {
                    console.error('[TTS] audio error:', e);
                    if (this._ttsAudio === audio) this._ttsAudio = null;
                    this.speakingIdx = -1;
                };
                try {
                    await audio.play();
                } catch (playErr) {
                    console.error('[TTS] audio.play() rejeitou:', playErr);
                    this.setStatus('Autoplay bloqueado — clique em Ouvir manualmente');
                    return false;
                }
                return true;
            } catch (e) {
                console.warn('[TTS OpenAI] exceção:', e);
                return false;
            }
        },

        _stopSpeaking() {
            if (this._ttsAudio) {
                this._ttsAudio.pause();
                this._ttsAudio = null;
            }
            if ('speechSynthesis' in window) window.speechSynthesis.cancel();
            this.speakingIdx = -1;
        },

        async toggleSpeak(idx) {
            const msg = this.messages[idx];
            if (!msg || msg.role !== 'assistant' || msg.loading) return;

            if (this.speakingIdx === idx) {
                this._stopSpeaking();
                return;
            }

            const text = this._stripMarkdown(msg.text);
            if (!text) { this.setStatus('Nada para ler'); return; }
            // Feedback instantâneo: botão vira "Parar" na hora, mesmo que
            // o OpenAI demore ~1s pra começar o áudio de verdade.
            this.speakingIdx = idx;
            const ok = await this._speakText(text, idx);
            // Se falhou E a seleção ainda é esse idx (usuário não cancelou), limpa
            if (!ok && this.speakingIdx === idx) this.speakingIdx = -1;
        },

        toggleAutoSpeak() {
            this.autoSpeak = !this.autoSpeak;
            localStorage.setItem('ghost_auto_speak', this.autoSpeak ? '1' : '0');
            this.setStatus(this.autoSpeak ? 'Leitura automática ativa' : 'Leitura automática desativada');
            if (!this.autoSpeak && this.speakingIdx >= 0) {
                this._stopSpeaking();
            }
        },

        async _maybeAutoSpeak() {
            if (!this.autoSpeak) return;
            const lastIdx = this.messages.length - 1;
            const msg = this.messages[lastIdx];
            if (!msg || msg.role !== 'assistant' || msg.loading || !msg.text) return;
            const text = this._stripMarkdown(msg.text);
            if (!text) return;
            this.speakingIdx = lastIdx;
            const ok = await this._speakText(text, lastIdx);
            if (!ok && this.speakingIdx === lastIdx) this.speakingIdx = -1;
        },

        async openUrl(url) {
            console.log('[openUrl] calling', url);
            try {
                const r = await window.pywebview.api.open_url(url);
                console.log('[openUrl] result', r);
            } catch (e) {
                console.error('[openUrl] failed', e);
            }
        },

        async loadSettings() {
            try {
                this.settings = await window.pywebview.api.get_settings() || this.settings;
                this.settingsError = "";
                this.settingsSuccess = "";
                this.openaiKeyInput = "";
            } catch (e) {}
        },

        async pasteKey() {
            try {
                const r = await window.pywebview.api.read_clipboard();
                if (r?.error) {
                    this.settingsError = 'Erro lendo clipboard: ' + r.error;
                    return;
                }
                const text = (r?.text || '').trim();
                if (!text) {
                    this.settingsError = 'Clipboard vazio. Copie sua chave primeiro.';
                    return;
                }
                this.openaiKeyInput = text;
                this.settingsError = "";
                if (text.startsWith('sk-')) {
                    this.settingsSuccess = 'Chave colada. Clique em Salvar.';
                } else {
                    this.settingsError = 'Conteúdo do clipboard não parece uma chave (deve começar com "sk-")';
                }
            } catch (e) {
                this.settingsError = 'Falha ao colar: ' + e;
            }
        },

        async saveKey(replaceExisting = false) {
            const key = (this.openaiKeyInput || "").trim();
            if (!key || this.savingKey) return;
            this.savingKey = true;
            this.settingsError = "";
            this.settingsSuccess = "";
            this.keyPermissions = null;
            this.keyWarnings = [];
            try {
                const result = await window.pywebview.api.save_openai_key(key, replaceExisting);
                if (result?.replace_required) {
                    if (confirm('Já existe uma chave configurada. Substituir pela nova?')) {
                        this.savingKey = false;
                        return this.saveKey(true);
                    }
                    this.settingsError = result.error;
                } else if (result?.error) {
                    this.settingsError = result.error;
                    if (result.permissions) this.keyPermissions = result.permissions;
                } else {
                    this.settingsSuccess = "Chave salva e validada ✓";
                    this.keyPermissions = result.permissions || null;
                    this.keyWarnings = result.warnings || [];
                    this.openaiKeyInput = "";
                    await this.loadSettings();
                }
            } catch (e) {
                this.settingsError = String(e);
            } finally {
                this.savingKey = false;
            }
        },

        async clearKey() {
            try {
                await window.pywebview.api.clear_openai_key();
                await this.loadSettings();
                this.settingsSuccess = "Chave removida";
            } catch (e) {
                this.settingsError = String(e);
            }
        },

        async toggleCaptureVisibility() {
            const next = !this.captureVisible;
            try {
                await window.pywebview.api.set_capture_visibility(next);
                this.captureVisible = next;
                localStorage.setItem('ghost_capture_visible', next ? '1' : '0');
                this.setStatus(next
                    ? 'Ghost agora aparece em screen share'
                    : 'Ghost invisível em screen share');
            } catch (e) {
                this.setStatus('Erro ao trocar visibilidade');
            }
        },

        async dockFromCompact() {
            // Exit compact first to restore size, then dock to edge
            if (this.compactMode) {
                await window.pywebview.api.exit_compact_bar();
                this.compactMode = false;
            }
            await this.dockToEdge();
        },

        async dockToEdge() {
            await window.pywebview.api.minimize_to_edge();
            this.docked = true;
        },

        async expandFromEdge() {
            await window.pywebview.api.restore_from_edge();
            this.docked = false;
        },

        _scheduleUpdateCheck() {
            // Fire-and-forget on init, then re-check every 6h while the app runs.
            const run = async () => {
                try {
                    const r = await window.pywebview.api.check_for_updates();
                    if (r && !r.error) this.updateInfo = r;
                } catch (_) { /* offline */ }
            };
            run();
            if (!this._updateTimer) {
                this._updateTimer = setInterval(run, 6 * 60 * 60 * 1000);
            }
        },
        async checkForUpdatesNow() {
            try {
                const r = await window.pywebview.api.check_for_updates(true);
                if (r && !r.error) {
                    this.updateInfo = r;
                    this.updateBannerDismissed = false;
                    this.setStatus(r.hasUpdate
                        ? `Nova versão disponível: ${r.latest}`
                        : `Você está na versão mais recente (${r.current})`);
                } else {
                    this.setStatus('Não foi possível verificar atualizações (offline?)');
                }
            } catch (e) { this.setStatus('Erro ao verificar atualizações'); }
        },
        openReleasePage() {
            const url = this.updateInfo.releaseUrl || this.appInfo.releasesUrl;
            if (!url) return;
            try { window.pywebview.api.open_url ? window.pywebview.api.open_url(url) : window.open(url, '_blank'); }
            catch (_) { window.open(url, '_blank'); }
        },

        async enterCompact() {
            await window.pywebview.api.enter_compact_bar();
            this.compactMode = true;
            // Open the popup alongside and sync messages
            this._syncPopup();
        },

        async exitCompact() {
            await window.pywebview.api.exit_compact_bar();
            this.compactMode = false;
        },

        _syncPopup() {
            if (!this.compactMode) return;
            try {
                const serializable = this.messages.map(m => ({
                    role: m.role,
                    text: m.text || '',
                    image: m.image || null,
                    loading: !!m.loading,
                }));
                window.pywebview.api.show_response_popup(serializable);
                // Propaga título depois dum delay curto (popup precisa estar montado)
                setTimeout(() => this._syncPopupTitle(), 200);
            } catch (e) {}
        },

        _updatePopup() {
            if (!this.compactMode) return;
            try {
                const serializable = this.messages.map(m => ({
                    role: m.role,
                    text: m.text || '',
                    image: m.image || null,
                    loading: !!m.loading,
                }));
                window.pywebview.api.update_response_popup(serializable);
            } catch (e) {}
        },

        async onInputClick(event) {
            // When user clicks the text input and global kbd capture is off,
            // force Ghost to foreground so they can type directly.
            if (this.kbdCapture) return;
            try { await window.pywebview.api.force_focus(); } catch (e) {}
        },

        async toggleKbdCapture() {
            const next = !this.kbdCapture;
            if (next) {
                await window.pywebview.api.start_kb_capture();
            } else {
                await window.pywebview.api.stop_kb_capture();
            }
            this.kbdCapture = next;
            this.setStatus(next ? 'Teclado global ativo' : 'Teclado global desligado');
        },


        handleGlobalKey(payload) {
            if (!payload) return;
            console.log('[ghostKey]', payload);
            if (payload.type === 'char') {
                this.inputText = (this.inputText || '') + payload.value;
                // Force the input element to reflect the new value
                this.$nextTick(() => {
                    const el = this.$refs.promptInput;
                    if (el && el.value !== this.inputText) {
                        el.value = this.inputText;
                    }
                });
            } else if (payload.type === 'backspace') {
                this.inputText = (this.inputText || '').slice(0, -1);
                this.$nextTick(() => {
                    const el = this.$refs.promptInput;
                    if (el) el.value = this.inputText;
                });
            } else if (payload.type === 'enter') {
                if ((this.inputText || '').trim()) {
                    this.sendMessage();
                }
            } else if (payload.type === 'esc') {
                this.toggleKbdCapture();
            }
        },

        async _waitForApi() {
            // pywebview fires 'pywebviewready' event when bridge is ready
            if (window.pywebview?.api) return;
            await new Promise(resolve => {
                const check = () => {
                    if (window.pywebview?.api) { resolve(); return true; }
                    return false;
                };
                if (check()) return;
                window.addEventListener('pywebviewready', resolve, { once: true });
                const timer = setInterval(() => {
                    if (check()) clearInterval(timer);
                }, 50);
                setTimeout(() => { clearInterval(timer); resolve(); }, 5000);
            });
        },

        // --- Window controls ---
        closeApp() {
            // Ao clicar no X, não fecha direto — pergunta o que fazer
            this.closeConfirmOpen = true;
        },
        async confirmClose(action) {
            this.closeConfirmOpen = false;
            try {
                if (action === 'hide') {
                    await window.pywebview.api.hide_app();
                } else if (action === 'exit') {
                    await window.pywebview.api.close_app();
                }
            } catch (e) {
                console.error('[close]', e);
            }
        },
        // ============ Histórico de conversas ============
        async loadHistoryList() {
            try {
                const r = await window.pywebview.api.history_list();
                this.historyList = r?.conversations || [];
            } catch (e) { console.warn('[history] list', e); }
        },
        async openHistoryModal() {
            this.historyModalOpen = true;
            await this.loadHistoryList();
        },
        async switchToConversation(convId) {
            try {
                const r = await window.pywebview.api.history_get(convId);
                if (!r?.ok) { this.setStatus('Erro ao carregar'); return; }
                const conv = r.conversation || {};
                this.messages = conv.messages || [];
                this.currentConvId = conv.id || '';
                this.currentConvTitle = conv.title || '';
                this._titleRequested = true;
                this._titledAtMsgCount = (conv.messages || []).length;
                this._syncPopupTitle();
                this.historyModalOpen = false;
                this.scrollToBottom();
                this.setStatus('Conversa carregada');
            } catch (e) { console.error('[history] switch', e); }
        },
        async deleteConversation(convId, event) {
            event?.stopPropagation();
            try {
                await window.pywebview.api.history_delete(convId);
                await this.loadHistoryList();
                if (convId === this.currentConvId) {
                    this.messages = [];
                    this.currentConvId = '';
                }
            } catch (e) { console.error('[history] delete', e); }
        },
        async _persistHistory() {
            if (this._savingHistory) return;
            if (!this.messages.length) return;
            this._savingHistory = true;
            try {
                if (!this.currentConvId) {
                    const r = await window.pywebview.api.history_new_id();
                    this.currentConvId = r?.id || 'conv-' + Date.now();
                }
                // Serializa mensagens (Alpine proxies → plain)
                const serial = this.messages.map(m => ({
                    role: m.role,
                    text: m.text || '',
                    image: m.image || null,
                    transcript: m.transcript || null,
                    needsApiKey: !!m.needsApiKey,
                    isBranchSummary: !!m.isBranchSummary,
                }));
                await window.pywebview.api.history_save(this.currentConvId, serial);

                // Título DINÂMICO: refaz a cada 4 mensagens novas (conversa pode
                // mudar de tópico). Primeira geração aos 2 msgs, depois 6, 10, 14…
                const count = serial.length;
                const hadEnough = count >= 2;
                const shouldRefresh = hadEnough && (count - this._titledAtMsgCount >= 4 || !this._titleRequested);
                if (shouldRefresh) {
                    this._titleRequested = true;
                    this._titledAtMsgCount = count;
                    try {
                        const r = await window.pywebview.api.history_suggest_title(this.currentConvId);
                        // Aguarda ~2s pro worker terminar de gerar e ler do disco
                        setTimeout(() => this._refreshCurrentTitle(), 2500);
                    } catch (e) {}
                }
            } catch (e) { console.warn('[history] persist', e); }
            this._savingHistory = false;
        },

        async _refreshCurrentTitle() {
            // Busca título atualizado do conv atual e propaga pra UI + popup
            if (!this.currentConvId) return;
            try {
                const r = await window.pywebview.api.history_get(this.currentConvId);
                if (r?.ok && r.conversation?.title) {
                    this.currentConvTitle = r.conversation.title;
                    this._syncPopupTitle();
                }
            } catch (e) {}
        },

        _syncPopupTitle() {
            // Atualiza o header do popup (modo compact) via evaluate_js no popup
            try {
                if (window.pywebview?.api?.update_popup_title) {
                    window.pywebview.api.update_popup_title(this.currentConvTitle || '');
                }
            } catch (e) {}
        },

        // ============ Ações rápidas sobre a resposta ============
        applyQuickAction(idx, action) {
            const msg = this.messages[idx];
            if (!msg || msg.role !== 'assistant' || !msg.text) return;
            const prompts = {
                simplify: 'Explique isso de uma forma mais simples, como se eu fosse iniciante.',
                detail: 'Me dê mais detalhes sobre isso. Quero entender a fundo.',
                translate_en: 'Traduza essa resposta pra inglês.',
                example: 'Me dê um exemplo prático dessa resposta.',
                shorter: 'Resuma isso em 2-3 frases curtas.',
                continue: 'Continue a partir daqui.',
            };
            const prompt = prompts[action];
            if (!prompt) return;
            this.inputText = prompt;
            this.$nextTick(() => {
                const el = this.$refs.promptInput;
                if (el) el.focus();
            });
            this.sendMessage();
        },

        // ============ Streaming handlers (chamados por window.ghostStream*) ============
        _setupStreamingGlobals() {
            window.ghostStreamChunk = (payload) => {
                if (!payload || payload.id !== this._currentStreamId) return;
                const last = this.messages[this.messages.length - 1];
                if (!last || last.role !== 'assistant') return;
                last.text = (last.text || '') + payload.chunk;
                last.loading = false;
                last.streaming = true;
                this.scrollToBottom();
            };
            window.ghostStreamDone = (payload) => {
                if (!payload || payload.id !== this._currentStreamId) return;
                const last = this.messages[this.messages.length - 1];
                if (!last || last.role !== 'assistant') return;
                last.loading = false;
                last.streaming = false;
                if (payload.error) {
                    if (this._isApiKeyError(payload.error)) {
                        last.needsApiKey = true;
                        last.text = '';
                    } else {
                        last.text = '❌ ' + payload.error;
                    }
                } else if (payload.text && !last.text) {
                    last.text = payload.text;
                }
                // Watch mode: NÃO anexa o thumbnail na msg do usuário.
                // A imagem ainda vai pra IA via server-side, só não polui o chat.
                this.busy = false;
                this._currentStreamId = '';
                this.scrollToBottom();
                this._updatePopup();
                this._maybeAutoSpeak();
                this._persistHistory();
            };
        },

        // ============ Drag-and-drop ============
        _setupDragAndDrop() {
            const onDragOver = (e) => {
                e.preventDefault();
                if (e.dataTransfer?.types?.includes('Files')) this.isDragOver = true;
            };
            const onDragLeave = (e) => {
                if (e.clientX === 0 && e.clientY === 0) this.isDragOver = false;
            };
            const onDrop = async (e) => {
                e.preventDefault();
                this.isDragOver = false;
                const files = Array.from(e.dataTransfer?.files || []);
                for (const f of files) await this._ingestDroppedFile(f);
            };
            document.addEventListener('dragover', onDragOver);
            document.addEventListener('dragleave', onDragLeave);
            document.addEventListener('drop', onDrop);
        },
        async _ingestDroppedFile(file) {
            try {
                const buf = await file.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                const b64 = btoa(binary);
                this.setStatus('Processando ' + file.name + '...');
                const r = await window.pywebview.api.parse_dropped_file(file.name, file.type || '', b64);
                if (!r?.ok) {
                    this.setStatus('Erro: ' + (r?.error || 'arquivo não suportado'));
                    return;
                }
                this.droppedFiles.push({
                    kind: r.kind,
                    filename: r.filename,
                    content: r.content || null,
                    data_url: r.data_url || null,
                    note: r.note || '',
                });
                this.setStatus(`"${r.filename}" pronto — digite sua pergunta`);
            } catch (e) {
                console.error('[drop]', e);
                this.setStatus('Erro ao ler arquivo: ' + (e?.message || e));
            }
        },
        removeDroppedFile(i) {
            this.droppedFiles.splice(i, 1);
        },

        // ============ Live Q&A durante reunião ============
        async askMeetingLive() {
            const q = (this.meetingQuestion || '').trim();
            if (!q || this.meetingQaAsking) return;
            this.meetingQaAsking = true;
            try {
                const r = await window.pywebview.api.meeting_live_question(q);
                if (!r?.ok) {
                    this.setStatus('Erro: ' + (r?.error || 'falha'));
                    return;
                }
                this.messages.push({
                    role: 'user',
                    text: `[Pergunta sobre a reunião em andamento]\n${q}`,
                });
                this.messages.push({
                    role: 'assistant',
                    text: r.text || '(sem resposta)',
                });
                this.meetingQuestion = '';
                this.scrollToBottom();
                this._persistHistory();
                this._maybeAutoSpeak();
            } catch (e) {
                this.setStatus('Erro: ' + (e?.message || e));
            } finally {
                this.meetingQaAsking = false;
            }
        },

        // ============ Detecção de info sensível ============
        async _checkSensitive(text) {
            try {
                const r = await window.pywebview.api.scan_sensitive(text || '');
                return r?.sensitive || [];
            } catch (e) { return []; }
        },
        dismissSensitiveWarning(action) {
            const pending = this.sensitiveWarning?.pending;
            this.sensitiveWarning = null;
            if (action === 'send' && pending) {
                // Reenvia ignorando o aviso
                pending.ignoreSensitive = true;
                this._doSendMessage(pending);
            }
        },

        newConversation() {
            window.pywebview.api.clear_history();
            this.messages = [];
            this.currentConvId = '';
            this.currentConvTitle = '';
            this.droppedFiles = [];
            this._titleRequested = false;
            this._titledAtMsgCount = 0;
            this._syncPopupTitle();
            this.setStatus('Nova conversa');
        },

        startDrag(event) {
            if (event.target.closest('button, select, input, a')) return;
            if (event.button !== 0) return;
            window.pywebview.api.start_window_drag();
        },

        async toggleWatch() {
            const next = !this.watchEnabled;
            const result = await window.pywebview.api.toggle_watch(next, 3.0);
            if (result?.error) {
                this.setStatus('Erro: ' + result.error);
                return;
            }
            this.watchEnabled = !!result.enabled;
            this.setStatus(this.watchEnabled ? 'Vigiando a tela' : 'Vigilância desligada');
        },

        async toggleMeeting() {
            if (this.meetingProcessing) return;
            if (!this.meetingRunning) {
                await this._openMeetingModal();
            } else {
                await this._stopMeeting();
            }
        },

        async _openMeetingModal() {
            this.setStatus('Listando janelas...');
            try {
                this.availableWindows = await window.pywebview.api.list_windows() || [];
            } catch (e) {
                this.availableWindows = [];
            }
            // Default: current monitor
            if (this.monitors.length) {
                this.meetingTarget = 'monitor:' + this.monitors[0].index;
            }
            this.meetingModalOpen = true;
            this.setStatus('Configure e inicie');
        },

        async confirmMeetingStart() {
            this.meetingModalOpen = false;
            const [kind, id] = (this.meetingTarget || 'monitor:1').split(':');
            const targetId = parseInt(id, 10) || null;

            const result = await window.pywebview.api.start_meeting(kind, targetId);
            if (result?.error) { this.setStatus('Erro: ' + result.error); return; }
            this.meetingRunning = true;
            this.meetingProcessing = false;
            this.meetingStatusText = kind === 'window'
                ? 'Gravando áudio + janela escolhida'
                : 'Gravando áudio + monitor escolhido';
            this.setStatus('Reunião iniciada');
            this._startMeetingTimer();
        },

        async _stopMeeting() {
            const result = await window.pywebview.api.stop_meeting();
            if (result?.error) { this.setStatus('Erro: ' + result.error); return; }
            this.meetingRunning = false;
            this.meetingProcessing = true;
            this.meetingStatusText = 'Processando...';
            this.setStatus('Processando reunião...');
            this._stopMeetingTimer();
            this._pollMeetingResult();
        },

        _startMeetingTimer() {
            this._stopMeetingTimer();
            this._meetingTimer = setInterval(async () => {
                try {
                    const status = await window.pywebview.api.get_meeting_status();
                    this.meetingElapsed = status.elapsed_formatted || '00:00';
                    this.meetingStatusText = status.status_text || '';
                } catch (e) { /* ignore */ }
            }, 1000);
        },

        _stopMeetingTimer() {
            if (this._meetingTimer) {
                clearInterval(this._meetingTimer);
                this._meetingTimer = null;
            }
        },

        _pollMeetingResult() {
            const poll = async () => {
                try {
                    const status = await window.pywebview.api.get_meeting_status();
                    this.meetingStatusText = status.status_text || '';
                    if (!status.processing) {
                        const result = await window.pywebview.api.consume_meeting_result();
                        if (result?.ok) {
                            this._onMeetingDone(result);
                        } else if (result?.error) {
                            this.setStatus('Erro: ' + result.error);
                            this.meetingProcessing = false;
                        }
                        return;
                    }
                    setTimeout(poll, 1500);
                } catch (e) {
                    setTimeout(poll, 2000);
                }
            };
            setTimeout(poll, 1500);
        },

        _onMeetingDone(result) {
            this.meetingProcessing = false;
            this.meetingStatusText = '';
            this.setStatus('Documento gerado');
            const fileName = (result.doc_path || '').split(/[\\/]/).pop();
            const bullets = (result.summary_bullets || []).map(b => `- ${b}`).join('\n');
            this.messages.push({
                role: 'assistant',
                text: `### ✅ Reunião processada\n\n` +
                      `**Duração:** ${result.duration}\n` +
                      `**Documento:** \`${fileName}\`\n\n` +
                      `Salvo em \`Desktop/Ghost-Reunioes/\` com transcrição timestamped + resumo + áudio + screenshots.\n\n` +
                      `---\n\n**Resumo executivo:**\n${bullets || '_(sem bullets)_'}`,
            });
            this.scrollToBottom();
        },

        // --- Focus management (no-op: NOACTIVATE is permanent) ---
        onInputFocus() {},
        onInputBlur() {},

        updateScrollVisibility() {
            // Alpine handles this via x-show; method here for future extension
        },

        setStatus(text) {
            this.statusMessage = text.toUpperCase();
        },

        scrollToBottom() {
            this.$nextTick(() => {
                const el = this.$refs.chatScroll;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        // --- Markdown ---
        renderMarkdown(text) {
            if (!text) return '';
            let html = marked.parse(text);
            // Wrap code blocks with copy button + language badge
            html = html.replace(
                /<pre><code class="language-([a-z0-9+\-]+)">([\s\S]*?)<\/code><\/pre>/g,
                (match, lang, code) => `
                    <div class="code-wrap">
                        <span class="code-lang-badge">${lang}</span>
                        <button class="code-copy-btn" onclick="copyCode(this)" data-code="${btoa(unescape(encodeURIComponent(code)))}"><span class="code-copy-label">Copiar</span></button>
                        <pre><code class="language-${lang}">${code}</code></pre>
                    </div>
                `
            );
            html = html.replace(
                /<pre><code>([\s\S]*?)<\/code><\/pre>/g,
                (match, code) => `
                    <div class="code-wrap">
                        <button class="code-copy-btn" onclick="copyCode(this)" data-code="${btoa(unescape(encodeURIComponent(code)))}"><span class="code-copy-label">Copiar</span></button>
                        <pre><code>${code}</code></pre>
                    </div>
                `
            );
            return html;
        },

        _ensurePreset() {
            if (!this.selectedPreset && this.presets.length) {
                this.selectedPreset = this.presets[0];
            }
            return !!this.selectedPreset;
        },

        // --- Capture flows ---
        // Agora a captura NÃO envia direto: ela deixa a imagem pendente acima
        // do composer pra o usuário digitar/gravar a pergunta e enviar junto.
        async triggerCapture() {
            if (this.busy) return;
            if (!this.ready) { this.setStatus('Aguarde, inicializando...'); return; }
            if (this.captureMode === 'tela') await this.captureFullscreen();
            else if (this.captureMode === 'area') await this.captureArea();
            else if (this.captureMode === 'scroll') await this.captureScroll();
        },

        _setPendingCapture(result, label) {
            this.pendingCapture = {
                thumbnail: result.thumbnail,
                label: label,
            };
            this.setStatus(label + ' — digite sua pergunta e envie');
        },

        clearPendingCapture() {
            this.pendingCapture = null;
        },

        async captureFullscreen() {
            this.busy = true;
            this.setStatus('Capturando tela...');
            try {
                const result = await window.pywebview.api.capture_fullscreen();
                if (result.error) { this.setStatus('Erro: ' + result.error); return; }
                this._setPendingCapture(result, 'Tela capturada');
            } finally { this.busy = false; }
        },

        async captureArea() {
            this.busy = true;
            this.setStatus('Selecione uma área (ESC cancela)...');
            try {
                const result = await window.pywebview.api.capture_area();
                if (result.cancelled) { this.setStatus('Cancelado'); return; }
                if (result.error) { this.setStatus('Erro: ' + result.error); return; }
                this._setPendingCapture(result, 'Área capturada');
            } finally { this.busy = false; }
        },

        async captureScroll() {
            this.busy = true;
            this.setStatus('Iniciando rolagem em 3s...');
            await new Promise(r => setTimeout(r, 1000));
            this.setStatus('Iniciando em 2s...');
            await new Promise(r => setTimeout(r, 1000));
            this.setStatus('Iniciando em 1s...');
            await new Promise(r => setTimeout(r, 1000));
            this.setStatus('Capturando rolagem...');
            try {
                const result = await window.pywebview.api.capture_with_scroll(
                    this.selectedMonitor, this.maxScrolls
                );
                if (result.error) { this.setStatus('Erro: ' + result.error); return; }
                this._setPendingCapture(result, `${result.pages} páginas capturadas`);
            } finally { this.busy = false; }
        },

        async _submitWithImage_showInPopup(text, loading) {
            if (this.compactMode) {
                try { window.pywebview.api.show_response_popup(text || '', loading); } catch (e) {}
            }
        },

        async _submitWithImage(captureResult) {
            const userText = this.inputText.trim();
            const displayText = userText || this.selectedPreset;

            this.messages.push({
                role: 'user',
                text: displayText,
                image: captureResult.thumbnail,
            });
            this.scrollToBottom();

            const loadingMsg = { role: 'assistant', text: '', loading: true };
            this.messages.push(loadingMsg);
            this.scrollToBottom();

            this.inputText = '';
            this.setStatus('Analisando...');
            this._updatePopup();

            try {
                const result = await window.pywebview.api.analyze_last_capture(
                    this.selectedPreset, userText
                );
                const lastMsg = this.messages[this.messages.length - 1];
                if (result.error) {
                    if (this._isApiKeyError(result.error)) {
                        lastMsg.needsApiKey = true;
                        lastMsg.text = '';
                    } else {
                        lastMsg.text = '❌ ' + result.error;
                    }
                    this.setStatus('Erro');
                } else {
                    lastMsg.text = result.text;
                    navigator.clipboard?.writeText(result.text).catch(() => {});
                    this.setStatus('Copiado para clipboard');
                }
                lastMsg.loading = false;
            } catch (e) {
                const lastMsg = this.messages[this.messages.length - 1];
                lastMsg.text = '❌ ' + (e?.message || e);
                lastMsg.loading = false;
                this.setStatus('Erro');
            }
            this.scrollToBottom();
            this._updatePopup();
            this._maybeAutoSpeak();
        },

        async sendMessage() {
            // Guarda contra envio durante gravação/transcrição: senão o usuário
            // perde o áudio que estava gravando e manda só o que já tem.
            if (this.voiceRecording) {
                this.setStatus('Pare a gravação antes de enviar');
                return;
            }
            if (this.voiceTranscribing) {
                this.setStatus('Aguarde a transcrição terminar');
                return;
            }
            const questionRaw = this.inputText.trim();
            const ctx = this.systemTranscript.trim();
            const files = [...this.droppedFiles];
            const capture = this.pendingCapture;
            if (!questionRaw && !ctx && !files.length && !capture) return;
            if (this.busy) return;
            if (!this.ready) { this.setStatus('Aguarde...'); return; }

            if (!this.settings?.has_openai_key) {
                this.messages.push({ role: 'user', text: questionRaw || ctx || '(anexo)' });
                this.messages.push({ role: 'assistant', needsApiKey: true, text: '' });
                this.inputText = '';
                this.systemTranscript = '';
                this.droppedFiles = [];
                this.pendingCapture = null;
                this.scrollToBottom();
                return;
            }

            // Monta contexto de arquivos droppados + imagem de captura pendente
            let fileContext = '';
            let fileImage = null;
            for (const f of files) {
                if (f.kind === 'text' && f.content) {
                    fileContext += `\n\n[Arquivo: ${f.filename}]${f.note ? ' — ' + f.note : ''}\n"""\n${f.content}\n"""`;
                } else if (f.kind === 'image' && f.data_url) {
                    fileImage = f.data_url;
                }
            }
            // Captura da tela pendente tem prioridade visual (é o que o usuário
            // acabou de capturar pra essa pergunta específica)
            if (capture && capture.thumbnail) {
                fileImage = capture.thumbnail;
            }

            const question = questionRaw || (ctx ? 'O que você pode me dizer sobre isso?' :
                                              (fileContext || fileImage ? 'Analise o anexo.' : ''));

            // Texto final enviado pro GPT
            const parts = [];
            if (ctx) parts.push(`[Trecho de áudio transcrito]\n"${ctx}"`);
            if (fileContext) parts.push(fileContext.trim());
            if (question) parts.push(question);
            const apiText = parts.join('\n\n');

            // Aviso proativo de info sensível (se conteúdo tiver padrão de CPF/cartão/etc)
            const scanTarget = apiText;
            const sensitive = await this._checkSensitive(scanTarget);
            if (sensitive.length && !this._lastConfirmedSensitive) {
                this.sensitiveWarning = {
                    types: sensitive,
                    pending: { questionRaw, ctx, files, fileImage, apiText, question },
                };
                return;
            }
            this._lastConfirmedSensitive = false;

            // Na UI: mostra transcript + imagem de arquivo
            const userMsg = {
                role: 'user',
                text: question,
                transcript: ctx || null,
                image: fileImage || null,
                attachments: files.filter(f => f.kind === 'text').map(f => f.filename),
            };
            this.messages.push(userMsg);
            const loadingMsg = { role: 'assistant', text: '', loading: true };
            this.messages.push(loadingMsg);
            this.inputText = '';
            this.systemTranscript = '';
            this.droppedFiles = [];
            this.pendingCapture = null;
            this.busy = true;

            const mode = this.meetingRunning ? 'reunião ao vivo'
                        : (this.watchEnabled ? 'com visão' : 'texto');
            this.setStatus('Analisando (' + mode + ')...');
            this.scrollToBottom();
            this._updatePopup();

            // STREAMING se habilitado, senão fallback pro send_text tradicional
            if (this.streamingEnabled && !fileImage) {
                // Streaming não suporta imagem de drag-drop ainda (backend usa só text + watch).
                // Se o usuário droppou imagem, cai no send_text tradicional que suporta vision.
                const streamId = 'stream-' + Date.now();
                this._currentStreamId = streamId;
                try {
                    const r = await window.pywebview.api.send_text_streaming(apiText, streamId);
                    if (r?.error) {
                        loadingMsg.text = '❌ ' + r.error;
                        loadingMsg.loading = false;
                        this.busy = false;
                        this._currentStreamId = '';
                    }
                } catch (e) {
                    loadingMsg.text = '❌ ' + (e?.message || e);
                    loadingMsg.loading = false;
                    this.busy = false;
                    this._currentStreamId = '';
                }
                return;  // o resto é tratado por ghostStreamDone
            }

            // ===== Caminho tradicional (vision, drag-drop imagem, ou streaming desabilitado) =====
            try {
                const result = await window.pywebview.api.send_text(apiText, fileImage || '');
                const lastMsg = this.messages[this.messages.length - 1];
                if (result.error) {
                    if (this._isApiKeyError(result.error)) {
                        lastMsg.needsApiKey = true;
                        lastMsg.text = '';
                    } else {
                        lastMsg.text = '❌ ' + result.error;
                    }
                } else {
                    lastMsg.text = result.text;
                    // Watch mode: thumbnail não aparece no chat (mantém o visual limpo)
                    navigator.clipboard?.writeText(result.text).catch(() => {});
                    this.setStatus('Copiado para clipboard');
                }
                lastMsg.loading = false;
            } catch (e) {
                const lastMsg = this.messages[this.messages.length - 1];
                lastMsg.text = '❌ ' + (e?.message || e);
                lastMsg.loading = false;
                this.setStatus('Erro');
            } finally {
                this.busy = false;
                this.scrollToBottom();
                this._updatePopup();
                this._maybeAutoSpeak();
                this._persistHistory();
            }
        },

        // Helper pra reenviar após confirmar info sensível
        _doSendMessage(pending) {
            // Restaura estado e chama sendMessage (que vai pular a checagem com _lastConfirmedSensitive)
            this._lastConfirmedSensitive = true;
            this.inputText = pending.questionRaw || '';
            this.systemTranscript = pending.ctx || '';
            this.droppedFiles = pending.files || [];
            this.sendMessage();
        },
    };
}

// ===== Global tooltip manager (position:fixed escapa overflow: hidden) =====
(function() {
    let tipEl = null;
    let showTimer = null;
    let currentEl = null;

    function getTip() {
        if (!tipEl) {
            tipEl = document.createElement('div');
            tipEl.className = 'gtooltip';
            document.body.appendChild(tipEl);
        }
        return tipEl;
    }

    function show(el) {
        const text = el.getAttribute('data-tooltip');
        if (!text) return;
        const below = el.getAttribute('data-tooltip-pos') === 'below';
        const tip = getTip();
        tip.textContent = text;
        tip.style.left = '-9999px';
        tip.style.top = '-9999px';
        tip.classList.add('visible');
        const rect = el.getBoundingClientRect();
        const tRect = tip.getBoundingClientRect();
        let left = rect.left + rect.width / 2 - tRect.width / 2;
        let top = below ? rect.bottom + 8 : rect.top - tRect.height - 8;
        left = Math.max(6, Math.min(left, window.innerWidth - tRect.width - 6));
        top = Math.max(6, Math.min(top, window.innerHeight - tRect.height - 6));
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
    }

    function hide() {
        if (tipEl) tipEl.classList.remove('visible');
        currentEl = null;
        clearTimeout(showTimer);
    }

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

// Global copy handler for code blocks
window.copyCode = function(btn) {
    const encoded = btn.dataset.code;
    const text = decodeURIComponent(escape(atob(encoded)));
    // Strip HTML tags (Prism/marked adiciona spans de syntax highlight)
    const temp = document.createElement('div');
    temp.innerHTML = text;
    const plainText = (temp.textContent || temp.innerText || '').trim();
    navigator.clipboard.writeText(plainText).then(() => {
        const labelSpan = btn.querySelector('.code-copy-label') || btn;
        const orig = labelSpan.textContent;
        labelSpan.textContent = 'Copiado';
        btn.classList.add('copied');
        setTimeout(() => {
            labelSpan.textContent = orig || 'Copiar';
            btn.classList.remove('copied');
        }, 1400);
    }).catch(err => {
        console.error('[copyCode] failed:', err);
    });
};
