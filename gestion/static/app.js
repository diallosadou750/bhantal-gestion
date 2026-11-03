/* Gestion libre : noyau de l'interface (données, synchronisation hors connexion, onglets, connexion).
   Les écrans métier sont dans les autres fichiers .js. Aucun framework ; aucune donnée n'est insérée avec innerHTML. Licence AGPL-3.0. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const view = $('#view'), tabsEl = $('#tabs'), topEl = $('#top'), dlg = $('#dlg');
  // Les écrans écrivent « condition && élément » : on ignore false/null au lieu de les afficher comme du texte.
  const _replaceChildren = Element.prototype.replaceChildren;
  Element.prototype.replaceChildren = function (...nodes) { return _replaceChildren.apply(this, nodes.flat(Infinity).filter((n) => n !== false && n !== null && n !== undefined)); };

  // ---------- utilitaires ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* stockage plein ou bloqué */ } },
  };
  // Cache des données : IndexedDB (pas de limite de 5 Mo), avec repli sur localStorage
  const idb = {
    db: null,
    open() {
      if (this.db) return this.db;
      this.db = new Promise((res) => {
        try { const r = indexedDB.open('gestion', 1); r.onupgradeneeded = () => r.result.createObjectStore('kv'); r.onsuccess = () => res(r.result); r.onerror = () => res(null); } catch { res(null); }
      });
      return this.db;
    },
    async get(k) {
      const db = await this.open(); if (!db) return store.get('idb_' + k);
      return new Promise((res) => { const q = db.transaction('kv').objectStore('kv').get(k); q.onsuccess = () => res(q.result); q.onerror = () => res(undefined); });
    },
    async set(k, v) {
      const db = await this.open(); if (!db) return store.set('idb_' + k, v);
      return new Promise((res) => { const t = db.transaction('kv', 'readwrite'); t.objectStore('kv').put(v, k); t.oncomplete = () => res(); t.onerror = () => res(); });
    },
  };
  const deviceId = store.get('gestion_device') || (() => { const d = uuid(); store.set('gestion_device', d); return d; })();
  const pad = (n) => String(n).padStart(2, '0');
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const addDays = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const daysBetween = (a, b) => Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 86400000);
  const fmtDate = (iso) => { try { return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'medium' }); } catch { return iso; } };
  const fmtDay = (iso) => (iso ? iso.split('-').reverse().join('/') : '');
  const money = (n) => new Intl.NumberFormat('fr-FR').format(Math.round(n || 0)) + ' GNF';
  let fieldSeq = 0;
  const field = (label, input) => {                                  // le libellé est relié à son champ : lecteurs d'écran, clic sur le libellé, tests automatiques
    const l = h('label', {}, label);
    if (input && /^(INPUT|SELECT|TEXTAREA)$/.test(input.nodeName)) { input.id = input.id || 'champ' + (++fieldSeq); l.htmlFor = input.id; }
    return h('div', {}, l, input);
  };

  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 4500);
  }

  class OfflineError extends Error {}
  async function api(path, { method = 'GET', body } = {}) {
    let res;
    try {
      res = await fetch(path, { method, credentials: 'same-origin', headers: { 'X-Requested-With': 'gestion', 'X-Device-Id': deviceId, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    } catch { throw new OfflineError('Hors connexion'); }
    let data = null;
    try { data = await res.json(); } catch { /* corps vide */ }
    if (!res.ok) {
      if (res.status === 401 && path !== '/api/login') { state.me = null; render(); }
      const err = new Error((data && (data.error || (data.detail && JSON.stringify(data.detail)))) || 'Erreur ' + res.status);
      err.status = res.status; err.code = data && data.code; throw err;
    }
    return data;
  }

  // ---------- état, cache hors connexion, file d'attente ----------
  const COLL = ['clients', 'products', 'sales', 'payments', 'credit_notes', 'suppliers', 'purchases', 'supplier_payments', 'adjustments', 'expenses', 'cash_movements', 'cash_closings', 'bank_lines', 'bank_reconciliations', 'journal_entries'];
  const emptyData = () => ({ clients: [], products: [], sales: [], payments: [], credit_notes: [], suppliers: [], purchases: [], supplier_payments: [], adjustments: [], expenses: [], cash_movements: [], cash_closings: [], bank_lines: [], bank_reconciliations: [], journal_entries: [], assets: [], provisions: [], provision_suggestions: [], deferrals: [], quotes: [], porders: [], delivery_notes: [], pay_accounts: [], transfers: [], recurring: [], supplier_notes: [], depots: [], stock_depots: {}, lots: [], stock_transfers: [], accounting: null, bank_accounts: {}, bank_items: {}, bank_status: {}, bank_meta: {}, stock: {}, cash: null, budgets: {}, categories: [], rentability: null, settings: {}, methods: [] });
  const state = { demo: false, me: null, perms: [], tab: 'tableau', data: emptyData(), cursor: 0, epoch: null, dbId: null, online: navigator.onLine, syncing: false, setup: false, rolesMap: {} };
  const key = (n) => `gestion_${n}_${state.me ? state.me.id : 0}`;
  const queue = () => store.get(key('queue'), []);
  const saveQueue = (q) => { store.set(key('queue'), q); updateBadges(); };
  const rejected = () => store.get(key('rejected'), []);
  const saveRejected = (r) => { store.set(key('rejected'), r); updateBadges(); };
  const can = (p) => state.perms.includes('*') || state.perms.includes(p);
  const tabs = [];

  function updateBadges() {
    $('#pending').textContent = String(state.me ? queue().length : 0);
    const net = $('#net'); net.textContent = state.online ? 'En ligne' : 'Hors connexion'; net.className = 'pill ' + (state.online ? 'ok' : 'off');
    const rj = state.me ? rejected().length : 0, b = $('#rejected'); b.hidden = !rj; b.textContent = `⚠ ${rj} refusée${rj > 1 ? 's' : ''}`;
  }

  const persist = () => idb.set(key('cache'), { data: state.data, cursor: state.cursor, epoch: state.epoch, dbId: state.dbId });

  function applyPull(r) {
    for (const c of COLL) {
      if (r.full) state.data[c] = r[c];
      else { const m = new Map(state.data[c].map((x) => [x.id, x])); for (const x of r[c]) m.set(x.id, x); state.data[c] = [...m.values()]; }
    }
    state.data.assets = r.assets || []; state.data.provisions = r.provisions || []; state.data.deferrals = r.deferrals || []; state.data.quotes = r.quotes || []; state.data.porders = r.porders || []; state.data.delivery_notes = r.delivery_notes || []; state.data.pay_accounts = r.pay_accounts || []; state.data.transfers = r.transfers || []; state.data.recurring = r.recurring || []; state.data.supplier_notes = r.supplier_notes || []; state.data.depots = r.depots || []; state.data.stock_depots = r.stock_depots || {}; state.data.lots = r.lots || []; state.data.stock_transfers = r.stock_transfers || []; state.data.provision_suggestions = r.provision_suggestions || []; state.data.accounting = r.accounting || null; state.data.bank_accounts = r.bank_accounts || {}; state.data.bank_items = r.bank_items || {}; state.data.bank_status = r.bank_status || {}; state.data.bank_meta = r.bank_meta || {};
    state.data.stock = r.stock || {}; state.data.cash = r.cash || null; state.data.budgets = r.budgets || {}; state.data.categories = r.expense_categories || []; state.data.rentability = r.rentability || null; state.data.settings = r.settings; state.data.methods = r.methods; state.data.salespeople = r.salespeople || []; state.cursor = r.cursor; state.epoch = r.epoch; state.dbId = r.db_id;
    persist();
  }
  async function pull() { applyPull(await api(`/api/sync/pull?since=${state.cursor}${state.epoch !== null ? '&epoch=' + state.epoch : ''}${state.dbId ? '&db=' + state.dbId : ''}`)); }

  // Affichage = données du serveur + opérations en attente appliquées localement (« optimiste »)
  function viewData() {
    const d = state.data, pend = new Set();
    const clients = new Map(d.clients.map((c) => [c.id, { ...c }])), products = new Map(d.products.map((p) => [p.id, { ...p }]));
    const sales = new Map(d.sales.map((s) => [s.id, { ...s }])), payments = new Map(d.payments.map((p) => [p.id, { ...p }]));
    const suppliers = new Map((d.suppliers || []).map((s) => [s.id, { ...s }])), purchases = new Map((d.purchases || []).map((p) => [p.id, { ...p }])), spays = new Map((d.supplier_payments || []).map((p) => [p.id, { ...p }]));
    const expenses = new Map((d.expenses || []).map((e) => [e.id, { ...e }]));
    const stock = JSON.parse(JSON.stringify(d.stock || {})); const bump = (pid, n) => { if (pid && stock[pid]) stock[pid].qty += n; };
    for (const o of queue()) {
      const x = o.data || {};
      switch (o.type) {
        case 'client.create': clients.set(x.id, { id: x.id, code: '…', version: 0, deleted: false, name: '', kind: '', phone: '', zone: '', credit_limit: 0, note: '', owner_user_id: state.me.id, ...x }); pend.add(x.id); break;
        case 'client.update': if (clients.has(x.id)) { Object.assign(clients.get(x.id), x.changes); pend.add(x.id); } break;
        case 'client.delete': clients.delete(x.id); break;
        case 'product.create': products.set(x.id, { id: x.id, code: '…', version: 0, deleted: false, name: '', category: '', unit: '', price: 0, ...x }); pend.add(x.id); break;
        case 'product.update': if (products.has(x.id)) { Object.assign(products.get(x.id), x.changes); pend.add(x.id); } break;
        case 'product.delete': products.delete(x.id); break;
        case 'sale.create': {
          const lines = (x.lines || []).map((l) => { const p = l.product_id && products.get(l.product_id); const price = p && !can('sales.override_price') ? p.price : Number(l.unit_price ?? (p ? p.price : 0)); const disc = Number(l.discount_pct || 0);
            return { product_id: l.product_id || null, name: p ? p.name : l.name, qty: Number(l.qty), unit_price: price, discount_pct: disc, amount: Math.floor((Number(l.qty) * price * (100 - disc) + 50) / 100) }; });
          const total = lines.reduce((a, l) => a + l.amount, 0), paid = x.kind === 'cash' ? total : Number(x.paid_at_sale || 0);
          (x.lines || []).forEach((l) => bump(l.product_id, -Number(l.qty)));
          sales.set(x.id, { id: x.id, number: '…', sale_date: x.sale_date || today(), client_id: x.client_id, kind: x.kind, due_date: x.kind === 'credit' ? x.due_date : null, total, paid_at_sale: paid, pay_method: x.pay_method || '', note: x.note || '', status: 'active', version: 0,
            lines, payments_total: 0, credit_notes_total: 0, refunds_total: 0, net_total: total, paid_total: paid, balance: total - paid, seller_name: state.me.full_name, delivery_due: x.delivery_due || null, delivered_at: null }); pend.add(x.id); break;
        }
        case 'payment.create': { const s = sales.get(x.sale_id); if (s) { payments.set(x.id, { id: x.id, sale_id: x.sale_id, client_id: s.client_id, pay_date: x.pay_date || today(), amount: Number(x.amount), method: x.method, reference: x.reference || '', status: 'active', version: 0 });
          s.payments_total += Number(x.amount); s.paid_total += Number(x.amount); s.balance -= Number(x.amount); pend.add(x.sale_id); pend.add(x.id); } break; }
        case 'supplier.create': suppliers.set(x.id, { id: x.id, code: '…', version: 0, deleted: false, name: '', phone: '', zone: '', note: '', ...x }); pend.add(x.id); break;
        case 'supplier.update': if (suppliers.has(x.id)) { Object.assign(suppliers.get(x.id), x.changes); pend.add(x.id); } break;
        case 'purchase.create': {
          const lines = (x.lines || []).map((l) => { const p = products.get(l.product_id); return { product_id: l.product_id, name: p ? p.name : '', qty: Number(l.qty), unit_cost: Number(l.unit_cost), amount: Number(l.qty) * Number(l.unit_cost), prev_cost: null }; });
          const total = lines.reduce((a, l) => a + l.amount, 0), paid = x.kind === 'cash' ? total : Number(x.paid_at_purchase || 0);
          purchases.set(x.id, { id: x.id, number: '…', supplier_id: x.supplier_id, purchase_date: x.purchase_date || today(), kind: x.kind, due_date: x.kind === 'credit' ? x.due_date : null, supplier_ref: x.supplier_ref || '', total, paid_at_purchase: paid, pay_method: x.pay_method || '',
            status: 'active', version: 0, lines, payments_total: 0, paid_total: paid, balance: total - paid }); pend.add(x.id); lines.forEach((l) => bump(l.product_id, l.qty)); break;
        }
        case 'supplierpay.create': { const p = purchases.get(x.purchase_id); if (p) { spays.set(x.id, { id: x.id, purchase_id: x.purchase_id, supplier_id: p.supplier_id, pay_date: x.pay_date || today(), amount: Number(x.amount), method: x.method, reference: x.reference || '', status: 'active', version: 0 });
          p.payments_total += Number(x.amount); p.paid_total += Number(x.amount); p.balance -= Number(x.amount); pend.add(x.purchase_id); pend.add(x.id); } break; }
        case 'expense.create': expenses.set(x.id, { id: x.id, number: '…', expense_date: x.expense_date || today(), category: x.category, payee: x.payee || '', description: x.description || '', amount: Number(x.amount), method: x.method, reference: x.reference || '', status: 'active', version: 0,
          nature: ((d.categories || []).find((c) => c.key === x.category) || {}).nature || 'Variable' }); pend.add(x.id); break;
        case 'stock.adjust': bump(x.product_id, Number(x.qty)); pend.add(x.product_id); break;
        case 'stock.inventory': (x.counts || []).forEach((cn) => { if (stock[cn.product_id]) stock[cn.product_id].qty = Number(cn.counted); pend.add(cn.product_id); }); break;
        case 'sale.deliver': { const s = sales.get(x.id); if (s) { s.delivered_at = x.delivered_at || today(); pend.add(x.id); } break; }
        default: if (x.id) pend.add(x.id); if (x.sale_id) pend.add(x.sale_id);
      }
    }
    const by = (a, b) => (b.sale_date || b.pay_date || '').localeCompare(a.sale_date || a.pay_date || '') || String(b.number || '').localeCompare(String(a.number || ''));
    return { clients: [...clients.values()].filter((c) => !c.deleted), products: [...products.values()].filter((p) => !p.deleted), sales: [...sales.values()].sort(by), payments: [...payments.values()], notes: d.credit_notes, settings: d.settings, methods: d.methods,
      suppliers: [...suppliers.values()].filter((s) => !s.deleted), purchases: [...purchases.values()].sort((a, b) => b.purchase_date.localeCompare(a.purchase_date)), spays: [...spays.values()], adjustments: d.adjustments || [], jentries: d.journal_entries || [], accounting: d.accounting, bankLines: d.bank_lines || [], bankRecs: d.bank_reconciliations || [], bankAccounts: d.bank_accounts || {}, bankItems: d.bank_items || {}, bankStatus: d.bank_status || {}, bankMeta: d.bank_meta || {}, stock, expenses: [...expenses.values()].sort((a, b) => b.expense_date.localeCompare(a.expense_date) || String(b.number).localeCompare(String(a.number))), moves: d.cash_movements || [], closings: d.cash_closings || [], cash: d.cash, budgets: d.budgets || {}, categories: d.categories || [], rentability: d.rentability, pend };
  }

  const lastResults = new Map();
  async function flush({ silent = false } = {}) {
    if (!state.me || state.syncing || !state.online) return;
    state.syncing = true;
    try {
      const q = queue();
      if (q.length) {
        const r = await api('/api/sync/push', { method: 'POST', body: { ops: q } });
        const conflicts = [], rej = rejected();
        r.results.forEach((res, i) => {
          lastResults.set(q[i].op_id, res);
          if (res.status === 'ok') return;
          if (res.status === 'conflict') conflicts.push({ op: q[i], server: res.server, message: res.message });
          else if (!q[i].interactive) rej.push({ op: q[i], message: res.message || 'Opération refusée.', at: new Date().toISOString(), status: res.status });
        });
        saveQueue([]); saveRejected(rej);
        if (conflicts.length) await resolveConflicts(conflicts);
      }
      await pull();
      if (!silent && !queue().length) toast('Synchronisé ✔');
    } catch (e) {
      if (e instanceof OfflineError) state.online = false; else if (!silent) toast(e.message);
    } finally { state.syncing = false; updateBadges(); if (state.me) render(true); }
  }

  /** Envoie une opération : tout de suite si on est en ligne (la réponse sert à afficher une erreur dans le formulaire), sinon la met en attente. */
  async function submit(op) {
    const item = { op_id: uuid(), interactive: true, ...op };
    saveQueue([...queue(), item]);
    await flush({ silent: true });
    const res = lastResults.get(item.op_id);
    lastResults.delete(item.op_id);
    const q = queue(); const mine = q.find((o) => o.op_id === item.op_id);
    if (mine) { delete mine.interactive; saveQueue(q); return { status: 'queued' }; }
    return res || { status: 'queued' };
  }

  function resolveConflicts(list) {
    return new Promise((resolve) => {
      const next = () => {
        const c = list.shift();
        if (!c) { dlg.close(); resolve(); return; }
        const mine = (c.op.data && c.op.data.changes) || {};
        const rows = Object.keys(mine).map((k) => h('div', { class: 'diff' }, `${G.LABELS[k] || k} : serveur « `, h('del', {}, String(c.server[k] ?? '')), ' » → ma modification « ', h('ins', {}, String(mine[k])), ' »'));
        dlg.replaceChildren(h('h2', {}, 'Conflit de modification'), h('p', {}, `« ${c.server.name || c.server.number || ''} » a été modifié par quelqu'un d'autre pendant que vous étiez hors connexion.`), ...rows,
          h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: next }, 'Garder la version du serveur'),
            h('button', { class: 'primary', type: 'button', onclick: () => { const q = queue(); q.push({ ...c.op, op_id: uuid(), data: { ...c.op.data, base_version: c.server.version } }); saveQueue(q); next(); } }, 'Appliquer ma modification')));
        if (!dlg.open) dlg.showModal();
      };
      next();
    }).then(() => flush({ silent: true }));
  }

  function showRejected() {
    const list = rejected();
    const rows = list.map((r, i) => h('article', { class: 'card' }, h('strong', {}, G.OPLABELS[r.op.type] || r.op.type), h('div', { class: 'error' }, r.message), h('div', { class: 'muted' }, fmtDate(r.at)),
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => { const rr = rejected(); rr.splice(i, 1); saveRejected(rr); showRejected(); } }, 'Supprimer'),
        h('button', { class: 'primary', type: 'button', onclick: () => { const rr = rejected(); const [x] = rr.splice(i, 1); saveRejected(rr); const q = queue(); q.push({ ...x.op, op_id: uuid() }); saveQueue(q); flush({ silent: true }); showRejected(); } }, 'Réessayer'))));
    dlg.replaceChildren(h('h2', {}, 'Opérations refusées par le serveur'), h('p', { class: 'muted' }, 'Ces saisies faites hors connexion n\'ont pas pu être enregistrées. Rien n\'est perdu : supprimez-les ou réessayez.'), ...(rows.length ? rows : [h('p', {}, 'Aucune.')]),
      h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    if (!dlg.open) dlg.showModal();
  }

  // ---------- libellés ----------
  const LABELS = { code: 'Code', name: 'Nom', kind: 'Type', phone: 'Téléphone', zone: 'Zone', credit_limit: 'Limite de crédit', note: 'Note', owner_user_id: 'Commercial', deleted: 'Supprimé', role: 'Rôle', active: 'Actif',
    full_name: 'Nom complet', username: 'Identifiant', password: 'Mot de passe', price: 'Prix', category: 'Catégorie', unit: 'Unité', number: 'Numéro', client: 'Client', total: 'Total', paid_at_sale: 'Payé à la vente',
    due_date: 'Échéance', amount: 'Montant', method: 'Mode', reference: 'Référence', sale: 'Vente', date: 'Date', status: 'État', reason: 'Motif', settlement: 'Règlement', delivered_at: 'Livrée le', lines: 'Lignes',
    category: 'Catégorie', payee: 'Bénéficiaire', description: 'Description', kind_move: 'Type', date: 'Date', attendu: 'Attendu', 'compté': 'Compté', 'écart': 'Écart', mensuel: 'Budget mensuel', reorder_level: 'Seuil d\'alerte', track_stock: 'Suivi du stock', stock: 'Stock', supplier: 'Fournisseur', valeur: 'Valeur', company_name: 'Entreprise', address: 'Adresse', tax_id: 'N° fiscal', footer: 'Pied de page', tax_rate: 'Taux de taxe' };
  const ACTIONS = {
    'login.ok': 'Connexion', 'login.failed': 'Échec de connexion', 'login.locked': 'Compte verrouillé', 'login.blocked': 'Connexion refusée (compte verrouillé)', logout: 'Déconnexion',
    'setup.owner_created': 'Installation : propriétaire créé', 'user.created': 'Utilisateur créé', 'user.updated': 'Utilisateur modifié', 'password.changed': 'Mot de passe changé', 'password.change_failed': 'Échec de changement de mot de passe',
    'client.create': 'Client créé', 'client.update': 'Client modifié', 'client.delete': 'Client supprimé', 'client.conflict': 'Conflit de modification (client)', 'client.create.denied': 'Création de client refusée',
    'client.update.denied': 'Modification de client refusée', 'client.delete.denied': 'Suppression de client refusée', 'access.denied': 'Accès refusé', 'audit.exported': 'Journal exporté',
    'product.create': 'Produit créé', 'product.update': 'Produit modifié', 'product.delete': 'Produit supprimé', 'product.conflict': 'Conflit de modification (produit)',
    'sale.create': 'Vente enregistrée', 'sale.create.refused': 'Vente refusée', 'sale.credit_override': 'Crédit accordé malgré la limite', 'sale.deliver': 'Livraison enregistrée', 'sale.cancel': 'Vente annulée', 'sale.conflict': 'Conflit de modification (vente)',
    'payment.create': 'Paiement enregistré', 'payment.cancel': 'Paiement annulé', 'payment.conflict': 'Conflit de modification (paiement)', 'creditnote.create': 'Avoir créé', 'settings.update': 'Informations de l\'entreprise modifiées',
    'invoice.viewed': 'Facture consultée ou imprimée', 'receipt.viewed': 'Reçu consulté ou imprimé', 'import.excel': 'Import du classeur Excel', 'login.demo': 'Entrée en mode démonstration',
    'supplier.create': 'Fournisseur créé', 'supplier.update': 'Fournisseur modifié', 'supplier.delete': 'Fournisseur supprimé', 'supplier.conflict': 'Conflit de modification (fournisseur)',
    'purchase.create': 'Achat enregistré', 'purchase.cancel': 'Achat annulé', 'purchase.conflict': 'Conflit de modification (achat)', 'supplierpay.create': 'Règlement fournisseur', 'supplierpay.cancel': 'Règlement fournisseur annulé',
    'stock.adjust': 'Ajustement de stock', 'stock.inventory': 'Inventaire enregistré', 'expense.create': 'Dépense enregistrée', 'expense.cancel': 'Dépense annulée', 'expense.conflict': 'Conflit de modification (dépense)',
    'budget.set': 'Budget modifié', 'cash.move': 'Mouvement de caisse', 'cash.move_cancel': 'Mouvement de caisse annulé', 'cash.close': 'Caisse clôturée', 'cash.reopen': 'Clôture de caisse rouverte', 'cash_movement.conflict': 'Conflit (mouvement de caisse)', 'cash_closing.conflict': 'Conflit (clôture de caisse)',
    'account.create': 'Compte comptable créé', 'journal.create': 'Écriture manuelle passée', 'journal.cancel': 'Écriture manuelle annulée', 'accounting.settings': 'Réglages comptables modifiés', 'accounting.lock': 'Verrou comptable modifié', 'accounting.exported': 'Export comptable', 'fiscal.close': 'Exercice clôturé', 'fiscal.reopen': 'Exercice rouvert', 'snote.create': 'Avoir fournisseur créé', 'snote.cancel': 'Avoir fournisseur annulé', 'snote.conflict': 'Conflit de modification (avoir fournisseur)', 'snote.create.denied': 'Avoir fournisseur refusé', 'snote.cancel.denied': 'Annulation d\'avoir refusée', 'recurring.create': 'Modèle de dépense récurrente créé', 'recurring.update': 'Modèle récurrent modifié', 'recurring.generate': 'Dépenses récurrentes générées', 'recurring.conflict': 'Conflit de modification (récurrente)', 'recurring.create.denied': 'Modèle récurrent refusé', 'recurring.generate.denied': 'Génération refusée', 'transfer.create': 'Virement entre comptes', 'transfer.cancel': 'Virement entre comptes annulé', 'transfer.conflict': 'Conflit de modification (virement)', 'transfer.create.denied': 'Virement refusé', 'payacct.create': 'Compte bancaire ou mobile ajouté', 'payacct.set_active': 'Compte activé ou désactivé', 'payacct.create.denied': 'Ajout de compte refusé', 'delivery.create': 'Bon de livraison créé', 'delivery.cancel': 'Bon de livraison annulé', 'delivery.viewed': 'Bon de livraison consulté ou imprimé', 'delivery.conflict': 'Conflit de modification (bon de livraison)', 'delivery.create.denied': 'Bon de livraison refusé', 'delivery.cancel.denied': 'Annulation de bon refusée', 'quote.create': 'Devis créé', 'quote.close': 'Devis clos (refusé ou annulé)', 'quote.convert': 'Devis converti en vente', 'quote.viewed': 'Devis consulté ou imprimé', 'quote.conflict': 'Conflit de modification (devis)', 'quote.create.denied': 'Devis refusé', 'report.exported': 'Rapport exporté', 'expcat.create': 'Catégorie de dépense créée', 'expcat.update': 'Catégorie de dépense modifiée', 'expcat.conflict': 'Conflit de modification (catégorie)', 'expcat.create.denied': 'Création de catégorie refusée', 'expcat.update.denied': 'Modification de catégorie refusée', 'depot.create': 'Dépôt créé', 'depot.update': 'Dépôt modifié', 'depot.conflict': 'Conflit de modification (dépôt)', 'depot.create.denied': 'Création de dépôt refusée', 'stransfer.create': 'Transfert de stock', 'stransfer.cancel': 'Transfert de stock annulé', 'stransfer.conflict': 'Conflit (transfert de stock)', 'stransfer.create.denied': 'Transfert refusé', 'attachment.added': 'Pièce jointe ajoutée', 'attachment.removed': 'Pièce jointe retirée', 'attachment.viewed': 'Pièce jointe consultée', 'logo.updated': 'Logo de l\'entreprise modifié', 'logo.removed': 'Logo de l\'entreprise retiré', 'session.revoked': 'Connexion fermée', '2fa.begin': 'Double authentification : clé générée', '2fa.enabled': 'Double authentification activée', '2fa.disabled': 'Double authentification désactivée', '2fa.reset': 'Double authentification réinitialisée', '2fa.enable_failed': 'Activation 2FA : code faux', '2fa.disable_failed': 'Désactivation 2FA refusée', 'login.2fa_required': 'Connexion : code demandé', 'dunning.logged': 'Relance d\'impayé notée', 'audit.sealed': 'Journal scellé (signature hors serveur)', 'audit.seal_key': 'Clé de signature du journal enregistrée', 'backup.created': 'Sauvegarde créée', 'backup.downloaded': 'Sauvegarde téléchargée', 'export.all': 'Export complet des données', 'deferral.create': 'Régularisation d\'étalement créée', 'deferral.cancel': 'Régularisation annulée', 'deferral.conflict': 'Conflit de modification (régularisation)', 'deferral.create.denied': 'Régularisation refusée', 'deferral.cancel.denied': 'Annulation de régularisation refusée', 'provision.create': 'Provision constituée', 'provision.release': 'Reprise de provision', 'provision.unrelease': 'Reprise de provision annulée', 'provision.cancel': 'Provision annulée', 'provision.conflict': 'Conflit de modification (provision)', 'provision.create.denied': 'Provision refusée', 'provision.release.denied': 'Reprise refusée', 'provision.cancel.denied': 'Annulation de provision refusée', 'asset.create': 'Immobilisation enregistrée', 'asset.dispose': 'Immobilisation cédée ou mise au rebut', 'asset.cancel': 'Immobilisation annulée', 'asset.conflict': 'Conflit de modification (immobilisation)', 'asset.create.denied': 'Immobilisation refusée', 'asset.dispose.denied': 'Cession d\'immobilisation refusée', 'asset.cancel.denied': 'Annulation d\'immobilisation refusée',
    'bank.opening': 'Solde d\'ouverture bancaire', 'bank.import': 'Relevé importé', 'bank.match': 'Ligne de relevé pointée', 'bank.unmatch': 'Pointage annulé', 'bank.flag': 'Ligne de relevé à justifier', 'bank.void': 'Ligne de relevé retirée',
    'bank.automatch': 'Pointage automatique', 'bank.reconcile': 'Rapprochement bancaire', 'bank_line.conflict': 'Conflit (ligne de relevé)', 'bank.opening.denied': 'Accès bancaire refusé', 'bank.import.denied': 'Import de relevé refusé',
    'expense.create.denied': 'Dépense refusée', 'cash.move.denied': 'Mouvement de caisse refusé', 'cash.close.denied': 'Clôture de caisse refusée',
  };
  const SECURITY = new Set(['audit.seal_key', 'report.exported', 'stransfer.cancel', 'attachment.removed', 'snote.cancel', 'session.revoked', 'transfer.cancel', 'delivery.cancel', '2fa.enabled', '2fa.disabled', '2fa.reset', '2fa.enable_failed', '2fa.disable_failed', 'backup.downloaded', 'export.all', 'deferral.cancel', 'deferral.create.denied', 'deferral.cancel.denied', 'provision.unrelease', 'provision.cancel', 'provision.create.denied', 'provision.release.denied', 'provision.cancel.denied', 'asset.cancel', 'asset.dispose', 'asset.create.denied', 'asset.dispose.denied', 'asset.cancel.denied', 'fiscal.close', 'fiscal.reopen', 'accounting.lock', 'journal.cancel', 'accounting.settings', 'bank.unmatch', 'bank.void', 'bank.flag', 'bank.reconcile', 'bank.opening.denied', 'bank.import.denied', 'expense.cancel', 'cash.move_cancel', 'cash.reopen', 'expense.create.denied', 'cash.move.denied', 'cash.close.denied', 'purchase.cancel', 'supplierpay.cancel', 'stock.adjust', 'stock.inventory', 'login.failed', 'login.locked', 'login.blocked', 'access.denied', 'password.change_failed', 'client.create.denied', 'client.update.denied', 'client.delete.denied', 'client.conflict', 'sale.create.refused', 'sale.credit_override',
    'sale.cancel', 'payment.cancel', 'product.conflict', 'sale.conflict', 'payment.conflict']);
  const OPLABELS = { 'client.create': 'Nouveau client', 'client.update': 'Modification de client', 'client.delete': 'Suppression de client', 'product.create': 'Nouveau produit', 'product.update': 'Modification de produit', 'product.delete': 'Suppression de produit',
    'sale.create': 'Nouvelle vente', 'sale.deliver': 'Livraison', 'sale.cancel': 'Annulation de vente', 'payment.create': 'Nouveau paiement', 'payment.cancel': 'Annulation de paiement', 'creditnote.create': 'Nouvel avoir', 'settings.update': 'Informations de l\'entreprise', 'supplier.create': 'Nouveau fournisseur', 'supplier.update': 'Modification de fournisseur', 'supplier.delete': 'Suppression de fournisseur',
    'purchase.create': 'Nouvel achat', 'porder.create': 'Bon de commande fournisseur', 'porder.viewed': 'Bon de commande consulté ou imprimé', 'employee.create': 'Salarié ajouté', 'employee.update': 'Salarié modifié', 'payslip.create': 'Bulletin de paie créé', 'payslip.pay': 'Bulletin de paie payé', 'payslip.cancel': 'Bulletin de paie annulé', 'payslip.viewed': 'Bulletin de paie consulté ou imprimé', 'payroll.settings': 'Réglages de la paie modifiés', 'porder.close': 'Commande fournisseur annulée', 'porder.receive': 'Commande fournisseur reçue', 'purchase.cancel': 'Annulation d\'achat', 'supplierpay.create': 'Règlement fournisseur', 'supplierpay.cancel': 'Annulation de règlement', 'stock.adjust': 'Ajustement de stock', 'stock.inventory': 'Inventaire', 'expense.create': 'Nouvelle dépense', 'expense.cancel': 'Annulation de dépense', 'budget.set': 'Budget', 'cash.move': 'Mouvement de caisse', 'cash.move_cancel': 'Annulation de mouvement', 'cash.close': 'Clôture de caisse', 'cash.cancel_close': 'Réouverture de caisse', 'bank.opening': 'Solde d\'ouverture bancaire', 'bank.import': 'Import de relevé', 'bank.match': 'Pointage', 'bank.unmatch': 'Dépointage', 'bank.flag': 'Ligne à justifier', 'bank.void': 'Retrait de ligne', 'bank.automatch': 'Pointage automatique', 'bank.reconcile': 'Rapprochement', 'bank.create_expense': 'Dépense depuis le relevé', 'account.create': 'Nouveau compte', 'journal.create': 'Écriture manuelle', 'journal.cancel': 'Annulation d\'écriture', 'accounting.settings': 'Réglages comptables', 'accounting.lock': 'Verrou comptable', 'fiscal.close': 'Clôture d\'exercice', 'fiscal.reopen': 'Réouverture d\'exercice', 'expcat.create': 'Nouvelle catégorie de dépense', 'expcat.update': 'Modification de catégorie de dépense', 'depot.create': 'Nouveau dépôt', 'depot.update': 'Modification de dépôt', 'stransfer.create': 'Transfert de stock', 'stransfer.cancel': 'Annulation de transfert', 'snote.create': 'Nouvel avoir fournisseur', 'snote.cancel': 'Annulation d\'avoir fournisseur', 'recurring.create': 'Nouveau modèle récurrent', 'recurring.update': 'Modification de modèle récurrent', 'recurring.generate': 'Génération de dépenses récurrentes', 'transfer.create': 'Virement entre comptes', 'transfer.cancel': 'Annulation de virement', 'payacct.create': 'Nouveau compte bancaire', 'payacct.set_active': 'Activation de compte', 'delivery.create': 'Nouveau bon de livraison', 'delivery.cancel': 'Annulation de bon de livraison', 'quote.create': 'Nouveau devis', 'quote.close': 'Clôture de devis', 'quote.convert': 'Conversion de devis', 'deferral.create': 'Nouvelle régularisation d\'étalement', 'deferral.cancel': 'Annulation de régularisation', 'provision.create': 'Nouvelle provision', 'provision.release': 'Reprise de provision', 'provision.unrelease': 'Annulation de reprise', 'provision.cancel': 'Annulation de provision', 'asset.create': 'Nouvelle immobilisation', 'asset.dispose': 'Cession d\'immobilisation', 'asset.cancel': 'Annulation d\'immobilisation' };

  // ---------- écrans de connexion ----------
  function brandIntro() {
    return h('div', { class: 'login-brand' }, h('img', { src: '/brand-bhantal.jpg', alt: 'Bhantal SARL' }),
      h('p', { class: 'muted' }, 'Application de gestion commerciale et comptable'));
  }
  function renderSetup() {
    const err = h('div', { class: 'error', hidden: true });
    const f = { u: h('input', { autocomplete: 'username', required: true }), n: h('input', { autocomplete: 'name', required: true }), p: h('input', { type: 'password', autocomplete: 'new-password', required: true }) };
    view.replaceChildren(h('div', { class: 'center card' }, h('div', { class: 'row' }, h('span', { class: 'grow' }), I18N.switcher()), brandIntro(), h('h1', {}, 'Bienvenue 👋'), h('p', { class: 'muted' }, 'Première installation : créez le compte du propriétaire. Il pourra ensuite créer les comptes de l\'équipe et consulter le journal de tout ce qui est fait.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/setup', { method: 'POST', body: { username: f.u.value, full_name: f.n.value, password: f.p.value } }); await start(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
        field('Identifiant', f.u), field('Nom complet', f.n), field('Mot de passe (10 caractères minimum, avec majuscule, minuscule et chiffre)', f.p), err, h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Créer le propriétaire')))));
  }
  const DEMO_ROLES = [['owner', 'Propriétaire'], ['accountant', 'Comptable'], ['sales', 'Commercial'], ['cashier', 'Caissier'], ['auditor', 'Auditeur']];
  async function demoEnter(role) {
    try { await api('/api/demo/login', { method: 'POST', body: { role } }); await start(); } catch (e) { toast(e.message); }
  }
  function renderDemoBar() {
    const el = $('#demo'); el.hidden = !state.demo || !state.me;
    if (el.hidden) return;
    el.replaceChildren(h('strong', {}, 'MODE DÉMONSTRATION : données fictives.'), h('span', {}, 'Voir l\'application en tant que :'), ...DEMO_ROLES.map(([r, l]) => h('button', { type: 'button', 'aria-pressed': String(state.me.username === 'demo_' + r), onclick: () => demoEnter(r) }, l)));
  }
  function renderLogin() {
    if (state.demo) {
      view.replaceChildren(h('div', { class: 'center card' }, h('div', { class: 'row' }, h('span', { class: 'grow' }), I18N.switcher()), brandIntro(), h('h1', {}, 'Mode démonstration'), h('p', { class: 'muted' }, 'Données fictives, sans identifiant. Choisissez le rôle à découvrir :'),
        h('div', { class: 'list' }, DEMO_ROLES.map(([r, l]) => h('button', { class: 'primary', type: 'button', onclick: () => demoEnter(r) }, l)))));
      return;
    }
    const err = h('div', { class: 'error', hidden: true });
    const u = h('input', { autocomplete: 'username', required: true, autocapitalize: 'none' }), p = h('input', { type: 'password', autocomplete: 'current-password', required: true });
    const code = h('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 8, placeholder: '6 chiffres' }), codeField = field('Code de vérification (application d\'authentification)', code); codeField.hidden = true;
    view.replaceChildren(h('div', { class: 'center card' }, h('div', { class: 'row' }, h('span', { class: 'grow' }), I18N.switcher()), brandIntro(), h('h1', {}, 'Connexion'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/login', { method: 'POST', body: { username: u.value, password: p.value, code: code.value || undefined } }); await start(); } catch (x) { if (x.code === 'code_requis') { codeField.hidden = false; code.focus(); } err.textContent = x instanceof OfflineError ? 'Connexion impossible : vous êtes hors connexion.' : x.message; err.hidden = false; } } },
        field('Identifiant', u), field('Mot de passe', p), codeField, err, h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Se connecter')))));
  }
  function renderMustChange() {
    const err = h('div', { class: 'error', hidden: true });
    const o = h('input', { type: 'password', autocomplete: 'current-password', required: true }), n = h('input', { type: 'password', autocomplete: 'new-password', required: true });
    view.replaceChildren(h('div', { class: 'center card' }, h('h1', {}, 'Choisissez votre mot de passe'), h('p', { class: 'muted' }, 'Le mot de passe provisoire donné par le propriétaire doit être remplacé avant de continuer.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true; try { await api('/api/password', { method: 'POST', body: { old: o.value, new: n.value } }); await start(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
        field('Mot de passe provisoire', o), field('Nouveau mot de passe', n), err, h('p', {}, h('button', { class: 'primary', type: 'submit' }, 'Enregistrer')))));
  }

  // ---------- onglets et affichage ----------
  function visibleTabs() { return tabs.filter((t) => !t.show || t.show()); }
  // Navigation principale sur une ligne ; les sous-sections restent accessibles dans un menu compact.
  const GROUPS = [{ id: 'accueil', label: 'Accueil', tabs: ['tableau'] }, { id: 'commerce', label: 'Commerce', tabs: ['ventes', 'creances', 'relances', 'devis', 'clients'] },
    { id: 'marchandises', label: 'Marchandises', tabs: ['produits', 'achats'] }, { id: 'argent', label: 'Argent', tabs: ['caisse', 'banque'] },
    { id: 'finances', label: 'Finances', tabs: ['compta', 'paie', 'rapports'] }, { id: 'plus', label: 'Plus', tabs: ['users', 'audit', 'account', 'help'] }];
  const lastTab = {};
  function renderTabs() {
    const vt = visibleTabs(), known = new Set(GROUPS.flatMap((g) => g.tabs));
    const groups = GROUPS.map((g) => ({ ...g, items: vt.filter((t) => g.tabs.includes(t.id) || (g.id === 'plus' && !known.has(t.id))) })).filter((g) => g.items.length);
    const cur = groups.find((g) => g.items.some((t) => t.id === state.tab)) || groups[0];
    lastTab[cur.id] = state.tab;
    const go = (id) => { state.tab = id; render(); };
    const row = groups.map((g) => h('button', { type: 'button', role: 'tab', class: 'grp', 'aria-selected': String(g === cur), 'data-noms': '|' + g.items.map((t) => t.label).join('|') + '|',
      onclick: () => go(g.items.some((t) => t.id === lastTab[g.id]) ? lastTab[g.id] : g.items[0].id) }, g.items.length === 1 ? g.items[0].label : g.label));
    const subnav = cur.items.length > 1 ? h('select', { class: 'sous-select', 'aria-label': 'Navigation secondaire',
      onchange: (e) => go(e.target.value) }, cur.items.map((t) => h('option', { value: t.id }, t.label))) : null;
    if (subnav) subnav.value = state.tab;
    tabsEl.replaceChildren(h('div', { class: 'navrow' }, h('div', { class: 'rang', role: 'tablist' }, ...row), subnav));
    const sel = tabsEl.querySelector('.grp[aria-selected="true"]');                     // sur téléphone, la rubrique ouverte reste visible dans la rangée qui défile
    if (sel) sel.parentNode.scrollLeft = Math.max(0, sel.offsetLeft - 12);
  }
  function render(soft = false) {
    updateBadges(); renderDemoBar();
    const logged = !!state.me;
    topEl.hidden = !logged; tabsEl.hidden = !logged;
    if (state.setup) return renderSetup();
    if (!logged) return renderLogin();
    if (state.me.must_change) { tabsEl.hidden = true; return renderMustChange(); }
    if (soft && dlg.open) return;                                   // ne pas casser un formulaire ouvert pendant une synchronisation
    const vt = visibleTabs(); if (!vt.find((t) => t.id === state.tab)) state.tab = vt[0].id;
    renderTabs(); vt.find((t) => t.id === state.tab).render();
  }

  async function start() {
    state.setup = false;
    const me = await api('/api/me');
    state.me = me.user; state.perms = me.permissions;
    state.data = emptyData(); state.cursor = 0; state.epoch = null; state.dbId = null;
    const cached = await idb.get(key('cache'));
    if (cached) { state.data = cached.data; state.cursor = cached.cursor; state.epoch = cached.epoch; state.dbId = cached.dbId || null; }
    state.tab = 'tableau'; render();
    if (!state.me.must_change) flush({ silent: true });
  }

  async function boot() {
    try {
      const s = await api('/api/state'); state.rolesMap = s.roles; state.demo = !!s.demo;
      if (s.setup_required) { state.setup = true; render(); return; }
      try { await start(); } catch { state.me = null; if (state.demo) await demoEnter('owner'); else render(); }
    } catch { view.replaceChildren(h('div', { class: 'center card' }, h('h1', {}, 'Serveur injoignable'), h('p', {}, 'Vérifiez la connexion, puis rechargez la page.'))); }
  }

  const G = { $, h, field, uuid, store, idb, toast, money, fmtDate, fmtDay, today, addDays, daysBetween, api, OfflineError, state, can, dlg, view, queue, saveQueue, submit, flush, render, viewData, tabs, LABELS, ACTIONS, SECURITY, OPLABELS, deviceId };
  window.G = G;

  window.addEventListener('load', () => {
    window.addEventListener('online', () => { state.online = true; updateBadges(); flush({ silent: true }); });
    window.addEventListener('offline', () => { state.online = false; updateBadges(); });
    $('#sync').addEventListener('click', () => { state.online = navigator.onLine; flush(); });
    $('#rejected').addEventListener('click', showRejected);
    setInterval(() => { if (state.me && state.online && !document.hidden && !dlg.open) flush({ silent: true }); }, 30000);
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => { /* ouverture hors connexion non disponible */ });
    boot();
  });
})();
