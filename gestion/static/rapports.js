/* Écran Rapports : marges par produit et par client (ceux qui voient les coûts), ventes par vendeur et par mois (équipe : propriétaire, comptable, auditeur ; un commercial voit les siennes). Demande une connexion. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, view, tabs, api, OfflineError, dlg } = G;
  const y = today().slice(0, 4), st = { mode: null, from: `${y}-01-01`, to: today() };
  const pct = (v) => (v === null || v === undefined ? '—' : v + ' %');
  const qs = () => new URLSearchParams({ from: st.from, to: st.to }).toString();
  const tbl = (heads, rows, cls) => h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, heads.map((x, i) => h('th', { class: cls[i] === 'n' ? 'num' : '' }, x))), ...rows.map((r) => h('tr', { class: r._cls || '' }, r.cells.map((x, i) => h('td', { class: cls[i] === 'n' ? 'num' : '' }, x))))));
  const kpi = (l, v, c) => h('div', { class: 'card kpi ' + (c || '') }, h('div', { class: 'muted' }, l), h('strong', {}, v));
  const REPORT = { product: 'margins_product', client: 'margins_client', seller: 'sales_seller', month: 'sales_month', category: 'sales_category' };
  const pdfBtn = () => h('a', { class: 'btn', href: `/api/reports/pdf?report=${REPORT[st.mode]}&${qs()}`, target: '_blank', rel: 'noopener' }, 'Exporter en PDF');
  const monthLabel = (m) => m.slice(5) + '/' + m.slice(2, 4);

  function modes() {
    const out = [];
    if (can('costs.read')) out.push(['product', 'Marge par produit'], ['client', 'Marge par client']);
    if (can('reports.read') || can('sales.read_own')) out.push(['seller', can('reports.read') ? 'Ventes par vendeur' : 'Mes ventes'], ['month', 'Ventes par mois'], ['category', 'Ventes par catégorie']);
    return out;
  }

  function marginsView(r) {
    const T = r.totals;
    const rows = st.mode === 'product'
      ? r.by_product.map((g) => ({ cells: [g.name, g.qty, money(g.revenue), g.avg_cost === null ? '—' : money(g.avg_cost), g.margin === null ? '—' : money(g.margin), pct(g.margin_pct), pct(g.share_of_margin)], _cls: g.margin !== null && g.margin < 0 ? 'bad' : '' }))
      : r.by_client.map((g) => ({ cells: [`${g.name}${g.code ? ' (' + g.code + ')' : ''}`, g.sales, money(g.revenue), g.margin === null ? '—' : money(g.margin), pct(g.margin_pct), g.balance ? money(g.balance) : '', g.reliability || ''], _cls: g.margin !== null && g.margin < 0 ? 'bad' : '' }));
    return h('div', {}, h('div', { class: 'kpis' }, kpi('Chiffre d\'affaires hors taxe', money(T.revenue)), kpi('Marge estimée', money(T.margin), T.margin < 0 ? 'bad' : ''), kpi('Taux de marge', pct(T.margin_pct)), kpi('CA sans coût connu', money(T.revenue_unknown), T.revenue_unknown ? 'bad' : '')),
      h('div', { class: 'row' }, h('h2', { class: 'grow' }, st.mode === 'product' ? 'Marge par produit' : 'Marge par client'), pdfBtn(), h('a', { class: 'btn', href: `/api/reports/margins.csv?kind=${st.mode}&${qs()}`, download: '' }, 'Exporter en CSV')),
      rows.length ? (st.mode === 'product' ? tbl(['Produit', 'Qté vendue', 'CA hors taxe', 'Coût moyen unit.', 'Marge', 'Marge %', 'Part de la marge'], rows, ['', 'n', 'n', 'n', 'n', 'n', 'n']) : tbl(['Client', 'Ventes', 'CA hors taxe', 'Marge', 'Marge %', 'Reste dû', 'Fiabilité de paiement'], rows, ['', 'n', 'n', 'n', 'n', 'n', ''])) : h('p', { class: 'muted' }, 'Aucune vente sur cette période.'),
      r.credit_notes_total > 0 && h('p', { class: 'muted' }, `Avoirs de la période : ${money(r.credit_notes_total)} (non déduits des marges ci-dessus).`), h('p', { class: 'muted' }, r.note),
      h('p', { class: 'muted' }, 'Un client très rentable qui paie mal l\'est moins qu\'il n\'y paraît : regardez le reste dû et la fiabilité de paiement.'));
  }

  async function targetsDialog() {
    let list; try { list = (await api('/api/reports/targets')).sellers; } catch (e) { G.toast(e.message); return; }
    const err = h('div', { class: 'error', hidden: true }), dlg = G.dlg;
    const inputs = list.map((s) => ({ s, input: h('input', { type: 'number', min: 0, step: 1000, value: s.target || '', placeholder: 'aucun objectif', 'aria-label': 'Objectif mensuel de ' + s.name }) }));
    dlg.replaceChildren(h('h2', {}, 'Objectifs de ventes par vendeur'), h('p', { class: 'muted' }, 'Objectif MENSUEL en montant TTC (GNF) pour chaque commercial. Laissez vide pour ne pas fixer d\'objectif. Chaque changement est noté dans le journal.'),
      list.length ? h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/reports/targets', { method: 'POST', body: { targets: Object.fromEntries(inputs.map((x) => [String(x.s.id), x.input.value === '' ? 0 : Number(x.input.value)])) } }); dlg.close(); G.toast('Objectifs enregistrés.'); render(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
        ...inputs.map((x) => field(x.s.name, x.input)), err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer')))
        : h('div', {}, h('p', { class: 'muted' }, 'Aucun commercial actif : créez d\'abord un compte « Commercial » dans Équipe.'), h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    dlg.showModal();
  }

  function salesView(r) {
    const T = r.totals, csv = (k) => h('span', {}, pdfBtn(), ' ', h('a', { class: 'btn', href: `/api/reports/sales.csv?kind=${k}&${qs()}`, download: '' }, 'Exporter en CSV'));
    const head = h('div', { class: 'kpis' }, kpi('Ventes (TTC)', money(T.revenue)), kpi('Nombre de ventes', String(T.sales)), kpi('Encaissé à ce jour', money(T.collected)), kpi('Reste dû', money(T.balance), T.balance ? 'bad' : ''));
    if (st.mode === 'category') {
      const rows = r.by_category.map((g) => ({ cells: [g.category, g.qty, g.sales, money(g.revenue), money(g.revenue_ht), pct(g.share)] }));
      return h('div', {}, head, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Ventes par catégorie de produit'), csv('category')),
        rows.length ? tbl(['Catégorie de produit', 'Quantités', 'Ventes', 'Montant TTC', 'Hors taxe', 'Part'], rows, ['', 'n', 'n', 'n', 'n', 'n']) : h('p', { class: 'muted' }, 'Aucune vente sur cette période.'),
        h('p', { class: 'muted' }, 'Les ventes d\'articles libres (sans produit du catalogue) sont regroupées à part ; un produit sans catégorie va dans « Sans catégorie ». ' + r.note));
    }
    if (st.mode === 'seller') {
      const obj = r.has_targets;
      const rows = r.by_seller.map((g) => ({ cells: [g.name, g.sales, money(g.revenue), pct(g.share), money(g.avg_basket), pct(g.credit_share), g.discount ? `${money(g.discount)} (${g.discount_pct} %)` : '', g.balance ? money(g.balance) : '', g.cancelled ? `${g.cancelled} (${money(g.cancelled_amount)})` : ''].concat(obj ? [g.target_period ? money(g.target_period) : '', g.target_period ? pct(g.progress_pct) : ''] : []),
        _cls: g.target_period && g.progress_pct < 50 ? 'bad' : '' }));
      return h('div', {}, head, h('div', { class: 'row' }, h('h2', { class: 'grow' }, r.scope === 'own' ? 'Mes ventes' : 'Ventes par vendeur'), can('budgets.set') && h('button', { class: 'secondary', type: 'button', onclick: targetsDialog }, 'Objectifs par vendeur'), csv('seller')),
        rows.length ? tbl(['Vendeur', 'Ventes', 'Montant TTC', 'Part', 'Panier moyen', 'À crédit', 'Remises accordées', 'Reste dû', 'Annulées'].concat(obj ? ['Objectif de la période', 'Atteint'] : []), rows, ['', 'n', 'n', 'n', 'n', 'n', 'n', 'n', 'n'].concat(obj ? ['n', 'n'] : [])) : h('p', { class: 'muted' }, 'Aucune vente sur cette période.'),
        obj && h('p', { class: 'muted' }, 'L\'objectif de la période = l\'objectif mensuel × le nombre de mois de la période (un mois commencé compte pour un mois entier). En rouge : moins de la moitié de l\'objectif atteint.'),
        h('p', { class: 'muted' }, r.note));
    }
    const max = Math.max(1, ...r.by_month.map((m) => m.revenue));
    const bars = h('div', { class: 'bars', role: 'img', 'aria-label': 'Ventes par mois' }, r.by_month.slice(-18).map((m) => { const b = h('div', { class: 'bar' }); b.style.height = Math.max(2, Math.round(m.revenue / max * 100)) + '%'; b.title = money(m.revenue); return h('div', { class: 'barcol' }, b, h('span', { class: 'muted' }, monthLabel(m.month))); }));
    const rows = r.by_month.map((m) => ({ cells: [m.month, m.sales, money(m.revenue), money(m.revenue_ht), money(m.avg_basket), m.growth_pct === null ? '—' : (m.growth_pct > 0 ? '+' : '') + m.growth_pct + ' %', money(m.collected)], _cls: m.growth_pct !== null && m.growth_pct < 0 ? 'bad' : '' }));
    return h('div', {}, head, h('div', { class: 'card' }, h('h2', {}, 'Ventes par mois'), bars), h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Détail'), csv('month')),
      tbl(['Mois', 'Ventes', 'Montant TTC', 'Hors taxe', 'Panier moyen', 'Évolution', 'Encaissé dans le mois'], rows, ['', 'n', 'n', 'n', 'n', 'n', 'n']), h('p', { class: 'muted' }, r.note));
  }

  async function render() {
    const ms = modes(); if (!ms.find((m) => m[0] === st.mode)) st.mode = ms[0][0];
    const box = h('div', {}, h('p', { class: 'muted' }, 'Chargement…')), t = today(), py = Number(y) - 1, set = (a, b) => { st.from = a; st.to = b; render(); };
    view.replaceChildren(h('h1', {}, 'Rapports'),
      h('div', { class: 'card' }, h('div', { class: 'filters' }, field('Du', h('input', { type: 'date', value: st.from, onchange: (e) => { st.from = e.target.value || st.from; render(); } })), field('Au', h('input', { type: 'date', value: st.to, max: t, onchange: (e) => { st.to = e.target.value || st.to; render(); } }))),
        h('div', { class: 'chips' }, h('button', { class: 'chip', type: 'button', onclick: () => set(t.slice(0, 8) + '01', t) }, 'Ce mois'), h('button', { class: 'chip', type: 'button', onclick: () => set(`${y}-01-01`, t) }, 'Cette année'), h('button', { class: 'chip', type: 'button', onclick: () => set(`${py}-01-01`, `${py}-12-31`) }, 'Année ' + py), h('button', { class: 'chip', type: 'button', onclick: () => set('2000-01-01', t) }, 'Tout'))),
      h('div', { class: 'chips' }, ms.map(([id, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.mode === id), onclick: () => { st.mode = id; render(); } }, l))), box,
      h('p', { class: 'muted' }, `Période du ${fmtDay(st.from)} au ${fmtDay(st.to)}.`));
    if (!state.online) { box.replaceChildren(h('div', { class: 'error' }, 'Les rapports demandent une connexion.')); return; }
    const margin = st.mode === 'product' || st.mode === 'client';
    let r; try { r = await api((margin ? '/api/reports/margins?' : '/api/reports/sales?') + qs()); } catch (e) { box.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'Les rapports demandent une connexion.' : e.message)); return; }
    box.replaceChildren(margin ? marginsView(r) : salesView(r));
  }
  tabs.push({ id: 'rapports', label: 'Rapports', show: () => can('costs.read') || can('reports.read') || can('sales.read_own'), render });
})();
