/* Tableau de bord : les chiffres du jour, calculés sur les données synchronisées (fonctionne hors connexion). */
(() => {
  'use strict';
  const { h, money, fmtDay, today, can, state, view, tabs, daysBetween } = G;

  function render() {
    const vd = G.viewData(), t = today(), month = t.slice(0, 7);
    const act = vd.sales.filter((s) => s.status === 'active');
    const sum = (arr, f) => arr.reduce((a, x) => a + f(x), 0);
    const salesToday = sum(act.filter((s) => s.sale_date === t), (s) => s.total);
    const salesMonth = sum(act.filter((s) => s.sale_date.startsWith(month)), (s) => s.total);
    const activePays = vd.payments.filter((p) => p.status === 'active');
    const cashToday = sum(act.filter((s) => s.sale_date === t), (s) => s.paid_at_sale) + sum(activePays.filter((p) => p.pay_date === t), (p) => p.amount);
    const cashMonth = sum(act.filter((s) => s.sale_date.startsWith(month)), (s) => s.paid_at_sale) + sum(activePays.filter((p) => p.pay_date.startsWith(month)), (p) => p.amount);
    const due = sum(act.filter((s) => s.balance > 0), (s) => s.balance);
    const overdueSales = act.filter((s) => s.balance > 0 && s.due_date && s.due_date < t);
    const overdue = sum(overdueSales, (s) => s.balance);
    const lateDeliveries = act.filter((s) => s.delivery_due && !s.delivered_at && s.delivery_due < t);
    const acc = G.accounts(vd), debtors = [...acc.entries()].filter(([, a]) => a.overdue > 0).sort((a, b) => b[1].overdue - a[1].overdue).slice(0, 5);
    const low = can('stock.read') ? vd.products.filter((p) => p.track_stock && G.stockStatus(p, vd) && ['low', 'neg'].includes(G.stockStatus(p, vd).key)) : [];
    const debts = can('purchases.read') ? vd.purchases.filter((p) => p.status === 'active' && p.balance > 0) : [];
    const debtTotal = sum(debts, (p) => p.balance), debtLate = sum(debts.filter((p) => p.due_date && p.due_date < t), (p) => p.balance);
    const cashSt = can('cash.read') ? vd.cash : null, rt = vd.rentability;
    const monthExp = can('expenses.read_all') ? sum(vd.expenses.filter((e) => e.status === 'active' && e.expense_date.startsWith(month)), (e) => e.amount) : null;
    const kpi = (label, value, cls) => h('div', { class: 'card kpi ' + (cls || '') }, h('div', { class: 'muted' }, label), h('strong', {}, value));
    // ventes des 6 derniers mois (barres sans bibliothèque)
    const months = []; for (let i = 5; i >= 0; i--) { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i); months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`); }
    const vals = months.map((m) => sum(act.filter((s) => s.sale_date.startsWith(m)), (s) => s.total)), max = Math.max(1, ...vals);
    const bars = h('div', { class: 'bars', role: 'img', 'aria-label': 'Ventes des six derniers mois' }, months.map((m, i) => { const b = h('div', { class: 'bar' }); b.style.height = Math.max(2, Math.round(vals[i] / max * 100)) + '%'; b.title = money(vals[i]); return h('div', { class: 'barcol' }, b, h('span', { class: 'muted' }, m.slice(5) + '/' + m.slice(2, 4))); }));
    const insightsBox = h('div', {});
    if (can('reports.read') && state.online) G.api('/api/reports/insights').then((r) => {
      const f = r.forecast, ok = r.reliability.filter((x) => x.score !== null);
      insightsBox.replaceChildren(
        h('div', { class: 'card' }, h('h2', {}, 'Prévision de trésorerie (4 semaines)'), h('p', { class: 'muted' }, `Aujourd'hui : ${money(f.start.total)} (caisse ${money(f.start.cash)} + banques ${money(f.start.banks)})`),
          h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, ['Période', 'À encaisser', 'À payer', 'Position'].map((x, i) => h('th', { class: i ? 'num' : '' }, x))),
            ...f.buckets.map((b) => h('tr', {}, h('td', {}, b.label + (b.from ? ' · ' + fmtDay(b.from).slice(0, 5) : '')), h('td', { class: 'num' }, b.in ? money(b.in) : ''), h('td', { class: 'num' }, b.out ? money(b.out) : ''), h('td', { class: 'num' + (b.position < 0 ? ' neg' : '') }, money(b.position)))))),
          h('p', { class: 'muted' }, f.note)),
        h('div', { class: 'card' }, h('h2', {}, 'Fiabilité de paiement des clients'), h('p', { class: 'muted' }, 'Repère calculé sur les retards passés (note sur 100). « Risqué » : exiger un acompte ou réduire la limite de crédit. Les clients sans assez d\'historique ne sont pas notés.'),
          r.reliability.length ? h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, ['Client', 'Note', 'Avis', 'Retards', 'Retard moyen', 'En retard'].map((x, i) => h('th', { class: i > 2 ? 'num' : '' }, x))),
            ...r.reliability.slice(0, 15).map((x) => h('tr', {}, h('td', {}, x.name), h('td', {}, x.score === null ? '—' : String(x.score)), h('td', {}, x.label), h('td', { class: 'num' }, `${x.late}/${x.counted}`), h('td', { class: 'num' }, x.counted ? x.avg_delay_days + ' j' : ''), h('td', { class: 'num' + (x.overdue_amount ? ' neg' : '') }, x.overdue_amount ? money(x.overdue_amount) : ''))))) : h('p', { class: 'muted' }, 'Pas encore de vente à crédit.'),
          ok.length > 15 && h('p', { class: 'muted' }, `15 clients affichés sur ${r.reliability.length}.`)));
    }).catch(() => { insightsBox.replaceChildren(); });
    const todo = [];
    if (overdue > 0) todo.push(`Relancer ${debtors.length ? debtors.length + ' client(s)' : 'les clients'} en retard de paiement (${money(overdue)}).`);
    if (cashSt && cashSt.needs_opening && (cashSt.in_total + cashSt.out_total) > 0) todo.push('Faire la première clôture de caisse (compter les espèces et fixer le solde d\'ouverture).');
    else if (cashSt && !cashSt.needs_opening && cashSt.last_close_date < t && (cashSt.in_total + cashSt.out_total) > 0) todo.push(`Clôturer la caisse (dernière clôture le ${fmtDay(cashSt.last_close_date)}).`);
    const drifted = can('cash.read') ? vd.closings.filter((x) => x.status === 'active' && x.drift).length : 0;
    if (drifted) todo.push(`⚠ ${drifted} clôture(s) de caisse modifiée(s) après coup : voir Caisse, Historique.`);
    const toMatch = can('bank.read') ? Object.values(vd.bankStatus).reduce((a, s) => a + (s.open_lines || 0), 0) : 0;
    if (toMatch) todo.push(`${toMatch} ligne(s) de relevé bancaire ou mobile money à pointer (onglet Banque).`);
    if (low.length) todo.push(`Réapprovisionner ${low.length} produit(s) : ${low.slice(0, 4).map((p) => p.name).join(', ')}${low.length > 4 ? '…' : ''}.`);
    if (debtLate > 0) todo.push(`Régler les fournisseurs en retard (${money(debtLate)}).`);
    if (lateDeliveries.length) todo.push(`${lateDeliveries.length} livraison(s) en retard.`);
    const expiring = (state.data.quotes || []).filter((q) => q.status === 'open' && !q.expired && q.valid_until <= G.addDays(t, 7));
    if (expiring.length) todo.push(`${expiring.length} devis arrive(nt) à expiration cette semaine : relancer les clients (${expiring.slice(0, 3).map((q) => q.number).join(', ')}${expiring.length > 3 ? '…' : ''}).`);
    const poLate = can('purchases.read') ? (state.data.porders || []).filter((o) => o.late) : [];
    if (poLate.length) todo.push(`${poLate.length} commande(s) fournisseur en retard de livraison (${poLate.slice(0, 3).map((o) => o.number).join(', ')}${poLate.length > 3 ? '…' : ''}).`);
    const recDue = (state.data.recurring || []).reduce((a, r) => a + r.pending.length, 0);
    if (recDue) todo.push(`${recDue} dépense(s) récurrente(s) à générer (Caisse, Récurrentes).`);
    if (G.queue().length) todo.push(`${G.queue().length} opération(s) en attente d'envoi : reconnectez-vous pour les synchroniser.`);
    view.replaceChildren(h('h1', {}, `Bonjour ${state.me.full_name.split(' ')[0]} 👋`), h('p', { class: 'muted' }, 'Données du ' + fmtDay(t) + (state.online ? '' : ' (hors connexion : dernière synchronisation)')),
      h('div', { class: 'kpis' }, kpi('Ventes du jour', money(salesToday)), kpi('Encaissé aujourd\'hui', money(cashToday)), kpi('Ventes du mois', money(salesMonth)), kpi('Encaissé ce mois-ci', money(cashMonth)),
        kpi('Créances (reste à encaisser)', money(due)), kpi('Dont en retard', money(overdue), overdue ? 'bad' : ''), kpi('Livraisons en retard', String(lateDeliveries.length), lateDeliveries.length ? 'bad' : ''),
        can('stock.read') && kpi('Produits à commander', String(low.length), low.length ? 'bad' : ''), can('purchases.read') && kpi('Dettes fournisseurs', money(debtTotal)), can('purchases.read') && kpi('Dettes en retard', money(debtLate), debtLate ? 'bad' : ''),
        cashSt && kpi('Espèces attendues en caisse', money(cashSt.expected)), monthExp !== null && kpi('Dépenses du mois', money(monthExp)), rt && rt.breakeven && kpi('Seuil de rentabilité atteint', (rt.reached > 9.99 ? 'plus de 999 %' : Math.round(rt.reached * 100) + ' %'), rt.reached < 1 ? 'bad' : '')),
      todo.length ? h('div', { class: 'card' }, h('h2', {}, 'À faire'), h('ul', {}, todo.map((x) => h('li', {}, x)))) : h('div', { class: 'ok-box' }, 'Rien d\'urgent 👍'),
      debtors.length > 0 && h('div', { class: 'card' }, h('h2', {}, 'Clients en retard'), debtors.map(([id, a]) => { const c = vd.clients.find((x) => x.id === id) || { name: '—' }, wa = c.phone ? G.waLink(c, a.overdue, true) : null;
        return h('div', { class: 'row' }, h('span', { class: 'grow' }, `${c.name} : ${money(a.overdue)} (${a.oldest} j) · Solde total : ${money(a.balance)}`), wa && h('a', { href: wa, target: '_blank', rel: 'noopener noreferrer' }, '💬 Relancer')); })),
      h('div', { class: 'card' }, h('h2', {}, 'Ventes des 6 derniers mois'), bars), insightsBox,
      can('sales.create') && h('p', {}, h('button', { class: 'primary', type: 'button', onclick: () => G.saleForm({}) }, '＋ Nouvelle vente')));
  }
  tabs.push({ id: 'tableau', label: 'Tableau', show: () => can('sales.read_all') || can('sales.read_own'), render });
})();
