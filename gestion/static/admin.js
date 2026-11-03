/* Équipe, journal d'audit, mon compte et informations de l'entreprise. */
(() => {
  'use strict';
  const { h, field, can, state, dlg, view, tabs, api, toast, fmtDate, OfflineError, LABELS, ACTIONS, SECURITY, deviceId } = G;

  // ---------- équipe ----------
  const genPw = () => { const a = 'ABCDEFGHJKLMNPQRSTUVWXYZ', b = 'abcdefghijkmnpqrstuvwxyz', c = '23456789'; const pick = (s) => s[crypto.getRandomValues(new Uint32Array(1))[0] % s.length]; return pick(a) + pick(b) + pick(c) + Array.from({ length: 9 }, () => pick(a + b + c)).join(''); };
  function showCredentials({ u, n, p }) {
    const text = `Identifiant : ${u}\nMot de passe provisoire : ${p}`;
    dlg.replaceChildren(h('h2', {}, 'Compte créé pour ' + n), h('p', {}, 'Donnez ces informations à la personne, de vive voix ou par un message privé. Le mot de passe provisoire ne sera plus affiché ; elle devra le remplacer à sa première connexion.'),
      h('pre', { class: 'card' }, text), h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: async () => { try { await navigator.clipboard.writeText(text); toast('Copié.'); } catch { toast('Copie impossible : recopiez à la main.'); } } }, 'Copier'),
        h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Terminé')));
    dlg.showModal();
  }
  async function patchUser(id, body) { try { await api('/api/users/' + id, { method: 'PATCH', body }); toast('Modifié.'); renderUsers(); } catch (e) { toast(e.message); } }
  async function renderUsers() {
    view.replaceChildren(h('p', { class: 'muted' }, 'Chargement…'));
    let users; try { users = (await api('/api/users')).users; } catch (e) { view.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'Cette page demande une connexion.' : e.message)); return; }
    const roles = state.rolesMap || {};
    const f = { u: h('input', { required: true, autocapitalize: 'none' }), n: h('input', { required: true }), r: h('select', {}, ...Object.entries(roles).map(([k, v]) => h('option', { value: k }, v))), p: h('input', { value: genPw(), required: true }) };
    const err = h('div', { class: 'error', hidden: true });
    const add = h('form', { class: 'card', onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/users', { method: 'POST', body: { username: f.u.value, full_name: f.n.value, role: f.r.value, temp_password: f.p.value } }); const made = { u: f.u.value, n: f.n.value, p: f.p.value }; showCredentials(made); renderUsers(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
      h('h2', {}, 'Ajouter un membre de l\'équipe'), h('div', { class: 'filters' }, field('Identifiant', f.u), field('Nom complet', f.n), field('Rôle', f.r), field('Mot de passe provisoire', f.p)), err, h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Créer le compte')));
    const rows = users.map((u) => h('article', { class: 'card' }, h('div', { class: 'row' }, h('strong', { class: 'grow' }, `${u.full_name} (${u.username})`), h('span', { class: 'badge' + (u.active ? '' : ' bad') }, u.active ? u.role_label : 'Désactivé')),
      h('div', { class: 'links' },
        h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const role = prompt('Rôle : ' + Object.keys(roles).join(', '), u.role); if (role && role !== u.role) await patchUser(u.id, { role }); } }, 'Changer le rôle'),
        h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (confirm(u.active ? 'Désactiver ce compte ? Il sera déconnecté immédiatement.' : 'Réactiver ce compte ?')) await patchUser(u.id, { active: !u.active }); } }, u.active ? 'Désactiver' : 'Réactiver'),
        h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const pw = genPw(); if (confirm('Réinitialiser le mot de passe ? Nouveau mot de passe provisoire : ' + pw)) await patchUser(u.id, { reset_password: pw }); } }, 'Réinitialiser le mot de passe'), u.totp_on && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (confirm('Réinitialiser la double authentification de ' + u.username + ' ? Ses sessions seront fermées.')) { try { await api('/api/users/' + u.id + '/2fa-reset', { method: 'POST' }); toast('Double authentification réinitialisée.'); renderUsers(); } catch (x) { toast(x.message); } } } }, 'Réinitialiser la double authentification'))));
    view.replaceChildren(h('h1', {}, 'Équipe'), add, h('div', { class: 'list two' }, rows));
  }

  // ---------- journal d'audit ----------
  const A = { entries: [], filters: {}, actions: [], users: [], done: false };
  async function loadAudit(more) {
    const p = new URLSearchParams(); for (const [k, v] of Object.entries(A.filters)) if (v) p.set(k, v);
    if (more && A.entries.length) p.set('before_id', A.entries[A.entries.length - 1].id);
    p.set('limit', '50');
    const r = await api('/api/audit?' + p);
    A.entries = more ? A.entries.concat(r.entries) : r.entries; A.actions = r.actions; A.users = r.users; A.done = r.entries.length < 50;
  }
  function diffNodes(e) {
    if (!e.before && !e.after) return null;
    const show = (v) => (v === null || v === undefined || v === '' ? '—' : v === true ? 'oui' : v === false ? 'non' : Array.isArray(v) ? v.join(', ') : String(v));
    const keys = [...new Set([...Object.keys(e.before || {}), ...Object.keys(e.after || {})])].filter((k) => e.before || (e.after[k] !== '' && e.after[k] !== null && e.after[k] !== false && e.after[k] !== 0));
    return h('div', { class: 'diff' }, keys.map((k, i) => [i ? ' · ' : '', `${LABELS[k] || k} : `, e.before && k in e.before && h('del', {}, show(e.before[k])), e.before && e.after && k in e.before && k in e.after ? ' → ' : '', e.after && k in e.after && h('ins', {}, show(e.after[k]))]));
  }
  function shortDevice(d) {
    const [id = '', ua = ''] = (d || '').split(' | ');
    const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows' : /Mac OS/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : 'appareil inconnu';
    const br = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'navigateur';
    return `${os} · ${br} · n° ${id.slice(0, 8) || '—'}`;
  }
  function entryCard(e) {
    return h('article', { class: 'card entry ' + (SECURITY.has(e.action) ? 'sec' : 'chg') },
      h('div', { class: 'when' }, fmtDate(e.ts)), h('div', {}, h('strong', {}, e.user || 'inconnu'), e.role ? ` (${e.role})` : '', ' — ', ACTIONS[e.action] || e.action),
      e.entity && h('div', { class: 'muted' }, `${e.entity} ${e.entity_id ? String(e.entity_id).slice(0, 8) : ''}`), diffNodes(e), e.note && h('div', { class: 'muted' }, e.note),
      h('div', { class: 'muted', title: e.device || '' }, 'Appareil : ' + shortDevice(e.device) + (e.ip ? ' · ' + e.ip : '')));
  }
  async function renderSeals(box) {
    let s; try { s = await api('/api/audit/seal'); } catch (e) { box.hidden = true; return; }
    const owner = can('audit.seal'), err = h('div', { class: 'error', hidden: true });
    const say = (t) => { err.textContent = t; err.hidden = false; };
    const point = s.checkpoint ? JSON.stringify({ id: s.checkpoint.id, hash: s.checkpoint.hash }) : '';
    const keyIn = h('input', { type: 'text', placeholder: 'Clé publique (64 caractères)', 'aria-label': 'Clé publique de signature', autocomplete: 'off' });
    const sealIn = h('textarea', { rows: 3, placeholder: 'Collez ici le sceau signé (résultat de « signer »)', 'aria-label': 'Sceau signé' });
    const reload = () => { box.replaceChildren(); renderSeals(box); };
    box.replaceChildren(h('h2', {}, 'Sceaux : signatures du journal hors du serveur'),
      h('p', { class: 'muted' }, 'La chaîne d\'empreintes détecte une ligne modifiée, mais quelqu\'un qui contrôle le serveur pourrait tout recalculer. Un sceau est une signature faite avec une clé PRIVÉE que vous gardez hors de cet ordinateur (clé USB) : le serveur ne peut pas en fabriquer, et toute réécriture de l\'historique avant un sceau se voit.'),
      s.public_key ? h('p', {}, `Clé publique enregistrée (empreinte ${s.fingerprint}). ${s.count} sceau(x)${s.last ? ', dernier : entrée n° ' + s.last.id + ' (' + s.entries_since_last + ' entrée(s) depuis)' : ''}. `, s.ok ? h('span', { class: 'ok-box' }, '✔ concordent') : h('strong', { class: 'error' }, '⚠ ne concordent plus : entrées ' + s.broken.join(', '))) : h('p', { class: 'muted' }, 'Aucune clé enregistrée : les sceaux ne sont pas encore en place.'),
      owner && h('details', {}, h('summary', {}, s.public_key ? 'Remplacer la clé publique' : 'Enregistrer la clé publique'),
        h('p', { class: 'muted' }, 'Sur VOTRE ordinateur (pas le serveur) : python -m gestion.signature generer cle_signature.txt — copiez la clé privée sur une clé USB, puis collez ici la clé publique affichée.'), keyIn,
        h('button', { class: 'secondary', type: 'button', onclick: async () => { err.hidden = true; try { await api('/api/audit/seal/key', { method: 'POST', body: { public_key: keyIn.value, replace: !!s.public_key } }); G.toast('Clé enregistrée.'); reload(); } catch (e) { say(e.message); } } }, 'Enregistrer')),
      owner && s.public_key && h('div', {}, h('p', {}, 'Point à signer (entrée n° ' + (s.checkpoint ? s.checkpoint.id : '—') + ') :'), h('pre', { class: 'seal-point' }, point),
        h('p', { class: 'muted' }, 'Sur votre ordinateur : python -m gestion.signature signer cle_signature.txt \'<le point ci-dessus>\' — collez le résultat ci-dessous.'), sealIn,
        h('button', { class: 'primary', type: 'button', onclick: async () => { err.hidden = true; let b; try { b = JSON.parse(sealIn.value); } catch (e) { say('Texte illisible : collez le résultat de « signer » tel quel.'); return; } try { await api('/api/audit/seal', { method: 'POST', body: b }); G.toast('Journal scellé.'); reload(); } catch (e) { say(e.message); } } }, 'Enregistrer le sceau')), err);
  }

  const MARK = { ok: '✔', warn: '⚠', todo: '✖' };
  /** Liste de contrôle de mise en service (propriétaire) : l'état réel de l'installation, avec la marche à suivre pour chaque point. */
  async function loadReadiness(box) {
    let r; try { r = await api('/api/readiness'); } catch (e) { return; }
    const c = r.counts, summary = r.ready ? (c.warn ? `Prête pour la saisie · ${c.warn} amélioration(s) conseillée(s)` : 'Prête pour la saisie : tout est en place ✔') : `${c.todo} point(s) à faire avant de saisir de vraies données`;
    box.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Mise en service : liste de contrôle'), h('p', {}, h('span', { class: 'badge ' + (r.ready ? (c.warn ? 'warn' : '') : 'bad') }, summary)),
      h('details', { open: !r.ready }, h('summary', {}, `Détail (${c.ok} ok, ${c.warn} à améliorer, ${c.todo} à faire)`),
        h('ul', { class: 'checklist' }, r.items.map((i) => h('li', {}, h('strong', {}, `${MARK[i.level]} ${i.title}`), h('div', { class: 'muted' }, i.detail), i.fix && h('div', {}, '→ ' + i.fix)))))));
  }

  async function renderAudit() {
    view.replaceChildren(h('p', { class: 'muted' }, 'Chargement…'));
    try { await loadAudit(false); } catch (e) { view.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'Le journal demande une connexion.' : e.message)); return; }
    const sel = (name, options, label) => h('select', { 'aria-label': label, onchange: (e) => { A.filters[name] = e.target.value; renderAudit(); } }, h('option', { value: '' }, label), ...options.map(([v, t]) => h('option', { value: v, selected: A.filters[name] === v }, t)));
    const date = (name, label) => h('input', { type: 'date', 'aria-label': label, value: A.filters[name] || '', onchange: (e) => { A.filters[name] = e.target.value; renderAudit(); } });
    const q = h('input', { type: 'search', placeholder: 'Rechercher dans les détails…', value: A.filters.q || '', onchange: (e) => { A.filters.q = e.target.value; renderAudit(); } });
    const result = h('div', { hidden: true });
    const sealBox = h('div', { class: 'card' }); renderSeals(sealBox);
    const exp = new URLSearchParams(A.filters);
    view.replaceChildren(h('h1', {}, 'Journal : qui a fait quoi, et quand'), h('div', { class: 'card' }, h('div', { class: 'filters' }, sel('user', A.users.map((u) => [u, u]), 'Tous les utilisateurs'), sel('action', A.actions.map((a) => [a, ACTIONS[a] || a]), 'Toutes les actions'), date('from', 'Du'), date('to', 'Au')), h('div', {}, field('', q)),
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: async () => { try { const v = await api('/api/audit/verify'); result.hidden = false; result.className = v.ok ? 'ok-box' : 'error'; result.textContent = v.ok ? `✔ Intégrité vérifiée : ${v.entries} entrées, aucune altération détectée.${v.seals && v.seals.count ? ` ${v.seals.count} sceau(x) signé(s) hors du serveur concordent.` : ' Aucun sceau signé : voir « Sceaux » ci-dessous.'} Empreinte actuelle : ${v.head.slice(0, 16)}… (notez-la et gardez-la ailleurs)` : (v.chain_ok === false ? `⚠ Le journal a été altéré à partir de l'entrée n° ${v.broken_at}.` : `⚠ La chaîne est cohérente MAIS ${v.seals.broken.length} sceau(x) signé(s) ne correspondent plus (entrées ${v.seals.broken.join(', ')}) : l'historique a été réécrit après la signature.`); } catch (e) { toast(e.message); } } }, 'Vérifier l\'intégrité'),
        h('a', { class: 'btn', href: '/api/audit/export.csv?' + exp, download: '' }, 'Exporter en CSV')), result),
      sealBox,
      h('div', { class: 'list', id: 'auditlist' }, A.entries.length ? A.entries.map(entryCard) : h('p', { class: 'muted' }, 'Aucune entrée.')),
      !A.done && h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: async () => { await loadAudit(true); document.getElementById('auditlist').replaceChildren(...A.entries.map(entryCard)); } }, 'Charger plus')));
  }

  // ---------- mon compte ----------
  function renderAccount() {
    const err = h('div', { class: 'error', hidden: true });
    const o = h('input', { type: 'password', autocomplete: 'current-password', required: true }), n = h('input', { type: 'password', autocomplete: 'new-password', required: true });
    const vd = G.viewData(), st = vd.settings || {};
    let company = null;
    if (can('settings.manage')) {
      const f = { company_name: h('input', { value: st.company_name || '', maxlength: 80 }), address: h('input', { value: st.address || '', maxlength: 160 }), phone: h('input', { value: st.phone || '', maxlength: 40 }), tax_id: h('input', { value: st.tax_id || '', maxlength: 40 }),
                  tax_rate: h('input', { type: 'number', min: 0, max: 30, step: 1, value: st.tax_rate || 0 }), footer: h('input', { value: st.footer || '', maxlength: 200, placeholder: 'Merci de votre confiance.' }),
                  doc_lang: h('select', {}, h('option', { value: 'auto' }, 'Automatique · Automatic'), ...(I18N.languages || []).map(l => h('option', { value: l.code, 'data-notr': '' }, l.name))) };
      f.doc_lang.value = st.doc_lang || 'auto';
      company = h('form', { class: 'card', onsubmit: async (e) => { e.preventDefault(); const data = {}; for (const [k, el] of Object.entries(f)) data[k] = k === 'tax_rate' ? Number(el.value || 0) : el.value; const r = await G.submit({ type: 'settings.update', data }); toast(r.status === 'ok' || r.status === 'queued' ? 'Informations enregistrées.' : r.message); } },
        h('h2', {}, 'Informations de l\'entreprise (en-tête des factures)'), field('Nom de l\'entreprise', f.company_name), field('Adresse', f.address), field('Téléphone', f.phone), field('N° fiscal / RCCM', f.tax_id), field('Taux de taxe inclus dans les prix, en % (0 : aucun) — à confirmer avec votre expert-comptable', f.tax_rate), field('Pied de page des factures', f.footer), field('Langue des documents imprimés (factures, devis, PDF). Automatique : la langue de l\'appareil de la personne qui imprime. Les PDF n\'existent qu\'en français et en anglais.', f.doc_lang),
        h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Enregistrer')));
    }
    const twofa = (() => {
      const box = h('div', { class: 'card' }), msg = h('div', { class: 'error', hidden: true });
      const draw = (extra) => box.replaceChildren(h('h2', {}, 'Double authentification (recommandée)'), h('p', { class: 'muted' }, state.me.totp_on ? '✔ Active : un code à 6 chiffres, changeant toutes les 30 secondes, est demandé à chaque connexion.' : 'Ajoute un second verrou : même si votre mot de passe est volé, il faut aussi le code de votre téléphone (Google Authenticator, Microsoft Authenticator, Aegis, FreeOTP…).'), extra, msg);
      const fail = (x) => { msg.textContent = x.message; msg.hidden = false; };
      const start2 = async () => { msg.hidden = true; try {
        const r = await api('/api/2fa/begin', { method: 'POST' }), c = h('input', { inputmode: 'numeric', maxlength: 8, placeholder: '6 chiffres' });
        draw(h('div', {}, h('p', {}, 'Dans votre application d\'authentification, ajoutez un compte « saisie manuelle » avec cette clé (type : basé sur le temps) :'), h('div', { class: 'big breakall' }, r.secret), field('Puis tapez le code affiché', c),
          h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => renderAccount() }, 'Annuler'), h('button', { class: 'primary', type: 'button', onclick: async () => { try { await api('/api/2fa/enable', { method: 'POST', body: { code: c.value } }); state.me.totp_on = true; toast('Double authentification activée.'); renderAccount(); } catch (x) { fail(x); } } }, 'Activer'))));
      } catch (x) { fail(x); } };
      const stop = () => { const pw = h('input', { type: 'password', autocomplete: 'current-password' }), c = h('input', { inputmode: 'numeric', maxlength: 8, placeholder: '6 chiffres' });
        draw(h('div', {}, field('Mot de passe', pw), field('Code actuel', c), h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => renderAccount() }, 'Annuler'), h('button', { class: 'danger', type: 'button', onclick: async () => { try { await api('/api/2fa/disable', { method: 'POST', body: { password: pw.value, code: c.value } }); state.me.totp_on = false; toast('Double authentification désactivée.'); renderAccount(); } catch (x) { fail(x); } } }, 'Désactiver')))); };
      draw(state.me.totp_on ? h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: stop }, 'Désactiver')) : h('p', {}, h('button', { class: 'primary', type: 'button', onclick: start2 }, 'Activer la double authentification')));
      return box;
    })();
    const sessBox = h('div', { class: 'card' }, h('h2', {}, 'Mes connexions'), h('p', { class: 'muted' }, 'Chargement…'));
    const loadSessions = () => api('/api/sessions' + (can('users.manage') ? '?all=true' : '')).then((r) => {
      const close = async (body) => { try { const x = await api('/api/sessions/revoke', { method: 'POST', body }); toast(x.revoked + ' connexion(s) fermée(s).'); loadSessions(); } catch (e) { toast(e.message); } };
      const others = r.sessions.filter((x) => x.current === false && x.user === state.me.username).length;
      sessBox.replaceChildren(h('h2', {}, can('users.manage') ? 'Connexions ouvertes (toute l\'équipe)' : 'Mes connexions'), h('p', { class: 'muted' }, 'Chaque appareil connecté à votre compte. Une connexion que vous ne reconnaissez pas ? Fermez-la, puis changez votre mot de passe.'),
        ...r.sessions.map((x) => h('div', { class: 'row' }, h('span', { class: 'grow' }, `${x.current ? '● Cet appareil · ' : ''}${can('users.manage') ? x.user + ' · ' : ''}${shortDevice(x.device)}${x.ip ? ' · ' + x.ip : ''} · ouverte le ${fmtDate(x.created_at)}`), !x.current && h('a', { href: '#', onclick: (e) => { e.preventDefault(); close({ id: x.id }); } }, 'Fermer'))),
        others > 0 && h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: () => close({ others: true }) }, 'Fermer toutes mes autres connexions')));
    }).catch(() => { sessBox.replaceChildren(); });
    if (state.online) loadSessions(); else sessBox.replaceChildren();
    let logoCard = null, signatureCard = null;
    if (can('settings.manage')) {
      const prev = h('img', { alt: 'Logo actuel', class: 'logo-prev', hidden: true }), msg = h('div', { class: 'muted' });
      const refresh = (known = !!st.has_logo) => { prev.hidden = true; if (!known) { msg.textContent = 'Aucun logo pour le moment.'; return; } fetch('/api/logo', { cache: 'no-store' }).then((r) => { if (r.ok) return r.blob(); throw new Error('none'); }).then((b) => { prev.src = URL.createObjectURL(b); prev.hidden = false; msg.textContent = 'Ce logo figure sur les factures, reçus, devis et bons de livraison.'; }).catch(() => { msg.textContent = 'Aucun logo pour le moment.'; }); };
      const file = h('input', { type: 'file', accept: 'image/png,image/jpeg', onchange: () => { const f = file.files[0]; if (!f) return; if (f.size > 250000) { toast('Image trop lourde (250 Ko au plus).'); return; }
        const fr = new FileReader(); fr.onload = async () => { try { await api('/api/logo', { method: 'POST', body: { image: String(fr.result).split(',')[1] } }); toast('Logo enregistré.'); refresh(true); } catch (e) { toast(e.message); } }; fr.readAsDataURL(f); } });
      logoCard = h('div', { class: 'card' }, h('h2', {}, 'Logo de l\'entreprise'), prev, msg, field('Choisir une image (PNG ou JPEG, 250 Ko au plus)', file),
        h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: async () => { try { await api('/api/logo', { method: 'DELETE' }); toast('Logo retiré.'); refresh(false); } catch (e) { toast(e.message); } } }, 'Retirer le logo')));
      if (state.online) refresh();
      const sigPrev = h('img', { alt: 'Signature actuelle', class: 'logo-prev', hidden: true }), sigMsg = h('div', { class: 'muted' });
      const sigRefresh = (known = !!st.has_signature) => { sigPrev.hidden = true; if (!known) { sigMsg.textContent = 'Aucune signature pour le moment.'; return; } fetch('/api/signature', { cache: 'no-store' }).then((r) => { if (r.ok) return r.blob(); throw new Error('none'); }).then((b) => { sigPrev.src = URL.createObjectURL(b); sigPrev.hidden = false; sigMsg.textContent = 'Cette signature est ajoutée aux factures et aux rapports PDF.'; }).catch(() => { sigMsg.textContent = 'Aucune signature pour le moment.'; }); };
      const sigFile = h('input', { type: 'file', accept: 'image/png,image/jpeg', onchange: () => { const f = sigFile.files[0]; if (!f) return; if (f.size > 250000) { toast('Image trop lourde (250 Ko au plus).'); return; }
        const fr = new FileReader(); fr.onload = async () => { try { await api('/api/signature', { method: 'POST', body: { image: String(fr.result).split(',')[1] } }); toast('Signature enregistrée.'); sigRefresh(true); } catch (e) { toast(e.message); } }; fr.readAsDataURL(f); } });
      signatureCard = h('div', { class: 'card' }, h('h2', {}, 'Signature sur les documents'), sigPrev, sigMsg, field('Choisir une image de signature (PNG ou JPEG, 250 Ko au plus)', sigFile),
        h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: async () => { try { await api('/api/signature', { method: 'DELETE' }); toast('Signature retirée.'); sigRefresh(false); } catch (e) { toast(e.message); } } }, 'Retirer la signature')));
      if (state.online) sigRefresh();
    }
    const langCard = h('div', { class: 'card' }, h('h2', {}, 'Langue'), h('p', { class: 'muted' }, 'Choisissez la langue de l\'interface (mémorisée sur cet appareil). Les documents imprimés (factures, devis, PDF) suivent la langue réglée pour l\'entreprise.'), I18N.switcher());
    let data = null;
    if (can('accounting.read')) {
      const list = h('div', { class: 'muted' }), extra = h('div', {});
      data = h('div', { class: 'card' }, h('h2', {}, 'Sauvegarde et export de vos données'),
        h('p', { class: 'muted' }, 'Vos données sont à vous. L\'export CSV (une table par fichier, sans mots de passe) vous permet de les sortir à tout moment. La sauvegarde complète (base entière) se restaure telle quelle ; gardez-en une copie HORS de cet ordinateur.'),
        h('div', { class: 'row' }, h('a', { class: 'btn', href: '/api/export/all.zip', download: '' }, 'Exporter tout en CSV (ZIP)'), can('backup.run') && h('a', { class: 'btn', href: '/api/backup/download', download: '' }, 'Télécharger une sauvegarde complète')),
        can('backup.run') && h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: async () => { try { const r = await api('/api/backup/run', { method: 'POST' }); toast('Sauvegarde créée : ' + r.name); renderAccount(); } catch (x) { toast(x.message); } } }, 'Sauvegarder maintenant sur le serveur')), can('backup.run') && list, can('backup.run') && extra);
      if (can('backup.run')) api('/api/backup/list').then((r) => { const e = r.encryption || {}, modes = { dpapi: 'clé protégée par Windows', phrase: 'clé dérivée d\'une phrase de passe', fichier: 'clé dans un fichier' };
        const sauv = r.backups.length ? `Sauvegardes automatiques du serveur (une par jour, ${r.backups.length} gardées, la plus récente : ${r.backups[0].name.slice(8, 12)}-${r.backups[0].name.slice(12, 14)}-${r.backups[0].name.slice(14, 16)}). Dossier : ${r.dir}` : 'Aucune sauvegarde automatique pour le moment.';
        const chif = e.postgresql ? '🐘 Base PostgreSQL : le chiffrement dépend du serveur (disque chiffré, connexion TLS).' : e.fichier_chiffre ? '🔒 Base chiffrée (AES-256) : ' + (modes[e.mode] || '') + '. Les sauvegardes sont chiffrées avec la même clé : sans elle, elles sont illisibles.' : e.bibliotheque ? '⚠ Base NON chiffrée.' : '⚠ Base NON chiffrée : bibliothèque de chiffrement absente.';
        const ex = r.extra || {}, st = ex.status, backupPath = String(r.dir || '').replaceAll('\\', '/'), hosted = backupPath.startsWith('/data/'), dirIn = h('input', { value: ex.dir || '', placeholder: 'Ex. E:\\Sauvegardes (clé USB) ou un dossier synchronisé', maxlength: 260 }), erreur = h('div', { class: 'error', hidden: true });
        const etat = !ex.dir ? '⚠ Aucune copie hors de l\'ordinateur : vos sauvegardes sont toutes au même endroit que vos données.' : st ? (st.ok ? `✔ Dernière copie hors de l'ordinateur : ${st.ts.replace('T', ' ').slice(0, 16)} (${st.name}), vérifiée identique.` : `⚠ Dernière copie en échec : ${st.message}`) : 'Copie hors de l\'ordinateur activée : en attente de la prochaine sauvegarde.';
        extra.replaceChildren(h('h3', {}, hosted ? 'Sauvegarde externe à prévoir' : 'Copie de secours hors de cet ordinateur'),
          h('p', { class: 'muted' }, hosted ? 'Cette application tourne sur Railway. Les sauvegardes automatiques et le dossier serveur ci-dessus restent dans l’environnement Railway : ils ne sont pas copiés sur votre ordinateur ni sur une clé USB. Téléchargez régulièrement une sauvegarde complète et gardez-la dans un emplacement sûr hors de Railway. Une copie automatique dans un stockage cloud demande la configuration d’un fournisseur séparé.' : 'Une panne ou un vol de l\'ordinateur emporterait aussi les sauvegardes qui sont dessus. Indiquez un dossier sur une clé USB, un disque externe ou un dossier synchronisé : chaque sauvegarde y sera copiée, vérifiée, et les 30 dernières y sont gardées.'),
          h('div', { class: st && !st.ok ? 'error' : 'muted' }, hosted ? 'Les sauvegardes quotidiennes restent utiles pour une restauration côté serveur, mais ne remplacent pas une copie téléchargée hors site.' : etat),
          hosted ? h('a', { class: 'btn', href: '/api/backup/download', download: '' }, 'Télécharger une sauvegarde complète') : field('Dossier de copie', dirIn), hosted ? null : erreur,
          !hosted && h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: async () => { erreur.hidden = true; try { const x = await api('/api/backup/extra', { method: 'POST', body: { dir: dirIn.value } }); toast(x.copy && x.copy.ok ? 'Copie hors de l\'ordinateur activée et vérifiée.' : 'Copie activée, mais la première copie a échoué : ' + ((x.copy || {}).message || '')); renderAccount(); } catch (x) { erreur.textContent = x.message; erreur.hidden = false; } } }, 'Enregistrer et copier maintenant'),
            ex.dir && h('button', { class: 'secondary', type: 'button', onclick: async () => { try { await api('/api/backup/extra', { method: 'DELETE' }); toast('Copie hors de l\'ordinateur désactivée.'); renderAccount(); } catch (x) { toast(x.message); } } }, 'Désactiver la copie')));
        list.replaceChildren(h('div', {}, sauv), h('div', {}, chif), (e.originaux_a_supprimer || []).length ? h('div', { class: 'error' }, 'Anciennes copies en clair à supprimer vous-même, après vérification : dossier clair_a_supprimer.') : ''); }).catch(() => { list.textContent = ''; });
    }
    const readyBox = h('div', {}); if (can('audit.seal') && state.online) loadReadiness(readyBox);
    view.replaceChildren(h('h1', {}, 'Mon compte'), h('div', { class: 'card' }, h('strong', {}, state.me.full_name), h('div', { class: 'muted' }, `${state.me.username} · ${state.me.role_label}`), h('div', { class: 'muted' }, 'Cet appareil : n° ' + deviceId.slice(0, 8))), readyBox, langCard, ...(G.reminderCards ? G.reminderCards() : []), twofa, sessBox, company, logoCard, signatureCard, data,
      h('form', { class: 'card', onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/password', { method: 'POST', body: { old: o.value, new: n.value } }); toast('Mot de passe changé.'); o.value = ''; n.value = ''; } catch (x) { err.textContent = x.message; err.hidden = false; } } },
        h('h2', {}, 'Changer le mot de passe'), field('Mot de passe actuel', o), field('Nouveau mot de passe', n), err, h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Changer'))),
      h('p', {}, h('button', { class: 'danger', type: 'button', onclick: async () => { try { await api('/api/logout', { method: 'POST' }); } catch { /* hors connexion : la session expirera */ } state.me = null; G.render(); } }, 'Se déconnecter')));
  }

  tabs.push({ id: 'users', label: 'Équipe', show: () => can('users.manage'), render: renderUsers });
  tabs.push({ id: 'audit', label: 'Journal', show: () => can('audit.read'), render: renderAudit });
  tabs.push({ id: 'account', label: 'Mon compte', render: renderAccount });
})();
