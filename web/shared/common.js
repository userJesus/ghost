/* Ghost — shared frontend helpers.
 *
 * Loaded by index.html, response.html, and dropdown.html BEFORE any script
 * that uses these symbols. Functions are exposed both as bare names (for
 * scripts that use `escapeHTML(...)`) and as `window.Ghost.*` (for cleaner
 * access where stylistically preferred).
 *
 * Goal: eliminate the ~300 lines of duplication that existed between
 * app.js and response.html before the 2026-04 refactor — the duplicated
 * helpers drifted independently and made cross-page fixes fragile.
 *
 * No framework dependency beyond `marked` (already loaded globally by every
 * Ghost HTML file via web/vendor/marked.min.js).
 */
(function (root) {
    'use strict';

    /* ---------- text helpers ---------- */

    /** HTML-escape a string by abusing the DOM text node. */
    function escapeHTML(s) {
        const div = document.createElement('div');
        div.textContent = s == null ? '' : String(s);
        return div.innerHTML;
    }

    /** Turn markdown into plain text suitable for TTS.
     *
     * Uses marked to render, then strips `<pre>` / `<code>` blocks (replacing
     * with a spoken placeholder) so the voice doesn't read raw syntax. If
     * marked fails for any reason, fall back to a crude punctuation strip.
     */
    function stripMarkdown(md) {
        if (!md) return '';
        try {
            const html = (root.marked ? root.marked.parse(md) : md);
            const div = document.createElement('div');
            div.innerHTML = html;
            div.querySelectorAll('pre, code').forEach(function (el) {
                el.replaceWith(document.createTextNode(' código omitido. '));
            });
            return (div.textContent || '').replace(/\s+/g, ' ').trim();
        } catch (e) {
            return md.replace(/[*_`#>\-\[\]()]/g, ' ').replace(/\s+/g, ' ').trim();
        }
    }

    /* ---------- TTS voice picker ----------
     *
     * Browser Speech Synthesis voice selection heuristic — prefer Windows
     * Natural/Neural voices, fall back to any pt-BR voice, then en-US.
     * Returns null if no voice is available yet (caller should retry on
     * `window.speechSynthesis.onvoiceschanged`).
     */
    function pickBestTtsVoice(lang /* 'pt-BR' by default */) {
        if (!('speechSynthesis' in root)) return null;
        const voices = root.speechSynthesis.getVoices();
        if (!voices || !voices.length) return null;

        const targetLang = (lang || 'pt-BR').toLowerCase();
        const isTarget = function (v) {
            return (v.lang || '').toLowerCase().startsWith(targetLang.split('-')[0]);
        };

        // Tier 1: Natural / Neural pt-BR voices
        const tier1 = voices.find(function (v) {
            return isTarget(v) && /natural|neural/i.test(v.name || '');
        });
        if (tier1) return tier1;

        // Tier 2: any pt-BR voice
        const tier2 = voices.find(isTarget);
        if (tier2) return tier2;

        // Tier 3: browser default
        return voices[0] || null;
    }

    /* ---------- tooltip manager ----------
     *
     * Click-anywhere-to-dismiss tooltip system used across the main window
     * and the compact-mode popup. Targets any element with `data-tooltip`.
     * Safe to re-init (idempotent) — only the first call installs listeners.
     */
    var _tooltipInitialized = false;
    function initTooltips() {
        if (_tooltipInitialized) return;
        _tooltipInitialized = true;

        var tip = null;
        function show(el) {
            hide();
            var text = el.getAttribute('data-tooltip');
            if (!text) return;
            tip = document.createElement('div');
            tip.className = 'ghost-tooltip';
            tip.textContent = text;
            document.body.appendChild(tip);
            var r = el.getBoundingClientRect();
            var tr = tip.getBoundingClientRect();
            tip.style.left = (r.left + r.width / 2 - tr.width / 2) + 'px';
            tip.style.top = (r.bottom + 6) + 'px';
        }
        function hide() {
            if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
            tip = null;
        }
        document.addEventListener('mouseover', function (e) {
            var el = e.target.closest ? e.target.closest('[data-tooltip]') : null;
            if (el) show(el);
        });
        document.addEventListener('mouseout', function (e) {
            var el = e.target.closest ? e.target.closest('[data-tooltip]') : null;
            if (el) hide();
        });
        document.addEventListener('click', hide);
        document.addEventListener('keydown', hide);
    }

    /* ---------- code-block copy-button injection ----------
     *
     * After a markdown render, scan for `<pre>` tags and graft a copy button
     * + language badge on top of each one. Same behavior as before the
     * refactor; was duplicated in app.js AND response.html.
     */
    function injectCopyButtons(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        rootEl.querySelectorAll('pre').forEach(function (pre) {
            if (pre.dataset.ghostCopyReady === '1') return;
            pre.dataset.ghostCopyReady = '1';
            var btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.type = 'button';
            btn.textContent = 'copiar';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                var code = pre.querySelector('code');
                var text = code ? code.textContent : pre.textContent;
                try {
                    navigator.clipboard.writeText(text);
                    var prev = btn.textContent;
                    btn.textContent = 'copiado';
                    setTimeout(function () { btn.textContent = prev; }, 1200);
                } catch (err) { /* clipboard blocked — no-op */ }
            });
            pre.appendChild(btn);
        });
    }

    /* ---------- exports ---------- */

    // Namespaced access for new code.
    root.Ghost = root.Ghost || {};
    root.Ghost.escapeHTML = escapeHTML;
    root.Ghost.stripMarkdown = stripMarkdown;
    root.Ghost.pickBestTtsVoice = pickBestTtsVoice;
    root.Ghost.initTooltips = initTooltips;
    root.Ghost.injectCopyButtons = injectCopyButtons;

    // Bare-name access to match the pre-refactor call sites in app.js and
    // response.html. Only set if not already present so a page that already
    // shadows one of these keeps its override.
    if (typeof root.escapeHTML === 'undefined') root.escapeHTML = escapeHTML;
    if (typeof root.stripMarkdown === 'undefined') root.stripMarkdown = stripMarkdown;
})(typeof window !== 'undefined' ? window : this);
