// Onglet « Aide » : visible par tous les rôles. Le texte vient du serveur (GET /api/help) ; la dernière version reçue est gardée sur l'appareil pour rester lisible hors connexion.
// Aucun style ni script en ligne (politique de sécurité de la page) : tout passe par des classes de style.css.
(() => {
  const G = window.G, { h, view, state, api, tabs } = G;
  const KEY = 'gestion_aide_v1';

  const fromCache = () => { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { return null; } };
  const toCache = (d) => { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch { /* stockage indisponible : l'aide se lira seulement en ligne */ } };

  function section(s, i, open) {
    const d = h('details', { class: 'help-section', id: 'aide-' + s.id }, h('summary', {}, `${i + 1}. ${s.title}`), h('p', {}, s.intro), h('ul', {}, ...s.items.map((x) => h('li', {}, x))),
      s.note ? h('p', { class: 'help-note' }, h('strong', {}, 'À retenir : '), s.note) : null);
    if (open) d.open = true;
    return d;
  }

  async function render() {
    let data = null, offline = false;
    try { data = await api('/api/help'); toCache(data); } catch { data = fromCache(); offline = true; }
    if (!data) { view.replaceChildren(h('h1', {}, 'Aide'), h('p', { class: 'muted' }, 'L\'aide demande une connexion la première fois.')); return; }
    const list = h('div', { class: 'help' }, ...data.sections.map((s, i) => section(s, i, i === 0)));
    const toc = h('p', { class: 'help-toc' }, ...data.sections.flatMap((s, i) => [h('a', { href: '#aide-' + s.id, class: 'help-link', onclick: (e) => { e.preventDefault(); const t = document.getElementById('aide-' + s.id); t.open = true; t.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }, s.title), ' · ']).slice(0, -1));
    const expand = h('button', { type: 'button', class: 'secondary', onclick: () => list.querySelectorAll('details').forEach((d) => { d.open = true; }) }, 'Tout ouvrir');
    const collapse = h('button', { type: 'button', class: 'secondary', onclick: () => list.querySelectorAll('details').forEach((d) => { d.open = false; }) }, 'Tout fermer');
    view.replaceChildren(h('h1', {}, 'Aide'), h('p', { class: 'muted' }, 'Prise en main de Gestion libre. Le même texte existe dans le fichier GUIDE_UTILISATEUR.md.' + (offline ? ' (Version enregistrée sur cet appareil : vous êtes hors connexion.)' : '')),
      h('div', { class: 'row' }, expand, collapse), toc, list);
  }

  tabs.push({ id: 'help', label: 'Aide', render });
})();
