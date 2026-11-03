/* Relances : factures impayées en retard, message prêt à envoyer selon l'ancienneté, journal des relances et promesses de paiement. */
(() => {
  'use strict';
  const { h, field, money, fmtDay, can, state, dlg, view, tabs, api, OfflineError } = G;
  let onlyDue = true;
  const LV = ['', 'ok-box', 'ok-box', 'warn', 'bad'];

  function noteDialog(it, channels, channel) {
    const err = h('div', { class: 'error', hidden: true });
    const sel = h('select', { 'aria-label': 'Canal' }, Object.entries(channels).map(([k, v]) => h('option', { value: k, selected: k === channel }, v)));
    const note = h('input', { type: 'text', maxlength: 300, placeholder: 'Ex. : a dit qu\'il paie vendredi' }), promise = h('input', { type: 'date' });
    dlg.replaceChildren(h('h2', {}, `Noter une relance : ${it.client}`), h('p', { class: 'muted' }, `Facture ${it.number} · ${money(it.balance)} · ${it.days_late} jours de retard`),
      h('form', { onsubmit: async (e) => { e.preventDefault(); err.hidden = true;
        try { await api('/api/dunning', { method: 'POST', body: { sale_id: it.sale_id, channel: sel.value, note: note.value, promise: promise.value || null } }); dlg.close(); G.toast('Relance notée.'); render(); }
        catch (x) { err.textContent = x.message; err.hidden = false; } } },
        field('Canal', sel), field('Note (facultatif)', note), field('Le client a promis de payer le (facultatif)', promise), err,
        h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', onclick: () => dlg.close() }, 'Annuler'), h('button', { class: 'primary', type: 'submit' }, 'Enregistrer'))));
    dlg.showModal();
  }

  const STATE = { a_relancer: ['À relancer', 'bad'], relance: ['Relancé récemment', ''], promesse: ['Paiement promis', 'ok-box'] };

  function card(it, channels) {
    const wa = it.phone ? `https://wa.me/${it.phone.replace('+', '')}?text=${encodeURIComponent(it.message)}` : null, [stLabel, stCls] = STATE[it.state];
    const copy = async () => { try { await navigator.clipboard.writeText(it.message); G.toast('Message copié.'); } catch (e) { G.toast('Copie impossible : sélectionnez le texte.'); } };
    return h('div', { class: 'card' },
      h('div', { class: 'row' }, h('strong', { class: 'grow' }, `${it.client} · ${it.number}`), h('span', { class: stCls }, stLabel)),
      h('div', {}, `${money(it.balance)} · échue le ${fmtDay(it.due_date)} · ${it.days_late} jours de retard · `, h('span', { class: LV[it.level] }, it.level_label)),
      it.last && h('div', { class: 'muted' }, `Dernière relance : ${fmtDay(it.last.ts.slice(0, 10))} par ${it.last.user || '?'} (${channels[it.last.channel] || it.last.channel})${it.last.note ? ' — ' + it.last.note : ''} · ${it.attempts} au total`),
      it.promise && h('div', { class: it.promise_active ? 'ok-box' : 'muted' }, it.promise_active ? `Paiement promis le ${fmtDay(it.promise)}` : `Promesse du ${fmtDay(it.promise)} non tenue`),
      h('p', { class: 'muted' }, it.message),
      can('sales.create') && h('div', { class: 'row' },
        wa && h('a', { class: 'btn', href: wa, target: '_blank', rel: 'noopener noreferrer', onclick: () => setTimeout(() => noteDialog(it, channels, 'whatsapp'), 600) }, '💬 WhatsApp'),
        it.phone && h('a', { class: 'btn', href: 'tel:' + it.phone, onclick: () => setTimeout(() => noteDialog(it, channels, 'appel'), 600) }, '📞 Appeler'),
        h('button', { class: 'secondary', type: 'button', onclick: copy }, 'Copier le message'),
        h('button', { class: 'secondary', type: 'button', onclick: () => noteDialog(it, channels, 'appel') }, 'Noter une relance')));
  }

  async function render() {
    view.replaceChildren(h('p', { class: 'muted' }, 'Chargement…'));
    let d;
    try { d = await api('/api/dunning' + (onlyDue ? '?only_due=true' : '')); } catch (e) { view.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'Les relances demandent une connexion.' : e.message)); return; }
    const t = d.totals;
    view.replaceChildren(h('h1', {}, 'Relances des impayés'),
      h('p', { class: 'muted' }, 'Factures à crédit échues et non soldées. Le message s\'adapte à l\'ancienneté du retard : rappel amical, relance, relance ferme, dernier avertissement. L\'application n\'envoie rien toute seule : envoyez le message, puis notez la relance (qui, quand, quel canal, date de paiement promise).'),
      h('div', { class: 'kpis' }, h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Factures en retard'), h('strong', {}, String(t.count))), h('div', { class: 'card kpi bad' }, h('div', { class: 'muted' }, 'Montant en retard'), h('strong', {}, money(t.amount))),
        h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'À relancer maintenant'), h('strong', {}, `${t.to_do} (${money(t.to_do_amount)})`)), h('div', { class: 'card kpi' }, h('div', { class: 'muted' }, 'Paiements promis'), h('strong', {}, money(t.promised_amount)))),
      h('label', { class: 'row' }, h('input', { type: 'checkbox', checked: onlyDue, onchange: (e) => { onlyDue = e.target.checked; render(); } }), 'Seulement celles à relancer maintenant'),
      d.items.length ? h('div', { class: 'list' }, d.items.map((it) => card(it, d.channels))) : h('div', { class: 'ok-box' }, onlyDue ? 'Rien à relancer pour l\'instant 👍' : 'Aucune facture en retard 👍'));
  }
  tabs.push({ id: 'relances', label: 'Relances', show: () => can('sales.read_all') && state.online !== false, render });
})();
