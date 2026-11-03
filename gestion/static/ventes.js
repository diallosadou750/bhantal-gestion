/* Écrans Ventes (liste, formulaire, détail, paiements, avoirs) et Créances. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, addDays, daysBetween, can, state, dlg, view, tabs, $ } = G;
  const filters = { q: '', f: 'all' };
  const clientName = (vd, id) => (vd.clients.find((c) => c.id === id) || {}).name || '—';

  function statusBadge(s) {
    if (s.status === 'cancelled') return h('span', { class: 'badge bad' }, 'Annulée');
    if (s.balance <= 0) return h('span', { class: 'badge' }, 'Soldée');
    if (s.due_date && s.due_date < today()) return h('span', { class: 'badge bad' }, `En retard ${daysBetween(s.due_date, today())} j · reste ${money(s.balance)}`);
    return h('span', { class: 'badge warn' }, `Reste ${money(s.balance)}`);
  }
  const openInvoice = (id) => window.open('/api/invoice/' + id, '_blank', 'noopener');
  const openReceipt = (id) => window.open('/api/receipt/' + id, '_blank', 'noopener');
  G.statusBadge = statusBadge;

  // ---------- liste ----------
  function renderSales() {
    const vd = G.viewData();
    const chips = [['all', 'Toutes'], ['today', 'Aujourd\'hui'], ['credit', 'À crédit'], ['unpaid', 'Impayées'], ['late', 'En retard'], ['todeliver', 'À livrer']];
    const test = { all: () => true, today: (s) => s.sale_date === today(), credit: (s) => s.kind === 'credit', unpaid: (s) => s.status === 'active' && s.balance > 0, late: (s) => s.status === 'active' && s.balance > 0 && s.due_date && s.due_date < today(), todeliver: (s) => s.status === 'active' && remainingOf(s).some((q) => q > 0) };
    const list = vd.sales.filter(test[filters.f]).filter((s) => !filters.q || (s.number + ' ' + clientName(vd, s.client_id)).toLowerCase().includes(filters.q.toLowerCase()));
    const search = h('input', { type: 'search', id: 'ssearch', placeholder: 'Rechercher (n° ou client)…', value: filters.q, 'aria-label': 'Rechercher', oninput: (e) => { filters.q = e.target.value; const p = e.target.selectionStart; renderSales(); const s = $('#ssearch'); s.focus(); s.setSelectionRange(p, p); } });
    const cards = list.slice(0, 200).map((s) => h('article', { class: 'card sale', onclick: () => saleDetail(s.id) },
      h('div', { class: 'head' }, h('span', { class: 'name' }, `${s.number} · ${clientName(vd, s.client_id)}`), h('span', { class: 'name' }, money(s.total))),
      h('div', { class: 'muted' }, `${fmtDay(s.sale_date)} · ${s.kind === 'cash' ? 'Comptant' : 'Crédit' + (s.due_date ? ' · échéance ' + fmtDay(s.due_date) : '')}${s.seller_name ? ' · ' + s.seller_name : ''}`),
      h('div', { class: 'row' }, statusBadge(s), vd.pend.has(s.id) && h('span', { class: 'badge warn' }, state.online ? 'Envoi en cours…' : 'En attente de connexion'),
        s.delivery_due && !s.delivered_at && s.status === 'active' && s.delivery_due < today() && h('span', { class: 'badge bad' }, 'Livraison en retard'))));
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, `Ventes (${list.length})`), can('sales.create') && h('button', { class: 'primary', type: 'button', onclick: () => saleForm({}) }, '＋ Nouvelle vente')),
      h('div', { class: 'card' }, search, h('div', { class: 'chips' }, chips.map(([id, label]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(filters.f === id), onclick: () => { filters.f = id; renderSales(); } }, label)))),
      list.length ? h('div', { class: 'list two' }, cards) : h('p', { class: 'muted' }, 'Aucune vente.'));
    if (filters.q) { const s = $('#ssearch'); s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }

  G.openUnpaidSales = () => { filters.f = 'unpaid'; filters.q = ''; state.tab = 'ventes'; G.render(); };

  // ---------- formulaire de vente ----------
  function saleForm({ clientId }) {
    const vd = G.viewData();
    if (!vd.clients.length) { G.toast('Créez d\'abord un client.'); return; }
    const maxDisc = state.me.role === 'sales' ? 10 : 100, free = can('sales.free_line'), priceEdit = can('sales.override_price');
    const err = h('div', { class: 'error', hidden: true });
    const f = {
      client: h('select', {}, ...vd.clients.slice().sort((a, b) => a.name.localeCompare(b.name, 'fr')).map((c) => h('option', { value: c.id, selected: c.id === clientId }, `${c.name} (${c.code})`))),
      kind: h('select', {}, h('option', { value: 'cash' }, 'Comptant'), can('sales.credit') && h('option', { value: 'credit' }, 'Crédit')),
      method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))),
      paid: h('input', { type: 'number', min: 0, step: 1000, value: 0 }), due: h('input', { type: 'date', value: addDays(today(), 30) }), delivery: h('input', { type: 'date' }),
      note: h('input', { maxlength: 300 }), override: h('input', { type: 'checkbox' }),
    };
    const linesBox = h('div', {}), totalEl = h('div', { class: 'big' }), hint = h('div', { class: 'muted' });
    const rows = [];
    function addRow() {
      const r = {
        product: h('select', { 'aria-label': 'Produit' }, h('option', { value: '' }, free ? '— Autre article —' : '— Choisir —'), ...vd.products.map((p) => h('option', { value: p.id }, `${p.name} — ${money(p.price)}`))),
        name: h('input', { placeholder: 'Désignation (article libre)', 'aria-label': 'Désignation', maxlength: 120 }), qty: h('input', { type: 'number', min: 1, step: 1, value: 1, 'aria-label': 'Quantité' }),
        price: h('input', { type: 'number', min: 0, step: 1, value: 0, 'aria-label': 'Prix unitaire' }), disc: h('input', { type: 'number', min: 0, max: maxDisc, step: 1, value: 0, 'aria-label': 'Remise en pour cent' }), amount: h('strong', {}, '0'),
        depot: h('select', { 'aria-label': 'Dépôt de sortie' }, ...(state.data.depots || []).filter((d) => d.active).map((d) => h('option', { value: d.id }, d.name))),
      };
      const sync = () => {
        const p = vd.products.find((x) => x.id === r.product.value);
        r.name.hidden = !!p; r.price.readOnly = !!p && !priceEdit; if (p && !priceEdit) r.price.value = p.price; else if (p && !Number(r.price.value)) r.price.value = p.price;
        r.amount.textContent = money(Math.floor((Number(r.qty.value || 0) * Number(r.price.value || 0) * (100 - Number(r.disc.value || 0)) + 50) / 100)); recalc();
      };
      if (vd.products.length) { r.product.value = vd.products[0].id; r.price.value = vd.products[0].price; }
      r.product.addEventListener('change', () => { const p = vd.products.find((x) => x.id === r.product.value); if (p) r.price.value = p.price; sync(); });
      for (const k of ['qty', 'price', 'disc', 'name']) r[k].addEventListener('input', sync);
      r.root = h('div', { class: 'card line' }, r.product, r.name, (state.data.depots || []).filter((d) => d.active).length > 1 && field('Sort du dépôt', r.depot), h('div', { class: 'filters' }, field('Quantité', r.qty), field('Prix unitaire', r.price), field(`Remise % (max ${maxDisc})`, r.disc)),
        h('div', { class: 'row' }, h('span', { class: 'grow' }, 'Montant : ', r.amount), h('button', { class: 'danger', type: 'button', onclick: () => { if (rows.length > 1) { rows.splice(rows.indexOf(r), 1); r.root.remove(); recalc(); } } }, 'Retirer')));
      rows.push(r); linesBox.append(r.root); sync();
    }
    const lineData = () => rows.map((r) => ({ product_id: r.product.value || null, name: r.name.value, qty: Number(r.qty.value || 0), unit_price: Number(r.price.value || 0), discount_pct: Number(r.disc.value || 0), depot_id: r.depot.value || 'main' }));
    const total = () => lineData().reduce((a, l) => a + Math.floor((l.qty * l.unit_price * (100 - l.discount_pct) + 50) / 100), 0);
    function recalc() {
      const t = total(), credit = f.kind.value === 'credit';
      totalEl.textContent = 'Total : ' + money(t);
      for (const el of document.querySelectorAll('.credit-only')) el.hidden = !credit;
      const cl = vd.clients.find((c) => c.id === f.client.value), acc = (G.accounts(vd).get(f.client.value) || { balance: 0 });
      hint.textContent = credit && cl ? `Limite : ${'credit_limit' in cl ? (cl.credit_limit ? money(cl.credit_limit) : 'aucun crédit accordé') : 'non visible'} · déjà dû : ${money(acc.balance)} · après cette vente : ${money(acc.balance + t - Number(f.paid.value || 0))}` : '';
    }
    for (const el of [f.client, f.kind, f.paid]) el.addEventListener('input', recalc);
    addRow();
    const creditBox = h('div', { class: 'credit-only', hidden: true }, field('Acompte versé (GNF)', f.paid), field('Échéance de paiement *', f.due), can('sales.override_credit') && h('label', { class: 'check' }, f.override, ' Autoriser malgré la limite de crédit (sera journalisé)'));
    const save = async (e) => {
      e.preventDefault(); err.hidden = true;
      const kind = f.kind.value, data = { id: G.uuid(), client_id: f.client.value, kind, lines: lineData(), pay_method: f.method.value, note: f.note.value };
      if (kind === 'credit') { data.due_date = f.due.value; data.paid_at_sale = Number(f.paid.value || 0); data.override_credit = f.override.checked; }
      if (f.delivery.value) data.delivery_due = f.delivery.value;
      const r = await G.submit({ type: 'sale.create', data });
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render();
      if (r.status === 'ok') { G.toast(`Vente ${r.sale.number} enregistrée.${(r.stock_warnings || []).length ? ' ⚠ ' + r.stock_warnings.join(' ') : ''}`); openInvoiceDialog(r.sale.id, r.sale.number); } else G.toast('Vente mise en attente : elle sera envoyée au retour de la connexion.');
    };
    dlg.replaceChildren(h('h2', {}, 'Nouvelle vente'), h('form', { onsubmit: save },
      field('Client', f.client), field('Type de vente', f.kind), linesBox, h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: addRow }, '＋ Ajouter une ligne')), totalEl,
      field('Mode de paiement', f.method), creditBox, hint, field('Livraison prévue (facultatif)', f.delivery), field('Note', f.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer la vente'))));
    recalc(); dlg.showModal();
  }
  G.saleForm = saleForm;

  function openInvoiceDialog(id, number) {
    dlg.replaceChildren(h('h2', {}, `Vente ${number} enregistrée ✔`), h('p', {}, 'Voulez-vous imprimer ou enregistrer la facture en PDF ?'),
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Plus tard'), h('button', { class: 'primary', type: 'button', onclick: () => { openInvoice(id); dlg.close(); } }, 'Ouvrir la facture')));
    dlg.showModal();
  }

  // ---------- détail d'une vente ----------
  function saleDetail(id) {
    const vd = G.viewData(), s = vd.sales.find((x) => x.id === id); if (!s) return;
    const pays = vd.payments.filter((p) => p.sale_id === id), notes = vd.notes.filter((n) => n.sale_id === id);
    const t = (rows) => h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, ...rows));
    const synced = s.version > 0;
    dlg.replaceChildren(
      h('h2', {}, `${s.number} · ${clientName(vd, s.client_id)}`), h('div', { class: 'row' }, statusBadge(s), s.delivered_at ? h('span', { class: 'badge' }, 'Livrée le ' + fmtDay(s.delivered_at)) : s.delivery_due && h('span', { class: 'badge warn' }, 'Livraison prévue le ' + fmtDay(s.delivery_due))),
      h('p', { class: 'muted' }, `${fmtDay(s.sale_date)} · ${s.kind === 'cash' ? 'Comptant' : 'Crédit, échéance ' + fmtDay(s.due_date)} · vendeur : ${s.seller_name || '—'}`),
      t([h('tr', {}, h('th', {}, 'Article'), h('th', {}, 'Qté'), h('th', {}, 'Prix'), h('th', {}, '%'), h('th', {}, 'Montant')), ...s.lines.map((l) => h('tr', {}, h('td', {}, l.name), h('td', { class: 'num' }, l.qty), h('td', { class: 'num' }, money(l.unit_price)), h('td', { class: 'num' }, l.discount_pct), h('td', { class: 'num' }, money(l.amount))))]),
      h('p', { class: 'big' }, 'Total : ', money(s.total)),
      h('div', { class: 'muted' }, `Payé : ${money(s.paid_total)}${s.credit_notes_total ? ' · avoirs : ' + money(s.credit_notes_total) : ''} · reste : ${money(s.balance)}`),
      (s.paid_at_sale > 0 || pays.length > 0) && h('h3', {}, 'Paiements'),
      s.paid_at_sale > 0 && h('div', { class: 'muted' }, `${fmtDay(s.sale_date)} · ${money(s.paid_at_sale)} à la vente (${s.pay_method})`),
      ...pays.map((p) => h('div', { class: 'row' + (p.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${fmtDay(p.pay_date)} · ${money(p.amount)} · ${p.method}${p.reference ? ' · ' + p.reference : ''}${p.status === 'cancelled' ? ' · annulé : ' + p.cancel_reason : ''}`),
        p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); openReceipt(p.id); } }, 'Reçu'),
        p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); G.attachDialog('payment', p.id, `${money(p.amount)} · ${fmtDay(p.pay_date)} · ${p.method}`, p.status === 'active' && can('payments.create'), () => saleDetail(id)); } }, '📎 Preuve'),
        p.status === 'active' && p.version > 0 && can('payments.cancel') && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const reason = prompt('Motif de l\'annulation du paiement :'); if (reason && reason.trim().length >= 3) { const r = await G.submit({ type: 'payment.cancel', data: { id: p.id, base_version: p.version, reason: reason.trim() } }); if (r.status === 'invalid' || r.status === 'denied') G.toast(r.message); dlg.close(); G.render(); } } }, 'Annuler'))),
      notes.length > 0 && h('h3', {}, 'Avoirs'), ...notes.map((n) => h('div', { class: 'muted' }, `${n.number} · ${fmtDay(n.note_date)} · ${money(n.amount)} · ${n.settlement === 'refund' ? 'remboursé' : 'déduit du solde'} · ${n.reason}${(n.returns || []).length ? ' · rendu en stock : ' + n.returns.map((x) => `${x.qty} × ${x.name}`).join(', ') : ''}`)),
      ...deliverySection(s),
      h('div', { class: 'row actions' },
        synced && s.status === 'active' && s.balance > 0 && can('payments.create') && h('button', { class: 'primary', type: 'button', onclick: () => paymentForm(s) }, 'Encaisser'),
        synced && s.status === 'active' && can('creditnotes.create') && h('button', { class: 'secondary', type: 'button', onclick: () => noteForm(s) }, 'Avoir / retour'),
        synced && s.status === 'active' && !s.delivered_at && can('sales.deliver') && h('button', { class: 'secondary', type: 'button', onclick: async () => { const r = await G.submit({ type: 'sale.deliver', data: { id: s.id, base_version: s.version } }); if (r.status === 'invalid' || r.status === 'denied') G.toast(r.message); dlg.close(); G.render(); } }, 'Tout livrer (crée un bon)'),
        synced && h('button', { class: 'secondary', type: 'button', onclick: () => openInvoice(s.id) }, 'Facture'),
        synced && h('button', { class: 'secondary', type: 'button', onclick: () => G.attachDialog('sale', s.id, `${s.number} · ${clientName(vd, s.client_id)}`, s.status === 'active' && can('sales.create'), () => saleDetail(s.id)) }, '📎 Pièces'),
        synced && s.status === 'active' && can('sales.cancel') && h('button', { class: 'danger', type: 'button', onclick: async () => { const reason = prompt('Motif de l\'annulation de la vente ' + s.number + ' :'); if (reason && reason.trim().length >= 3) { const r = await G.submit({ type: 'sale.cancel', data: { id: s.id, base_version: s.version, reason: reason.trim() } }); G.toast(r.status === 'ok' ? (r.refund_due ? `Vente annulée. ${money(r.refund_due)} à rembourser au client.` : 'Vente annulée.') : r.message); dlg.close(); G.render(); } } }, 'Annuler la vente'),
        h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    if (!dlg.open) dlg.showModal();
  }
  G.saleDetail = saleDetail;

  // ---------- bons de livraison ----------
  const notesOf = (saleId) => (state.data.delivery_notes || []).filter((n) => n.sale_id === saleId);
  function remainingOf(s) {
    const all = notesOf(s.id), act = all.filter((n) => n.status === 'active');
    if (s.delivered_at && !all.length) return s.lines.map(() => 0);                       // livrée avant les bons de livraison
    return s.lines.map((l, i) => Math.max(0, l.qty - act.reduce((a, n) => a + n.lines.filter((x) => x.sale_position === i + 1).reduce((b, x) => b + x.qty, 0), 0)));
  }
  function deliverySection(s) {
    const notes = notesOf(s.id), rem = remainingOf(s), left = rem.reduce((a, b) => a + b, 0), w = can('sales.deliver') && s.status === 'active' && s.version > 0;
    if (!notes.length && !w) return [];
    return [h('h3', {}, 'Livraisons'), left > 0 ? h('div', { class: 'muted' }, 'Reste à livrer : ' + s.lines.map((l, i) => (rem[i] ? `${rem[i]} × ${l.name}` : null)).filter(Boolean).join(', ')) : (s.status === 'active' && h('div', { class: 'muted' }, '✔ Tout est livré.')),
      ...notes.map((n) => h('div', { class: 'row' + (n.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${n.number} · ${fmtDay(n.delivery_date)} · ${n.lines.map((x) => `${x.qty} × ${x.name}`).join(', ')}${n.recipient ? ' · reçu par ' + n.recipient : ''}${n.status === 'cancelled' ? ' · annulé : ' + n.cancel_reason : ''}`),
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); window.open('/api/delivery/' + n.id, '_blank', 'noopener'); } }, 'Imprimer'),
        n.status === 'active' && w && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const reason = prompt('Motif de l\'annulation du bon ' + n.number + ' :'); if (!reason || reason.trim().length < 3) return;
          const r = await G.submit({ type: 'delivery.cancel', data: { id: n.id, base_version: n.version, reason: reason.trim() } }); if (r.status === 'invalid' || r.status === 'denied' || r.status === 'conflict') G.toast(r.message); dlg.close(); G.render(); } }, 'Annuler'))),
      w && left > 0 && h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => deliveryForm(s, rem) }, '＋ Bon de livraison (partiel ou complet)'))];
  }
  function deliveryForm(s, rem) {
    const err = h('div', { class: 'error', hidden: true }), who = h('input', { maxlength: 80, placeholder: 'Nom de la personne qui reçoit' }), when = h('input', { type: 'date', value: today(), min: s.sale_date, max: today() }), note = h('input', { maxlength: 200 });
    const qs = s.lines.map((l, i) => rem[i] > 0 ? { i, l, el: h('input', { type: 'number', min: 0, max: rem[i], step: 1, value: rem[i], 'aria-label': 'Quantité livrée' }) } : null).filter(Boolean);
    dlg.replaceChildren(h('h2', {}, `Bon de livraison · ${s.number}`), h('p', { class: 'muted' }, 'Indiquez ce qui est réellement remis aujourd\'hui. Le stock ne bouge pas (déjà sorti à la vente).'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } err.hidden = true;
        const r = await G.submit({ type: 'delivery.create', data: { id: G.uuid(), sale_id: s.id, delivery_date: when.value, recipient: who.value, note: note.value, lines: qs.map((q) => ({ sale_position: q.i + 1, qty: Number(q.el.value || 0) })).filter((x) => x.qty > 0) } });
        if (r.status === 'invalid' || r.status === 'denied' || r.status === 'conflict') { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Bon ${r.delivery.number} enregistré.`); window.open('/api/delivery/' + r.delivery.id, '_blank', 'noopener'); } },
        ...qs.map((q) => field(`${q.l.name} (reste ${rem[q.i]})`, q.el)), field('Date de livraison', when), field('Reçu par', who), field('Note', note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => G.saleDetail(s.id) }, 'Retour'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer le bon'))));
  }

  function paymentForm(s) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const f = { amount: h('input', { type: 'number', min: 1, step: 1, value: s.balance, required: true }), method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))), date: h('input', { type: 'date', value: today() }), ref: h('input', { maxlength: 60, placeholder: 'N° de transaction, de chèque…' }) };
    dlg.replaceChildren(h('h2', {}, `Encaisser · ${s.number}`), h('p', { class: 'muted' }, `Reste à payer : ${money(s.balance)}`), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const r = await G.submit({ type: 'payment.create', data: { id: G.uuid(), sale_id: s.id, amount: Number(f.amount.value), method: f.method.value, pay_date: f.date.value, reference: f.ref.value } });
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? 'Paiement enregistré.' : 'Paiement mis en attente.'); if (r.status === 'ok' && confirm('Ouvrir le reçu pour l\'imprimer ?')) openReceipt(r.payment.id);
    } }, field('Montant (GNF)', f.amount), field('Mode de paiement', f.method), field('Date', f.date), field('Référence', f.ref), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => saleDetail(s.id) }, 'Retour'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
  }
  G.paymentForm = paymentForm;

  function noteForm(s) {
    const err = h('div', { class: 'error', hidden: true });
    const f = { amount: h('input', { type: 'number', min: 1, step: 1, required: true }), settlement: h('select', {}, h('option', { value: 'deduct' }, 'Déduit du solde dû'), h('option', { value: 'refund' }, 'Remboursé au client')), reason: h('input', { required: true, maxlength: 200, placeholder: 'Retour de marchandise, erreur de prix…' }) };
    // marchandise rendue : seules les lignes de produits suivis en stock, avec ce qui reste à rendre
    const produits = G.viewData().products || [];
    const rets = (s.lines || []).filter((l) => l.position && l.product_id && (produits.find((p) => p.id === l.product_id) || {}).track_stock && l.qty - (l.returned || 0) > 0)
      .map((l) => ({ l, input: h('input', { type: 'number', min: 0, max: l.qty - (l.returned || 0), step: 1, value: 0, 'aria-label': 'Quantité rendue de ' + l.name }) }));
    let auto = true;                                                                      // le montant suit les quantités rendues tant qu'on ne l'a pas saisi soi-même
    f.amount.addEventListener('input', () => { auto = false; });
    const recalc = () => { if (!auto) return; const v = rets.reduce((a, r) => a + Math.round(Number(r.input.value || 0) * r.l.unit_price * (100 - (r.l.discount_pct || 0)) / 100), 0); f.amount.value = v > 0 ? v : ''; };
    for (const r of rets) r.input.addEventListener('input', recalc);
    const rendu = rets.length ? h('div', {}, h('h3', {}, 'Marchandise rendue par le client (elle remonte dans le stock)'), h('p', { class: 'muted' }, 'Laissez 0 pour un avoir sans retour de marchandise (erreur de prix, geste commercial).'),
      ...rets.map((r) => h('div', { class: 'row' }, h('span', { class: 'grow' }, `${r.l.name} · vendu ${r.l.qty}${r.l.returned ? ' (déjà rendu ' + r.l.returned + ')' : ''}`), r.input))) : null;
    dlg.replaceChildren(h('h2', {}, `Avoir sur ${s.number}`), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const returns = rets.filter((r) => Number(r.input.value || 0) > 0).map((r) => ({ position: r.l.position, qty: Number(r.input.value) }));
      const r = await G.submit({ type: 'creditnote.create', data: { id: G.uuid(), sale_id: s.id, amount: Number(f.amount.value), settlement: f.settlement.value, reason: f.reason.value, ...(returns.length ? { returns } : {}) } });
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? `Avoir ${r.credit_note.number} enregistré.` : 'Avoir mis en attente.');
    } }, rendu, field('Montant de l\'avoir (GNF)', f.amount), field('Règlement', f.settlement), field('Motif', f.reason), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => saleDetail(s.id) }, 'Retour'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
  }

  // ---------- créances ----------
  function renderReceivables() {
    const vd = G.viewData(), by = new Map();
    for (const s of vd.sales) {
      if (s.status !== 'active' || s.balance <= 0) continue;
      const late = s.due_date && s.due_date < today() ? daysBetween(s.due_date, today()) : 0;
      const b = by.get(s.client_id) || { id: s.client_id, buckets: [0, 0, 0, 0, 0], total: 0, sales: [] };
      b.buckets[late === 0 ? 0 : late <= 30 ? 1 : late <= 60 ? 2 : late <= 90 ? 3 : 4] += s.balance; b.total += s.balance; b.sales.push(s); by.set(s.client_id, b);
    }
    const rows = [...by.values()].sort((a, b) => (b.total - b.buckets[0]) - (a.total - a.buckets[0]) || b.total - a.total);
    const sum = (i) => rows.reduce((a, r) => a + r.buckets[i], 0), total = rows.reduce((a, r) => a + r.total, 0);
    const labels = ['Non échu', 'Retard 1–30 j', '31–60 j', '61–90 j', 'Plus de 90 j'];
    view.replaceChildren(h('h1', {}, 'Créances clients'),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Total dû'), h('strong', {}, money(total))), h('div', { class: 'card kpi bad' }, h('div', { class: 'muted' }, 'En retard'), h('strong', {}, money(total - sum(0)))),
        h('div', { class: 'card kpi bad' }, h('div', { class: 'muted' }, 'Plus de 90 jours'), h('strong', {}, money(sum(4))))),
      rows.length ? h('div', { class: 'list two' }, rows.map((r) => {
        const c = vd.clients.find((x) => x.id === r.id) || { name: '—' }, wa = c.phone ? G.waLink(c, r.total) : null;
        return h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, c.name), h('span', { class: 'name' }, money(r.total))),
          h('div', { class: 'muted' }, labels.map((l, i) => r.buckets[i] ? `${l} : ${money(r.buckets[i])}` : null).filter(Boolean).join(' · ')),
          h('div', { class: 'links' }, wa && h('a', { href: wa, target: '_blank', rel: 'noopener noreferrer' }, '💬 Relancer'), c.phone && h('a', { href: 'tel:' + c.phone }, '📞 Appeler'),
            h('a', { href: '#', onclick: (e) => { e.preventDefault(); unpaidList(r.id); } }, '🧾 Factures impayées')));
      })) : h('p', { class: 'muted' }, 'Aucune créance : tout est payé 🎉'));
  }
  function unpaidList(clientId) {
    const vd = G.viewData(), c = vd.clients.find((x) => x.id === clientId) || { name: '—' };
    const list = vd.sales.filter((s) => s.client_id === clientId && s.status === 'active' && s.balance > 0).sort((a, b) => (a.due_date || '').localeCompare(b.due_date || ''));
    dlg.replaceChildren(h('h2', {}, c.name), ...list.map((s) => h('article', { class: 'card sale', onclick: () => saleDetail(s.id) }, h('div', { class: 'head' }, h('span', { class: 'name' }, s.number), h('span', { class: 'name' }, money(s.balance))), h('div', { class: 'muted' }, `Échéance ${fmtDay(s.due_date)}`), statusBadge(s))),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    dlg.showModal();
  }

  tabs.push({ id: 'ventes', label: 'Ventes', show: () => can('sales.read_all') || can('sales.read_own'), render: renderSales });
  tabs.push({ id: 'creances', label: 'Créances', show: () => can('sales.read_all') || can('sales.read_own'), render: renderReceivables });
})();
