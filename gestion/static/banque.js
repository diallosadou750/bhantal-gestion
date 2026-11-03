/* Écran Banque : rapprochement du relevé (banque, Orange Money, MTN MoMo) avec les opérations de l'application. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, today, can, state, dlg, view, tabs } = G;
  const st = { account: 'bank', mode: 'pointer' };
  const failed = (r) => r.status === 'invalid' || r.status === 'denied';
  const online = () => { if (!state.online) { G.toast('Cette action demande une connexion.'); return false; } return true; };
  const signed = (n) => (n > 0 ? '+' : n < 0 ? '−' : '') + money(Math.abs(n));
  const cls = (n) => 'name ' + (n < 0 ? 'neg' : 'pos');

  // ---------- lecture d'un relevé (CSV ou texte collé) ----------
  const strip = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const MONTHS = { janv: 1, jan: 1, janvier: 1, fevr: 2, fev: 2, feb: 2, fevrier: 2, mars: 3, mar: 3, avr: 4, apr: 4, avril: 4, mai: 5, may: 5, juin: 6, jun: 6, juil: 7, jul: 7, juillet: 7, aout: 8, aug: 8, sept: 9, sep: 9, septembre: 9, oct: 10, octobre: 10, nov: 11, novembre: 11, dec: 12, decembre: 12 };
  function parseDate(s) {
    s = (s || '').trim(); let m, y, mo, d;
    if ((m = s.match(/^(\d{4})-(\d{2})-(\d{2})/))) [y, mo, d] = [m[1], m[2], m[3]];
    else if ((m = s.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})/))) [y, mo, d] = [m[3], m[2], m[1]];
    else if ((m = s.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{2})(?!\d)/))) [y, mo, d] = ['20' + m[3], m[2], m[1]];
    else if ((m = strip(s).match(/^(\d{1,2})[ \/.-]+([a-z]{3,9})\.?[ \/.-]+(\d{2}|\d{4})(?!\d)/)) && MONTHS[m[2]]) [y, mo, d] = [m[3].length === 2 ? '20' + m[3] : m[3], MONTHS[m[2]], m[1]];
    else return null;
    const dt = new Date(Number(y), Number(mo) - 1, Number(d));
    if (dt.getFullYear() !== Number(y) || dt.getMonth() !== Number(mo) - 1 || dt.getDate() !== Number(d)) return null;     // 32/13/2026 n'existe pas
    return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }
  function parseAmount(s) {
    s = (s || '').replace(/[\s  ]/g, '').replace(/[A-Za-z]/g, ''); if (!s) return null;
    let neg = false; if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
    if (s.startsWith('-') || s.endsWith('-') || s.startsWith('−')) { neg = true; s = s.replace(/[-−]/g, ''); }
    if (s.startsWith('+')) s = s.slice(1);
    if (s.includes(',') && s.includes('.')) { const dec = s.lastIndexOf(',') > s.lastIndexOf('.') ? ',' : '.', th = dec === ',' ? '.' : ','; s = s.split(th).join('').replace(dec, '.'); }
    else if (s.includes(',')) s = /^\d{1,3}(,\d{3})+$/.test(s) ? s.replace(/,/g, '') : s.replace(',', '.');                 // 1,234,567 = milliers ; 1234,56 = décimales
    else s = s.replace(/\.(?=\d{3}(\D|$))/g, '');
    const n = Number(s); if (!Number.isFinite(n)) return null; return Math.round(neg ? -n : n);
  }
  function splitRow(line, delim) {
    const out = []; let cur = '', q = false;
    for (const ch of line) { if (ch === '"') q = !q; else if (ch === delim && !q) { out.push(cur); cur = ''; } else cur += ch; }
    out.push(cur); return out.map((x) => x.trim());
  }
  const FIELDS = [['date', 'Date'], ['label', 'Libellé'], ['debit', 'Débit'], ['credit', 'Crédit'], ['amount', 'Montant (signé)'], ['sens', 'Sens (D/C)'], ['ref', 'Référence']];
  /** Colonnes reconnues d'après les titres (comparaison sans accents ni majuscules). */
  function guessColumns(headCells) {
    const head = headCells.map(strip), find = (re) => head.findIndex((x) => re.test(x));
    return { date: find(/date/), label: head.findIndex((x) => /libell|descript|detail|motif|operation|nature/.test(x) && !/date/.test(x)), debit: find(/debit|sortie|retrait/), credit: find(/credit|entree|depot|versement/), amount: find(/montant|amount|solde ?op/), sens: find(/^(sens|d\/c|dc|d-c|type)$/), ref: find(/ref|piece|numero|n°/) };
  }
  const usable = (c) => c.date >= 0 && (c.amount >= 0 || c.debit >= 0 || c.credit >= 0);
  /** override : { headerRow, columns } choisis à la main ; sinon on cherche la ligne de titres dans les 25 premières lignes (les banques mettent souvent des lignes d'en-tête avant). */
  function parseStatement(text, override) {
    const rows = text.replace(/^﻿/, '').replace(/\r/g, '').split('\n').filter((l) => l.trim());
    if (rows.length < 2) return { lines: [], errors: ['Collez au moins une ligne de titres et une ligne d\'opération.'], headers: [], columns: null, headerRow: -1 };
    const sample = rows.slice(0, 30), delim = [';', '\t', ',', '|'].map((d) => [d, sample.filter((r) => r.split(d).length >= 3).length]).sort((a, b) => b[1] - a[1])[0][0];
    let headerRow = -1, c = null;
    if (override && override.columns) { headerRow = override.headerRow; c = override.columns; }
    else for (let i = 0; i < Math.min(rows.length - 1, 25); i++) { const g = guessColumns(splitRow(rows[i], delim)); if (usable(g)) { headerRow = i; c = g; break; } }
    if (headerRow < 0 || !c || !usable(c)) return { lines: [], errors: ['Je ne trouve pas la ligne de titres. Elle doit contenir au minimum : Date, Débit et Crédit (ou Montant). Vous pouvez choisir les colonnes à la main ci-dessous.'], headers: splitRow(rows[0], delim), columns: c || guessColumns(splitRow(rows[0], delim)), headerRow: Math.max(0, headerRow), delim, rows };
    const lines = [], errors = [], headers = splitRow(rows[headerRow], delim);
    rows.slice(headerRow + 1).forEach((row, i) => {
      const cells = splitRow(row, delim), n = headerRow + i + 2;
      if (cells.length < 2 || (cells.length < 3 && !parseDate(cells[c.date]))) return;                                 // ligne de total, de pied de page ou vide
      const d = parseDate(cells[c.date]); let amount = null;
      if (c.amount >= 0) {
        amount = parseAmount(cells[c.amount]);
        if (amount !== null && c.sens >= 0) { const sv = strip(cells[c.sens] || ''); if (/^(d|db|debit|dr)\b/.test(sv)) amount = -Math.abs(amount); else if (/^(c|cr|credit)\b/.test(sv)) amount = Math.abs(amount); }
      } else { const de = c.debit >= 0 ? parseAmount(cells[c.debit]) : null, cr = c.credit >= 0 ? parseAmount(cells[c.credit]) : null; if (cr) amount = Math.abs(cr); else if (de) amount = -Math.abs(de); }
      if (!d && /^(total|sous-total|solde|ancien solde|nouveau solde|report)/.test(strip(cells[c.date] || cells[0] || ''))) return;       // ligne de total ou de solde, pas une opération
      if (!d) { if (cells.join('').trim()) errors.push(`Ligne ${n} : date illisible « ${cells[c.date] || ''} ».`); return; }
      if (!amount) { errors.push(`Ligne ${n} : montant absent ou nul.`); return; }
      lines.push({ date: d, label: c.label >= 0 ? cells[c.label] : '', amount, reference: c.ref >= 0 ? cells[c.ref] : '' });
    });
    return { lines, errors, headers, columns: c, headerRow, delim, rows };
  }
  G.bankParse = parseStatement;
  /** Un relevé exporté par Excel ou par une banque est souvent en Windows-1252, pas en UTF-8 : on essaie UTF-8 strict, puis Windows-1252. */
  function decode(buf) { try { return new TextDecoder('utf-8', { fatal: true }).decode(buf); } catch (e) { return new TextDecoder('windows-1252').decode(buf); } }
  const memo = {
    sig: (headers) => 'gestion.bankmap.' + headers.map(strip).join('|').slice(0, 200),
    get(headers) { try { return JSON.parse(localStorage.getItem(this.sig(headers)) || 'null'); } catch (e) { return null; } },
    set(headers, v) { try { localStorage.setItem(this.sig(headers), JSON.stringify(v)); } catch (e) { /* stockage indisponible : sans importance */ } },
  };

  // ---------- formulaires ----------
  function openingForm() {
    const err = h('div', { class: 'error', hidden: true }), vd = G.viewData(), label = (vd.bankMeta[st.account] || {}).label;
    const t = { bal: h('input', { type: 'number', step: 1, required: true, placeholder: 'Solde du relevé à cette date' }), date: h('input', { type: 'date', value: today(), max: today() }) };
    dlg.replaceChildren(h('h2', {}, `Solde d'ouverture : ${label}`), h('p', { class: 'muted' }, 'Indiquez le solde du compte à une date de départ (celui du dernier relevé avant de commencer à utiliser l\'application). Seules les opérations APRÈS cette date sont rapprochées.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'bank.opening', data: { account: st.account, balance: Number(t.bal.value), date: t.date.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); } }, field('Solde (GNF)', t.bal), field('À la date du', t.date), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  function importForm() {
    const err = h('div', { class: 'error', hidden: true }), info = h('div', { class: 'muted' }), prev = h('div', {}), mapBox = h('div', { class: 'card', hidden: true });
    const area = h('textarea', { rows: 8, placeholder: 'Date;Libellé;Débit;Crédit;Référence\n05/10/2026;VIREMENT CAMARA;;2 000 000;VIR-1\n06/10/2026;FRAIS DE TENUE DE COMPTE;15 000;;' });
    const file = h('input', { type: 'file', accept: '.csv,.txt,text/csv,text/plain' });
    let parsed = { lines: [], errors: [] }, override = null;
    const drawMap = () => {
      if (!parsed.columns || !parsed.headers || !parsed.headers.length) { mapBox.hidden = true; return; }
      mapBox.hidden = false;
      const sels = FIELDS.map(([k, lab]) => h('label', { class: 'field' }, h('span', {}, lab), h('select', { 'aria-label': 'Colonne : ' + lab, onchange: (e) => { override = { headerRow: parsed.headerRow, columns: { ...parsed.columns, [k]: Number(e.target.value) } }; refresh(true); } },
        h('option', { value: -1, selected: parsed.columns[k] < 0 }, '— aucune —'), ...parsed.headers.map((t, i) => h('option', { value: i, selected: parsed.columns[k] === i }, t || `colonne ${i + 1}`)))));
      mapBox.replaceChildren(h('strong', {}, 'Colonnes reconnues'), h('p', { class: 'muted' }, 'Vérifiez que chaque colonne correspond bien. Si votre banque nomme ses colonnes autrement, choisissez-les ici : le choix est retenu pour les prochains relevés de même format.'), ...sels);
    };
    const refresh = (keepOverride) => {
      if (!keepOverride) override = null;
      parsed = parseStatement(area.value, override);
      if (!override && parsed.headers && parsed.headers.length && parsed.columns) { const saved = memo.get(parsed.headers); if (saved && usable(saved)) { override = { headerRow: parsed.headerRow, columns: saved }; parsed = parseStatement(area.value, override); } }
      if (override && parsed.headers && parsed.headers.length) memo.set(parsed.headers, override.columns);
      prev.replaceChildren();
      info.textContent = area.value.trim() ? `${parsed.lines.length} ligne(s) lue(s), ${parsed.errors.length} problème(s).${parsed.headerRow > 0 ? ` Les ${parsed.headerRow} première(s) ligne(s) avant les titres sont ignorées.` : ''}` : '';
      if (parsed.errors.length) prev.append(h('div', { class: 'error' }, parsed.errors.slice(0, 5).map((x) => h('div', {}, x))));
      if (parsed.lines.length) prev.append(h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, h('tr', {}, h('th', {}, 'Date'), h('th', {}, 'Libellé'), h('th', {}, 'Montant')), ...parsed.lines.slice(0, 8).map((l) => h('tr', {}, h('td', {}, fmtDay(l.date)), h('td', {}, l.label), h('td', { class: 'num' }, signed(l.amount)))))));
      drawMap();
    };
    area.addEventListener('input', () => refresh(false));
    file.addEventListener('change', () => { const f = file.files[0]; if (!f) return; if (f.size > 2_000_000) { G.toast('Fichier trop gros (2 Mo maximum).'); return; } const rd = new FileReader(); rd.onload = () => { area.value = decode(rd.result); refresh(false); }; rd.readAsArrayBuffer(f); });
    dlg.replaceChildren(h('h2', {}, `Importer un relevé : ${(G.viewData().bankMeta[st.account] || {}).label}`), h('p', { class: 'muted' }, 'Exportez le relevé en CSV depuis votre banque ou votre opérateur (si vous n\'avez qu\'un fichier Excel : « Enregistrer sous » en CSV), ou copiez-collez ses lignes. Les lignes d\'en-tête de la banque avant les titres sont ignorées. Les lignes déjà importées sont ignorées : on peut recommencer sans doublon.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; if (!parsed.lines.length) { err.textContent = 'Aucune ligne exploitable.'; err.hidden = false; return; }
        const r = await G.submit({ type: 'bank.import', data: { account: st.account, lines: parsed.lines.slice(0, 2000) } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
        dlg.close(); G.render(); G.toast(`${r.created} ligne(s) importée(s), ${r.skipped} déjà présente(s)${r.errors && r.errors.length ? ', ' + r.errors.length + ' refusée(s)' : ''}.${r.warning ? ' ' + r.warning : ''}`); } },
        field('Fichier CSV', file), field('ou texte collé', area), info, mapBox, prev, err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Importer'))));
    dlg.showModal();
  }

  function pickForm(line) {
    const vd = G.viewData(), cands = (vd.bankItems[st.account] || []).filter((i) => Math.sign(i.amount) === Math.sign(line.amount))
      .sort((a, b) => Math.abs(G.daysBetween(a.date, line.line_date)) - Math.abs(G.daysBetween(b.date, line.line_date)) || a.date.localeCompare(b.date)).slice(0, 60);
    const err = h('div', { class: 'error', hidden: true }), sumEl = h('div', { class: 'big' }), boxes = [];
    const exact = cands.filter((i) => i.amount === line.amount); const pre = exact.length ? [exact[0]] : [];
    const recalc = () => { const s = boxes.filter((b) => b.el.checked).reduce((a, b) => a + b.item.amount, 0), d = line.amount - s; sumEl.textContent = `Choisi : ${signed(s)} sur ${signed(line.amount)} — ${d === 0 ? '✔ le compte est bon' : 'il manque ' + signed(d)}`; };
    dlg.replaceChildren(h('h2', {}, 'Pointer cette ligne du relevé'), h('div', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, line.label || '(sans libellé)'), h('span', { class: cls(line.amount) }, signed(line.amount))), h('div', { class: 'muted' }, `${fmtDay(line.line_date)}${line.reference ? ' · réf. ' + line.reference : ''}`)),
      h('p', { class: 'muted' }, cands.length ? 'Cochez les opérations du livre qui correspondent (une ou plusieurs : par exemple une remise de plusieurs chèques).' : 'Aucune opération du livre de même sens n\'est à pointer. Vérifiez qu\'elle est bien enregistrée (paiement, dépense…), ou créez-la.'),
      ...cands.map((i) => { const el = h('input', { type: 'checkbox', checked: pre.includes(i) }); el.addEventListener('change', recalc); boxes.push({ el, item: i });
        return h('label', { class: 'check card' }, el, h('span', { class: 'grow' }, `${fmtDay(i.date)} · ${i.label}${i.ref ? ' · ' + i.ref : ''}`), h('strong', { class: cls(i.amount) }, signed(i.amount))); }),
      sumEl, err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'button', onclick: async () => {
        if (!online()) return; err.hidden = true; const chosen = boxes.filter((b) => b.el.checked).map((b) => ({ type: b.item.type, id: b.item.id })); if (!chosen.length) { err.textContent = 'Cochez au moins une opération.'; err.hidden = false; return; }
        const r = await G.submit({ type: 'bank.match', data: { line_id: line.id, base_version: line.version, items: chosen } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast('Ligne pointée ✔'); } }, 'Pointer')));
    recalc(); dlg.showModal();
  }

  function expenseFromLine(line) {
    const vd = G.viewData(), err = h('div', { class: 'error', hidden: true });
    const t = { cat: h('select', {}, ...vd.categories.filter((c) => c.active !== false).map((c) => h('option', { value: c.key, selected: c.key === 'Frais bancaires' }, c.key))), desc: h('input', { maxlength: 200, placeholder: 'Constatée sur le relevé' }) };
    dlg.replaceChildren(h('h2', {}, 'Créer la dépense'), h('p', { class: 'muted' }, `${fmtDay(line.line_date)} · ${line.label} · ${signed(line.amount)}. La dépense est créée à la date et au montant du relevé, puis pointée.`),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'bank.create_expense', data: { line_id: line.id, base_version: line.version, category: t.cat.value, description: t.desc.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Dépense ${r.expense.number} créée et pointée.`); } }, field('Catégorie', t.cat), field('Description', t.desc), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Créer et pointer'))));
    dlg.showModal();
  }

  async function ask(label, type, line, key) {
    if (!online()) return; const v = prompt(label); if (!v || v.trim().length < 3) return;
    const r = await G.submit({ type, data: { line_id: line.id, base_version: line.version, [key]: v.trim() } }); if (failed(r)) G.toast(r.message); G.render();
  }

  function breakdown(c) {
    const row = (l, v, strong) => h('tr', {}, h('td', {}, strong ? h('strong', {}, l) : l), h('td', { class: 'num' }, strong ? h('strong', {}, signed(v)) : signed(v)));
    return h('div', { class: 'scroll' }, h('table', { class: 'tbl' }, row('Solde du relevé', c.statement_balance), row('− lignes du relevé non pointées (pas dans le livre)', -c.bank_only), row('= solde bancaire ajusté', c.statement_balance - c.bank_only, true),
      row('Solde du livre (application)', c.book_balance), row('− opérations du livre non pointées (pas à la banque : chèques, dépôts en transit)', -c.book_only), row('= solde du livre ajusté', c.book_balance - c.book_only, true), row('ÉCART', c.difference, true)));
  }
  function reconcileForm() {
    const err = h('div', { class: 'error', hidden: true }), box = h('div', {}), noteBox = h('div', { hidden: true });
    const t = { date: h('input', { type: 'date', value: today(), max: today() }), bal: h('input', { type: 'number', step: 1, required: true }), note: h('input', { maxlength: 200, placeholder: 'Explication de l\'écart' }) };
    noteBox.append(field('Explication de l\'écart (obligatoire)', t.note));
    dlg.replaceChildren(h('h2', {}, 'Rapprocher le compte'), h('p', { class: 'muted' }, 'Saisissez la date et le solde de clôture indiqués sur le relevé. L\'application compare avec son propre solde.'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; box.replaceChildren();
        const r = await G.submit({ type: 'bank.reconcile', data: { id: G.uuid(), account: st.account, date: t.date.value, statement_balance: Number(t.bal.value), note: t.note.value } });
        if (r.status === 'invalid') { err.textContent = r.message; err.hidden = false; if (r.computation) { r.computation.statement_balance = Number(t.bal.value); box.append(breakdown(r.computation)); noteBox.hidden = false; } return; }
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
        const c = r.computation; dlg.replaceChildren(h('h2', {}, c.difference === 0 ? 'Rapprochement équilibré ✔' : 'Rapprochement enregistré avec un écart'), breakdown(c), h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => { dlg.close(); G.render(); } }, 'Fermer')));
      } }, field('Date du relevé', t.date), field('Solde de clôture du relevé (GNF)', t.bal), box, noteBox, err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Rapprocher'))));
    dlg.showModal();
  }

  // ---------- virements entre comptes ----------
  function transferForm() {
    const vd = G.viewData(), meta = vd.bankMeta, keys = Object.keys(meta).filter((k) => meta[k].active !== false), err = h('div', { class: 'error', hidden: true });
    if (keys.length < 2) { G.toast('Il faut au moins deux comptes actifs.'); return; }
    const mk = (sel) => h('select', {}, ...keys.map((k) => h('option', { value: k, selected: k === sel }, meta[k].label)));
    const t = { from: mk(st.account), to: mk(keys.find((k) => k !== st.account)), amount: h('input', { type: 'number', min: 1, step: 1, required: true }), date: h('input', { type: 'date', value: today(), max: today() }), ref: h('input', { maxlength: 60, placeholder: 'Référence du virement' }), note: h('input', { maxlength: 200 }) };
    dlg.replaceChildren(h('h2', {}, 'Virement entre deux comptes'), h('p', { class: 'muted' }, 'De l\'argent qui change de compte : ce n\'est ni une dépense ni une vente. Il sera à pointer des deux côtés avec les relevés. Pour les espèces, utilisez un mouvement de caisse (dépôt ou retrait).'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'transfer.create', data: { id: G.uuid(), from_account: t.from.value, to_account: t.to.value, amount: Number(t.amount.value), transfer_date: t.date.value, reference: t.ref.value, note: t.note.value } });
        if (failed(r)) { err.textContent = r.message; err.hidden = false; return; } dlg.close(); G.render(); G.toast(`Virement ${r.transfer.number} enregistré.`); } }, field('Du compte', t.from), field('Vers le compte', t.to), field('Montant (GNF)', t.amount), field('Date', t.date), field('Référence', t.ref), field('Note', t.note), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }
  function transfersList() {
    const meta = G.viewData().bankMeta, list = (state.data.transfers || []).slice().sort((a, b) => b.transfer_date.localeCompare(a.transfer_date) || b.seq - a.seq), nm = (k) => (meta[k] || {}).label || k;
    const cancel = async (x) => { if (!online()) return; const reason = prompt('Motif de l\'annulation de ' + x.number + ' :'); if (!reason || reason.trim().length < 3) return; const r = await G.submit({ type: 'transfer.cancel', data: { id: x.id, base_version: x.version, reason: reason.trim() } }); G.toast(failed(r) || r.status === 'conflict' ? r.message : 'Virement annulé.'); G.render(); };
    return h('div', {}, h('div', { class: 'row' }, h('h2', { class: 'grow' }, 'Virements entre comptes'), can('bank.write') && h('button', { class: 'primary', type: 'button', onclick: transferForm }, '↔ Nouveau virement')),
      list.length ? h('div', { class: 'list two' }, list.map((x) => h('article', { class: 'card' + (x.status === 'cancelled' ? ' struck' : '') }, h('div', { class: 'head' }, h('span', { class: 'name' }, `${x.number} · ${nm(x.from_account)} → ${nm(x.to_account)}`), h('span', { class: 'name' }, money(x.amount))),
        h('div', { class: 'muted' }, `${fmtDay(x.transfer_date)}${x.reference ? ' · réf. ' + x.reference : ''}${x.note ? ' · ' + x.note : ''}${x.status === 'cancelled' ? ' · annulé : ' + x.cancel_reason : ''}`),
        x.status === 'active' && can('bank.write') && h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); cancel(x); } }, 'Annuler'))))) : h('p', { class: 'muted' }, 'Aucun virement entre comptes.'));
  }

  // ---------- comptes supplémentaires ----------
  function newAccountForm() {
    const err = h('div', { class: 'error', hidden: true }), label = h('input', { maxlength: 40, required: true, placeholder: 'Ex. Virement UBA, Wave, Compte BICIGUI…' }), kind = h('select', {}, h('option', { value: 'bank' }, 'Compte bancaire'), h('option', { value: 'mobile' }, 'Mobile money'));
    dlg.replaceChildren(h('h2', {}, 'Ajouter un compte'), h('p', { class: 'muted' }, 'Le nom devient un mode de paiement : choisissez-le sur un paiement, une dépense ou un achat pour le ranger sur ce compte, qui aura son propre rapprochement et son propre compte comptable (créé automatiquement).'),
      h('form', { onsubmit: async (e) => { e.preventDefault(); if (!online()) return; err.hidden = true; const r = await G.submit({ type: 'payacct.create', data: { label: label.value, kind: kind.value } }); if (failed(r)) { err.textContent = r.message; err.hidden = false; return; }
        dlg.close(); st.account = r.pay_account.key; G.render(); G.toast('Compte ajouté : fixez maintenant son solde d\'ouverture.'); } }, field('Nom du compte', label), field('Type', kind), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Ajouter'))));
    dlg.showModal();
  }
  async function toggleAccount(key) {
    if (!online()) return; const pa = (state.data.pay_accounts || []).find((x) => x.key === key); if (!pa) return;
    if (pa.active && !confirm('Désactiver « ' + pa.label + ' » ? Il n\'apparaîtra plus dans les choix de paiement ; son historique reste.')) return;
    const r = await G.submit({ type: 'payacct.set_active', data: { key, base_version: pa.version, active: !pa.active } }); if (r.status === 'invalid' || r.status === 'denied' || r.status === 'conflict') G.toast(r.message); G.render();
  }

  // ---------- écran ----------
  function render() {
    const vd = G.viewData(), meta = vd.bankMeta, accs = Object.keys(meta);
    if (!accs.length) { view.replaceChildren(h('p', { class: 'muted' }, 'Aucune donnée bancaire : synchronisez-vous.')); return; }
    if (!meta[st.account]) st.account = accs[0];
    const a = st.account, opening = vd.bankAccounts[a], status = vd.bankStatus[a] || {}, lines = vd.bankLines.filter((l) => l.account === a && l.status !== 'void'), items = vd.bankItems[a] || [];
    const open = lines.filter((l) => l.status === 'open' || l.status === 'flagged').sort((x, y) => x.line_date.localeCompare(y.line_date)), done = lines.filter((l) => l.status === 'matched').sort((x, y) => y.line_date.localeCompare(x.line_date));
    const recs = vd.bankRecs.filter((r) => r.account === a).sort((x, y) => y.rec_date.localeCompare(x.rec_date) || y.seq - x.seq), lastOk = recs.find((r) => r.difference === 0);
    const accChips = h('div', { class: 'chips' }, accs.map((k) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(a === k), onclick: () => { st.account = k; render(); } }, meta[k].label + (meta[k].active === false ? ' (désactivé)' : ''))),
      can('bank.write') && h('button', { type: 'button', class: 'chip', onclick: newAccountForm }, '＋ Ajouter un compte'), meta[a].extra && can('bank.write') && h('button', { type: 'button', class: 'chip', onclick: () => toggleAccount(a) }, meta[a].active ? 'Désactiver ce compte' : 'Réactiver ce compte'));
    const modes = [['pointer', `À pointer (${open.length})`], ['rapprochements', 'Rapprochements'], ['pointees', 'Pointées'], ['virements', 'Virements']];
    const bar = h('div', { class: 'chips' }, modes.map(([id, l]) => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(st.mode === id), onclick: () => { st.mode = id; render(); } }, l)));
    let body;
    if (st.mode === 'virements') body = transfersList();
    else if (!opening) body = h('div', { class: 'card' }, h('h2', {}, 'Première utilisation de ce compte'), h('p', {}, 'Avant de pointer, fixez le solde d\'ouverture : le solde du compte à une date de départ.'), h('button', { class: 'primary', type: 'button', onclick: openingForm }, 'Fixer le solde d\'ouverture'));
    else if (st.mode === 'pointer') {
      body = h('div', {}, h('h2', {}, `Lignes du relevé à pointer (${open.length})`),
        open.length ? h('div', { class: 'list two' }, open.map((l) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, l.label || '(sans libellé)'), h('span', { class: cls(l.amount) }, signed(l.amount))),
          h('div', { class: 'muted' }, `${fmtDay(l.line_date)}${l.reference ? ' · réf. ' + l.reference : ''}`), l.status === 'flagged' && h('div', {}, h('span', { class: 'badge warn' }, 'À justifier : ' + l.note)),
          h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (online()) pickForm(l); } }, '✔ Pointer'), l.amount < 0 && can('expenses.create') && h('a', { href: '#', onclick: (e) => { e.preventDefault(); if (online()) expenseFromLine(l); } }, '＋ Créer la dépense'),
            l.status === 'open' && h('a', { href: '#', onclick: (e) => { e.preventDefault(); ask('Pourquoi cette ligne est-elle à justifier ?', 'bank.flag', l, 'note'); } }, '? À justifier'), h('a', { href: '#', onclick: (e) => { e.preventDefault(); ask('Motif du retrait de cette ligne (erreur d\'import, doublon…) :', 'bank.void', l, 'reason'); } }, '🗑 Retirer'))))) : h('p', { class: 'ok-box' }, 'Toutes les lignes du relevé sont pointées ✔'),
        h('h2', {}, `Opérations du livre pas encore à la banque (${items.length})`), h('p', { class: 'muted' }, 'Enregistrées dans l\'application mais pas (encore) sur le relevé : chèques émis non débités, remises en transit, ou lignes du relevé pas encore importées.'),
        items.length ? h('div', { class: 'list two' }, items.map((i) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, i.label), h('span', { class: cls(i.amount) }, signed(i.amount))), h('div', { class: 'muted' }, `${fmtDay(i.date)}${i.ref ? ' · ' + i.ref : ''}`)))) : h('p', { class: 'muted' }, 'Aucune.'));
    } else if (st.mode === 'rapprochements') {
      body = recs.length ? h('div', { class: 'list two' }, recs.map((r) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, fmtDay(r.rec_date)), r.difference === 0 ? h('span', { class: 'badge' }, 'Équilibré') : h('span', { class: 'badge bad' }, 'Écart ' + signed(r.difference))),
        h('div', { class: 'muted' }, `Solde du relevé ${money(r.statement_balance)} · solde du livre ${money(r.book_balance)} · en transit ${signed(r.book_only)} · à justifier ${signed(r.bank_only)}`), r.note && h('div', { class: 'muted' }, r.note)))) : h('p', { class: 'muted' }, 'Aucun rapprochement enregistré.');
    } else {
      body = done.length ? h('div', { class: 'list two' }, done.map((l) => h('article', { class: 'card' }, h('div', { class: 'head' }, h('span', { class: 'name' }, l.label || '(sans libellé)'), h('span', { class: cls(l.amount) }, signed(l.amount))), h('div', { class: 'muted' }, fmtDay(l.line_date)),
        ...l.matches.map((m) => h('div', { class: m.active ? 'muted' : 'error' }, (m.active ? '↔ ' : '⚠ Opération annulée ou supprimée depuis : ') + (m.label || m.type))),
        h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); ask('Motif du dépointage :', 'bank.unmatch', l, 'reason'); } }, 'Dépointer'))))) : h('p', { class: 'muted' }, 'Aucune ligne pointée.');
    }
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Banque et mobile money'), opening && h('button', { class: 'secondary', type: 'button', onclick: () => { if (online()) importForm(); } }, '⬆ Importer un relevé'), opening && open.length > 0 && h('button', { class: 'secondary', type: 'button', onclick: async () => { if (!online()) return; const r = await G.submit({ type: 'bank.automatch', data: { account: a } }); G.render(); G.toast(failed(r) ? r.message : `${r.matched} ligne(s) pointée(s) automatiquement${r.ambiguous ? ', ' + r.ambiguous + ' à voir à la main (plusieurs candidats)' : ''}.`); } }, '⚡ Pointer automatiquement'),
      opening && h('button', { class: 'primary', type: 'button', onclick: () => { if (online()) reconcileForm(); } }, 'Rapprocher')),
      h('div', { class: 'card' }, accChips, opening && h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Solde du livre'), h('strong', {}, money(status.book_balance))),
        h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Lignes à pointer'), h('strong', {}, String(open.length))), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Dernier rapprochement équilibré'), h('strong', {}, lastOk ? fmtDay(lastOk.rec_date) : 'jamais'))), opening && bar), body);
  }
  tabs.push({ id: 'banque', label: 'Banque', show: () => can('bank.read'), render });
})();
