// ==UserScript==
// @name         Discourse Secure
// @namespace    cubicBrick
// @version      0.1
// @description  Keep your private messages secure!
// @author       cubicBrick
// @match        https://x-camp.discourse.group/*
// ==/UserScript==

(function () {
    'use strict';

    // Encode function: XOR with key, Base64, reverse, wrap with marker
    function encodeObfuscated(str, key) {
        const strBytes = new TextEncoder().encode("dextrapm" + str);
        const keyBytes = new TextEncoder().encode(key);
        const encodedBytes = strBytes.map((b, i) => b ^ keyBytes[i % keyBytes.length]);
        const base64 = btoa(String.fromCharCode(...encodedBytes));
        return "XxH@" + base64.split("").reverse().join("") + "@HxX";
    }

    // Decode function: un-reverse, Base64 decode, XOR with key, strip marker
    function decodeObfuscated(obfStr, key, triedFallback = false) {
        try {
            const cleaned = obfStr.replace(/^XxH@/, "").replace(/@HxX$/, "");
            const reversed = cleaned.split("").reverse().join("");
            const decodedStr = atob(reversed);
            const decodedBytes = new Uint8Array([...decodedStr].map(c => c.charCodeAt(0)));

            const keyBytes = new TextEncoder().encode(key);
            const originalBytes = decodedBytes.map((b, i) => b ^ keyBytes[i % keyBytes.length]);
            const cem = new TextDecoder().decode(originalBytes);

            if (!cem.startsWith("dextrapm")) {
                if (!triedFallback && key !== "discourse") {
                    return decodeObfuscated(obfStr, "discourse", true);
                }
                return "[This message is NOT for you!]";
            }

            return cem.replace("dextrapm", "");
        } catch (e) {
            if (!triedFallback && key !== "discourse") {
                return decodeObfuscated(obfStr, "discourse", true);
            }
            return "[This message is NOT for you!]";
        }
    }

    // DOM replacement: scan post content and replace !{...} with obfuscated/decoded
    function replaceSecretMessages() {
        const posts = document.querySelectorAll('.cooked, .excerpt');
        posts.forEach(post => {
            if (post.dataset.obfuscatedProcessed) return; // Avoid double-processing
            post.dataset.obfuscatedProcessed = 'true';

            post.innerHTML = post.innerHTML.replace(/!\{([^}]+)\}/g, (match, msg) => {
                const encoded = encodeObfuscated(msg, "discourse");
                const decoded = decodeObfuscated(encoded, "discourse");
                return `<span title="${encoded}" style="background: #ffe; border: 1px dashed #999; padding: 0 4px; border-radius: 4px;">${decoded}</span>`;
            });
        });
    }

    // Run initially and on new posts via mutation observer
    const observer = new MutationObserver(replaceSecretMessages);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('load', replaceSecretMessages);

    // Make encode/decode available in browser console
    window.encodeObfuscated = encodeObfuscated;
    window.decodeObfuscated = decodeObfuscated;
})();