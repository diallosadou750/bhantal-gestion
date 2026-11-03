/* Paie simple : salariés, bulletins du mois, paiement (crée une dépense), réglages des cotisations. En ligne seulement (données sensibles). */
(() => {
  'use strict';
  const { h, field, money, fmtDay, can, state, dlg, view, tabs, api, OfflineError } = G;
  const now = new Date(), st = { month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}` };
  const today = () => new Date().toISOString().slice(0, 10);
  const pct = (bp) => (bp / 100).toLocaleString('fr-FR', { maximumFractionDigits: 2 }) + ' %';

  function form(title, intro, fields, submit, onDone) {
    const err = h('div', { class: 'error', hidden: true });
    dlg.replaceChildren(h('h2', {}, title), intro && h('p', { class: 'muted' }, intro),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true;
        try { const r = await submit(); dlg.close(); if (onDone) onDone(r); render(); } catch (x) { err.textContent = x.message; err.hidden = false; } } },
        ...fields, err, h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }
  const post = (url, body) => api(url, { method: 'POST', body });
  const num = (v) => Math.round(Number(v) || 0);

  function settingsForm(s) {
    const f = { e: h('input', { type: 'number', min: 0, max: 100, step: 0.01, required: true, value: s.employee_bp / 100 }), r: h('input', { type: 'number', min: 0, max: 50, step: 0.01, required: true, value: s.employer_bp / 100 }),
      c: h('input', { type: 'number', min: 0, step: 1, value: s.ceiling }) };
    form('Réglages de la paie', 'Aucun taux n\'est fixé par l\'application : saisissez ceux que votre expert-comptable vous a confirmés (CNSS). Un changement ne modifie pas les bulletins déjà faits.',
      [field('Part salariale (retenue sur le salarié), en %', f.e), field('Part patronale (à la charge de l\'entreprise), en %', f.r), field('Plafond mensuel de la base de cotisation (0 = aucun)', f.c)],
      () => post('/api/payroll/settings', { employee_bp: Math.round(f.e.value * 100), employer_bp: Math.round(f.r.value * 100), ceiling: num(f.c.value) }), () => G.toast('Réglages enregistrés.'));
  }

  function employeeForm(e) {
    const f = { name: h('input', { maxlength: 120, required: true, value: e ? e.name : '' }), pos: h('input', { maxlength: 80, value: e ? e.position : '' }), phone: h('input', { maxlength: 30, value: e ? e.phone : '' }),
      sal: h('input', { type: 'number', min: 0, step: 1000, required: true, value: e ? e.base_salary : '' }), hire: h('input', { type: 'date', value: e ? e.hire_date || '' : today() }), note: h('input', { maxlength: 200, value: e ? e.note : '' }) };
    form(e ? `Salarié : ${e.name}` : 'Nouveau salarié', e ? 'Modifier le salaire de base ne change pas les bulletins déjà faits.' : null,
      [field('Nom', f.name), field('Poste', f.pos), field('Téléphone', f.phone), field('Salaire de base mensuel (GNF)', f.sal), field('Date d\'embauche', f.hire), field('Note', f.note)],
      () => post('/api/payroll/employees' + (e ? '/' + e.id : ''), { name: f.name.value, position: f.pos.value, phone: f.phone.value, base_salary: num(f.sal.value), hire_date: f.hire.value || null, note: f.note.value }));
  }

  function slipForm(e, ov) {
    const owed = e.advance_balance;
    const f = { bonus: h('input', { type: 'number', min: 0, step: 1000, value: 0 }), its: h('input', { type: 'number', min: 0, step: 1, value: 0 }), adv: h('input', { type: 'number', min: 0, max: owed, step: 1, value: owed }), note: h('input', { maxlength: 200 }) };
    const out = h('p', { class: 'muted' }), s = ov.settings;
    const calc = () => { const gross = e.base_salary + num(f.bonus.value), base = s.ceiling ? Math.min(gross, s.ceiling) : gross, se = Math.floor((base * s.employee_bp + 5000) / 10000), adv = owed > 0 ? num(f.adv.value) : 0;
      out.textContent = `Brut ${money(gross)} − cotisations ${money(se)} − ITS/retenues ${money(num(f.its.value))}${adv ? ' − avance ' + money(adv) : ''} = net ${money(gross - se - num(f.its.value) - adv)}`; };
    f.bonus.oninput = f.its.oninput = f.adv.oninput = calc; calc();
    form(`Bulletin ${st.month} : ${e.name}`, `Salaire de base ${money(e.base_salary)}. L'impôt sur les salaires (ITS) et les autres retenues se saisissent ici, calculés par vous ou votre comptable.`,
      [field('Primes du mois (GNF)', f.bonus), field('ITS et autres retenues (GNF)', f.its), owed > 0 && field(`Avance à reprendre (GNF, doit encore ${money(owed)})`, f.adv), field('Note', f.note), out].filter(Boolean),
      () => post('/api/payroll/slips', { employee_id: e.id, period: st.month, bonus: num(f.bonus.value), its: num(f.its.value), advance: owed > 0 ? num(f.adv.value) : 0, note: f.note.value }), () => G.toast('Bulletin créé.'));
  }

  function advanceForm(e) {
    const f = { amount: h('input', { type: 'number', min: 1, step: 1, required: true }), method: h('select', {}, ...(G.viewData().methods || []).map((m) => h('option', { value: m }, m))), date: h('input', { type: 'date', value: today(), max: today() }), note: h('input', { maxlength: 200 }) };
    form(`Avance : ${e.name}`, `Cela crée une dépense (caisse, banque ou mobile money suivent). L'avance reste due jusqu'à sa reprise sur un bulletin.${e.advance_balance ? ' Doit déjà ' + money(e.advance_balance) + '.' : ''}`,
      [field('Montant (GNF)', f.amount), field('Mode de paiement', f.method), field('Date', f.date), field('Note', f.note)],
      () => post('/api/payroll/advances', { employee_id: e.id, amount: num(f.amount.value), method: f.method.value, date: f.date.value, note: f.note.value }), (r) => G.toast(`Avance ${r.advance.number} enregistrée.`));
  }

  function leaveForm(e, ov) {
    const f = { kind: h('select', {}, ...ov.leave_kinds.map((k) => h('option', { value: k }, k))), start: h('input', { type: 'date', required: true, value: today() }), end: h('input', { type: 'date', required: true, value: today() }), days: h('input', { type: 'number', min: 1, step: 1, required: true, value: 1 }), note: h('input', { maxlength: 200 }) };
    const span = () => { if (f.start.value && f.end.value) { const n = Math.round((new Date(f.end.value) - new Date(f.start.value)) / 86400000) + 1; if (n > 0) f.days.value = n; } };
    f.start.onchange = f.end.onchange = span;
    form(`Absence : ${e.name}`, 'Simple relevé : l\'application ne calcule ni droits ni solde de congés, et l\'absence ne change pas le bulletin. Corrigez le nombre de jours s\'il ne faut pas compter les jours non travaillés.',
      [field('Type', f.kind), field('Du', f.start), field('Au', f.end), field('Jours d\'absence', f.days), field('Note', f.note)],
      () => post('/api/payroll/leaves', { employee_id: e.id, kind: f.kind.value, start: f.start.value, end: f.end.value, days: num(f.days.value), note: f.note.value }), () => G.toast('Absence enregistrée.'));
  }

  const cancelLink = (w, active, question, url) => w && active && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const reason = prompt(question); if (reason) post(url, { reason }).then(() => render(), (x) => G.toast(x.message)); } }, '✖ Annuler');

  function advanceCard(a, w) {
    return h('div', { class: 'row' + (a.status === 'cancelled' || !a.paid ? ' struck' : '') }, h('span', { class: 'grow' }, `${a.number} · ${a.employee} · ${money(a.amount)} · ${fmtDay(a.advance_date)} · ${a.pay_method}${a.note ? ' · ' + a.note : ''}`),
      a.status === 'cancelled' ? h('span', { class: 'badge bad' }, 'Annulée') : !a.paid && h('span', { class: 'badge bad' }, 'Paiement annulé'),
      cancelLink(w, a.status === 'active', 'Motif de l\'annulation de l\'avance ?', `/api/payroll/advances/${a.id}/cancel`));
  }

  function leaveCard(l, w) {
    return h('div', { class: 'row' + (l.status === 'cancelled' ? ' struck' : '') }, h('span', { class: 'grow' }, `${l.employee} · ${l.kind} · du ${fmtDay(l.start_date)} au ${fmtDay(l.end_date)} · ${l.days} j${l.note ? ' · ' + l.note : ''}`),
      l.status === 'cancelled' && h('span', { class: 'badge bad' }, 'Annulée'),
      cancelLink(w, l.status === 'active', 'Motif de l\'annulation de l\'absence ?', `/api/payroll/leaves/${l.id}/cancel`));
  }

  function payForm(s) {
    const f = { method: h('select', {}, ...(G.viewData().methods || []).map((m) => h('option', { value: m }, m))), date: h('input', { type: 'date', value: today(), max: today() }) };
    form(`Payer ${s.employee}`, `Net à verser : ${money(s.net)}. Cela crée une dépense (caisse, banque ou mobile money suivent).`, [field('Mode de paiement', f.method), field('Date', f.date)],
      () => post(`/api/payroll/slips/${s.id}/pay`, { method: f.method.value, date: f.date.value }), (r) => G.toast(`Payé (dépense ${r.slip.expense_number}).`));
  }

  function slipCard(s) {
    const w = can('payroll.manage');
    return h('article', { class: 'card' + (s.status === 'cancelled' ? ' struck' : '') },
      h('div', { class: 'head' }, h('span', { class: 'name' }, `${s.employee} · ${s.number}`), h('span', { class: 'name' }, money(s.net))),
      h('div', { class: 'muted' }, `Brut ${money(s.gross)} · cotisations salariales ${money(s.social_employee)} · ITS/retenues ${money(s.its)}${s.advance ? ' · avance reprise ' + money(s.advance) : ''} · part patronale ${money(s.social_employer)}`),
      h('div', { class: 'row' }, s.status === 'cancelled' ? h('span', { class: 'badge bad' }, 'Annulé') : s.paid ? h('span', { class: 'badge' }, `Payé le ${fmtDay(s.paid_date)}`) : s.net === 0 ? h('span', { class: 'badge' }, 'Rien à verser') : h('span', { class: 'badge warn' }, 'À payer'), s.cancel_reason && h('span', { class: 'muted' }, 'Motif : ' + s.cancel_reason)),
      h('div', { class: 'links' }, h('a', { href: '#', onclick: (e) => { e.preventDefault(); window.open('/api/payslip/' + s.id, '_blank', 'noopener'); } }, '🖨 Bulletin'),
        w && s.status === 'active' && !s.paid && s.net > 0 && h('a', { href: '#', onclick: (e) => { e.preventDefault(); payForm(s); } }, '💸 Payer'),
        w && s.status === 'active' && h('a', { href: '#', onclick: (e) => { e.preventDefault(); const reason = prompt('Motif de l\'annulation du bulletin ?'); if (reason) post(`/api/payroll/slips/${s.id}/cancel`, { reason }).then(() => render(), (x) => G.toast(x.message)); } }, '✖ Annuler')));
  }

  async function render() {
    view.replaceChildren(h('p', { class: 'muted' }, 'Chargement…'));
    let ov;
    try { ov = await api('/api/payroll?month=' + st.month); } catch (e) { view.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'La paie demande une connexion.' : e.message)); return; }
    const w = can('payroll.manage'), t = ov.totals, byId = new Map(ov.employees.map((e) => [e.id, e]));
    const month = h('input', { type: 'month', value: st.month, 'aria-label': 'Mois', onchange: (e) => { if (e.target.value) { st.month = e.target.value; render(); } } });
    const kpi = (l, v, c) => h('div', { class: 'card kpi' + (c ? ' ' + c : '') }, h('div', { class: 'muted' }, l), h('strong', {}, v));
    view.replaceChildren(h('div', { class: 'row' }, h('h1', { class: 'grow' }, 'Paie'), month, w && h('button', { class: 'secondary', type: 'button', onclick: () => settingsForm(ov.settings) }, 'Réglages'), w && h('button', { class: 'primary', type: 'button', onclick: () => employeeForm(null) }, '＋ Salarié')),
      ov.rates_missing && h('div', { class: 'warn' }, 'Les taux de cotisation sont à zéro : les bulletins n\'ont aucune cotisation. Faites fixer les taux par votre expert-comptable, puis saisissez-les dans « Réglages ». Les montants calculés par l\'application sont une aide, à faire valider.'),
      !ov.rates_missing && h('p', { class: 'muted' }, `Part salariale ${pct(ov.settings.employee_bp)} · part patronale ${pct(ov.settings.employer_bp)}${ov.settings.ceiling ? ' · plafond ' + money(ov.settings.ceiling) : ''}`),
      h('div', { class: 'kpis' }, kpi('Bulletins du mois', String(t.count)), kpi('Salaires bruts', money(t.gross)), kpi('Net à verser', money(t.net)), kpi('Reste à payer', money(t.unpaid_net), t.unpaid_net ? 'bad' : ''), kpi('Coût employeur', money(t.employer_cost)), kpi('Avances à reprendre', money(t.advances_open), t.advances_open ? 'bad' : '')),
      ov.to_generate.length > 0 && w && h('div', { class: 'card' }, h('h2', {}, `Sans bulletin pour ${st.month} (${ov.to_generate.length})`),
        h('div', { class: 'row' }, h('button', { class: 'primary', type: 'button', onclick: () => post('/api/payroll/generate', { period: st.month }).then((r) => { G.toast(`${r.created.length} bulletin(s) créé(s)${r.blocked.length ? ', ' + r.blocked.length + ' bloqué(s) : ' + r.blocked[0].message : ''}${r.with_advance.length ? '. Aucune avance reprise : ' + r.with_advance.join(', ') : ''}.`); render(); }, (x) => G.toast(x.message)) }, 'Préparer tous (salaire de base)')),
        ...ov.to_generate.map((id) => { const e = byId.get(id); return h('div', { class: 'row' }, h('span', { class: 'grow' }, `${e.name} · ${money(e.base_salary)}`), h('button', { class: 'secondary', type: 'button', onclick: () => slipForm(e, ov) }, 'Bulletin avec primes / retenues')); })),
      ov.slips.length ? h('div', { class: 'list two' }, ov.slips.map(slipCard)) : h('p', { class: 'muted' }, ov.employees.length ? 'Aucun bulletin ce mois-ci.' : 'Commencez par ajouter vos salariés, puis préparez les bulletins du mois.'),
      ov.employees.length > 0 && h('div', { class: 'card' }, h('h2', {}, 'Salariés'),
        ...ov.employees.map((e) => h('div', { class: 'row' + (e.active ? '' : ' struck') }, h('span', { class: 'grow' }, `${e.name}${e.position ? ' · ' + e.position : ''} · ${money(e.base_salary)}${e.advance_balance ? ' · doit ' + money(e.advance_balance) : ''}${e.leave_days ? ` · ${e.leave_days} j d'absence en ${st.month.slice(0, 4)}` : ''}${e.active ? '' : ' (inactif)'}`),
          w && e.active && h('button', { class: 'secondary', type: 'button', onclick: () => advanceForm(e) }, 'Avance'),
          w && h('button', { class: 'secondary', type: 'button', onclick: () => leaveForm(e, ov) }, 'Absence'),
          w && h('button', { class: 'secondary', type: 'button', onclick: () => employeeForm(e) }, 'Modifier'),
          w && h('button', { class: 'secondary', type: 'button', onclick: () => post('/api/payroll/employees/' + e.id, { active: !e.active }).then(() => render(), (x) => G.toast(x.message)) }, e.active ? 'Désactiver' : 'Réactiver')))),
      ov.advances.length > 0 && h('div', { class: 'card' }, h('h2', {}, `Avances sur salaire (${st.month.slice(0, 4)})`), ...ov.advances.map((a) => advanceCard(a, w))),
      ov.leaves.length > 0 && h('div', { class: 'card' }, h('h2', {}, `Absences et congés (${st.month.slice(0, 4)})`), ...ov.leaves.map((l) => leaveCard(l, w))));
  }
  tabs.push({ id: 'paie', label: 'Paie', show: () => can('payroll.read') && state.online !== false, render });
})();
