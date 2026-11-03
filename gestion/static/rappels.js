/* Rappels : (1) e-mail quotidien configuré par le propriétaire ; (2) résumé du jour avec envoi par WhatsApp (un clic de la personne) ; (3) notification sur cet appareil tant que l'application est ouverte. */
(() => {
  'use strict';
  const { h, field, can, state, api, toast } = G;
  const LS = 'gestion_rappels';
  const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* navigateur sans stockage : on s'en passe */ } };
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
  const waLink = (text) => 'https://wa.me/?text=' + encodeURIComponent(text);

  // ------------------------------------------------------------ notification sur cet appareil (application ouverte)
  async function notifyOnce() {
    if (!('Notification' in window) || Notification.permission !== 'granted' || lsGet(LS) !== 'on') return;
    if (!state.me || !can('reports.read') || !state.online || lsGet(LS + '_jour') === todayIso()) return;
    try {
      const r = await api('/api/digest');
      lsSet(LS + '_jour', todayIso());
      const n = r.digest.items.length; if (!n) return;
      const opts = { body: `${n} point(s) à voir : ${r.digest.items.slice(0, 3).map((x) => x.label).join(' · ')}`, icon: '/icon-192.png', tag: 'resume-du-jour' };
      const reg = 'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistration() : null;
      if (reg && reg.showNotification) reg.showNotification('Gestion libre', opts); else new Notification('Gestion libre', opts);
    } catch { /* hors connexion ou droits : pas de rappel, rien de grave */ }
  }
  setInterval(notifyOnce, 60 * 60 * 1000);                 // une vérification par heure tant que l'application reste ouverte
  setTimeout(notifyOnce, 4000);                            // et une peu après l'ouverture

  // ------------------------------------------------------------ cartes de « Mon compte »
  function deviceCard() {
    if (!can('reports.read')) return null;
    const box = h('div', { class: 'card' }), out = h('pre', { class: 'muted prewrap', hidden: true });
    const draw = () => {
      const supported = 'Notification' in window, perm = supported ? Notification.permission : 'denied', on = lsGet(LS) === 'on' && perm === 'granted';
      box.replaceChildren(h('h2', {}, 'Rappels sur cet appareil'),
        h('p', { class: 'muted' }, 'Une fois par jour, au premier contrôle, cet appareil vous affiche les points à voir (retards de paiement, stock, livraisons…). Cela ne fonctionne que tant que l\'application est ouverte (page ou application installée) : fermée, elle ne peut rien afficher. Pour être prévenu application fermée, utilisez le rappel par e-mail.'),
        !supported ? h('div', { class: 'error' }, 'Ce navigateur ne sait pas afficher de notifications.') : perm === 'denied' ? h('div', { class: 'error' }, 'Les notifications sont bloquées pour ce site dans le navigateur : autorisez-les dans ses réglages, puis revenez ici.') : null,
        h('div', { class: 'row' },
          supported && perm !== 'denied' && h('button', { class: on ? 'secondary' : 'primary', type: 'button', onclick: async () => {
            if (on) { lsSet(LS, 'off'); toast('Rappels désactivés sur cet appareil.'); draw(); return; }
            const p = await Notification.requestPermission();
            if (p === 'granted') { lsSet(LS, 'on'); lsSet(LS + '_jour', ''); toast('Rappels activés sur cet appareil.'); notifyOnce(); } else toast('Autorisation refusée.');
            draw();
          } }, on ? 'Désactiver les rappels sur cet appareil' : 'Activer les rappels sur cet appareil'),
          h('button', { class: 'secondary', type: 'button', onclick: async () => {
            try { const r = await api('/api/digest?names=true'); out.textContent = r.text; out.hidden = false; wa.href = waLink(r.text); wa.hidden = false; } catch (e) { toast(e.message); }
          } }, 'Voir le résumé d\'aujourd\'hui')),
        out, wa);
    };
    const wa = h('a', { class: 'btn', href: '#', target: '_blank', rel: 'noopener', hidden: true }, 'Envoyer ce résumé par WhatsApp');
    draw();
    return box;
  }

  function emailCard() {
    if (!can('settings.manage')) return null;
    const box = h('div', { class: 'card' }, h('h2', {}, 'Rappels par e-mail'), h('p', { class: 'muted' }, 'Chargement…'));
    api('/api/notifications').then((r) => {
      const s = r.settings, err = h('div', { class: 'error', hidden: true }), res = h('div', { class: 'muted' });
      const f = {
        enabled: h('input', { type: 'checkbox', checked: s.enabled }), host: h('input', { value: s.host || '', placeholder: 'smtp.gmail.com', maxlength: 253 }),
        port: h('input', { type: 'number', value: s.port, min: 1, max: 65535 }), security: h('select', {}, h('option', { value: 'starttls' }, 'STARTTLS (port 587)'), h('option', { value: 'ssl' }, 'SSL (port 465)'), h('option', { value: 'none' }, 'Aucune (déconseillé)')),
        user: h('input', { value: s.user || '', autocomplete: 'off', maxlength: 200 }), password: h('input', { type: 'password', value: '', autocomplete: 'new-password', placeholder: s.has_password ? (s.password_from_env ? 'défini par le serveur' : '•••••••• (inchangé)') : '' }),
        sender: h('input', { value: s.sender || '', placeholder: 'gestion@exemple.com', maxlength: 200 }), to: h('input', { value: (s.to || []).join(', '), placeholder: 'patron@exemple.com', maxlength: 400 }),
        hour: h('input', { type: 'time', value: s.hour || '07:30' }), names: h('input', { type: 'checkbox', checked: s.names }), always: h('input', { type: 'checkbox', checked: s.always }),
      };
      f.security.value = s.security;
      const box2 = (label, input) => h('label', { class: 'check' }, input, ' ' + label);
      const payload = () => ({ enabled: f.enabled.checked, host: f.host.value, port: Number(f.port.value), security: f.security.value, user: f.user.value, sender: f.sender.value, to: f.to.value, hour: f.hour.value, names: f.names.checked, always: f.always.checked,
        ...(f.password.value ? { password: f.password.value } : {}) });
      const status = (st) => st ? `${st.ok ? '✔' : '⚠'} ${st.ts.replace('T', ' ').slice(0, 16)} : ${st.message}` : 'Aucun envoi pour le moment.';
      res.textContent = status(s.last_status);
      box.replaceChildren(h('h2', {}, 'Rappels par e-mail'),
        h('p', { class: 'muted' }, 'Chaque matin, à l\'heure choisie, un résumé des points à voir part vers vos adresses (et seulement s\'il y a quelque chose à signaler). Rien ne part tant que vous n\'avez pas rempli et activé cette page. Pour Gmail ou Outlook, utilisez un « mot de passe d\'application », jamais votre vrai mot de passe. Un e-mail n\'est pas un canal chiffré : par défaut le message ne contient que des nombres et des montants, sans nom de client ni de produit.'),
        h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/notifications', { method: 'POST', body: payload() }); toast('Rappels enregistrés.'); G.render(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
          box2('Activer les rappels par e-mail', f.enabled), field('Serveur d\'envoi', f.host), field('Port', f.port), field('Sécurité de la connexion', f.security),
          field('Identifiant (souvent votre adresse e-mail ; vide si le serveur n\'en demande pas)', f.user), field('Mot de passe d\'application', f.password), field('Adresse d\'expédition', f.sender),
          field('Adresses de destination (cinq au plus, séparées par des virgules)', f.to), field('Heure d\'envoi', f.hour),
          box2('Inclure les noms des clients et des produits dans le message', f.names), box2('Envoyer un message même quand il n\'y a rien à signaler', f.always), err,
          h('div', { class: 'row' }, h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'),
            h('button', { class: 'secondary', type: 'button', onclick: async () => { err.hidden = true; res.textContent = 'Envoi en cours…'; try { await api('/api/notifications', { method: 'POST', body: payload() }); const t = await api('/api/notifications/test', { method: 'POST' }); res.textContent = status(t); } catch (x) { err.textContent = x.message; err.hidden = false; res.textContent = ''; } } }, 'Envoyer un essai maintenant'))),
        res);
    }).catch(() => box.replaceChildren(h('h2', {}, 'Rappels par e-mail'), h('p', { class: 'muted' }, 'Disponible en ligne seulement.')));
    return box;
  }

  G.reminderCards = () => [emailCard(), deviceCard()].filter(Boolean);
})();
