/* Écran Achats : achats (entrées de stock), fournisseurs et dettes. Réservé au propriétaire et au comptable. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, addDays, daysBetween, can, state, dlg, view, tabs, $ } = G;
  const st = { mode: 'achats', q: '' };
  const supName = (vd, id) => (vd.suppliers.find((s) => s.id === id) || {}).name || '—';
  const openForm = (fn, ...a) => fn(...a);

  function badge(p) {
    if (p.status === 'cancelled') return h('span', { class: 'badge bad' }, 'Annulé');
    if (p.balance <= 0) return h('span', { class: 'badge' }, 'Réglé');
    if (p.due_date && p.due_date < today()) return h('span', { class: 'badge bad' }, `En retard ${daysBetween(p.due_date, today())} j · reste ${money(p.balance)}`);
    return h('span', { class: 'badge warn' }, `Reste ${money(p.balance)}${p.due_date ? ' · échéance ' + fmtDay(p.due_date) : ''}`);
  }

  // ---------- fournisseur ----------
  function supplierForm(s, then) {
    const isNew = !s, x = s || { name: '', phone: '', zone: '', note: '' }, err = h('div', { class: 'error', hidden: true });
    const t = { name: h('input', { value: x.name, required: true, maxlength: 120 }), phone: h('input', { value: x.phone || '', inputmode: 'tel', placeholder: '224620000000' }), zone: h('input', { value: x.zone || '', maxlength: 60 }), note: h('input', { value: x.note || '', maxlength: 300 }) };
    dlg.replaceChildren(h('h2', {}, isNew ? 'Nouveau fournisseur' : 'Modifier le fournisseur'), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const vals = { name: t.name.value, phone: t.phone.value, zone: t.zone.value, note: t.note.value };
      let op;
      if (isNew) op = { type: 'supplier.create', data: { id: G.uuid(), ...vals } };
      else { const changes = {}; for (const [k, v] of Object.entries(vals)) if (String(v) !== String(s[k] ?? '')) changes[k] = v; if (!Object.keys(changes).length) { dlg.close(); return; } op = { type: 'supplier.update', data: { id: s.id, base_version: s.version, changes } }; }
      const r = await G.submit(op);
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); if (then) then(r);
    } }, field('Nom *', t.name), field('Téléphone', t.phone), field('Zone', t.zone), field('Note', t.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  // ---------- achat ----------
  function purchaseForm({ productId, supplierId } = {}) {
    const vd = G.viewData();
    if (!vd.suppliers.length) { G.toast('Créez d\'abord un fournisseur.'); supplierForm(null, () => purchaseForm({ productId })); return; }
    const err = h('div', { class: 'error', hidden: true });
    const prods = vd.products.filter((p) => p.track_stock);
    if (!prods.length) { G.toast('Aucun produit suivi en stock : créez d\'abord vos produits.'); return; }
    const t = { sup: h('select', {}, ...vd.suppliers.map((s) => h('option', { value: s.id, selected: s.id === supplierId }, `${s.name} (${s.code})`))), kind: h('select', {}, h('option', { value: 'cash' }, 'Comptant'), h('option', { value: 'credit' }, 'Crédit')),
                method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))), paid: h('input', { type: 'number', min: 0, step: 1000, value: 0 }), due: h('input', { type: 'date', value: addDays(today(), 30) }),
                ref: h('input', { maxlength: 60, placeholder: 'N° de la facture du fournisseur' }), note: h('input', { maxlength: 300 }) };
    const box = h('div', {}), totalEl = h('div', { class: 'big' }), rows = [];
    function addRow(pid) {
      const r = { product: h('select', { 'aria-label': 'Produit' }, ...prods.map((p) => h('option', { value: p.id, selected: p.id === pid }, p.name))), qty: h('input', { type: 'number', min: 1, step: 1, value: 1, 'aria-label': 'Quantité' }), cost: h('input', { type: 'number', min: 0, step: 1, value: 0, 'aria-label': 'Prix d\'achat unitaire' }), hint: h('div', { class: 'muted' }), tax: h('input', { type: 'number', min: 0, max: 30, step: 1, placeholder: 'réglages', 'aria-label': 'TVA de la ligne' }),
        depot: h('select', { 'aria-label': 'Dépôt de réception' }, ...(state.data.depots || []).filter((d) => d.active).map((d) => h('option', { value: d.id }, d.name))), lot: h('input', { maxlength: 40, placeholder: 'n° de lot (facultatif)', 'aria-label': 'N° de lot' }), exp: h('input', { type: 'date', 'aria-label': 'Date de péremption' }) };
      const sync = () => { const s = vd.stock[r.product.value] || {}; r.hint.textContent = s.last_cost ? `Dernier prix payé : ${money(s.last_cost)} · stock actuel : ${s.qty}` : `Stock actuel : ${s.qty ?? 0}`; recalc(); };
      r.product.addEventListener('change', () => { const s = vd.stock[r.product.value] || {}; if (s.last_cost) r.cost.value = s.last_cost; sync(); });
      for (const k of ['qty', 'cost']) r[k].addEventListener('input', recalc);
      { const s = vd.stock[r.product.value] || {}; if (s.last_cost) r.cost.value = s.last_cost; }
      r.root = h('div', { class: 'card line' }, r.product, h('div', { class: 'filters' }, field('Quantité reçue', r.qty), field('Prix d\'achat unitaire (GNF)', r.cost), field('TVA récupérable % (vide : réglage comptable)', r.tax)), (state.data.depots || []).filter((d) => d.active).length > 1 && field('Reçu dans le dépôt', r.depot), h('div', { class: 'filters' }, field('N° de lot', r.lot), field('Péremption', r.exp)), r.hint,
        h('button', { class: 'danger', type: 'button', onclick: () => { if (rows.length > 1) { rows.splice(rows.indexOf(r), 1); r.root.remove(); recalc(); } } }, 'Retirer'));
      rows.push(r); box.append(r.root); sync();
    }
    const lineData = () => rows.map((r) => ({ product_id: r.product.value, qty: Number(r.qty.value || 0), unit_cost: Number(r.cost.value || 0), ...(r.tax.value !== '' ? { tax_rate: Number(r.tax.value) } : {}), depot_id: r.depot.value || 'main', ...(r.lot.value ? { lot_no: r.lot.value } : {}), ...(r.exp.value ? { expiry_date: r.exp.value } : {}) }));
    function recalc() { totalEl.textContent = 'Total : ' + money(lineData().reduce((a, l) => a + l.qty * l.unit_cost, 0)); for (const el of document.querySelectorAll('.credit-only')) el.hidden = t.kind.value !== 'credit'; }
    t.kind.addEventListener('input', recalc);
    addRow(productId || prods[0].id);
    dlg.replaceChildren(h('h2', {}, 'Nouvel achat (entrée de stock)'), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const kind = t.kind.value, data = { id: G.uuid(), supplier_id: t.sup.value, kind, lines: lineData(), pay_method: t.method.value, supplier_ref: t.ref.value, note: t.note.value };
      if (kind === 'credit') { data.due_date = t.due.value; data.paid_at_purchase = Number(t.paid.value || 0); }
      const r = await G.submit({ type: 'purchase.create', data });
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render();
      if (r.status !== 'ok') { G.toast('Achat mis en attente : il sera envoyé au retour de la connexion.'); return; }
      if (r.cost_alerts && r.cost_alerts.length) costAlertDialog(r.purchase, r.cost_alerts); else G.toast(`Achat ${r.purchase.number} enregistré : le stock est mis à jour.`);
    } }, field('Fournisseur', h('div', { class: 'row' }, h('div', { class: 'grow' }, t.sup), h('button', { class: 'secondary', type: 'button', onclick: () => supplierForm(null, () => purchaseForm({ productId })) }, '＋'))),
      field('Type d\'achat', t.kind), box, h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: () => addRow(prods[0].id) }, '＋ Ajouter une ligne')), totalEl, field('Mode de paiement', t.method),
      h('div', { class: 'credit-only', hidden: true }, field('Acompte versé (GNF)', t.paid), field('Échéance de paiement *', t.due)), field('N° de facture du fournisseur', t.ref), field('Note', t.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer l\'achat'))));
    recalc(); dlg.showModal();
  }
  G.purchaseForm = purchaseForm;

  function costAlertDialog(p, alerts) {
    dlg.replaceChildren(h('h2', {}, `Achat ${p.number} enregistré ✔`), h('div', { class: 'error' }, h('strong', {}, '⚠ Le prix d\'achat a augmenté :')),
      ...alerts.map((a) => h('p', {}, `${a.name} : ${money(a.prev)} → ${money(a.new)} (+${a.pct} %)`)), h('p', { class: 'muted' }, 'Pensez à vérifier votre prix de vente et votre marge (onglet Stocks).'),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Compris')));
    dlg.showModal();
  }

  function purchaseDetail(id) {
    const vd = G.viewData(), p = vd.purchases.find((x) => x.id === id); if (!p) return;
    const pays = vd.spays.filter((x) => x.purchase_id === id), synced = p.version > 0;
    dlg.replaceChildren(h('h2', {}, `${p.number} · ${supName(vd, p.supplier_id)}`), h('div', { class: 'row' }, badge(p)), h('p', { class: 'muted' }, `${fmtDay(p.purchase_date)} · ${p.kind === 'cash' ? 'Comptant' : 'Crédit, échéance ' + fmtDay(p.due_date)}${p.supplier_ref ? ' · facture ' + p.supplier_ref : ''}`),
      h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, 'Article'), h('th', {}, 'Qté'), h('th', {}, 'Prix'), h('th', {}, 'Montant')),
        ...p.lines.map((l) => h('tr', {}, h('td', {}, l.name, l.prev_cost && l.unit_cost > l.prev_cost * 1.05 ? h('span', { class: 'badge bad' }, ' hausse') : null), h('td', { class: 'num' }, l.qty), h('td', { class: 'num' }, money(l.unit_cost)), h('td', { class: 'num' }, money(l.amount)))))),
      h('p', { class: 'big' }, 'Total : ', money(p.total)), h('div', { class: 'muted' }, `Payé : ${money(p.paid_total)} · reste : ${money(p.balance)}`),
      (p.paid_at_purchase > 0 || pays.length) && h('h3', {}, 'Règlements'), p.paid_at_purchase > 0 && h('div', { class: 'muted' }, `${fmtDay(p.purchase_date)} · ${money(p.paid_at_purchase)} à l'achat (${p.pay_method})`),
      ...pays.map((x) => h('div', { class: 'row' + (x.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${fmtDay(x.pay_date)} · ${money(x.amount)} · ${x.method}${x.reference ? ' · ' + x.reference : ''}${x.status === 'cancelled' ? ' · annulé : ' + x.cancel_reason : ''}`),
        x.status === 'active' && x.version > 0 && can('supplierpay.cancel') && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const reason = prompt('Motif de l\'annulation du règlement :'); if (reason && reason.trim().length >= 3) { const r = await G.submit({ type: 'supplierpay.cancel', data: { id: x.id, base_version: x.version, reason: reason.trim() } }); if (r.status === 'invalid' || r.status === 'denied') G.toast(r.message); dlg.close(); G.render(); } } }, 'Annuler'))),
      ...supplierNotes(p, synced),
      h('div', { class: 'row actions' }, synced && p.status === 'active' && p.balance > 0 && can('supplierpay.create') && h('button', { class: 'primary', type: 'button', onclick: () => payForm(p) }, 'Régler'),
        synced && p.status === 'active' && can('purchases.create') && h('button', { class: 'secondary', type: 'button', onclick: () => noteForm(p) }, 'Avoir fournisseur'),
        synced && h('button', { class: 'secondary', type: 'button', onclick: () => G.attachDialog('purchase', p.id, `${p.number} · ${supName(vd, p.supplier_id)}`, p.status === 'active' && can('purchases.create'), () => purchaseDetail(p.id)) }, '📎 Pièces'),
        synced && p.status === 'active' && can('purchases.cancel') && h('button', { class: 'danger', type: 'button', onclick: async () => { const reason = prompt('Motif de l\'annulation de l\'achat ' + p.number + ' :'); if (reason && reason.trim().length >= 3) { const r = await G.submit({ type: 'purchase.cancel', data: { id: p.id, base_version: p.version, reason: reason.trim() } }); G.toast(r.status === 'ok' ? 'Achat annulé : le stock est corrigé.' : r.message); dlg.close(); G.render(); } } }, 'Annuler l\'achat'),
        h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    if (!dlg.open) dlg.showModal();
  }

  function supplierNotes(p, synced) {
    const notes = (state.data.supplier_notes || []).filter((n) => n.purchase_id === p.id); if (!notes.length) return [];
    return [h('h3', {}, 'Avoirs fournisseur'), ...notes.map((n) => h('div', { class: 'row' + (n.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${n.number} · ${fmtDay(n.note_date)} · ${money(n.amount)} · ${n.settlement === 'refund' ? 'remboursé (' + n.refund_method + ')' : 'déduit de la dette'} · ${n.reason}${n.lines.length ? ' · rendu : ' + n.lines.map((l) => l.qty + ' × ' + l.name).join(', ') : ''}${n.status === 'cancelled' ? ' · annulé : ' + n.cancel_reason : ''}`),
      n.status === 'active' && synced && can('purchases.cancel') && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const reason = prompt('Motif de l\'annulation de l\'avoir ' + n.number + ' :'); if (!reason || reason.trim().length < 3) return;
        const r = await G.submit({ type: 'snote.cancel', data: { id: n.id, base_version: n.version, reason: reason.trim() } }); if (r.status !== 'ok') G.toast(r.message); dlg.close(); G.render(); } }, 'Annuler')))];
  }
  function noteForm(p) {
    if (!state.online) { G.toast('Cette action demande une connexion.'); return; }
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const f = { settle: h('select', {}, h('option', { value: 'deduct' }, 'Déduit de ce qu\'on lui doit'), h('option', { value: 'refund' }, 'Remboursé par le fournisseur')), method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))), amount: h('input', { type: 'number', min: 1, step: 1, placeholder: 'Laisser vide : valeur des marchandises rendues' }), reason: h('input', { maxlength: 200, required: true, placeholder: 'Ex. 10 sacs défectueux, erreur de prix…' }) };
    const methodField = field('Mode d\'encaissement du remboursement', f.method); methodField.hidden = true; f.settle.addEventListener('input', () => { methodField.hidden = f.settle.value !== 'refund'; });
    const qty = p.lines.filter((l) => l.product_id).map((l) => ({ l, el: h('input', { type: 'number', min: 0, max: l.qty, step: 1, value: 0, 'aria-label': 'Quantité rendue' }) }));
    dlg.replaceChildren(h('h2', {}, `Avoir fournisseur · ${p.number}`), h('p', { class: 'muted' }, `Total net de l'achat : ${money(p.net_total === undefined ? p.total : p.net_total)} · reste dû : ${money(p.balance)}. Les marchandises rendues sortent du stock.`),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; const data = { id: G.uuid(), purchase_id: p.id, settlement: f.settle.value, reason: f.reason.value, lines: qty.filter((q) => Number(q.el.value) > 0).map((q) => ({ product_id: q.l.product_id, qty: Number(q.el.value) })) };
        if (f.amount.value) data.amount = Number(f.amount.value); if (data.settlement === 'refund') data.refund_method = f.method.value;
        const r = await G.submit({ type: 'snote.create', data }); if (r.status !== 'ok') { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Avoir ' + r.supplier_note.number + ' enregistré.'); } },
        field('Règlement de l\'avoir', f.settle), methodField, h('h3', {}, 'Marchandises rendues (facultatif)'), ...qty.map((q) => field(`${q.l.name} (acheté : ${q.l.qty})`, q.el)), field('Montant de l\'avoir (GNF)', f.amount), field('Motif', f.reason), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => purchaseDetail(p.id) }, 'Retour'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
  }

  function payForm(p) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const t = { amount: h('input', { type: 'number', min: 1, step: 1, value: p.balance, required: true }), method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))), date: h('input', { type: 'date', value: today() }), ref: h('input', { maxlength: 60, placeholder: 'N° de virement, de chèque…' }) };
    dlg.replaceChildren(h('h2', {}, `Régler ${p.number}`), h('p', { class: 'muted' }, `Reste à payer : ${money(p.balance)}`), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const r = await G.submit({ type: 'supplierpay.create', data: { id: G.uuid(), purchase_id: p.id, amount: Number(t.amount.value), method: t.method.value, pay_date: t.date.value, reference: t.ref.value } });
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? 'Règlement enregistré.' : 'Règlement mis en attente.');
    } }, field('Montant (GNF)', t.amount), field('Mode de paiement', t.method), field('Date', t.date), field('Référence', t.ref), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => purchaseDetail(p.id) }, 'Retour'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
  }

  // ---------- écran ----------
  // ---------- bons de commande fournisseurs ----------
  const poBadge = (o) => o.status === 'received' ? h('span', { class: 'badge' }, 'Reçue') : o.status === 'cancelled' ? h('span', { class: 'badge bad' }, 'Annulée')
    : o.late ? h('span', { class: 'badge bad' }, `En retard de ${daysBetween(o.expected_date, today())} j`) : h('span', { class: 'badge warn' }, `À recevoir le ${fmtDay(o.expected_date)}`);

  function orderForm({ supplierId } = {}) {
    const vd = G.viewData();
    if (!vd.suppliers.length) { G.toast('Créez d\'abord un fournisseur.'); supplierForm(null, () => orderForm({})); return; }
    if (!vd.products.length) { G.toast('Créez d\'abord vos produits.'); return; }
    const err = h('div', { class: 'error', hidden: true }), rows = [], box = h('div', {}), totalEl = h('div', { class: 'big' });
    const t = { sup: h('select', {}, ...vd.suppliers.map((s) => h('option', { value: s.id, selected: s.id === supplierId }, `${s.name} (${s.code})`))), exp: h('input', { type: 'date', value: addDays(today(), 7), min: today() }), note: h('input', { maxlength: 300 }) };
    const lineData = () => rows.map((r) => ({ product_id: r.product.value, qty: Number(r.qty.value || 0), unit_cost: Number(r.cost.value || 0) }));
    const recalc = () => { totalEl.textContent = 'Total : ' + money(lineData().reduce((a, l) => a + l.qty * l.unit_cost, 0)); };
    function addRow() {
      const r = { product: h('select', { 'aria-label': 'Produit' }, ...vd.products.map((p) => h('option', { value: p.id }, p.name))), qty: h('input', { type: 'number', min: 1, step: 1, value: 1, 'aria-label': 'Quantité commandée' }), cost: h('input', { type: 'number', min: 0, step: 1, value: 0, 'aria-label': 'Prix d\'achat unitaire' }) };
      const fill = () => { const s = vd.stock[r.product.value] || {}; if (s.last_cost) r.cost.value = s.last_cost; recalc(); };
      r.product.addEventListener('change', fill); r.qty.addEventListener('input', recalc); r.cost.addEventListener('input', recalc);
      r.root = h('div', { class: 'card line' }, r.product, h('div', { class: 'filters' }, field('Quantité commandée', r.qty), field('Prix d\'achat unitaire (GNF)', r.cost)),
        h('button', { class: 'danger', type: 'button', onclick: () => { if (rows.length > 1) { rows.splice(rows.indexOf(r), 1); r.root.remove(); recalc(); } } }, 'Retirer'));
      rows.push(r); box.append(r.root); fill();
    }
    addRow();
    dlg.replaceChildren(h('h2', {}, 'Nouveau bon de commande'), h('p', { class: 'muted' }, 'Une commande n\'est pas un achat : rien ne bouge en stock ni en comptabilité tant que la marchandise n\'est pas reçue.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true;
        const r = await G.submit({ type: 'porder.create', data: { id: G.uuid(), supplier_id: t.sup.value, expected_date: t.exp.value, lines: lineData(), note: t.note.value } });
        if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
        dlg.close(); st.mode = 'commandes'; G.render(); G.toast(r.status === 'ok' ? `Commande ${r.porder.number} enregistrée.` : 'Commande mise en attente : elle sera envoyée au retour de la connexion.'); } },
        field('Fournisseur', t.sup), field('Livraison attendue le', t.exp), box, h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: addRow }, '＋ Ajouter une ligne')), totalEl, field('Note', t.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer la commande'))));
    recalc(); dlg.showModal();
  }

  function receiveForm(o) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const t = { kind: h('select', {}, h('option', { value: 'cash' }, 'Comptant'), h('option', { value: 'credit' }, 'Crédit')), method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))),
                paid: h('input', { type: 'number', min: 0, step: 1000, value: 0 }), due: h('input', { type: 'date', value: addDays(today(), 30) }), ref: h('input', { maxlength: 60, placeholder: 'N° de la facture du fournisseur' }) };
    const lines = o.lines.map((l) => ({ l, qty: h('input', { type: 'number', min: 0, step: 1, value: l.qty, 'aria-label': 'Quantité reçue : ' + l.name }), cost: h('input', { type: 'number', min: 0, step: 1, value: l.unit_cost, 'aria-label': 'Prix : ' + l.name }) }));
    const totalEl = h('div', { class: 'big' }), credit = h('div', { hidden: true }, field('Acompte versé (GNF)', t.paid), field('Échéance de paiement *', t.due));
    const recalc = () => { totalEl.textContent = 'Total reçu : ' + money(lines.reduce((a, x) => a + Number(x.qty.value || 0) * Number(x.cost.value || 0), 0)); credit.hidden = t.kind.value !== 'credit'; };
    t.kind.addEventListener('input', recalc); for (const x of lines) { x.qty.addEventListener('input', recalc); x.cost.addEventListener('input', recalc); }
    dlg.replaceChildren(h('h2', {}, `Recevoir la commande ${o.number}`), h('p', { class: 'muted' }, `${supName(vd, o.supplier_id)} · commandé ${money(o.total)}. Corrigez les quantités et les prix réellement livrés (0 = non livré). L'achat et la mise à jour du stock se font à l'enregistrement.`),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true;
        const kind = t.kind.value, data = { id: o.id, base_version: o.version, purchase_id: G.uuid(), kind, pay_method: t.method.value, supplier_ref: t.ref.value, lines: lines.map((x) => ({ position: x.l.position, qty: Number(x.qty.value || 0), unit_cost: Number(x.cost.value || 0) })) };
        if (kind === 'credit') { data.due_date = t.due.value; data.paid_at_purchase = Number(t.paid.value || 0); }
        const r = await G.submit({ type: 'porder.receive', data });
        if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
        dlg.close(); G.render(); G.toast(r.status === 'ok' ? `Commande reçue : achat ${r.purchase.number} enregistré, le stock est mis à jour.` : 'Réception mise en attente : elle sera envoyée au retour de la connexion.'); } },
        ...lines.map((x) => h('div', { class: 'card line' }, h('strong', {}, x.l.name), h('div', { class: 'filters' }, field(`Quantité reçue (commandé : ${x.l.qty})`, x.qty), field(`Prix unitaire (commandé : ${money(x.l.unit_cost)})`, x.cost)))),
        totalEl, field('Type d\'achat', t.kind), field('Mode de paiement', t.method), credit, field('N° de facture du fournisseur', t.ref), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer la réception'))));
    recalc(); dlg.showModal();
  }

  function orderText(o, sup) {
    const company = (G.viewData().settings.company_name) || 'notre entreprise';
    return `Bonjour ${sup.name}, bon de commande ${o.number} de ${company} : ${o.lines.map((l) => `${l.qty} × ${l.name}`).join(', ')}. Livraison souhaitée le ${fmtDay(o.expected_date)}. Merci de confirmer.`;
  }

  function orderCard(o) {
    const vd = G.viewData(), sup = vd.suppliers.find((s) => s.id === o.supplier_id) || { name: '—', phone: '' }, text = orderText(o, sup), wa = sup.phone ? `https://wa.me/${sup.phone.replace('+', '')}?text=${encodeURIComponent(text)}` : null;
    return h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, `${o.number} · ${sup.name}`), h('span', { class: 'name' }, money(o.total))),
      h('div', { class: 'muted' }, `Commandé le ${fmtDay(o.order_date)} · ${o.lines.map((l) => `${l.qty} × ${l.name}`).join(', ')}`), o.note && h('div', { class: 'muted' }, o.note),
      h('div', { class: 'row' }, poBadge(o), vd.pend.has(o.id) && h('span', { class: 'badge warn' }, 'En attente'), o.purchase_number && h('span', { class: 'muted' }, 'Achat ' + o.purchase_number), o.cancel_reason && h('span', { class: 'muted' }, 'Motif : ' + o.cancel_reason)),
      h('div', { class: 'links' }, !vd.pend.has(o.id) && h('a', { href: '#', onclick: (e) => { e.preventDefault(); window.open('/api/porder/' + o.id, '_blank', 'noopener'); } }, '🖨 Imprimer')),
      o.status === 'open' && h('div', { class: 'links' }, wa && h('a', { href: wa, target: '_blank', rel: 'noopener noreferrer' }, '💬 Envoyer au fournisseur'),
        can('purchases.create') && h('a', { href: '#', onclick: (e) => { e.preventDefault(); receiveForm(o); } }, '📦 Recevoir'),
        can('purchases.create') && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const reason = prompt('Motif de l\'annulation de la commande ?'); if (reason) G.submit({ type: 'porder.close', data: { id: o.id, base_version: o.version, reason } }).then((r) => { if (r.status === 'invalid' || r.status === 'denied') G.toast(r.message); G.render(); }); } }, '✖ Annuler')));
  }

  function render() {
    const vd = G.viewData();
    const chips = [['achats', 'Achats'], ['commandes', 'Commandes'], ['fournisseurs', 'Fournisseurs'], ['dettes', 'Dettes']];
    const head = h('div', { class: 'row' }, h('h1', { class: 'grow' }, st.mode === 'achats' ? 'Achats' : st.mode === 'commandes' ? 'Bons de commande' : st.mode === 'fournisseurs' ? 'Fournisseurs' : 'Dettes fournisseurs'),
      can('purchases.create') && (st.mode === 'commandes' ? h('button', { class: 'primary', type: 'button', onclick: () => orderForm({}) }, '＋ Nouvelle commande') : h('button', { class: 'primary', type: 'button', onclick: () => purchaseForm({}) }, '＋ Nouvel achat')), can('suppliers.write') && st.mode === 'fournisseurs' && h('button', { class: 'secondary', type: 'button', onclick: () => supplierForm(null) }, '＋ Fournisseur'));
    const bar = h('div', { class: 'chips' }, chips.map(([id, label]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.mode === id), onclick: () => { st.mode = id; render(); } }, label)));
    let body;
    if (st.mode === 'achats') {
      body = vd.purchases.length ? h('div', { class: 'list two' }, vd.purchases.slice(0, 200).map((p) => h('article', { class: 'card sale', onclick: () => purchaseDetail(p.id) },
        h('div', { class: 'head' }, h('span', { class: 'name' }, `${p.number} · ${supName(vd, p.supplier_id)}`), h('span', { class: 'name' }, money(p.total))),
        h('div', { class: 'muted' }, `${fmtDay(p.purchase_date)} · ${p.kind === 'cash' ? 'Comptant' : 'Crédit'} · ${p.lines.length} ligne(s)`),
        h('div', { class: 'row' }, badge(p), vd.pend.has(p.id) && h('span', { class: 'badge warn' }, 'En attente'), p.lines.some((l) => l.prev_cost && l.unit_cost > l.prev_cost * 1.05) && h('span', { class: 'badge bad' }, 'Hausse de prix'))))) : h('p', { class: 'muted' }, 'Aucun achat : saisissez vos réceptions de marchandises pour que le stock se mette à jour tout seul.');
    } else if (st.mode === 'commandes') {
      const all = (state.data.porders || []).slice().sort((a, b) => (a.status === 'open' ? 0 : 1) - (b.status === 'open' ? 0 : 1) || a.expected_date.localeCompare(b.expected_date));
      body = all.length ? h('div', { class: 'list two' }, all.slice(0, 200).map(orderCard)) : h('p', { class: 'muted' }, 'Aucune commande : passez commande à vos fournisseurs ici, puis « Recevoir » transforme la commande en achat et met le stock à jour.');
    } else if (st.mode === 'fournisseurs') {
      const bal = new Map(); for (const p of vd.purchases) if (p.status === 'active') bal.set(p.supplier_id, (bal.get(p.supplier_id) || 0) + p.balance);
      body = vd.suppliers.length ? h('div', { class: 'list two' }, vd.suppliers.slice().sort((a, b) => a.name.localeCompare(b.name, 'fr')).map((s) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, s.name), h('span', { class: 'muted' }, s.code)),
        h('div', { class: 'muted' }, [s.zone, s.phone].filter(Boolean).join(' · ') || ' '), (bal.get(s.id) || 0) > 0 && h('div', {}, h('span', { class: 'badge warn' }, `On lui doit ${money(bal.get(s.id))}`)), vd.pend.has(s.id) && h('span', { class: 'badge warn' }, 'En attente'),
        h('div', { class: 'links' }, s.phone && h('a', { href: 'tel:' + s.phone }, '📞 Appeler'), can('purchases.create') && s.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); purchaseForm({ supplierId: s.id }); } }, '📦 Nouvel achat'),
          can('suppliers.write') && h('a', { href: '#', onclick: (e) => { e.preventDefault(); supplierForm(s); } }, '✏️ Modifier'),
          can('suppliers.write') && s.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (confirm('Supprimer ce fournisseur ? L\'historique reste conservé.')) G.submit({ type: 'supplier.delete', data: { id: s.id, base_version: s.version } }).then((r) => { if (r.status === 'invalid') G.toast(r.message); G.render(); }); } }, '🗑️ Supprimer'))))) : h('p', { class: 'muted' }, 'Aucun fournisseur.');
    } else {
      const by = new Map();
      for (const p of vd.purchases) {
        if (p.status !== 'active' || p.balance <= 0) continue;
        const late = p.due_date && p.due_date < today() ? daysBetween(p.due_date, today()) : 0;
        const b = by.get(p.supplier_id) || { id: p.supplier_id, buckets: [0, 0, 0, 0], total: 0, list: [] };
        b.buckets[late === 0 ? 0 : late <= 30 ? 1 : late <= 60 ? 2 : 3] += p.balance; b.total += p.balance; b.list.push(p); by.set(p.supplier_id, b);
      }
      const rows = [...by.values()].sort((a, b) => (b.total - b.buckets[0]) - (a.total - a.buckets[0]) || b.total - a.total), total = rows.reduce((a, r) => a + r.total, 0), late = rows.reduce((a, r) => a + r.total - r.buckets[0], 0);
      const week = vd.purchases.filter((p) => p.status === 'active' && p.balance > 0 && p.due_date && p.due_date >= today() && p.due_date <= addDays(today(), 7)).reduce((a, p) => a + p.balance, 0);
      const labels = ['Non échu', 'Retard 1–30 j', '31–60 j', 'Plus de 60 j'];
      body = h('div', {}, h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Total dû'), h('strong', {}, money(total))), h('div', { class: 'card kpi bad' }, h('div', { class: 'muted' }, 'En retard'), h('strong', {}, money(late))),
        h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'À payer sous 7 jours'), h('strong', {}, money(week)))),
        rows.length ? h('div', { class: 'list two' }, rows.map((r) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, supName(vd, r.id)), h('span', { class: 'name' }, money(r.total))),
          h('div', { class: 'muted' }, labels.map((l, i) => r.buckets[i] ? `${l} : ${money(r.buckets[i])}` : null).filter(Boolean).join(' · ')),
          h('div', { class: 'links' }, r.list.sort((a, b) => (a.due_date || '').localeCompare(b.due_date || '')).map((p) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); purchaseDetail(p.id); } }, `${p.number} (${money(p.balance)})`)))))) : h('p', { class: 'muted' }, 'Aucune dette : tout est réglé.'));
    }
    view.replaceChildren(head, h('div', { class: 'card' }, bar), body);
  }
  tabs.push({ id: 'achats', label: 'Achats', show: () => can('purchases.read'), render });
})();
