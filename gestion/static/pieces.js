/* Pièces jointes : photo d'un reçu, facture PDF d'un fournisseur, bon signé… rattachées à un document. Demande une connexion. */
(() => {
  'use strict';
  const { h, field, fmtDate, state, dlg, toast, api, OfflineError } = G;
  const MAX = 5 * 1024 * 1024, fmtSize = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' Mo' : Math.max(1, Math.round(n / 1024)) + ' Ko');

  // Une photo de téléphone pèse souvent 4 à 8 Mo : on la réduit (1600 px au plus, JPEG) avant l'envoi.
  async function prepare(file) {
    if (!file.type.startsWith('image/') || file.size < 800 * 1024) return file;
    try {
      const bmp = await createImageBitmap(file), k = Math.min(1, 1600 / Math.max(bmp.width, bmp.height)), cv = document.createElement('canvas');
      cv.width = Math.round(bmp.width * k); cv.height = Math.round(bmp.height * k); cv.getContext('2d').drawImage(bmp, 0, 0, cv.width, cv.height);
      const blob = await new Promise((res) => cv.toBlob(res, 'image/jpeg', 0.8)); return blob && blob.size < file.size ? new File([blob], file.name.replace(/\.\w+$/, '') + '.jpg', { type: 'image/jpeg' }) : file;
    } catch { return file; }
  }
  async function upload(entity, id, file) {
    const f = await prepare(file);
    if (f.size > MAX) throw new Error('Fichier trop lourd (5 Mo au plus).');
    const r = await fetch(`/api/attachments?entity=${encodeURIComponent(entity)}&id=${encodeURIComponent(id)}&name=${encodeURIComponent(file.name)}`, { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'gestion', 'X-Device-Id': G.deviceId, 'Content-Type': 'application/octet-stream' }, body: f });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'Envoi refusé (' + r.status + ').');
    return data.attachment;
  }

  G.attachDialog = async function (entity, id, title, writable, back) {
    if (!state.online) { toast('Les pièces jointes demandent une connexion.'); return; }
    const box = h('div', {}), err = h('div', { class: 'error', hidden: true });
    const file = h('input', { type: 'file', accept: 'image/*,application/pdf', 'aria-label': 'Ajouter une pièce', onchange: async () => { const f = file.files[0]; if (!f) return; err.hidden = true; try { await upload(entity, id, f); file.value = ''; toast('Pièce ajoutée.'); load(); } catch (e) { err.textContent = e.message; err.hidden = false; } } });
    async function load() {
      let list; try { list = (await api(`/api/attachments?entity=${entity}&id=${id}`)).attachments; } catch (e) { box.replaceChildren(h('div', { class: 'error' }, e instanceof OfflineError ? 'Connexion nécessaire.' : e.message)); return; }
      box.replaceChildren(list.length ? h('div', { class: 'list' }, list.map((a) => h('div', { class: 'row' },
        a.mime.startsWith('image/') ? h('img', { src: '/api/attachments/' + a.id, alt: a.name, class: 'thumb' }) : h('span', { class: 'badge' }, 'PDF'),
        h('span', { class: 'grow' }, h('a', { href: '/api/attachments/' + a.id, target: '_blank', rel: 'noopener' }, a.name), h('div', { class: 'muted' }, `${fmtSize(a.size)} · ${fmtDate(a.created_at)}${a.uploaded_by ? ' · ' + a.uploaded_by : ''}`)),
        writable && h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const reason = prompt('Motif du retrait de « ' + a.name + ' » (la pièce reste dans la base, inaccessible, et le retrait est journalisé) :'); if (!reason || reason.trim().length < 3) return;
          try { await api(`/api/attachments/${a.id}/remove`, { method: 'POST', body: { reason: reason.trim() } }); toast('Pièce retirée.'); load(); } catch (x) { toast(x.message); } } }, 'Retirer')))) : h('p', { class: 'muted' }, 'Aucune pièce jointe.'));
    }
    dlg.replaceChildren(h('h2', {}, 'Pièces jointes'), h('div', { class: 'muted' }, title), box, writable && h('div', { class: 'card' }, field('Ajouter une photo ou un PDF (5 Mo au plus ; sur téléphone, « prendre une photo » est proposé)', file)), err,
      h('div', { class: 'row' }, back && h('button', { class: 'secondary', type: 'button', onclick: back }, 'Retour'), h('button', { class: 'primary', type: 'button', onclick: () => dlg.close() }, 'Fermer')));
    if (!dlg.open) dlg.showModal();
    load();
  };
})();
