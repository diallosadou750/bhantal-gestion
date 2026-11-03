/* Écran Compta : résultat, bilan, TVA, journal, balance, grand livre, écritures manuelles, plan comptable, verrou. Réservé au propriétaire et au comptable. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, dlg, view, tabs, api, OfflineError } = G;
  const y = today().slice(0, 4);
  const st = { mode: 'synthese', asset: null, from: `${y}-01-01`, to: today(), journal: '', account: '411' };
  const failed = (r) => r.status === 'invalid' || r.status === 'denied';
  const amt = (n) => (n ? money(n) : '');
  const WARN = 'Outil de travail inspiré du plan SYSCOHADA, établi sans accès au texte officiel complet : à faire valider par un expert-comptable avant tout usage officiel ou déclaratif.';

  const tbl = (heads, rows, cls = []) => h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, heads.map((x, i) => h('th', { class: cls[i] === 'n' ? 'num' : '' }, x))),
    ...rows.map((r) => h('tr', { class: r._cls || '' }, r.cells.map((x, i) => h('td', { class: cls[i] === 'n' ? 'num' : '' }, x))))));
  const row = (cells, c) => ({ cells, _cls: c });
  const qs = (extra = {}) => new URLSearchParams({ from: st.from, to: st.to, ...extra }).toString();

  function periodBar() {
    const set = (a, b) => { st.from = a; st.to = b; render(); };
    const t = today(), month = t.slice(0, 8) + '01';
    const fy = (state.data.accounting && state.data.accounting.first_year) || Number(y), years = []; for (let k = Number(y); k >= Math.max(fy, Number(y) - 5); k--) years.push(k);
    return h('div', { class: 'card' }, h('div', { class: 'filters' }, field('Du', h('input', { type: 'date', value: st.from, onchange: (e) => { st.from = e.target.value || st.from; render(); } })), field('Au', h('input', { type: 'date', value: st.to, max: t, onchange: (e) => { st.to = e.target.value || st.to; render(); } }))),
      h('div', { class: 'chips' }, h('button', { class: 'chip', type: 'button', onclick: () => set(month, t) }, 'Ce mois'), h('button', { class: 'chip', type: 'button', onclick: () => set(`${y}-01-01`, t) }, 'Cette année'), h('button', { class: 'chip', type: 'button', onclick: () => set('2000-01-01', t) }, 'Tout'), years.map((k) => h('button', { class: 'chip', type: 'button', onclick: () => set(`${k}-01-01`, k === Number(y) ? t : `${k}-12-31`) }, 'Exercice ' + k))));
  }

  async function fetchReport(kind, extra) {
    try { return await api(`/api/accounting/report?kind=${kind}&${qs(extra)}`); } catch (e) { return { error: e instanceof OfflineError ? 'La comptabilité demande une connexion.' : e.message }; }
  }

  // ---------- synthèse ----------
  async function synthese(box) {
    const r = await fetchReport('statements'); if (r.error) { box.replaceChildren(h('div', { class: 'error' }, r.error)); return; }
    const inc = r.income, bs = r.balance_sheet, v = r.vat;
    box.replaceChildren(
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Chiffre d\'affaires'), h('strong', {}, money(inc.sales))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Marge brute (ventes − achats ± stock)'), h('strong', {}, money(inc.gross_margin))),
        h('div', { class: 'card kpi' + (inc.result < 0 ? ' bad' : '') }, h('div', { class: 'muted' }, 'Résultat de la période'), h('strong', {}, money(inc.result)))),
      h('div', { class: 'card' }, h('h2', {}, 'Compte de résultat'), tbl(['Compte', 'Intitulé', 'Montant'], [row([h('strong', {}, 'PRODUITS'), '', ''], 'sub'), ...inc.products.map((x) => row([x.account, x.name, money(x.amount)])), row(['', h('strong', {}, 'Total des produits'), h('strong', {}, money(inc.products_total))]),
        row([h('strong', {}, 'CHARGES'), '', ''], 'sub'), ...inc.charges.map((x) => row([x.account, x.name, money(x.amount)])), row(['', h('strong', {}, 'Total des charges'), h('strong', {}, money(inc.charges_total))]), row(['', h('strong', {}, 'RÉSULTAT'), h('strong', {}, money(inc.result))])], ['', '', 'n']),
        h('p', { class: 'muted' }, 'Le compte 603 (variation de stocks) vient de l\'inventaire calculé : stock valorisé au coût moyen d\'achat (estimation).')),
      h('div', { class: 'card' }, h('h2', {}, `Bilan au ${fmtDay(bs.as_of)}`), h('div', { class: bs.balanced ? 'ok-box' : 'error' }, bs.balanced ? '✔ Le bilan est équilibré (actif = passif).' : '⚠ Le bilan n\'est pas équilibré : signalez-le.'),
        h('div', { class: 'list two' }, h('div', {}, h('h3', {}, 'ACTIF'), tbl(['Compte', 'Intitulé', 'Montant'], [...bs.assets.map((x) => row([x.account, x.name, money(x.amount)])), row(['', h('strong', {}, 'Total actif'), h('strong', {}, money(bs.assets_total))])], ['', '', 'n'])),
          h('div', {}, h('h3', {}, 'PASSIF'), tbl(['Compte', 'Intitulé', 'Montant'], [...bs.liabilities.map((x) => row([x.account, x.name, money(x.amount)])), row(['', h('strong', {}, 'Total passif'), h('strong', {}, money(bs.liabilities_total))])], ['', '', 'n']))),
        h('p', { class: 'muted' }, bs.note + ' Un compte de caisse ou de banque au passif signale un solde négatif ou des soldes d\'ouverture manquants.')),
      h('div', { class: 'card' }, h('h2', {}, 'TVA'), tbl(['Mois', 'Collectée', 'Récupérable', 'À payer (ou crédit)'], [...v.rows.map((x) => row([x.month, money(x.collected), money(x.deductible), money(x.due)])), row([h('strong', {}, 'Total'), h('strong', {}, money(v.collected)), h('strong', {}, money(v.deductible)), h('strong', {}, money(v.due))])], ['', 'n', 'n', 'n']),
        v.by_rate && (v.by_rate.sales.length > 0 || v.by_rate.purchases.length > 0) && h('div', {}, h('h3', {}, 'Par taux (documents de la période, avoirs non déduits)'),
          tbl(['', 'Taux', 'Total TTC', 'Base hors taxe', 'TVA'], [...v.by_rate.sales.map((x) => row(['Ventes', x.rate + ' %', money(x.total), money(x.base), money(x.tax)])), ...v.by_rate.purchases.map((x) => row(['Achats', x.rate + ' %', money(x.total), money(x.base), money(x.tax)]))], ['', '', 'n', 'n', 'n'])),
        h('p', { class: 'muted' }, `Taux de TVA par défaut sur ventes : ${v.sales_rate} % · sur achats : ${v.purchase_rate} % (chaque produit ou ligne peut avoir son propre taux). ${v.note}`)),
      h('p', { class: 'muted' }, WARN));
  }

  // ---------- journal, balance, grand livre ----------
  async function journalView(box) {
    const r = await fetchReport('journal', st.journal ? { journal: st.journal } : {}); if (r.error) { box.replaceChildren(h('div', { class: 'error' }, r.error)); return; }
    const jn = (state.data.accounting && state.data.accounting.journals) || {};
    box.replaceChildren(h('div', { class: 'chips' }, [['', 'Tous'], ...Object.entries(jn)].map(([k, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.journal === k), onclick: () => { st.journal = k; render(); } }, k ? `${k} · ${l}` : l)),
      h('a', { class: 'btn', href: '/api/accounting/export.csv?kind=journal&' + qs(st.journal ? { journal: st.journal } : {}), download: '' }, 'Exporter en CSV'), h('a', { class: 'btn', href: '/api/reports/pdf?report=journal&' + qs(st.journal ? { journal: st.journal } : {}), target: '_blank', rel: 'noopener' }, 'Exporter en PDF')),
      r.entries.length ? h('div', { class: 'list' }, r.entries.slice(0, 300).map((e) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, `${fmtDay(e.date)} · ${e.journal} · ${e.ref}`), h('span', { class: 'badge' + (e.auto ? '' : ' warn') }, e.auto ? 'automatique' : 'manuelle')), h('div', { class: 'muted' }, e.label),
        tbl(['Compte', 'Intitulé', 'Débit', 'Crédit'], e.lines.map((l) => row([l.account, l.name, amt(l.debit), amt(l.credit)])), ['', '', 'n', 'n'])))) : h('p', { class: 'muted' }, 'Aucune écriture sur cette période.'),
      r.entries.length > 300 && h('p', { class: 'muted' }, `Les 300 premières écritures sur ${r.entries.length} sont affichées : réduisez la période ou exportez en CSV.`));
  }

  async function balanceView(box) {
    const r = await fetchReport('balance'); if (r.error) { box.replaceChildren(h('div', { class: 'error' }, r.error)); return; }
    const t = r.totals;
    box.replaceChildren(h('div', { class: 'row' }, h('div', { class: r.balanced ? 'ok-box grow' : 'error grow' }, r.balanced ? '✔ Balance équilibrée : total des débits = total des crédits.' : '⚠ Balance déséquilibrée : signalez-le.'), h('a', { class: 'btn', href: '/api/accounting/export.csv?kind=balance&' + qs(), download: '' }, 'Exporter en CSV'), h('a', { class: 'btn', href: '/api/reports/pdf?report=balance&' + qs(), target: '_blank', rel: 'noopener' }, 'Exporter en PDF')),
      tbl(['Compte', 'Intitulé', 'Ouv. D', 'Ouv. C', 'Mvt D', 'Mvt C', 'Solde D', 'Solde C'], [...r.rows.map((x) => row([x.account, x.name, amt(x.open_debit), amt(x.open_credit), amt(x.debit), amt(x.credit), amt(x.close_debit), amt(x.close_credit)])),
        row(['', h('strong', {}, 'Totaux'), ...[t.od, t.oc, t.md, t.mc, t.cd, t.cc].map((v) => h('strong', {}, money(v)))])], ['', '', 'n', 'n', 'n', 'n', 'n', 'n']));
  }

  async function ledgerView(box) {
    const accounts = (state.data.accounting && state.data.accounting.accounts) || [];
    const sel = h('select', { 'aria-label': 'Compte', onchange: (e) => { st.account = e.target.value; render(); } }, ...accounts.map((a) => h('option', { value: a.code, selected: a.code === st.account }, `${a.code} · ${a.name}`)));
    const r = await fetchReport('ledger', { account: st.account }); if (r.error) { box.replaceChildren(field('Compte', sel), h('div', { class: 'error' }, r.error)); return; }
    box.replaceChildren(field('Compte', sel), h('div', { class: 'row' }, h('div', { class: 'grow' }, `Solde d'ouverture : ${money(r.opening)}`), h('a', { class: 'btn', href: '/api/accounting/export.csv?kind=ledger&' + qs({ account: st.account }), download: '' }, 'Exporter en CSV'), h('a', { class: 'btn', href: '/api/reports/pdf?report=ledger&' + qs({ account: st.account }), target: '_blank', rel: 'noopener' }, 'Exporter en PDF')),
      r.rows.length ? tbl(['Date', 'Jnl', 'Pièce', 'Libellé', 'Débit', 'Crédit', 'Solde'], [...r.rows.map((x) => row([fmtDay(x.date), x.journal, x.ref, x.label, amt(x.debit), amt(x.credit), money(x.balance)])), row(['', '', '', h('strong', {}, 'Solde de clôture'), '', '', h('strong', {}, money(r.closing))])], ['', '', '', '', 'n', 'n', 'n']) : h('p', { class: 'muted' }, 'Aucun mouvement sur cette période.'));
  }

  // ---------- écritures manuelles ----------
  function entryForm() {
    const acc = (state.data.accounting && state.data.accounting.accounts) || [], err = h('div', { class: 'error', hidden: true }), box = h('div', {}), sum = h('div', { class: 'big' }), rows = [];
    const dl = h('datalist', { id: 'acctlist' }, ...acc.map((a) => h('option', { value: a.code }, a.name)));
    const t = { date: h('input', { type: 'date', value: today(), max: today() }), label: h('input', { maxlength: 200, required: true, placeholder: 'Ex. Apport en capital, correction…' }) };
    const recalc = () => { const d = rows.reduce((a, r) => a + Number(r.d.value || 0), 0), c = rows.reduce((a, r) => a + Number(r.c.value || 0), 0); sum.textContent = `Débit ${money(d)} · Crédit ${money(c)} — ${d === c && d > 0 ? '✔ équilibrée' : 'écart ' + money(d - c)}`; };
    const addRow = () => { const r = { a: h('input', { list: 'acctlist', placeholder: 'Compte (ex. 521)', 'aria-label': 'Compte' }), d: h('input', { type: 'number', min: 0, step: 1, placeholder: 'Débit', 'aria-label': 'Débit' }), c: h('input', { type: 'number', min: 0, step: 1, placeholder: 'Crédit', 'aria-label': 'Crédit' }), name: h('div', { class: 'muted' }) };
      r.a.addEventListener('input', () => { const x = acc.find((z) => z.code === r.a.value); r.name.textContent = x ? x.name : ''; }); r.d.addEventListener('input', recalc); r.c.addEventListener('input', recalc);
      r.root = h('div', { class: 'card line' }, r.a, r.name, h('div', { class: 'filters' }, r.d, r.c)); rows.push(r); box.append(r.root); };
    addRow(); addRow();
    dlg.replaceChildren(h('h2', {}, 'Écriture manuelle (opérations diverses)'), h('p', { class: 'muted' }, 'Pour les soldes d\'ouverture, corrections et opérations que l\'application ne produit pas toute seule. Total des débits = total des crédits.'), dl,
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } err.hidden = true;
        const lines = rows.filter((r) => r.a.value).map((r) => ({ account: r.a.value, debit: Number(r.d.value || 0), credit: Number(r.c.value || 0) }));
        const r = await G.submit({ type: 'journal.create', data: { id: G.uuid(), entry_date: t.date.value, label: t.label.value, lines } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Écriture ${r.number || ''} enregistrée.`); } },
        field('Date', t.date), field('Libellé', t.label), box, h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: addRow }, '＋ Ligne')), sum, err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    recalc(); dlg.showModal();
  }
  function manualList() {
    const vd = G.viewData(), list = vd.jentries.slice().sort((a, b) => b.entry_date.localeCompare(a.entry_date) || b.seq - a.seq);
    return h('div', {}, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Écritures manuelles'), can('accounting.write') && h('button', { class: 'primary', type: 'button', onclick: entryForm }, '＋ Nouvelle écriture')),
      list.length ? h('div', { class: 'list two' }, list.map((e) => h('article', { class: 'card' + (e.status === 'cancelled' ? ' struck' : '') }, h('div', { class: 'head' }, h('span', { class: 'name' }, `${e.number} · ${fmtDay(e.entry_date)}`)), h('div', { class: 'muted' }, e.label),
        tbl(['Compte', 'Débit', 'Crédit'], e.lines.map((l) => row([l.account, amt(l.debit), amt(l.credit)])), ['', 'n', 'n']), e.status === 'cancelled' && h('div', { class: 'muted' }, 'Annulée : ' + e.cancel_reason),
        e.status === 'active' && can('accounting.write') && h('div', { class: 'links' }, h('a', { href: '#', onclick: async (ev) => { ev.preventDefault(); const reason = prompt('Motif de l\'annulation de ' + e.number + ' :'); if (!reason || reason.trim().length < 3) return; const r = await G.submit({ type: 'journal.cancel', data: { id: e.id, base_version: e.version, reason: reason.trim() } }); if (failed(r)) G.toast(r.message); G.render(); } }, 'Annuler'))))) : h('p', { class: 'muted' }, 'Aucune écriture manuelle.'));
  }

  // ---------- réglages ----------
  function settingsView() {
    const a = state.data.accounting; if (!a) return h('p', { class: 'muted' }, 'Synchronisez-vous.');
    const ml = a.map_labels, inputs = {};
    const dl = h('datalist', { id: 'planlist' }, ...a.accounts.map((x) => h('option', { value: x.code }, x.name)));
    const pt = h('input', { type: 'number', min: 0, max: 30, step: 1, value: a.purchase_tax_rate });
    const lockIn = h('input', { type: 'date', value: a.locked_through || '', max: today() });
    const nc = { code: h('input', { inputmode: 'numeric', placeholder: 'ex. 52101' }), name: h('input', { maxlength: 120, placeholder: 'Intitulé' }) };
    return h('div', {}, dl,
      h('div', { class: 'card' }, h('h2', {}, 'Comptes utilisés par les écritures automatiques'), ...Object.entries(ml).map(([k, l]) => { inputs[k] = h('input', { list: 'planlist', value: a.map[k] }); return field(l, inputs[k]); }),
        field('TVA sur achats en % (0 : pas de TVA récupérable). À confirmer avec votre expert-comptable.', pt), h('p', { class: 'muted' }, `TVA sur ventes : ${a.sales_tax_rate} % (à régler dans Mon compte).`),
        h('p', {}, h('button', { class: 'primary', type: 'button', onclick: async () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const map = {}; for (const [k, el] of Object.entries(inputs)) if (el.value !== a.map[k]) map[k] = el.value;
          const r = await G.submit({ type: 'accounting.settings', data: { map, purchase_tax_rate: Number(pt.value) } }); G.toast(failed(r) ? r.message : 'Réglages enregistrés.'); G.render(); } }, 'Enregistrer les réglages'))),
      h('div', { class: 'card' }, h('h2', {}, 'Verrou de période'), h('p', {}, a.locked_through ? `Comptabilité verrouillée jusqu'au ${fmtDay(a.locked_through)} : aucune opération de cette période ne peut être créée ni annulée.` : 'Aucun verrou : toute période est modifiable.'),
        can('accounting.lock') ? h('div', {}, field('Verrouiller jusqu\'au (inclus)', lockIn), h('p', {}, h('button', { class: 'primary', type: 'button', onclick: async () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return; }
          let reason = ''; if (a.locked_through && (!lockIn.value || lockIn.value < a.locked_through)) { reason = prompt('Motif (obligatoire pour réduire ou lever le verrou) :') || ''; if (reason.trim().length < 3) return; }
          const r = await G.submit({ type: 'accounting.lock', data: { through: lockIn.value, reason } }); G.toast(failed(r) ? r.message : 'Verrou mis à jour.'); G.render(); } }, 'Appliquer'))) : h('p', { class: 'muted' }, 'Seul le propriétaire peut modifier le verrou.')),
      h('div', { class: 'card' }, h('h2', {}, 'Plan comptable'), can('accounting.write') && h('div', { class: 'filters' }, field('Code', nc.code), field('Intitulé', nc.name)),
        can('accounting.write') && h('p', {}, h('button', { class: 'secondary', type: 'button', onclick: async () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const r = await G.submit({ type: 'account.create', data: { code: nc.code.value, name: nc.name.value } }); G.toast(failed(r) ? r.message : 'Compte ajouté.'); if (!failed(r)) G.render(); } }, 'Ajouter un compte')),
        tbl(['Code', 'Intitulé'], a.accounts.map((x) => row([x.code, x.name + (x.custom ? ' (ajouté)' : '')]))), h('p', { class: 'muted' }, WARN)));
  }

  // ---------- clôture d'exercice ----------
  async function closeDialog(year) {
    if (!state.online) { G.toast('Cette action demande une connexion.'); return; }
    let pv; try { pv = await api(`/api/accounting/closing?year=${year}`); } catch (e) { G.toast(e.message); return; }
    const err = h('div', { class: 'error', hidden: true }), ok = h('input', { type: 'checkbox' }), conf = h('input', { inputmode: 'numeric', placeholder: 'Tapez ' + year });
    const alloc = h('select', {}, h('option', { value: 'none' }, 'Laisser en « résultat en instance d\'affectation » (compte 131/139) : décision de l\'assemblée à venir'), h('option', { value: 'carry_forward' }, 'Reporter à nouveau (compte 121 ou 129)'), pv.result > 0 && h('option', { value: 'reserves' }, 'Affecter aux réserves (compte 106)'));
    const note = h('input', { maxlength: 200, placeholder: 'Note facultative (par ex. validé avec l\'expert-comptable le …)' });
    dlg.replaceChildren(h('h2', {}, `Clôturer l'exercice ${year}`), h('p', { class: 'muted' }, `Du ${fmtDay(pv.from)} au ${fmtDay(pv.to)}`),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Chiffre d\'affaires'), h('strong', {}, money(pv.sales))), h('div', { class: 'card kpi' + (pv.result < 0 ? ' bad' : '') }, h('div', { class: 'muted' }, 'Résultat'), h('strong', {}, money(pv.result)))),
      pv.blocked.length ? h('div', { class: 'error' }, h('strong', {}, 'Clôture impossible pour le moment :'), ...pv.blocked.map((x) => h('div', {}, '• ' + x))) : null,
      pv.warnings.length ? h('div', { class: 'card' }, h('strong', {}, 'À lire avant de clôturer'), ...pv.warnings.map((x) => h('div', { class: 'muted' }, '• ' + x))) : null,
      pv.can_close && h('div', {}, h('p', { class: 'muted' }, 'La clôture solde les comptes de charges et de produits, enregistre le résultat, reporte les soldes du bilan sur l\'exercice suivant et VERROUILLE la période : plus aucune opération de cet exercice ne pourra être créée ni annulée. Seul le propriétaire peut la rouvrir (motif obligatoire, journalisé).'),
        field('Affectation du résultat', alloc), field('Note', note), h('label', { class: 'check' }, ok, ' J\'ai lu les avertissements et je veux clôturer cet exercice'), field('Pour confirmer, tapez l\'année de l\'exercice', conf)),
      err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer'), pv.can_close && h('button', { class: 'primary', type: 'button', onclick: async () => {
        err.hidden = true; if (!ok.checked || conf.value.trim() !== String(year)) { err.textContent = 'Cochez la case et tapez l\'année pour confirmer.'; err.hidden = false; return; }
        const r = await G.submit({ type: 'fiscal.close', data: { year, allocation: alloc.value, accept_warnings: true, note: note.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Exercice ${year} clôturé.`); } }, 'Clôturer définitivement')));
    dlg.showModal();
  }
  function closureView() {
    const a = state.data.accounting; if (!a) return h('p', { class: 'muted' }, 'Synchronisez-vous.');
    const done = a.fiscal_years, last = done.length ? done[done.length - 1] : null, owner = can('accounting.close');
    return h('div', {}, h('div', { class: 'card' }, h('h2', {}, 'Clôture d\'exercice'), h('p', { class: 'muted' }, 'L\'exercice est l\'année civile (1er janvier au 31 décembre). Clôturer, c\'est arrêter les comptes de l\'année : on solde les charges et les produits, on enregistre le résultat, on reporte les soldes du bilan sur l\'année suivante et on verrouille la période. Faites-le avec votre expert-comptable, après avoir clôturé la caisse, rapproché la banque et compté le stock.')),
      a.next_to_close ? h('div', { class: 'card' }, h('h2', {}, `Exercice ${a.next_to_close} : prêt à être clôturé`), h('p', {}, 'L\'année est terminée. Consultez l\'aperçu avant de décider.'), h('button', { class: 'primary', type: 'button', onclick: () => closeDialog(a.next_to_close) }, `Préparer la clôture ${a.next_to_close}`)) : h('div', { class: 'ok-box' }, 'Aucun exercice terminé en attente de clôture.'),
      done.length ? h('div', { class: 'list two' }, done.slice().reverse().map((f) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, `Exercice ${f.year}`), h('span', { class: 'badge' }, 'Clôturé')), h('div', { class: 'muted' }, `${fmtDay(f.start)} au ${fmtDay(f.end)} · clôturé le ${fmtDay(f.closed_at.slice(0, 10))}`),
        h('div', { class: 'name' }, 'Résultat : ' + money(f.result)), h('div', { class: 'muted' }, 'Affectation : ' + ({ none: 'résultat en instance d\'affectation', carry_forward: 'report à nouveau', reserves: 'réserves' }[f.allocation] || f.allocation)),
        owner && last && f.year === last.year && h('div', { class: 'links' }, h('a', { href: '#', onclick: async (e) => { e.preventDefault(); if (!state.online) { G.toast('Cette action demande une connexion.'); return; } const reason = prompt(`Motif de la réouverture de l'exercice ${f.year} (les écritures de clôture seront supprimées et le verrou levé) :`);
          if (!reason || reason.trim().length < 3) return; const r = await G.submit({ type: 'fiscal.reopen', data: { year: f.year, reason: reason.trim() } }); G.toast(failed(r) ? r.message : 'Exercice rouvert.'); G.render(); } }, 'Rouvrir cet exercice'))))) : h('p', { class: 'muted' }, 'Aucun exercice clôturé pour le moment.'),
      !owner && h('p', { class: 'muted' }, 'Seul le propriétaire peut clôturer ou rouvrir un exercice.'));
  }

  // ---------- charges et produits constatés d'avance ----------
  function deferralForm() {
    const a = state.data.accounting, err = h('div', { class: 'error', hidden: true }), t0 = today();
    const used = new Set((state.data.deferrals || []).filter((x) => x.status === 'active' && x.expense_id).map((x) => x.expense_id));
    const exps = (state.data.expenses || []).filter((e) => e.status === 'active' && !used.has(e.id)).sort((x, z) => z.expense_date.localeCompare(x.expense_date)).slice(0, 60);
    const f = { kind: h('select', {}, h('option', { value: 'charge' }, 'Charge constatée d\'avance (j\'ai payé d\'avance)'), h('option', { value: 'product' }, 'Produit constaté d\'avance (j\'ai facturé d\'avance)')),
      exp: h('select', {}, h('option', { value: '' }, '— Saisie manuelle —'), ...exps.map((e) => h('option', { value: e.id }, `${e.number} · ${fmtDay(e.expense_date)} · ${e.category} · ${money(e.amount)}`))),
      label: h('input', { maxlength: 160, required: true, placeholder: 'Ex. Assurance annuelle, loyer du trimestre…' }), account: h('input', { list: 'acctlist2', inputmode: 'numeric', placeholder: 'ex. 625 (charge) ou 706 (produit)' }),
      amount: h('input', { type: 'number', min: 1, step: 1, placeholder: 'Laisser vide : montant de la dépense' }), book: h('input', { type: 'date', value: t0, max: t0 }), start: h('input', { type: 'date', value: t0, required: true }), end: h('input', { type: 'date', required: true }), note: h('input', { maxlength: 200 }) };
    const dl = h('datalist', { id: 'acctlist2' }, ...((a && a.accounts) || []).filter((x) => '67'.includes(x.code[0])).map((x) => h('option', { value: x.code }, x.name)));
    const manual = [field('Compte de charge ou de produit à étaler', f.account), field('Montant à étaler', f.amount), field('Date de la dépense ou de la facture', f.book)];
    const sync = () => { const link = f.exp.value !== '' && f.kind.value === 'charge'; manual[0].hidden = link; manual[2].hidden = link; manual[1].hidden = false; f.exp.parentElement && (f.exp.parentElement.hidden = f.kind.value !== 'charge'); }; f.kind.addEventListener('input', sync); f.exp.addEventListener('input', sync);
    const expField = field('Dépense déjà saisie', f.exp);
    f.exp.addEventListener('input', () => { sync(); expField.hidden = f.kind.value !== 'charge'; }); f.kind.addEventListener('input', () => { expField.hidden = f.kind.value !== 'charge'; });
    dlg.replaceChildren(h('h2', {}, 'Étaler une dépense ou une facture'), dl, h('p', { class: 'muted' }, 'Une dépense payée d\'avance (assurance, loyer d\'un trimestre) ne pèse sur le résultat que mois par mois, au prorata des jours. La période commence au plus tôt le 1er du mois de la dépense et finit après ce mois.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const link = f.exp.value && f.kind.value === 'charge';
        const data = { id: G.uuid(), kind: f.kind.value, label: f.label.value, start_date: f.start.value, end_date: f.end.value, note: f.note.value }; if (f.amount.value) data.amount = Number(f.amount.value);
        if (link) data.expense_id = f.exp.value; else { data.account = f.account.value; data.book_date = f.book.value; }
        const r = await G.submit({ type: 'deferral.create', data }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Étalement enregistré.'); } },
        field('Nature', f.kind), expField, field('Libellé', f.label), ...manual, field('La période couverte commence le', f.start), field('… et se termine le', f.end), field('Note', f.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    sync(); dlg.showModal();
  }

  function deferralsView() {
    const list = (state.data.deferrals || []).slice().sort((x, z) => z.book_date.localeCompare(x.book_date) || z.seq - x.seq), live = list.filter((x) => x.status === 'active' && !x.orphan);
    const ask = async (x) => { if (!online()) return; const reason = prompt(`Motif de l'annulation de ${x.number} :`); if (!reason || reason.trim().length < 3) return; const r = await G.submit({ type: 'deferral.cancel', data: { id: x.id, base_version: x.version, reason: reason.trim() } }); G.toast(failed(r) ? r.message : 'Annulée.'); G.render(); };
    return h('div', {}, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Charges et produits constatés d\'avance'), can('accounting.write') && h('button', { class: 'primary', type: 'button', onclick: deferralForm }, '＋ Étaler')),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Charges d\'avance (actif, compte 476)'), h('strong', {}, money(live.filter((x) => x.kind === 'charge').reduce((s, x) => s + x.remaining, 0)))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Produits d\'avance (dette, compte 477)'), h('strong', {}, money(live.filter((x) => x.kind === 'product').reduce((s, x) => s + x.remaining, 0))))),
      list.length ? tbl(['N°', 'Libellé', 'Période couverte', 'Montant', 'Déjà au résultat', 'Reste à étaler', 'État', ''], list.map((x) => row([x.number, x.label + (x.expense_number ? ' (' + x.expense_number + ')' : ''), `${fmtDay(x.start_date)} → ${fmtDay(x.end_date)}`, money(x.amount), money(x.recognized), money(x.status === 'active' && !x.orphan ? x.remaining : 0), x.status === 'cancelled' ? 'Annulée' : (x.orphan ? 'Dépense annulée' : (x.remaining ? 'En cours' : 'Soldée')), x.status === 'active' && can('accounting.write') ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); ask(x); } }, 'Annuler') : ''], x.status === 'cancelled' || x.orphan ? 'struck' : '')), ['', '', '', 'n', 'n', 'n', '', '']) : h('p', { class: 'muted' }, 'Aucun étalement.'),
      h('p', { class: 'muted' }, 'Méthode : la dépense est sortie du résultat le jour où elle est comptabilisée (compte 476), puis chaque fin de mois la part du mois y retourne, au prorata des jours (arrondis exacts). Au bilan, le reste est un actif (charge d\'avance) ou une dette (produit d\'avance). À faire valider par votre expert-comptable.'));
  }

  // ---------- provisions ----------
  function provisionForm(pre) {
    const a = state.data.accounting, kinds = (a && a.provision_kinds) || {}, err = h('div', { class: 'error', hidden: true });
    const sales = (state.data.sales || []).filter((s) => s.status === 'active' && s.balance > 0 && s.kind === 'credit');
    const f = { kind: h('select', {}, ...Object.entries(kinds).map(([k, v]) => h('option', { value: k, selected: pre ? k === 'receivable' : false }, `${v.label} (compte ${v.account})`))),
      sale: h('select', {}, ...sales.map((s) => h('option', { value: s.id, selected: pre && pre.sale_id === s.id }, `${s.number} · reste ${money(s.balance)}`))),
      label: h('input', { maxlength: 160, required: true, value: pre ? `Créance douteuse ${pre.number} · ${pre.client_name}` : '', placeholder: 'Ex. Client introuvable, litige fournisseur…' }), amount: h('input', { type: 'number', min: 1, step: 1, required: true, value: pre ? pre.suggested : '' }),
      date: h('input', { type: 'date', value: today(), max: today() }), note: h('input', { maxlength: 200 }) };
    const saleField = field('Facture concernée', f.sale), sync = () => { saleField.hidden = f.kind.value !== 'receivable'; }; f.kind.addEventListener('input', sync); sync();
    dlg.replaceChildren(h('h2', {}, 'Nouvelle provision'), h('p', { class: 'muted' }, 'Une provision constate une perte PROBABLE mais pas encore certaine : elle diminue le résultat dès maintenant. Le montant est votre jugement (à décider avec l\'expert-comptable) ; l\'application ne le fixe pas.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true;
        const data = { id: G.uuid(), kind: f.kind.value, label: f.label.value, amount: Number(f.amount.value), prov_date: f.date.value, note: f.note.value }; if (f.kind.value === 'receivable') data.sale_id = f.sale.value;
        const r = await G.submit({ type: 'provision.create', data }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Provision constituée.'); } },
        field('Nature', f.kind), saleField, field('Libellé', f.label), field('Montant', f.amount), field('Date', f.date), field('Note', f.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  function provisionDetail(p) {
    const err = h('div', { class: 'error', hidden: true }), w = can('accounting.write') && p.status === 'active';
    const reprise = () => {
      const amount = h('input', { type: 'number', min: 1, max: p.remaining, step: 1, value: p.remaining }), when = h('input', { type: 'date', value: today(), min: p.prov_date, max: today() }), reason = h('input', { maxlength: 200, placeholder: 'Ex. client payé, litige gagné…' });
      dlg.replaceChildren(h('h2', {}, `Reprise sur ${p.number}`), h('p', { class: 'muted' }, `Solde de la provision : ${money(p.remaining)}. Une reprise constate que le risque a diminué ou disparu : elle augmente le résultat.`), field('Montant repris', amount), field('Date', when), field('Motif', reason), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'button', onclick: async () => { if (!online()) return; err.hidden = true;
          const r = await G.submit({ type: 'provision.release', data: { id: p.id, base_version: p.version, amount: Number(amount.value), date: when.value, reason: reason.value } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Reprise enregistrée.'); } }, 'Valider la reprise')));
    };
    const ask = async (type, extra, msg) => { if (!online()) return; const reason = prompt(msg); if (!reason || reason.trim().length < 3) return; const r = await G.submit({ type, data: { id: p.id, base_version: p.version, reason: reason.trim(), ...extra } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); };
    dlg.replaceChildren(h('h2', {}, `${p.number} · ${p.label}`), h('div', { class: 'muted' }, `${p.kind_label} · compte ${p.account} · ${fmtDay(p.prov_date)}${p.sale_number ? ' · facture ' + p.sale_number + ' (' + p.client_name + ')' : ''}${p.status === 'cancelled' ? ' · ANNULÉE : ' + p.cancel_reason : ''}`),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Montant constitué'), h('strong', {}, money(p.amount))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Déjà repris'), h('strong', {}, money(p.released))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Solde'), h('strong', {}, money(p.remaining)))),
      p.releases.length ? tbl(['Date', 'Montant', 'Motif', ''], p.releases.map((r) => row([fmtDay(r.date), money(r.amount), r.reason, r.status === 'cancelled' ? 'annulée' : (w ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); ask('provision.unrelease', { release_id: r.id }, 'Motif de l\'annulation de cette reprise :'); } }, 'Annuler') : '')], r.status === 'cancelled' ? 'struck' : '')), ['', 'n', '', '']) : null, err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer'), w && !p.releases.some((r) => r.status === 'active') && h('button', { class: 'secondary', type: 'button', onclick: () => ask('provision.cancel', {}, 'Motif de l\'annulation (erreur de saisie) :') }, 'Annuler (erreur)'), w && p.remaining > 0 && h('button', { class: 'primary', type: 'button', onclick: reprise }, 'Reprendre (risque disparu)')));
    dlg.showModal();
  }

  function provisionsView() {
    const list = (state.data.provisions || []).slice().sort((a, b) => b.prov_date.localeCompare(a.prov_date) || b.seq - a.seq), sg = state.data.provision_suggestions || [], live = list.filter((p) => p.status === 'active');
    return h('div', {}, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Provisions et dépréciations'), can('accounting.write') && h('button', { class: 'primary', type: 'button', onclick: () => provisionForm() }, '＋ Nouvelle provision')),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Provisions en cours (solde)'), h('strong', {}, money(live.reduce((a, p) => a + p.remaining, 0)))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Créances à examiner'), h('strong', {}, String(sg.length)))),
      sg.length ? h('div', { class: 'card' }, h('h3', {}, 'Créances en retard à examiner'), h('p', { class: 'muted' }, 'Repère indicatif seulement : 50 % du reste dû dès 90 jours de retard, 100 % dès 180 jours. Décidez vous-même, avec votre expert-comptable.'),
        tbl(['Facture', 'Client', 'Échéance', 'Retard', 'Reste dû', 'Repère', ''], sg.map((x) => row([x.number, x.client_name, fmtDay(x.due_date), `${x.days_late} j`, money(x.balance), money(x.suggested), can('accounting.write') ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); provisionForm(x); } }, 'Provisionner') : ''])), ['', '', '', 'n', 'n', 'n', ''])) : null,
      list.length ? tbl(['N°', 'Nature', 'Libellé', 'Date', 'Constitué', 'Repris', 'Solde', 'État'], list.map((p) => row([h('a', { href: '#', onclick: (e) => { e.preventDefault(); provisionDetail(p); } }, p.number), p.kind_label, p.label, fmtDay(p.prov_date), money(p.amount), amt(p.released), money(p.status === 'active' ? p.remaining : 0), p.status === 'active' ? (p.remaining ? 'En cours' : 'Soldée') : 'Annulée'], p.status === 'cancelled' ? 'struck' : '')), ['', '', '', '', 'n', 'n', 'n', '']) : h('p', { class: 'muted' }, 'Aucune provision.'),
      h('p', { class: 'muted' }, 'Comptes : dotation 691 contre 491 (créances), 391 (stocks), 191 (litiges), 198 (autres risques) ; reprise 791. Les comptes sont indicatifs. La déductibilité fiscale des provisions obéit à des règles propres, non traitées ici : à voir avec votre expert-comptable.'));
  }

  // ---------- états financiers de synthèse ----------
  async function statesView(box) {
    const r = await fetchReport('financials'); if (r.error) { box.replaceChildren(h('div', { class: 'error' }, r.error)); return; }
    const bs = r.balance_sheet, inc = r.income, cf = r.cash_flow, co = (state.data.settings && state.data.settings.company_name) || '';
    const sect = (g, cols) => [row([h('strong', {}, g.group), ...cols.map(() => '')], 'sub'), ...g.rows.map((x) => row([`${x.account} ${x.name}`, ...cols.map((c) => (c === 'a' ? money(x.amount) : ''))]))];
    const actifRows = []; for (const g of bs.assets) { actifRows.push(row([h('strong', {}, g.group), h('strong', {}, money(g.gross)), h('strong', {}, g.dep ? money(-g.dep) : ''), h('strong', {}, money(g.net))])); }
    actifRows.push(row([h('strong', {}, 'TOTAL ACTIF'), h('strong', {}, money(bs.gross_total)), h('strong', {}, bs.dep_total ? money(-bs.dep_total) : ''), h('strong', {}, money(bs.assets_total))]));
    const passifRows = bs.liabilities.map((g) => row([h('strong', {}, g.group), h('strong', {}, money(g.amount))])); passifRows.push(row([h('strong', {}, 'TOTAL PASSIF'), h('strong', {}, money(bs.liabilities_total))]));
    const detail = (g) => g.rows.map((x) => row([h('span', { class: 'muted' }, `   ${x.account} ${x.name}`), '', '', h('span', { class: 'muted' }, money(x.amount))]));
    box.replaceChildren(
      h('div', { class: 'row' }, h('div', { class: r.all_ok ? 'ok-box grow' : 'error grow' }, r.all_ok ? '✔ Contrôles réussis : ' + r.checks.map((x) => x.label).join(' · ') : '⚠ Contrôle en échec : ' + r.checks.filter((x) => !x.ok).map((x) => x.label).join(' · ')), h('a', { class: 'btn', href: `/api/reports/pdf?report=financials&${qs()}`, target: '_blank', rel: 'noopener' }, 'Exporter en PDF'), h('button', { class: 'secondary', type: 'button', onclick: () => window.print() }, 'Imprimer')),
      h('div', { class: 'card' }, h('h2', {}, `États financiers de synthèse${co ? ' · ' + co : ''}`), h('div', { class: 'muted' }, `Période du ${fmtDay(r.from)} au ${fmtDay(r.to)} · bilan au ${fmtDay(bs.as_of)} · en GNF`)),
      h('div', { class: 'card' }, h('h2', {}, 'Bilan'), h('h3', {}, 'ACTIF'), tbl(['Rubrique', 'Brut', 'Amortissements', 'Net'], actifRows, ['', 'n', 'n', 'n']), h('h3', {}, 'PASSIF'), tbl(['Rubrique', 'Montant'], passifRows, ['', 'n']),
        h('p', { class: 'muted' }, 'Situation cumulée depuis l\'ouverture des livres. Un solde de caisse ou de banque au passif signale un découvert ou des soldes d\'ouverture manquants.')),
      h('div', { class: 'card' }, h('h2', {}, 'Compte de résultat par soldes intermédiaires'), tbl(['Rubrique', 'Montant'], inc.lines.map((x) => (x.kind === 'total' ? row([h('strong', {}, x.label), h('strong', {}, money(x.amount))], 'sub') : row([x.label, money(x.amount)]))), ['', 'n']),
        h('p', { class: 'muted' }, 'Produits en positif, charges en négatif (contribution au résultat).')),
      h('div', { class: 'card' }, h('h2', {}, 'Tableau des flux de trésorerie'), tbl(['Rubrique', 'Montant'], cf.lines.map((x) => (x.kind === 'total' ? row([h('strong', {}, x.label), h('strong', {}, money(x.amount))], 'sub') : row([x.label, money(x.amount)]))), ['', 'n']), h('p', { class: 'muted' }, cf.note)),
      h('div', { class: 'card' }, h('h2', {}, 'Informations générales'), ...r.notes.map((x) => h('div', { class: 'muted' }, '• ' + x))),
      h('p', { class: 'muted' }, WARN));
  }

  // ---------- immobilisations et amortissements ----------
  const PAYLAB = { owner: 'Payé par le patron (compte courant)', credit: 'À payer au fournisseur (à crédit)' };
  const STATUS = { active: 'En service', disposed: 'Sortie', cancelled: 'Annulée' };
  const online = () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return false; } return true; };

  function assetForm() {
    const a = state.data.accounting, cats = (a && a.asset_categories) || [], err = h('div', { class: 'error', hidden: true });
    const f = { name: h('input', { maxlength: 120, required: true, placeholder: 'Ex. Ordinateur portable, camion Isuzu…' }),
      category: h('select', {}, ...cats.map((c) => h('option', { value: c.key }, c.key))),
      origin: h('select', {}, h('option', { value: 'purchase' }, 'Je l\'achète maintenant (ou récemment)'), h('option', { value: 'opening' }, 'Je le possède déjà (reprise dans l\'application)')),
      date: h('input', { type: 'date', value: today(), max: today() }), start: h('input', { type: 'date', value: '', max: today() }), cost: h('input', { type: 'number', min: 1, step: 1, required: true }),
      residual: h('input', { type: 'number', min: 0, step: 1, value: 0 }), years: h('input', { type: 'number', min: 1, max: 50, step: 1, value: 3 }),
      scheme: h('select', {}, h('option', { value: 'linear' }, 'Linéaire (même dotation chaque année)'), h('option', { value: 'degressive' }, 'Dégressif (plus forte les premières années)')), coeff: h('input', { type: 'number', min: 1, max: 3, step: 0.05, value: 1.25 }),
      pay: h('select', {}, ...[...(state.data.methods || []), 'owner', 'credit'].map((m) => h('option', { value: m }, PAYLAB[m] || m))), note: h('input', { maxlength: 200 }) };
    const info = h('div', { class: 'muted' }), payField = field('Réglé par', f.pay);
    const coeffField = field('Coefficient dégressif (indicatif : 1,25 pour 3-4 ans, 1,75 pour 5-6 ans, 2,25 au-delà) — à faire valider', f.coeff), dateField = field('Date d\'achat', f.date), startField = field('Date de mise en service (laisser vide = date d\'achat)', f.start);
    const defCoeff = (y) => (y <= 4 ? 1.25 : y <= 6 ? 1.75 : 2.25);
    let lastYears = null, lastDeg = false;
    const sync = () => { const c = cats.find((x) => x.key === f.category.value) || {}, op = f.origin.value === 'opening'; if (c.years) f.years.value = c.years; f.years.disabled = !c.dep;
      payField.hidden = op; f.scheme.disabled = !c.dep || Number(f.years.value) < 3; if (f.scheme.disabled) f.scheme.value = 'linear';
      const deg = f.scheme.value === 'degressive'; coeffField.hidden = !deg; if (deg && (lastYears !== f.years.value || !lastDeg)) { f.coeff.value = defCoeff(Number(f.years.value)); } lastYears = f.years.value; lastDeg = deg;
      const base = Number(f.cost.value || 0) - Number(f.residual.value || 0), n = Number(f.years.value || 0) * 12;
      const yrs = Number(f.years.value || 0), co = Number(f.coeff.value || 0);
      if (c.dep && deg && base > 0 && yrs >= 3) { const first = Math.round(Math.max(base * co / yrs, base / yrs)); info.textContent = `Dégressif (coefficient ${co}) : environ ${money(first)} la première année (${money(Math.round(first / 12))} par mois), puis des dotations qui baissent ; la méthode bascule d'elle-même en linéaire quand c'est plus avantageux, et la dernière année solde le bien.`; return; }
      info.textContent = !c.dep ? 'Ce type de bien (terrain) ne s\'amortit pas.' : (base > 0 && n > 0 ? `Dotation d'environ ${money(Math.round(base / n))} par mois pendant ${f.years.value} an(s), à partir du mois de mise en service (linéaire).` : ''); };
    for (const el of [f.category, f.origin, f.cost, f.residual, f.years, f.scheme, f.coeff]) el.addEventListener('input', sync); sync();
    dlg.replaceChildren(h('h2', {}, 'Nouvelle immobilisation'), h('p', { class: 'muted' }, 'Un bien durable (matériel, véhicule, local…) n\'est pas une dépense du mois : il est inscrit à l\'actif puis amorti sur sa durée d\'utilisation. Les durées proposées sont indicatives : faites-les valider.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true;
        const op = f.origin.value === 'opening', data = { id: G.uuid(), name: f.name.value, category: f.category.value, origin: f.origin.value, acquisition_date: f.date.value, start_date: f.start.value || f.date.value, cost: Number(f.cost.value), residual: Number(f.residual.value || 0), life_years: Number(f.years.value), note: f.note.value };
        if (f.scheme.value === 'degressive') { data.scheme = 'degressive'; data.coeff = Number(f.coeff.value); }
        if (!op) data.pay_mode = f.pay.value;
        const r = await G.submit({ type: 'asset.create', data }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Immobilisation enregistrée.'); } },
        field('Désignation', f.name), field('Catégorie', f.category), field('Situation', f.origin), dateField, startField, field('Coût d\'acquisition (hors taxe récupérable)', f.cost), field('Valeur résiduelle estimée à la fin (0 si nulle)', f.residual), field('Durée d\'amortissement (années)', f.years), field('Méthode d\'amortissement', f.scheme), coeffField, payField, field('Note', f.note), info, err,
        h('p', { class: 'muted' }, 'Pour un bien déjà possédé : la date indiquée est celle de la reprise dans l\'application ; indiquez en « mise en service » la date d\'achat d\'origine, les amortissements déjà pratiqués sont calculés pour vous.'),
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  async function assetDetail(id) {
    let p; try { p = await api(`/api/accounting/report?kind=schedule&asset_id=${id}&${qs()}`); } catch (e) { G.toast(e.message); return; }
    const x = p.asset, can_w = can('accounting.write') && x.status === 'active', err = h('div', { class: 'error', hidden: true });
    const dispose = () => {
      const k = h('select', {}, h('option', { value: 'sold' }, 'Vendu (cession)'), h('option', { value: 'scrapped' }, 'Mis au rebut (sans prix)')), price = h('input', { type: 'number', min: 0, step: 1, value: 0 }), method = h('select', {}, ...(state.data.methods || []).map((m) => h('option', { value: m }, m))), when = h('input', { type: 'date', value: today(), max: today() });
      dlg.replaceChildren(h('h2', {}, `Sortir ${x.number}`), h('p', { class: 'muted' }, `Valeur nette comptable estimée à ce jour : ${money(x.net_value)}. La différence avec le prix de vente est constatée en plus-value ou moins-value.`), field('Nature', k), field('Date', when), field('Prix de vente', price), field('Encaissé par', method), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'button', onclick: async () => { if (!online()) return; err.hidden = true;
          const r = await G.submit({ type: 'asset.dispose', data: { id: x.id, base_version: x.version, kind: k.value, price: Number(price.value || 0), method: method.value, date: when.value } });
          if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Immobilisation sortie.'); } }, 'Valider la sortie')));
    };
    const cancel = async () => { if (!online()) return; const reason = prompt(`Motif de l'annulation de ${x.number} (erreur de saisie : l'achat et les amortissements disparaissent des comptes) :`); if (!reason || reason.trim().length < 3) return;
      const r = await G.submit({ type: 'asset.cancel', data: { id: x.id, base_version: x.version, reason: reason.trim() } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Immobilisation annulée.'); };
    dlg.replaceChildren(h('h2', {}, `${x.number} · ${x.name}`), h('div', { class: 'muted' }, `${x.category} · ${STATUS[x.status]}${x.origin === 'opening' ? ' · reprise' : ''}`),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Coût'), h('strong', {}, money(x.cost))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Amortissements cumulés'), h('strong', {}, money(x.accumulated))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Valeur nette'), h('strong', {}, money(x.status === 'disposed' ? 0 : x.net_value)))),
      h('p', { class: 'muted' }, `Acquis le ${fmtDay(x.acquisition_date)} · mise en service ${fmtDay(x.start_date)} · comptes ${x.asset_account}${x.dep_account ? ' / ' + x.dep_account : ''} · ${x.method === 'linear' ? `${x.scheme === 'degressive' ? `dégressif (coefficient ${x.dec_coeff})` : 'linéaire'} sur ${x.life_months / 12} an(s), ${x.status === 'active' ? money(x.monthly) + ' par mois' + (x.scheme === 'degressive' ? ' (change chaque année)' : '') : ''}${x.fully_depreciated_on ? ', complètement amorti le ' + fmtDay(x.fully_depreciated_on) : ''}` : 'non amorti'}`),
      x.status === 'disposed' && h('div', { class: 'card' }, `${x.disposal_kind === 'sold' ? 'Vendu' : 'Mis au rebut'} le ${fmtDay(x.disposal_date)} · prix ${money(x.disposal_price)} · ${x.gain_loss >= 0 ? 'plus-value' : 'moins-value'} de ${money(Math.abs(x.gain_loss))}`),
      x.status === 'cancelled' && h('div', { class: 'error' }, 'Annulée : ' + x.cancel_reason),
      p.years.length ? h('div', {}, h('h3', {}, 'Plan d\'amortissement'), tbl(['Année', 'Dotation', 'Cumul', 'Valeur nette'], p.years.map((y2) => row([y2.year, money(y2.charge), money(y2.accumulated), money(y2.net_value)])), ['', 'n', 'n', 'n'])) : null, err,
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer'), h('button', { class: 'secondary', type: 'button', onclick: () => G.attachDialog('asset', x.id, `${x.number} · ${x.name}`, can('accounting.write') && x.status !== 'cancelled', () => assetDetail(x.id)) }, '📎 Pièces'), can_w && h('button', { class: 'secondary', type: 'button', onclick: cancel }, 'Annuler (erreur)'), can_w && h('button', { class: 'primary', type: 'button', onclick: dispose }, 'Céder ou mettre au rebut')));
    dlg.showModal();
  }

  async function assetsView(box) {
    const r = await fetchReport('assets'); if (r.error) { box.replaceChildren(h('div', { class: 'error' }, r.error)); return; }
    const t = r.totals;
    box.replaceChildren(h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Immobilisations et amortissements'), can('accounting.write') && h('button', { class: 'primary', type: 'button', onclick: assetForm }, '＋ Nouvelle immobilisation')),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Coût des biens en service'), h('strong', {}, money(t.cost))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Amortissements cumulés'), h('strong', {}, money(t.accumulated))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Valeur nette'), h('strong', {}, money(t.net_value))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Dotation de la période'), h('strong', {}, money(t.period_charge)))),
      r.rows.length ? tbl(['N°', 'Désignation', 'Catégorie', 'Acquis le', 'Coût', 'Amort. cumulés', 'Valeur nette', 'Dotation période', 'État'], r.rows.map((x) => row([h('a', { href: '#', onclick: (e) => { e.preventDefault(); assetDetail(x.id); } }, x.number), x.name, x.category, fmtDay(x.acquisition_date), money(x.cost), money(x.accumulated), money(x.net_value), amt(x.period_charge), STATUS[x.status]], x.status === 'disposed' ? 'struck' : '')), ['', '', '', '', 'n', 'n', 'n', 'n', ''])
        : h('p', { class: 'muted' }, 'Aucune immobilisation. Un achat durable (ordinateur, véhicule, machine…) se saisit ici et non en dépense : il est amorti mois par mois.'),
      h('p', { class: 'muted' }, 'Méthodes : linéaire, ou dégressif au choix (à partir de 3 ans), au mois entier à partir du mois de mise en service, dotation comptabilisée en fin de mois. Les durées par catégorie et les coefficients dégressifs sont indicatifs. Pas de distinction entre amortissement comptable et fiscal. À valider avec votre expert-comptable.'));
  }

  // ---------- écran ----------
  async function render() {
    const modes = [['synthese', 'Synthèse'], ['journal', 'Journal'], ['balance', 'Balance'], ['grandlivre', 'Grand livre'], ['manuelles', 'Écritures'], ['immo', 'Immobilisations'], ['prov', 'Provisions'], ['avance', 'Charges d\'avance'], ['etats', 'États financiers'], ['cloture', 'Clôture'], ['reglages', 'Réglages']];
    const box = h('div', {}, h('p', { class: 'muted' }, 'Chargement…'));
    view.replaceChildren(h('h1', {}, 'Comptabilité'), h('div', { class: 'card' }, h('div', { class: 'chips' }, modes.map(([id, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.mode === id), onclick: () => { st.mode = id; render(); } }, l)))),
      ['synthese', 'journal', 'balance', 'grandlivre', 'immo', 'etats'].includes(st.mode) && periodBar(), box);
    if (st.mode === 'synthese') await synthese(box); else if (st.mode === 'journal') await journalView(box); else if (st.mode === 'balance') await balanceView(box);
    else if (st.mode === 'grandlivre') await ledgerView(box); else if (st.mode === 'immo') await assetsView(box); else if (st.mode === 'etats') await statesView(box); else if (st.mode === 'prov') box.replaceChildren(provisionsView()); else if (st.mode === 'avance') box.replaceChildren(deferralsView()); else if (st.mode === 'manuelles') box.replaceChildren(manualList()); else if (st.mode === 'cloture') box.replaceChildren(closureView()); else box.replaceChildren(settingsView());
  }
  tabs.push({ id: 'compta', label: 'Compta', show: () => can('accounting.read'), render });
})();
