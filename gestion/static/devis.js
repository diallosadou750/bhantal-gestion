/* Écran Devis : offres de prix valables jusqu'à une date, convertibles en vente. Demande une connexion (comme la comptabilité). */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, addDays, can, state, dlg, view, tabs } = G;
  const st = { f: 'open' };
  const failed = (r) => r.status === 'invalid' || r.status === 'denied' || r.status === 'conflict';
  const online = () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return false; } return true; };
  const clientName = (vd, id) => (vd.clients.find((c) => c.id === id) || {}).name || '—';
  const LABEL = { open: 'Ouvert', converted: 'Accepté (vendu)', declined: 'Refusé', cancelled: 'Annulé' };

  function badge(q) {
    if (q.status === 'open' && q.expired) return h('span', { class: 'badge bad' }, 'Expiré');
    return h('span', { class: 'badge' + (q.status === 'open' ? ' warn' : q.status === 'converted' ? '' : ' bad') }, LABEL[q.status]);
  }

  function renderQuotes() {
    const vd = G.viewData(), all = (state.data.quotes || []).slice().sort((a, b) => b.quote_date.localeCompare(a.quote_date) || b.seq - a.seq);
    const list = st.f === 'open' ? all.filter((q) => q.status === 'open') : all;
    const open = all.filter((q) => q.status === 'open' && !q.expired);
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, `Devis (${list.length})`), can('sales.create') && h('button', { class: 'primary', type: 'button', onclick: () => quoteForm() }, '＋ Nouveau devis')),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Devis en cours (valables)'), h('strong', {}, String(open.length))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Montant en attente de réponse'), h('strong', {}, money(open.reduce((a, q) => a + q.total, 0))))),
      h('div', { class: 'chips' }, [['open', 'Ouverts'], ['all', 'Tous']].map(([id, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.f === id), onclick: () => { st.f = id; renderQuotes(); } }, l))),
      list.length ? h('div', { class: 'list two' }, list.slice(0, 200).map((q) => h('article', { class: 'card sale', onclick: () => detail(q.id) },
        h('div', { class: 'head' }, h('span', { class: 'name' }, `${q.number} · ${clientName(vd, q.client_id)}`), h('span', { class: 'name' }, money(q.total))),
        h('div', { class: 'muted' }, `${fmtDay(q.quote_date)} · valable jusqu'au ${fmtDay(q.valid_until)}${q.sale_number ? ' · vente ' + q.sale_number : ''}`), h('div', { class: 'row' }, badge(q))))) : h('p', { class: 'muted' }, 'Aucun devis.'));
  }

  function quoteForm() {
    const vd = G.viewData();
    if (!vd.clients.length) { G.toast('Créez d\'abord un client.'); return; }
    const maxDisc = state.me.role === 'sales' ? 10 : 100, free = can('sales.free_line'), priceEdit = can('sales.override_price'), err = h('div', { class: 'error', hidden: true });
    const f = { client: h('select', {}, ...vd.clients.slice().sort((a, b) => a.name.localeCompare(b.name, 'fr')).map((c) => h('option', { value: c.id }, `${c.name} (${c.code})`))), until: h('input', { type: 'date', value: addDays(today(), 30) }), note: h('input', { maxlength: 300, placeholder: 'Conditions, délai de livraison…' }) };
    const box = h('div', {}), totalEl = h('div', { class: 'big' }), rows = [];
    const lineData = () => rows.map((r) => ({ product_id: r.product.value || null, name: r.name.value, qty: Number(r.qty.value || 0), unit_price: Number(r.price.value || 0), discount_pct: Number(r.disc.value || 0) }));
    const amountOf = (l) => Math.floor((l.qty * l.unit_price * (100 - l.discount_pct) + 50) / 100), recalc = () => { totalEl.textContent = 'Total : ' + money(lineData().reduce((a, l) => a + amountOf(l), 0)); };
    function addRow() {
      const r = { product: h('select', { 'aria-label': 'Produit' }, h('option', { value: '' }, free ? '— Autre article —' : '— Choisir —'), ...vd.products.map((p) => h('option', { value: p.id }, `${p.name} — ${money(p.price)}`))), name: h('input', { placeholder: 'Désignation (article libre)', maxlength: 120 }),
        qty: h('input', { type: 'number', min: 1, step: 1, value: 1 }), price: h('input', { type: 'number', min: 0, step: 1, value: 0 }), disc: h('input', { type: 'number', min: 0, max: maxDisc, step: 1, value: 0 }) };
      const sync = () => { const p = vd.products.find((x) => x.id === r.product.value); r.name.hidden = !!p; r.price.readOnly = !!p && !priceEdit; if (p && !priceEdit) r.price.value = p.price; recalc(); };
      if (vd.products.length) { r.product.value = vd.products[0].id; r.price.value = vd.products[0].price; }
      r.product.addEventListener('change', () => { const p = vd.products.find((x) => x.id === r.product.value); if (p) r.price.value = p.price; sync(); }); for (const k of ['qty', 'price', 'disc', 'name']) r[k].addEventListener('input', sync);
      r.root = h('div', { class: 'card line' }, r.product, r.name, h('div', { class: 'filters' }, field('Quantité', r.qty), field('Prix unitaire', r.price), field(`Remise % (max ${maxDisc})`, r.disc)), h('div', { class: 'row' }, h('button', { class: 'danger', type: 'button', onclick: () => { if (rows.length > 1) { rows.splice(rows.indexOf(r), 1); r.root.remove(); recalc(); } } }, 'Retirer')));
      rows.push(r); box.append(r.root); sync();
    }
    addRow();
    dlg.replaceChildren(h('h2', {}, 'Nouveau devis'), h('p', { class: 'muted' }, 'Un devis ne change ni le stock ni la comptabilité tant qu\'il n\'est pas converti en vente.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'quote.create', data: { id: G.uuid(), client_id: f.client.value, valid_until: f.until.value, note: f.note.value, lines: lineData() } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Devis ' + r.quote.number + ' enregistré.'); window.open('/api/quote/' + r.quote.id, '_blank', 'noopener'); } },
        field('Client', f.client), box, h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: addRow }, '＋ Ajouter une ligne')), totalEl, field('Valable jusqu\'au', f.until), field('Note', f.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    recalc(); dlg.showModal();
  }

  function convert(q) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const kind = h('select', {}, h('option', { value: 'cash' }, 'Comptant'), can('sales.credit') && h('option', { value: 'credit' }, 'Crédit')), method = h('select', {}, ...(vd.methods || []).map((m) => h('option', { value: m }, m))), due = h('input', { type: 'date', value: addDays(today(), 30) }), paid = h('input', { type: 'number', min: 0, step: 1000, value: 0 });
    const creditBox = h('div', { hidden: true }, field('Échéance de paiement', due), field('Acompte versé', paid)); kind.addEventListener('input', () => { creditBox.hidden = kind.value !== 'credit'; });
    dlg.replaceChildren(h('h2', {}, `Convertir ${q.number} en vente`), h('p', { class: 'muted' }, `Total ${money(q.total)}. La vente suit toutes les règles d'une vente (limite de crédit, droits, stock).`), field('Type de vente', kind), field('Mode de paiement', method), creditBox, err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'button', onclick: async () => { if (!online()) return; err.hidden = true;
        const data = { id: q.id, base_version: q.version, kind: kind.value, pay_method: method.value }; if (kind.value === 'credit') { data.due_date = due.value; data.paid_at_sale = Number(paid.value || 0); }
        const r = await G.submit({ type: 'quote.convert', data }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Vente ${r.sale.number} créée à partir du devis.`); if (G.saleDetail) setTimeout(() => G.saleDetail(r.sale.id), 300); } }, 'Créer la vente')));
  }

  function detail(id) {
    const vd = G.viewData(), q = (state.data.quotes || []).find((x) => x.id === id); if (!q) return;
    const err = h('div', { class: 'error', hidden: true }), w = can('sales.create') && q.status === 'open';
    const close = async (outcome) => { if (!online()) return; const reason = prompt(outcome === 'declined' ? 'Motif du refus par le client :' : 'Motif de l\'annulation :'); if (!reason || reason.trim().length < 3) return;
      const r = await G.submit({ type: 'quote.close', data: { id: q.id, base_version: q.version, outcome, reason: reason.trim() } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); };
    dlg.replaceChildren(h('h2', {}, `${q.number} · ${clientName(vd, q.client_id)}`), h('div', { class: 'row' }, badge(q)), h('p', { class: 'muted' }, `${fmtDay(q.quote_date)} · valable jusqu'au ${fmtDay(q.valid_until)}${q.sale_number ? ' · converti en vente ' + q.sale_number : ''}${q.cancel_reason ? ' · motif : ' + q.cancel_reason : ''}`),
      h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, ['Article', 'Qté', 'Prix', '%', 'Montant'].map((x) => h('th', {}, x))), ...q.lines.map((l) => h('tr', {}, h('td', {}, l.name), h('td', { class: 'num' }, l.qty), h('td', { class: 'num' }, money(l.unit_price)), h('td', { class: 'num' }, l.discount_pct), h('td', { class: 'num' }, money(l.amount)))))),
      h('p', { class: 'big' }, 'Total : ', money(q.total)), q.note && h('p', { class: 'muted' }, q.note), err,
      h('div', { class: 'row actions' }, h('button', { class: 'secondary', type: 'button', onclick: () => window.open('/api/quote/' + q.id, '_blank', 'noopener') }, 'Imprimer / PDF'),
        w && !q.expired && h('button', { class: 'primary', type: 'button', onclick: () => convert(q) }, 'Convertir en vente'), w && h('button', { class: 'secondary', type: 'button', onclick: () => close('declined') }, 'Refusé par le client'), w && h('button', { class: 'danger', type: 'button', onclick: () => close('cancelled') }, 'Annuler'),
        h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    dlg.showModal();
  }

  tabs.push({ id: 'devis', label: 'Devis', show: () => can('sales.read_all') || can('sales.read_own'), render: renderQuotes });
})();
