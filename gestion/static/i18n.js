/* Langues de l'interface. Le français est la langue d'origine : tous les textes du programme sont écrits en français et traduits à l'affichage par un dictionnaire
   (gestion/static/i18n/<code>.json : « textes » exacts + « motifs » pour les phrases à chiffres). Un texte sans traduction reste en français : jamais de trou, jamais de clé technique.
   Ajouter une langue : voir TRADUCTIONS.md. Le choix de langue est mémorisé sur l'appareil. */
(() => {
  'use strict';
  const KEY = 'gestion_lang';
  let lang = 'fr', dict = {}, pats = [], langs = [{ code: 'fr', name: 'Français' }];
  const missing = new Set(), orig = new WeakMap(), wrote = new WeakMap();
  const DEBUG = /[?&]i18n=debug/.test(location.search);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE']);
  const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  const WORD = /\p{L}{2,}/u;

  const UNITS = /^(GNF|j|Ko|Mo|kg|L|mm|cm|m|h|x|[A-Z]{1,3}-)*$/;                            // « 1 200 GNF », « 12 j » : rien à traduire
  const has = (k) => Object.prototype.hasOwnProperty.call(dict, k) && dict[k] !== '';

  // Traduction d'un texte entier : texte exact, puis motifs (phrases à chiffres), puis morceaux (« a · b », « libellé : valeur »). undefined si rien ne correspond.
  function lookup(k, depth) {
    if (has(k)) return dict[k];
    for (const [re, to] of pats) {
      const m = re.exec(k); if (!m) continue;
      return to.replace(/\$(\d)|\{t:(\d)\}/g, (_, a, b) => (a ? (m[a] ?? '') : (m[b] === undefined ? '' : (lookup(m[b].trim(), depth + 1) ?? m[b]))));
    }
    if (depth > 2) return undefined;
    if (k.includes(' · ')) {
      let any = false; const out = k.split(' · ').map((x) => { const r = lookup(x.trim(), depth + 1); if (r !== undefined) any = true; return r ?? x; });
      if (any) return out.join(' · ');
    }
    const c = k.indexOf(' : ');
    if (c > 0) { const l = k.slice(0, c), r = k.slice(c + 3), tl = has(l) ? dict[l] : has(l + ' :') ? dict[l + ' :'].replace(/\s*:$/, '') : undefined; if (tl !== undefined) return `${tl}: ${lookup(r.trim(), depth + 1) ?? r}`; }
    return undefined;
  }

  function t(s) {
    if (lang === 'fr' || typeof s !== 'string' || !s) return s;
    const k = s.trim(); if (!k || !WORD.test(k)) return s;
    const letters = k.replace(/[^\p{L}]+/gu, ' ').trim();
    if (!letters) return s;
    if (letters.split(' ').every((w) => UNITS.test(w))) {                              // « 12 j », « 1 200 GNF » : seuls les motifs peuvent les réécrire
      for (const [re, to] of pats) { const m = re.exec(k); if (m && !to.includes('{t:')) return s.replace(k, () => to.replace(/\$(\d)/g, (_, a) => m[a] ?? '')); }
      return s;
    }
    const r = lookup(k, 0);
    if (r === undefined) { missing.add(k.replace(/\d+/g, '#')); return s; }
    return s.replace(k, () => r);
  }

  function node(n) {
    if (lang === 'fr') return;
    if (n.nodeType === 3) {
      const p = n.parentNode; if (!p || SKIP.has(p.nodeName) || (p.closest && p.closest('[data-notr]'))) return;
      if (wrote.get(n) === n.nodeValue) return;                                        // c'est déjà notre traduction
      const o = n.nodeValue, r = t(o); orig.set(n, o); if (r !== o) { wrote.set(n, r); n.nodeValue = r; }
    } else if (n.nodeType === 1) {
      if (SKIP.has(n.nodeName) || (n.closest && n.closest('[data-notr]'))) return;
      for (const a of ATTRS) if (n.hasAttribute(a)) {
        const v = n.getAttribute(a), w = wrote.get(n) || {}; if (w[a] === v) continue;
        const r = t(v); if (r !== v) { w[a] = r; wrote.set(n, w); n.setAttribute(a, r); }
      }
      if (n.nodeName === 'INPUT' && (n.type === 'button' || n.type === 'submit') && n.value) { const r = t(n.value); if (r !== n.value) n.value = r; }
      for (const c of n.childNodes) node(c);
    }
  }

  function observe() {
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === 'characterData') node(m.target);
        else if (m.type === 'attributes') { const w = wrote.get(m.target) || {}; if (w[m.attributeName] !== m.target.getAttribute(m.attributeName)) node(m.target); }
        else for (const n of m.addedNodes) node(n);
      }
    }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }

  function detect(list) {
    let saved = ''; try { saved = localStorage.getItem(KEY) || ''; } catch { /* stockage indisponible */ }
    const codes = list.map((l) => l.code);
    if (codes.includes(saved)) return saved;
    for (const l of navigator.languages || [navigator.language || 'fr']) { const b = String(l).toLowerCase().split('-')[0]; if (codes.includes(b)) return b; }
    return 'fr';
  }

  function setLang(code) {
    try { if (code === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, code); } catch { /* sans mémoire : le choix durera jusqu'au rechargement */ }
    location.reload();
  }
  const stored = () => { try { return localStorage.getItem(KEY) || ''; } catch { return ''; } };

  function switcher() {
    const sel = document.createElement('select'); sel.setAttribute('aria-label', 'Langue / Language'); sel.setAttribute('data-notr', '');
    const auto = document.createElement('option'); auto.value = 'auto'; auto.textContent = 'Automatique · Automatic'; auto.selected = !stored() || !langs.some((l) => l.code === stored()); sel.append(auto);
    for (const l of langs) { const o = document.createElement('option'); o.value = l.code; o.textContent = l.name; o.selected = !auto.selected && l.code === lang; sel.append(o); }
    sel.addEventListener('change', () => setLang(sel.value));
    return sel;
  }

  const ready = (async () => {
    try { const r = await fetch('/i18n/langues.json', { cache: 'no-cache' }); if (r.ok) langs = await r.json(); } catch { /* hors connexion sans cache : français */ }
    lang = detect(langs);
    document.documentElement.lang = lang;
    if (lang === 'fr') return;
    try {
      const r = await fetch(`/i18n/${lang}.json`, { cache: 'no-cache' }); if (!r.ok) throw new Error('langue indisponible');
      const d = await r.json(); dict = d.textes || {}; pats = (d.motifs || []).map((m) => [new RegExp(m.re, 'u'), m.to]);
      if (d.dir === 'rtl') document.documentElement.dir = 'rtl';
    } catch { lang = 'fr'; document.documentElement.lang = 'fr'; return; }
    for (const name of ['alert', 'confirm', 'prompt']) { const f = window[name].bind(window); window[name] = (m, d) => (name === 'prompt' ? f(t(String(m)), d) : f(t(String(m)))); }
    document.title = t(document.title);
    node(document.body); observe();
  })();

  window.I18N = { t, ready, switcher, setLang, get lang() { return lang; }, get languages() { return langs; }, missing: () => [...missing].sort() };
})();
