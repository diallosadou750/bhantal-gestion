/* Écran Stocks : catalogue, niveaux de stock, alertes, ajustements et inventaire. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, dlg, view, tabs, $ } = G;
  const f = { q: '', f: 'all' };
  const costs = () => can('costs.read');

  function status(p, vd) {
    if (!p.track_stock) return null;
    const s = vd.stock[p.id]; if (!s) return null;
    if (s.qty < 0) return { cls: 'bad', text: `Stock négatif (${s.qty}) : achat non saisi ?`, key: 'neg' };
    if (s.qty === 0) return { cls: 'bad', text: 'Rupture de stock', key: 'low' };
    if (s.reorder > 0 && s.qty <= s.reorder) return { cls: 'warn', text: `À commander : ${s.qty} en stock (seuil ${s.reorder})`, key: 'low' };
    return { cls: '', text: `${s.qty} en stock`, key: 'ok' };
  }

  // ---------- formulaire produit ----------
  function productForm(p) {
    const isNew = !p, x = p || { name: '', category: '', unit: '', price: 0, reorder_level: 0, track_stock: true }, err = h('div', { class: 'error', hidden: true });
    const t = { name: h('input', { value: x.name, required: true, maxlength: 120 }), category: h('input', { value: x.category || '', maxlength: 60, placeholder: 'Alimentaire, Construction…' }), unit: h('input', { value: x.unit || '', maxlength: 30, placeholder: 'sac, bidon, barre…' }),
                price: h('input', { type: 'number', min: 0, step: 1, value: x.price, required: true }), reorder: h('input', { type: 'number', min: 0, step: 1, value: x.reorder_level || 0 }), tax: h('input', { type: 'number', min: 0, max: 30, step: 1, value: x.tax_rate === null || x.tax_rate === undefined ? '' : x.tax_rate, placeholder: 'taux de l\'entreprise' }), track: h('input', { type: 'checkbox', checked: x.track_stock !== false }) };
    dlg.replaceChildren(h('h2', {}, isNew ? 'Nouveau produit' : 'Modifier le produit'), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const vals = { name: t.name.value, category: t.category.value, unit: t.unit.value, price: Number(t.price.value), reorder_level: Number(t.reorder.value || 0), track_stock: t.track.checked, tax_rate: t.tax.value };
      let op;
      if (isNew) op = { type: 'product.create', data: { id: G.uuid(), ...vals } };
      else { const changes = {}; for (const [k, v] of Object.entries(vals)) if (String(v) !== String(p[k] ?? '')) changes[k] = v; if (!Object.keys(changes).length) { dlg.close(); return; } op = { type: 'product.update', data: { id: p.id, base_version: p.version, changes } }; }
      const r = await G.submit(op);
      if (r.status === 'invalid' || r.status === 'denied') { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render();
    } }, field('Nom *', t.name), field('Catégorie', t.category), field('Unité', t.unit), field('Prix de vente (GNF), taxe comprise', t.price), field('TVA de ce produit en % (vide : taux de l\'entreprise ; 0 : exonéré). Le taux est figé sur chaque vente.', t.tax), field('Seuil d\'alerte : prévenir quand le stock descend à…', t.reorder),
      h('label', { class: 'check' }, t.track, ' Suivre le stock de ce produit (décocher pour un service)'), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  // ---------- dépôts, lots, transferts ----------
  const depots = () => state.data.depots || [], activeDepots = () => depots().filter((d) => d.active), depName = (id) => (depots().find((d) => d.id === id) || { name: id }).name;
  const depotQty = (pid, dep) => ((state.data.stock_depots || {})[pid] || {})[dep] || 0;
  const online = () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return false; } return true; };
  const failed = (r) => r.status === 'invalid' || r.status === 'denied' || r.status === 'conflict';
  const depotSelect = (sel) => h('select', {}, ...activeDepots().map((d) => h('option', { value: d.id, selected: d.id === sel }, d.name)));
  const LOT = { ok: ['', 'OK'], soon: ['warn', 'Bientôt périmé'], expired: ['bad', 'PÉRIMÉ'] };

  // ---------- ajustement ----------
  function adjustForm(p, lot) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true }), many = activeDepots().length > 1, plots = (state.data.lots || []).filter((l) => l.product_id === p.id);
    const t = { reason: h('select', {}, h('option', { value: 'initial' }, 'Stock initial (reprise)'), h('option', { value: 'return' }, 'Retour client remis en stock'), h('option', { value: 'loss', selected: !!lot }, 'Casse, perte, vol, péremption'), h('option', { value: 'other' }, 'Autre')),
                dir: h('select', {}, h('option', { value: '1' }, 'Ajouter au stock'), h('option', { value: '-1' }, 'Retirer du stock')), qty: h('input', { type: 'number', min: 1, step: 1, value: lot ? lot.remaining : 1, required: true }), note: h('input', { maxlength: 200, value: lot && lot.status === 'expired' ? 'Lot périmé jeté' : '', placeholder: 'Explication (obligatoire pour casse, perte, autre)' }),
                depot: depotSelect('main'), lot: h('select', {}, h('option', { value: '' }, '— Sans lot —'), ...plots.map((l) => h('option', { value: l.id, selected: lot && lot.id === l.id }, `${l.lot_no}${l.expiry_date ? ' · périme le ' + fmtDay(l.expiry_date) : ''} · reste ${l.remaining}`))) };
    const dirBox = h('div', { hidden: true }, field('Sens', t.dir)), info = h('p', { class: 'muted' });
    const sync = () => { dirBox.hidden = t.reason.value !== 'other'; info.textContent = `Stock actuel : ${(vd.stock[p.id] || {}).qty || 0}${many ? ' (dans « ' + depName(t.depot.value) + ' » : ' + depotQty(p.id, t.depot.value) + ')' : ''}`; };
    t.reason.addEventListener('change', sync); t.depot.addEventListener('change', sync); sync();
    dlg.replaceChildren(h('h2', {}, `Ajuster le stock : ${p.name}`), info, h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const sign = t.reason.value === 'loss' ? -1 : t.reason.value === 'other' ? Number(t.dir.value) : 1;
      const data = { id: G.uuid(), product_id: p.id, qty: sign * Number(t.qty.value), reason: t.reason.value, note: t.note.value, depot_id: t.depot.value }; if (t.lot.value) data.lot_id = t.lot.value;
      const r = await G.submit({ type: 'stock.adjust', data });
      if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? `Stock ajusté : ${r.stock} en stock.` : 'Ajustement mis en attente.');
    } }, field('Raison', t.reason), dirBox, many && field('Dépôt', t.depot), plots.length > 0 && field('Lot concerné (casse, péremption : retire du lot)', t.lot), field('Quantité', t.qty), field('Note', t.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  // ---------- inventaire (un dépôt à la fois) ----------
  function inventoryForm() {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true }), many = activeDepots().length > 1, dep = depotSelect('main');
    const list = vd.products.filter((p) => p.track_stock).sort((a, b) => a.name.localeCompare(b.name, 'fr'));
    const inputs = list.map((p) => ({ p, el: h('input', { type: 'number', min: 0, step: 1, placeholder: 'compté', 'aria-label': 'Quantité comptée de ' + p.name }), th: h('span', { class: 'muted' }) }));
    const sync = () => { for (const i of inputs) i.th.textContent = `(théorique : ${depotQty(i.p.id, dep.value)})`; }; dep.addEventListener('change', sync); sync();
    const note = h('input', { maxlength: 200, placeholder: 'Ex. inventaire de fin de mois' });
    dlg.replaceChildren(h('h2', {}, 'Inventaire physique'), h('p', { class: 'muted' }, 'Comptez chaque produit en rayon et saisissez la quantité. Laissez vide ce que vous n\'avez pas compté. Seuls les écarts sont enregistrés, et chaque correction est journalisée.' + (many ? ' On inventorie un dépôt à la fois.' : '')),
      h('form', { onsubmit: async (e) => {
        e.preventDefault(); err.hidden = true;
        const counts = inputs.filter((i) => i.el.value !== '').map((i) => ({ product_id: i.p.id, counted: Number(i.el.value) }));
        if (!counts.length) { err.textContent = 'Saisissez au moins une quantité comptée.'; err.hidden = false; return; }
        const r = await G.submit({ type: 'stock.inventory', data: { id: G.uuid(), counts, note: note.value, date: today(), depot_id: dep.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
        if (r.status === 'queued') { dlg.close(); G.render(); G.toast('Inventaire mis en attente : il sera enregistré au retour de la connexion.'); return; }
        dlg.replaceChildren(h('h2', {}, 'Inventaire enregistré ✔'), h('p', {}, `${r.counted} produit(s) comptés, ${r.gaps.length} écart(s).`),
          ...r.gaps.map((g) => h('div', { class: 'row' }, h('span', { class: 'grow' }, `${g.name} : ${g.theoretical} → ${g.counted} (${g.delta > 0 ? '+' : ''}${g.delta})`), costs() && h('span', { class: g.delta < 0 ? 'badge bad' : 'badge' }, money(g.value)))),
          costs() && r.gaps.length > 0 && h('p', { class: 'muted' }, `Valeur des manques : ${money(r.loss_value)} · des excédents : ${money(r.gain_value)} (au coût moyen d'achat).`),
          h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => { dlg.close(); G.render(); } }, 'Fermer')));
      } }, many && field('Dépôt inventorié', dep), ...inputs.map(({ p, el, th }) => h('div', { class: 'row' }, h('span', { class: 'grow' }, `${p.name} `, th), h('div', { class: 'inv' }, el))),
        field('Note', note), err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer l\'inventaire'))));
    dlg.showModal();
  }

  // ---------- transfert entre dépôts ----------
  function transferForm(pre) {
    if (activeDepots().length < 2) { G.toast('Ajoutez d\'abord un deuxième dépôt (bouton Dépôts).'); return; }
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true }), prods = vd.products.filter((p) => p.track_stock && p.version > 0).sort((a, b) => a.name.localeCompare(b.name, 'fr'));
    const t = { prod: h('select', {}, ...prods.map((p) => h('option', { value: p.id, selected: pre && pre.id === p.id }, p.name))), from: depotSelect('main'), to: depotSelect(activeDepots().find((d) => d.id !== 'main').id), qty: h('input', { type: 'number', min: 1, step: 1, value: 1, required: true }), note: h('input', { maxlength: 200, placeholder: 'Ex. réassort du magasin du marché' }) };
    const info = h('p', { class: 'muted' }), sync = () => { info.textContent = `Disponible dans « ${depName(t.from.value)} » : ${depotQty(t.prod.value, t.from.value)}`; }; for (const el of [t.prod, t.from]) el.addEventListener('change', sync); sync();
    dlg.replaceChildren(h('h2', {}, 'Transférer du stock'), h('p', { class: 'muted' }, 'Déplace une quantité d\'un dépôt à l\'autre ; le stock total ne change pas.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'stransfer.create', data: { id: G.uuid(), product_id: t.prod.value, from_depot: t.from.value, to_depot: t.to.value, qty: Number(t.qty.value), note: t.note.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Transfert ${r.transfer.number} enregistré.`); } },
        field('Produit', t.prod), field('Du dépôt', t.from), info, field('Vers le dépôt', t.to), field('Quantité', t.qty), field('Note', t.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Transférer'))));
    dlg.showModal();
  }

  // ---------- dépôts ----------
  function depotsDialog() {
    const vd = G.viewData(), pname = (id) => (vd.products.find((p) => p.id === id) || { name: '—' }).name, w = can('stock.adjust'), err = h('div', { class: 'error', hidden: true }), nm = h('input', { maxlength: 60, placeholder: 'Ex. Magasin du marché' });
    const act = async (data) => { if (!online()) return; const r = await G.submit(data); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } G.render(); depotsDialog(); };
    dlg.replaceChildren(h('h2', {}, 'Dépôts'), h('p', { class: 'muted' }, 'Chaque achat entre dans un dépôt et chaque vente en sort d\'un ; le stock total est la somme. Le dépôt principal existe toujours.'),
      ...depots().map((d) => h('div', { class: 'row' + (d.active ? '' : ' struck') }, h('span', { class: 'grow' }, `${d.code} · ${d.name}${d.main ? ' (principal)' : ''}${d.active ? '' : ' · désactivé'}`),
        w && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const v = prompt('Nouveau nom du dépôt :', d.name); if (v && v.trim() !== d.name) act({ type: 'depot.update', data: { id: d.id, base_version: d.version, changes: { name: v.trim() } } }); } }, 'Renommer'),
        w && !d.main && h('a', { href: '#', onclick: (e) => { e.preventDefault(); act({ type: 'depot.update', data: { id: d.id, base_version: d.version, changes: { active: !d.active } } }); } }, d.active ? 'Désactiver' : 'Réactiver'))),
      w && h('div', { class: 'row' }, h('div', { class: 'grow' }, nm), h('button', { class: 'primary', type: 'button', onclick: () => { if (nm.value.trim()) act({ type: 'depot.create', data: { id: G.uuid(), name: nm.value.trim() } }); } }, '＋ Ajouter')), err,
      (state.data.stock_transfers || []).length ? h('h3', {}, 'Transferts') : null,
      ...(state.data.stock_transfers || []).slice().reverse().slice(0, 15).map((x) => h('div', { class: 'row' + (x.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${x.number} · ${fmtDay(x.transfer_date)} · ${x.qty} × ${pname(x.product_id)} : ${depName(x.from_depot)} → ${depName(x.to_depot)}${x.status === 'cancelled' ? ' · annulé : ' + x.cancel_reason : ''}`),
        w && x.status === 'active' && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const reason = prompt('Motif de l\'annulation de ' + x.number + ' :'); if (reason && reason.trim().length >= 3) act({ type: 'stransfer.cancel', data: { id: x.id, base_version: x.version, reason: reason.trim() } }); } }, 'Annuler'))),
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer'), w && h('button', { class: 'primary', type: 'button', onclick: () => transferForm() }, '⇄ Transférer du stock')));
    if (!dlg.open) dlg.showModal();
  }

  // ---------- lots et péremptions ----------
  function lotsDialog() {
    const vd = G.viewData(), list = (state.data.lots || []).slice(), w = can('stock.adjust');
    dlg.replaceChildren(h('h2', {}, 'Lots et dates de péremption'), h('p', { class: 'muted' }, 'À la vente, le stock sort du lot qui périme le premier, jamais d\'un lot périmé. Indiquez le n° de lot et la date de péremption quand vous saisissez un achat.'),
      list.length ? h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, ['Produit', 'Lot', 'Péremption', 'Reste', 'État', ''].map((x, i) => h('th', { class: i === 3 ? 'num' : '' }, x))),
        ...list.map((l) => h('tr', {}, h('td', {}, l.product_name), h('td', {}, l.lot_no), h('td', {}, l.expiry_date ? fmtDay(l.expiry_date) : '—'), h('td', { class: 'num' }, l.remaining), h('td', {}, h('span', { class: 'badge ' + (LOT[l.status] || ['', ''])[0] }, (LOT[l.status] || ['', ''])[1])),
          h('td', {}, w && l.status !== 'ok' && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const p = vd.products.find((x) => x.id === l.product_id); if (p) adjustForm(p, l); } }, 'Sortir du stock')))))) : h('p', { class: 'muted' }, 'Aucun lot en stock. Un lot se crée en saisissant un n° de lot ou une péremption sur une ligne d\'achat.'),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    dlg.showModal();
  }

  // ---------- écran ----------
  function render() {
    const vd = G.viewData();
    const all = vd.products.map((p) => ({ p, st: status(p, vd) }));
    const test = { all: () => true, low: (x) => x.st && (x.st.key === 'low'), neg: (x) => x.st && x.st.key === 'neg' };
    const list = all.filter(test[f.f]).filter(({ p }) => !f.q || (p.name + ' ' + p.code + ' ' + (p.category || '')).toLowerCase().includes(f.q.toLowerCase())).sort((a, b) => a.p.name.localeCompare(b.p.name, 'fr'));
    const nLow = all.filter(test.low).length, nNeg = all.filter(test.neg).length;
    const search = h('input', { type: 'search', id: 'psearch', placeholder: 'Rechercher un produit…', value: f.q, 'aria-label': 'Rechercher', oninput: (e) => { f.q = e.target.value; const p = e.target.selectionStart; render(); const s = $('#psearch'); s.focus(); s.setSelectionRange(p, p); } });
    const chips = [['all', 'Tous'], ['low', `À commander (${nLow})`], ['neg', `Stock négatif (${nNeg})`]];
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, `Stocks (${list.length})`), (state.data.lots || []).length > 0 && h('button', { class: 'secondary', type: 'button', onclick: lotsDialog }, '🏷️ Lots' + ((state.data.lots || []).some((l) => l.status === 'expired' || l.status === 'soon') ? ' ⚠' : '')), can('stock.read') && h('button', { class: 'secondary', type: 'button', onclick: depotsDialog }, '🏬 Dépôts'),
      can('stock.adjust') && h('button', { class: 'secondary', type: 'button', onclick: inventoryForm }, '📋 Inventaire'), can('products.write') && h('button', { class: 'primary', type: 'button', onclick: () => productForm(null) }, '＋ Nouveau produit')),
      h('div', { class: 'card' }, search, h('div', { class: 'chips' }, chips.map(([id, label]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(f.f === id), onclick: () => { f.f = id; render(); } }, label)))),
      list.length ? h('div', { class: 'list two' }, list.map(({ p, st }) => {
        const s = vd.stock[p.id] || {};
        const margin = costs() && s.avg_cost && p.price ? Math.round((p.price - s.avg_cost) * 100 / p.price) : null;
        return h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, p.name), h('span', { class: 'muted' }, p.code)),
          h('div', { class: 'muted' }, [p.category, p.unit && 'par ' + p.unit].filter(Boolean).join(' · ') || ' '), h('div', { class: 'name' }, money(p.price)),
          st && h('div', {}, h('span', { class: 'badge ' + st.cls }, st.text)), st && activeDepots().length > 1 && h('div', { class: 'muted' }, depots().filter((d) => depotQty(p.id, d.id)).map((d) => `${d.name} : ${depotQty(p.id, d.id)}`).join(' · ')), !p.track_stock && h('div', { class: 'muted' }, 'Stock non suivi (service)'),
          costs() && s.avg_cost !== undefined && h('div', { class: 'muted' }, `Coût moyen d'achat : ${money(s.avg_cost)}${s.last_cost !== null && s.last_cost !== undefined ? ' · dernier : ' + money(s.last_cost) : ''}${margin !== null ? ' · marge ' + margin + ' %' : ''}`),
          vd.pend.has(p.id) && h('span', { class: 'badge warn' }, 'En attente'),
          h('div', { class: 'links' }, can('products.write') && h('a', { href: '#', onclick: (e) => { e.preventDefault(); productForm(p); } }, '✏️ Modifier'),
            p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); G.attachDialog('product', p.id, p.name, can('products.write'), () => { dlg.close(); render(); }); } }, '📎 Photos / preuves'),
            can('stock.adjust') && p.track_stock && p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); adjustForm(p); } }, '± Ajuster'),
            can('purchases.create') && p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); G.purchaseForm({ productId: p.id }); } }, '📦 Acheter'),
            can('products.write') && p.version > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (confirm('Retirer ce produit du catalogue ? Les anciennes ventes le gardent.')) G.submit({ type: 'product.delete', data: { id: p.id, base_version: p.version } }).then(() => G.render()); } }, '🗑️ Retirer')));
      })) : h('p', { class: 'muted' }, f.q || f.f !== 'all' ? 'Aucun résultat.' : 'Aucun produit : ajoutez votre catalogue pour vendre plus vite et suivre le stock.'));
    if (f.q) { const s = $('#psearch'); s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  G.stockStatus = status;
  tabs.push({ id: 'produits', label: 'Stocks', show: () => can('products.read'), render });
})();
