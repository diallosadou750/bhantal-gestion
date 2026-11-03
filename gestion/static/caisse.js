/* Écran Caisse : caisse du jour et clôture, dépenses et budgets, historique et mouvements. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, dlg, view, tabs } = G;
  const st = { mode: 'caisse', month: today().slice(0, 7) };
  const MOVES = { bank_deposit: 'Dépôt d\'espèces en banque (sortie)', bank_withdrawal: 'Retrait en banque vers la caisse (entrée)', owner_in: 'Apport du patron (entrée)', owner_out: 'Prélèvement du patron (sortie)', customer_refund: 'Remboursement à un client (sortie)' };
  const CASHIER_MOVES = ['bank_deposit', 'customer_refund'];
  const IN_LABELS = { sales: 'Ventes encaissées en espèces', payments: 'Paiements clients en espèces', bank_withdrawal: 'Retraits en banque', owner_in: 'Apports du patron' };
  const OUT_LABELS = { expenses: 'Dépenses en espèces', purchases: 'Achats payés en espèces', supplier_payments: 'Règlements fournisseurs en espèces', bank_deposit: 'Dépôts en banque', owner_out: 'Prélèvements du patron', customer_refund: 'Remboursements clients' };
  const failed = (r) => r.status === 'invalid' || r.status === 'denied';

  // ---------- formulaires ----------
  function expenseForm() {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true }), cashier = state.me.role === 'cashier';
    const t = { date: h('input', { type: 'date', value: today() }), cat: h('select', {}, ...vd.categories.filter((c) => c.active !== false && c.account !== '422').map((c) => h('option', { value: c.key }, `${c.key} (${c.nature.toLowerCase()})`))), payee: h('input', { maxlength: 120, placeholder: 'À qui avez-vous payé ?' }),
                desc: h('input', { maxlength: 200, placeholder: 'Pourquoi ?' }), amount: h('input', { type: 'number', min: 1, step: 1, required: true }), method: h('select', {}, ...vd.methods.map((m) => h('option', { value: m }, m))), ref: h('input', { maxlength: 60, placeholder: 'N° de reçu, de transaction…' }) };
    dlg.replaceChildren(h('h2', {}, 'Nouvelle dépense'), cashier && h('p', { class: 'muted' }, 'Caissier : dépenses jusqu\'à 100 000 GNF chacune ; au-delà, voyez le comptable.'), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const r = await G.submit({ type: 'expense.create', data: { id: G.uuid(), expense_date: t.date.value, category: t.cat.value, payee: t.payee.value, description: t.desc.value, amount: Number(t.amount.value), method: t.method.value, reference: t.ref.value } });
      if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? (r.closed_period_warning ? 'Dépense enregistrée, mais la période de caisse était déjà clôturée : signalé dans le journal.' : 'Dépense enregistrée.') : 'Dépense mise en attente.');
    } }, field('Date', t.date), field('Catégorie', t.cat), field('Bénéficiaire', t.payee), field('Description', t.desc), field('Montant (GNF)', t.amount), field('Mode de paiement', t.method), field('Référence', t.ref), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }
  G.expenseForm = expenseForm;

  function moveForm() {
    const err = h('div', { class: 'error', hidden: true }), kinds = Object.keys(MOVES).filter((k) => state.me.role !== 'cashier' || CASHIER_MOVES.includes(k));
    const t = { kind: h('select', {}, ...kinds.map((k) => h('option', { value: k }, MOVES[k]))), amount: h('input', { type: 'number', min: 1, step: 1, required: true }), date: h('input', { type: 'date', value: today() }), note: h('input', { maxlength: 200, required: true, placeholder: 'N° de bordereau, nom du client…' }) };
    const banks = Object.entries(G.viewData().bankMeta || {}).filter(([, v]) => v.kind === 'bank' && v.active !== false), acct = h('select', {}, ...banks.map(([k, v]) => h('option', { value: k }, v.label)));
    const acctField = field('Compte bancaire', acct), syncAcct = () => { acctField.hidden = banks.length < 2 || !['bank_deposit', 'bank_withdrawal'].includes(t.kind.value); }; t.kind.addEventListener('input', syncAcct); syncAcct();
    dlg.replaceChildren(h('h2', {}, 'Mouvement de caisse'), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const r = await G.submit({ type: 'cash.move', data: { id: G.uuid(), kind: t.kind.value, amount: Number(t.amount.value), move_date: t.date.value, note: t.note.value, ...(!acctField.hidden ? { account: acct.value } : {}) } });
      if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? 'Mouvement enregistré.' : 'Mouvement mis en attente.');
    } }, field('Type', t.kind), acctField, field('Montant (GNF)', t.amount), field('Date', t.date), field('Explication *', t.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  function closeForm(cash) {
    const err = h('div', { class: 'error', hidden: true });
    const t = { date: h('input', { type: 'date', value: today(), max: today() }), counted: h('input', { type: 'number', min: 0, step: 1, required: true }), opening: h('input', { type: 'number', min: 0, step: 1, value: 0 }), note: h('input', { maxlength: 200, placeholder: 'Obligatoire s\'il y a un écart' }) };
    const diff = h('div', { class: 'big' });
    t.counted.addEventListener('input', () => { const d = Number(t.counted.value || 0) - cash.expected - (cash.needs_opening ? Number(t.opening.value || 0) : 0); diff.textContent = t.counted.value === '' ? '' : (d === 0 ? '✔ Aucun écart' : `Écart : ${d > 0 ? '+' : ''}${money(d)}`); });
    dlg.replaceChildren(h('h2', {}, 'Clôturer la caisse'), h('p', { class: 'muted' }, `Espèces attendues : ${money(cash.expected)}${cash.needs_opening ? ' (hors solde d\'ouverture)' : ''}. Comptez les billets et saisissez le total.`), h('form', { onsubmit: async (e) => {
      e.preventDefault(); err.hidden = true;
      const data = { id: G.uuid(), close_date: t.date.value, counted: Number(t.counted.value), note: t.note.value }; if (cash.needs_opening) data.opening = Number(t.opening.value || 0);
      const r = await G.submit({ type: 'cash.close', data });
      if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
      dlg.close(); G.render(); G.toast(r.status === 'ok' ? (r.difference === 0 ? 'Caisse clôturée : aucun écart ✔' : `Caisse clôturée avec un écart de ${money(r.difference)}.`) : 'Clôture mise en attente.');
    } }, field('Date de clôture', t.date), cash.needs_opening && field('Solde d\'ouverture : espèces en caisse au départ (GNF)', t.opening), field('Espèces comptées (GNF)', t.counted), diff, field('Explication de l\'écart', t.note), err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Clôturer'))));
    dlg.showModal();
  }

  async function askReason(label, op) {
    const reason = prompt(label); if (!reason || reason.trim().length < 3) return;
    const r = await G.submit({ ...op, data: { ...op.data, reason: reason.trim() } });
    if (failed(r)) G.toast(r.message); G.render();
  }

  // ---------- caisse du jour ----------
  function renderCash(vd) {
    const c = vd.cash;
    if (!c) return h('p', { class: 'muted' }, 'Aucune donnée de caisse pour le moment : synchronisez-vous.');
    const rows = (labels, obj, sign) => Object.entries(labels).filter(([k]) => obj[k]).map(([k, l]) => h('div', { class: 'row' }, h('span', { class: 'grow' }, l), h('strong', {}, (sign < 0 ? '− ' : '+ ') + money(obj[k]))));
    const pending = G.queue().length;
    return h('div', {},
      h('div', { class: 'card' }, h('div', { class: 'muted' }, 'Espèces attendues en caisse'), h('div', { class: 'bigger' }, money(c.expected)),
        h('div', { class: 'muted' }, c.needs_opening ? 'Pas encore de clôture : le premier comptage fixera le solde d\'ouverture.' : `Départ : ${money(c.opening)} comptés le ${fmtDay(c.last_close_date)}`),
        pending > 0 && h('div', { class: 'badge warn' }, `${pending} opération(s) en attente non incluse(s)`)),
      h('div', { class: 'card' }, h('h2', {}, 'Détail depuis la dernière clôture'), ...rows(IN_LABELS, c.in, 1), ...rows(OUT_LABELS, c.out, -1), (c.in_total + c.out_total === 0) && h('p', { class: 'muted' }, 'Aucun mouvement en espèces.'),
        h('p', { class: 'muted' }, 'Seuls les paiements en espèces comptent : Orange Money, MTN MoMo, virements et chèques n\'entrent pas dans la caisse.')),
      h('div', { class: 'row' }, can('cash.close') && h('button', { class: 'primary', type: 'button', onclick: () => closeForm(c) }, '✔ Clôturer la caisse'), can('cash.move') && h('button', { class: 'secondary', type: 'button', onclick: moveForm }, '↔ Mouvement de caisse'),
        can('expenses.create') && h('button', { class: 'secondary', type: 'button', onclick: expenseForm }, '− Dépense')));
  }

  // ---------- dépenses ----------
  function renderExpenses(vd) {
    const month = st.month, list = vd.expenses.filter((e) => e.status === 'active' && e.expense_date.startsWith(month));
    const by = new Map(); for (const e of list) by.set(e.category, (by.get(e.category) || 0) + e.amount);
    const total = [...by.values()].reduce((a, b) => a + b, 0);
    const fixed = list.filter((e) => e.nature === 'Fixe').reduce((a, e) => a + e.amount, 0);
    const mi = h('input', { type: 'month', value: month, 'aria-label': 'Mois', onchange: (e) => { st.month = e.target.value || month; render(); } });
    const r = vd.rentability && month === today().slice(0, 7) ? vd.rentability : null;
    const cats = vd.categories.filter((c) => by.has(c.key) || vd.budgets[c.key]);
    return h('div', {},
      h('div', { class: 'card' }, h('div', { class: 'row' }, field('Mois', mi))),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Dépenses du mois'), h('strong', {}, money(total))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'dont charges fixes'), h('strong', {}, money(fixed))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'dont charges variables'), h('strong', {}, money(total - fixed)))),
      r && h('div', { class: 'card' }, h('h2', {}, 'Seuil de rentabilité du mois'),
        r.breakeven ? h('div', {}, h('div', { class: 'big' }, `Il faut ${money(r.breakeven)} de ventes pour couvrir les charges fixes`), h('div', {}, `Ventes du mois : ${money(r.sales)} (`, h('strong', {}, r.reached > 9.99 ? 'plus de 999 %' : Math.round(r.reached * 100) + ' %'), ` du seuil) · marge brute estimée ${Math.round(r.margin_rate * 100)} %`),
          h('div', { class: r.result >= 0 ? 'ok-box' : 'error' }, `Résultat estimé du mois : ${money(r.result)} (ventes − coût d'achat estimé − dépenses)`)) : h('p', { class: 'muted' }, 'Pas assez de données (ventes, achats avec prix, charges fixes) pour calculer le seuil.'),
        h('p', { class: 'muted' }, 'Estimation : le coût d\'achat utilise le coût moyen des achats saisis ; ce n\'est pas un résultat comptable.')),
      (cats.length > 0) && h('div', { class: 'card' }, h('h2', {}, 'Par catégorie et budget'), ...cats.map((c) => { const used = by.get(c.key) || 0, b = vd.budgets[c.key] || 0, over = b > 0 && used > b;
        return h('div', { class: 'row' }, h('span', { class: 'grow' }, `${c.key} `, h('span', { class: 'muted' }, c.nature.toLowerCase())), h('span', {}, money(used)), b > 0 ? h('span', { class: 'badge ' + (over ? 'bad' : used > 0.9 * b ? 'warn' : '') }, `${over ? '⚠ dépasse' : 'budget'} ${money(b)}`) : h('span', { class: 'muted' }, 'sans budget'),
          can('budgets.set') && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const v = prompt(`Budget mensuel pour « ${c.key} » (GNF, 0 pour aucun) :`, String(b)); if (v === null) return; const r2 = await G.submit({ type: 'budget.set', data: { category: c.key, amount: Number(v) } }); if (failed(r2)) G.toast(r2.message); G.render(); } }, 'budget')); })),
      list.length ? h('div', { class: 'list two' }, vd.expenses.filter((e) => e.expense_date.startsWith(month)).map((e) => h('article', { class: 'card' + (e.status === 'cancelled' ? ' struck' : '') },
        h('div', { class: 'head' }, h('span', { class: 'name' }, `${e.number} · ${e.category}`), h('span', { class: 'name' }, money(e.amount))),
        h('div', { class: 'muted' }, `${fmtDay(e.expense_date)} · ${e.method} · ${[e.payee, e.description].filter(Boolean).join(' — ')}${e.reference ? ' · ' + e.reference : ''}`),
        e.status === 'cancelled' && h('div', { class: 'muted' }, 'Annulée : ' + e.cancel_reason), vd.pend.has(e.id) && h('span', { class: 'badge warn' }, 'En attente'),
        e.version > 0 && h('div', { class: 'links' }, h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); G.attachDialog('expense', e.id, `${e.number} · ${e.category} · ${money(e.amount)}`, e.status === 'active' && (can('expenses.create')), () => dlg.close()); } }, '📎 Pièces')),
        e.status === 'active' && e.version > 0 && can('expenses.cancel') && h('div', { class: 'links' }, h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); askReason('Motif de l\'annulation de la dépense ' + e.number + ' :', { type: 'expense.cancel', data: { id: e.id, base_version: e.version } }); } }, 'Annuler'))))) : h('p', { class: 'muted' }, 'Aucune dépense ce mois-ci.'));
  }

  // ---------- historique ----------
  function renderHistory(vd) {
    const closings = vd.closings.slice().sort((a, b) => b.close_date.localeCompare(a.close_date)), lastActive = closings.find((x) => x.status === 'active');
    const moves = vd.moves.slice().sort((a, b) => b.move_date.localeCompare(a.move_date) || b.seq - a.seq);
    return h('div', {},
      h('h2', {}, 'Clôtures de caisse'),
      closings.length ? h('div', { class: 'list two' }, closings.map((x) => h('article', { class: 'card' + (x.status === 'cancelled' ? ' struck' : '') }, h('div', { class: 'head' }, h('span', { class: 'name' }, fmtDay(x.close_date)), h('span', { class: 'name' }, money(x.counted))),
        h('div', { class: 'muted' }, `Attendu ${money(x.expected)} · départ ${money(x.opening)} · entrées ${money(x.cash_in)} · sorties ${money(x.cash_out)}`),
        h('div', { class: 'row' }, x.status === 'cancelled' ? h('span', { class: 'badge bad' }, 'Rouverte : ' + x.cancel_reason) : x.difference === 0 ? h('span', { class: 'badge' }, 'Aucun écart') : h('span', { class: 'badge bad' }, `Écart ${x.difference > 0 ? '+' : ''}${money(x.difference)}`),
          x.drift ? h('span', { class: 'badge warn' }, `⚠ Modifiée depuis la clôture (${x.drift > 0 ? '+' : ''}${money(x.drift)})`) : null),
        x.note && h('div', { class: 'muted' }, x.note), lastActive && x.id === lastActive.id && can('cash.reopen') && h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); askReason('Motif de la réouverture de la caisse du ' + fmtDay(x.close_date) + ' :', { type: 'cash.cancel_close', data: { id: x.id, base_version: x.version } }); } }, 'Rouvrir'))))) : h('p', { class: 'muted' }, 'Aucune clôture pour le moment.'),
      h('h2', {}, 'Mouvements de caisse'),
      moves.length ? h('div', { class: 'list two' }, moves.map((m) => h('article', { class: 'card' + (m.status === 'cancelled' ? ' struck' : '') }, h('div', { class: 'head' }, h('span', { class: 'name' }, MOVES[m.kind]), h('span', { class: 'name' }, money(m.amount))),
        h('div', { class: 'muted' }, `${fmtDay(m.move_date)} · ${m.note}${m.status === 'cancelled' ? ' · annulé : ' + m.cancel_reason : ''}`),
        m.status === 'active' && m.version > 0 && can('cash.cancel') && h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); askReason('Motif de l\'annulation du mouvement :', { type: 'cash.move_cancel', data: { id: m.id, base_version: m.version } }); } }, 'Annuler'))))) : h('p', { class: 'muted' }, 'Aucun mouvement.'));
  }

  // ---------- catégories de dépenses ----------
  function categoriesDialog() {
    const vd = G.viewData(), w = can('budgets.set'), err = h('div', { class: 'error', hidden: true });
    const plan = ((state.data.accounting && state.data.accounting.accounts) || []).filter((a) => /^6[0-7]\d/.test(a.code));
    const dl = h('datalist', { id: 'cat6' }, ...plan.map((a) => h('option', { value: a.code }, a.name)));
    const t = { key: h('input', { maxlength: 50, placeholder: 'Ex. Frais de gardiennage' }), account: h('input', { list: 'cat6', inputmode: 'numeric', placeholder: 'Compte de charge, ex. 638' }), nature: h('select', {}, h('option', { value: 'Fixe' }, 'Charge fixe (loyer, abonnement…)'), h('option', { value: 'Variable' }, 'Charge variable (carburant, transport…)')) };
    const act = async (data) => { if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const r = await G.submit(data); if (failed(r) || r.status === 'conflict') { err.textContent = r.message; err.hidden = false; return; } G.render(); categoriesDialog(); };
    const nameOf = (code) => (plan.find((a) => a.code === code) || {}).name || '';
    dlg.replaceChildren(h('h2', {}, 'Catégories de dépenses'), dl, h('p', { class: 'muted' }, 'La catégorie décide du compte comptable de la dépense et de sa nature (fixe ou variable, pour le seuil de rentabilité). Les catégories de base ne changent pas ; vous pouvez en ajouter. Changer le compte d\'une catégorie s\'applique à TOUTES ses dépenses, passées comprises (refusé si l\'une est dans une période verrouillée).'),
      ...vd.categories.map((c) => h('div', { class: 'row' + (c.active === false ? ' struck' : '') }, h('span', { class: 'grow' }, `${c.key} · ${c.nature.toLowerCase()} · compte ${c.account}${nameOf(c.account) ? ' (' + nameOf(c.account) + ')' : ''}${c.custom ? '' : ''}`), c.custom && h('span', { class: 'badge' }, c.active === false ? 'désactivée' : 'ajoutée'),
        w && c.custom && h('a', { href: '#', onclick: (e) => { e.preventDefault(); act({ type: 'expcat.update', data: { key: c.key, base_version: c.version, changes: { nature: c.nature === 'Fixe' ? 'Variable' : 'Fixe' } } }); } }, c.nature === 'Fixe' ? '→ variable' : '→ fixe'),
        w && c.custom && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const v = prompt('Nouveau compte de charge (classe 6) pour « ' + c.key + ' » :', c.account); if (v && v.trim() !== c.account) act({ type: 'expcat.update', data: { key: c.key, base_version: c.version, changes: { account: v.trim() } } }); } }, 'Compte'),
        w && c.custom && h('a', { href: '#', onclick: (e) => { e.preventDefault(); act({ type: 'expcat.update', data: { key: c.key, base_version: c.version, changes: { active: c.active === false } } }); } }, c.active === false ? 'Réactiver' : 'Désactiver'))),
      w && h('div', { class: 'card' }, h('h3', {}, 'Ajouter une catégorie'), field('Nom', t.key), field('Compte comptable', t.account), field('Nature', t.nature), h('p', {}, h('button', { class: 'primary', type: 'button', onclick: () => act({ type: 'expcat.create', data: { key: t.key.value, account: t.account.value, nature: t.nature.value } }) }, '＋ Ajouter'))), err,
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    if (!dlg.open) dlg.showModal();
  }

  // ---------- dépenses récurrentes ----------
  function recurringForm(r) {
    const cats = (state.data.categories || []).filter((x) => x.active !== false && x.account !== '422').map((x) => x.key || x), err = h('div', { class: 'error', hidden: true });
    const f = { label: h('input', { maxlength: 120, required: true, value: r ? r.label : '', placeholder: 'Ex. Loyer du dépôt' }), cat: h('select', {}, ...cats.map((k) => h('option', { value: k, selected: r && r.category === k }, k))), amount: h('input', { type: 'number', min: 1, step: 1, required: true, value: r ? r.amount : '' }),
      method: h('select', {}, ...(G.viewData().methods || []).map((m) => h('option', { value: m, selected: r && r.method === m }, m))), payee: h('input', { maxlength: 120, value: r ? r.payee : '' }), day: h('input', { type: 'number', min: 1, max: 31, step: 1, required: true, value: r ? r.day : 5 }),
      start: h('input', { type: 'date', value: r ? r.start_date : today(), disabled: !!r }), end: h('input', { type: 'date', value: r && r.end_date ? r.end_date : '' }) };
    dlg.replaceChildren(h('h2', {}, r ? `Modifier ${r.number}` : 'Nouvelle dépense récurrente'), h('p', { class: 'muted' }, r ? 'Les changements ne concernent que les échéances à venir.' : 'Un modèle prépare la dépense de chaque mois ; vous validez la génération vous-même.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } err.hidden = true;
        const base = { label: f.label.value, amount: Number(f.amount.value), method: f.method.value, payee: f.payee.value, day: Number(f.day.value), end_date: f.end.value || null };
        const res = r ? await G.submit({ type: 'recurring.update', data: { id: r.id, base_version: r.version, changes: base } }) : await G.submit({ type: 'recurring.create', data: { id: G.uuid(), category: f.cat.value, start_date: f.start.value, ...base } });
        if (failed(res) || res.status === 'conflict') { err.textContent = res.message; err.hidden = false; return; } dlg.close(); G.render(); } },
        field('Libellé', f.label), !r && field('Catégorie', f.cat), field('Montant (GNF)', f.amount), field('Mode de paiement', f.method), field('Bénéficiaire', f.payee), field('Jour du mois (le 31 devient le dernier jour des mois courts)', f.day), !r && field('Première échéance à partir du', f.start), field('Dernière échéance (facultatif)', f.end), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }
  function renderRecurring() {
    const list = state.data.recurring || [], due = list.reduce((a, r) => a + r.pending.length, 0);
    const generate = async () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const res = await G.submit({ type: 'recurring.generate', data: {} });
      if (failed(res)) { G.toast(res.message); return; } G.toast(`${res.created.length} dépense(s) créée(s)` + (res.blocked.length ? `, ${res.blocked.length} bloquée(s) : ${res.blocked[0].message}` : '.')); G.render(); };
    return h('div', {}, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Dépenses récurrentes'), can('recurring.manage') && h('button', { class: 'secondary', type: 'button', onclick: () => recurringForm() }, '＋ Nouveau modèle')),
      due ? h('div', { class: 'card' }, h('strong', {}, `${due} échéance(s) à générer`), h('div', { class: 'muted' }, 'Chaque échéance crée une vraie dépense (numéro DP, écritures, journal). Un mois déjà généré ne l\'est jamais deux fois.'), h('p', {}, h('button', { class: 'primary', type: 'button', onclick: generate }, 'Générer les dépenses échues'))) : h('div', { class: 'ok-box' }, 'Rien à générer pour le moment.'),
      list.length ? h('div', { class: 'list two' }, list.map((r) => h('article', { class: 'card' + (r.active ? '' : ' struck') }, h('div', { class: 'head' }, h('span', { class: 'name' }, `${r.number} · ${r.label}`), h('span', { class: 'name' }, money(r.amount))),
        h('div', { class: 'muted' }, `${r.category} · le ${r.day} de chaque mois · ${r.method}${r.payee ? ' · ' + r.payee : ''} · dès le ${fmtDay(r.start_date)}${r.end_date ? ' jusqu\'au ' + fmtDay(r.end_date) : ''}`), r.pending.length > 0 && h('span', { class: 'badge warn' }, r.pending.length + ' à générer'),
        can('recurring.manage') && h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); recurringForm(r); } }, 'Modifier'), h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (!state.online) return; await G.submit({ type: 'recurring.update', data: { id: r.id, base_version: r.version, changes: { active: !r.active } } }); G.render(); } }, r.active ? 'Suspendre' : 'Reprendre'))))) : h('p', { class: 'muted' }, 'Aucun modèle. Exemples : loyer, abonnement internet, salaire fixe.'));
  }

  function render() {
    const vd = G.viewData();
    const modes = [['caisse', 'Caisse du jour', can('cash.read')], ['depenses', 'Dépenses', can('expenses.create') || can('expenses.read_all') || can('expenses.read_own')], ['historique', 'Historique', can('cash.read')], ['recurrentes', 'Récurrentes', can('recurring.manage')]].filter((m) => m[2]);
    if (!modes.find((m) => m[0] === st.mode)) st.mode = modes[0][0];
    const body = st.mode === 'caisse' ? renderCash(vd) : st.mode === 'depenses' ? renderExpenses(vd) : st.mode === 'recurrentes' ? renderRecurring() : renderHistory(vd);
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Caisse et dépenses'), st.mode === 'depenses' && can('budgets.set') && h('button', { class: 'secondary', type: 'button', onclick: categoriesDialog }, '🏷️ Catégories'), can('expenses.create') && st.mode === 'depenses' && h('button', { class: 'primary', type: 'button', onclick: expenseForm }, '＋ Nouvelle dépense')),
      h('div', { class: 'card' }, h('div', { class: 'chips' }, modes.map(([id, label]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.mode === id), onclick: () => { st.mode = id; render(); } }, label)))), body);
  }
  tabs.push({ id: 'caisse', label: 'Caisse', show: () => can('cash.read') || can('expenses.create'), render });
})();
