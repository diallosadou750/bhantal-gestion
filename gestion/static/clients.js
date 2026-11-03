/* Écran Clients : fiches, solde dû, relance WhatsApp. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, dlg, view, tabs, $ } = G;
  let q = '';

  function waLink(c, bal, lateOnly = false) {
    const phone = (c.phone || '').replace('+', '');
    if (!phone) return null;
    const company = (G.viewData().settings.company_name) || 'notre entreprise';
    const text = bal > 0 ? (lateOnly
      ? `Bonjour ${c.name}, sauf erreur de notre part, le montant en retard sur votre compte chez ${company} est de ${money(bal)}. À quelle date prévoyez-vous de le régler ? Merci.`
      : `Bonjour ${c.name}, sauf erreur de notre part, il reste ${money(bal)} à régler chez ${company}. Pouvez-vous nous indiquer la date de paiement ? Merci.`)
      : `Bonjour ${c.name}, merci pour votre confiance. ${company} reste à votre disposition.`;
    return `https://wa.me/${phone}?text=${encodeURIComponent(text)}`;
  }

  /** Solde et retard de chaque client, calculés sur les ventes synchronisées. */
  function accounts(vd) {
    const m = new Map();
    for (const s of vd.sales) {
      if (s.status !== 'active' || s.balance <= 0) continue;
      const a = m.get(s.client_id) || { balance: 0, overdue: 0, oldest: 0 };
      a.balance += s.balance;
      if (s.due_date && s.due_date < today()) { a.overdue += s.balance; a.oldest = Math.max(a.oldest, G.daysBetween(s.due_date, today())); }
      m.set(s.client_id, a);
    }
    return m;
  }
  G.accounts = accounts; G.waLink = waLink;

  function clientForm(c) {
    const isNew = !c;
    const x = c || { name: '', kind: '', phone: '', zone: '', credit_limit: 0, note: '', owner_user_id: state.me.id };
    const sales = state.data.salespeople || [];
    const f = { name: h('input', { value: x.name, required: true, maxlength: 120 }), kind: h('input', { value: x.kind || '', maxlength: 60, placeholder: 'Boutique, restaurant, particulier…' }), phone: h('input', { value: x.phone || '', inputmode: 'tel', placeholder: '224620000000' }),
                zone: h('input', { value: x.zone || '', maxlength: 60 }) };
    if ('credit_limit' in x) f.credit = h('input', { type: 'number', min: 0, step: 1000, value: x.credit_limit ?? 0 });
    if ('note' in x) f.note = h('textarea', { maxlength: 500, rows: 3 }, x.note || '');
    if (can('clients.write') && sales.length) f.owner = h('select', {}, h('option', { value: '' }, '— non attribué —'), ...sales.map((s) => h('option', { value: s.id, selected: s.id === x.owner_user_id }, s.full_name)));
    const err = h('div', { class: 'error', hidden: true });
    const save = async (e) => {
      e.preventDefault(); err.hidden = true;
      const vals = { name: f.name.value, kind: f.kind.value, phone: f.phone.value, zone: f.zone.value };
      if (f.credit) vals.credit_limit = Number(f.credit.value || 0);
      if (f.note) vals.note = f.note.value;
      if (f.owner) vals.owner_user_id = f.owner.value === '' ? null : Number(f.owner.value);
      let op;
      if (isNew) op = { type: 'client.create', data: { id: G.uuid(), ...vals } };
      else {
        const changes = {}; for (const [k, v] of Object.entries(vals)) if (String(v ?? '') !== String(c[k] ?? '')) changes[k] = v;
        if (!Object.keys(changes).length) { dlg.close(); return; }
        const qd = G.queue(), creation = qd.find((o) => o.type === 'client.create' && o.data.id === c.id);
        if (creation) { Object.assign(creation.data, changes); G.saveQueue(qd); dlg.close(); G.flush({ silent: true }); G.render(); return; }   // pas encore envoyée : on corrige la création
        op = { type: 'client.update', data: { id: c.id, base_version: c.version, changes } };
      }
      const r = await G.submit(op);
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render();
    };
    dlg.replaceChildren(h('h2', {}, isNew ? 'Nouveau client' : 'Modifier le client'), h('form', { onsubmit: save },
      field('Nom *', f.name), field('Type', f.kind), field('Téléphone', f.phone), field('Zone / quartier', f.zone), f.credit && field('Limite de crédit (GNF) — 0 : aucun crédit accordé', f.credit), f.owner && field('Commercial responsable', f.owner), f.note && field('Note interne', f.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }
  G.clientForm = clientForm;

  function render() {
    const vd = G.viewData(), acc = accounts(vd);
    const list = vd.clients.filter((c) => !q || (c.name + ' ' + c.code + ' ' + (c.phone || '') + ' ' + (c.zone || '')).toLowerCase().includes(q.toLowerCase())).sort((a, b) => a.name.localeCompare(b.name, 'fr'));
    const search = h('input', { type: 'search', id: 'csearch', placeholder: 'Rechercher un client…', value: q, 'aria-label': 'Rechercher', oninput: (e) => { q = e.target.value; const p = e.target.selectionStart; render(); const s = $('#csearch'); s.focus(); s.setSelectionRange(p, p); } });
    const cards = list.map((c) => {
      const a = acc.get(c.id) || { balance: 0, overdue: 0, oldest: 0 }, wa = waLink(c, a.balance);
      return h('article', { class: 'card client' },
        h('div', { class: 'head' }, h('span', { class: 'name' }, c.name), h('span', { class: 'muted' }, c.code)),
        h('div', { class: 'muted' }, [c.kind, c.zone].filter(Boolean).join(' · ') || ' '),
        a.balance > 0 && h('div', {}, h('span', { class: 'badge ' + (a.overdue ? 'bad' : 'warn') }, a.overdue ? `En retard : ${money(a.overdue)} (${a.oldest} j)` : `Solde dû : ${money(a.balance)}`)),
        ('credit_limit' in c) && h('div', { class: 'muted' }, 'Limite de crédit : ' + (c.credit_limit ? money(c.credit_limit) : 'aucun crédit accordé')),
        vd.pend.has(c.id) && h('span', { class: 'badge warn' }, state.online ? 'Envoi en cours…' : 'En attente de connexion'),
        h('div', { class: 'links' }, c.phone && h('a', { href: 'tel:' + c.phone }, '📞 Appeler'), wa && h('a', { href: wa, target: '_blank', rel: 'noopener noreferrer' }, a.balance > 0 ? '💬 Relancer' : '💬 WhatsApp'),
          (can('sales.create')) && c.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); G.saleForm({ clientId: c.id }); } }, '🧾 Vendre'),
          (can('clients.write') || can('clients.write_own')) && h('a', { href: '#', onclick: (e) => { e.preventDefault(); clientForm(c); } }, '✏️ Modifier'),
          can('clients.delete') && c.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (confirm('Supprimer cette fiche ? Elle reste conservée dans le journal.')) { G.submit({ type: 'client.delete', data: { id: c.id, base_version: c.version } }).then(() => G.render()); } } }, '🗑️ Supprimer')));
    });
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, `Clients (${list.length})`), (can('clients.create') || can('clients.write')) && h('button', { class: 'primary', type: 'button', onclick: () => clientForm(null) }, '＋ Nouveau client')),
      h('div', { class: 'card' }, search), list.length ? h('div', { class: 'list two' }, cards) : h('p', { class: 'muted' }, q ? 'Aucun résultat.' : 'Aucun client pour le moment.'));
    if (q) { const s = $('#csearch'); s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  tabs.push({ id: 'clients', label: 'Clients', show: () => can('clients.read_all') || can('clients.read_own'), render });
})();
