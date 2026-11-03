"""Liste de contrôle « prêt pour la vraie saisie ? » : l'état réel de l'installation, avec pour chaque point ce qu'il faut faire.

Lecture seule : rien n'est modifié. Niveaux : « ok » (en place), « warn » (à améliorer), « todo » (à faire avant de saisir de vraies données).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import accounting, audit, backup, chiffrement, config, notify
from .sales_ops import settings_get


def _item(key: str, level: str, title: str, detail: str, fix: str = "") -> dict:
    return {"key": key, "level": level, "title": title, "detail": detail, "fix": fix}


def _age_days(ts: float) -> int:
    return (datetime.now(timezone.utc) - datetime.fromtimestamp(ts, timezone.utc)).days


def compute(c: sqlite3.Connection, host: str = "", scheme: str = "http") -> dict:
    items: list[dict] = []
    add = lambda *a: items.append(_item(*a))
    hosted_railway = host.lower().split(":", 1)[0].endswith(".up.railway.app") or str(config.data_dir()).replace("\\", "/").startswith("/data/")
    # mode démonstration
    if config.demo_mode(): add("demo", "todo", "Mode démonstration actif", "Les données sont fictives et l'entrée se fait sans identifiant.", "Lancez l'application normalement (lancer.bat), pas lancer_demo.bat.")
    # entreprise
    st = settings_get(c); missing = [lab for k, lab in (("company_name", "nom"), ("address", "adresse"), ("phone", "téléphone"), ("tax_id", "numéro fiscal (NIF)")) if not (st.get(k) or "").strip()]
    if not (st.get("company_name") or "").strip(): add("company", "todo", "Identité de l'entreprise", "Le nom n'est pas renseigné : il figure sur les factures et les rapports.", "Mon compte > Réglages de l'entreprise.")
    elif missing: add("company", "warn", "Identité de l'entreprise incomplète", "Manque : " + ", ".join(missing) + ".", "Mon compte > Réglages de l'entreprise (ces informations figurent sur les factures).")
    else: add("company", "ok", "Identité de l'entreprise", "Nom, adresse, téléphone et numéro fiscal sont renseignés.")
    # TVA
    rate = accounting.get_settings(c)["sales_tax_rate"]
    add("tax", "ok" if rate else "warn", "Taux de TVA sur les ventes", f"{rate} %" if rate else "Le taux est à 0 % : aucune TVA n'est calculée sur les ventes.", "" if rate else "Si l'entreprise est assujettie, indiquez le taux validé par votre expert (Compta > Réglages). Voir DOSSIER_EXPERT_COMPTABLE.md.")
    # équipe et double authentification
    users = c.execute("SELECT role, active, totp_on FROM users WHERE active=1").fetchall()
    owners = [u for u in users if u["role"] == "owner"]
    if owners and all(u["totp_on"] for u in owners): add("owner_2fa", "ok", "Double authentification du propriétaire", "Activée.")
    else: add("owner_2fa", "warn", "Double authentification du propriétaire", "Pas activée : un mot de passe volé suffirait pour tout voir.", "Mon compte > Double authentification (application sur le téléphone).")
    if len(users) <= 1: add("team", "warn", "Comptes de l'équipe", "Seul le compte propriétaire existe.", "Équipe > créez un compte personnel par personne (caissier, commercial…) : le journal saura qui a fait quoi.")
    else: add("team", "ok", "Comptes de l'équipe", f"{len(users)} comptes actifs.")
    # chiffrement
    ch = chiffrement.statut()
    if ch["postgresql"]: add("encryption", "ok", "Base de données", "PostgreSQL : la protection du disque dépend du serveur de base.")
    elif ch["actif"] and ch["fichier_chiffre"]: add("encryption", "ok", "Chiffrement de la base", f"Actif (mode {ch['mode']}).")
    else: add("encryption", "warn", "Chiffrement de la base", "La base est en clair sur le disque.", "Voir la section « Sauvegarde, chiffrement et clé de secours » de l'aide.")
    if ch.get("originaux_a_supprimer"): add("clear_copies", "warn", "Copies en clair à supprimer", f"{len(ch['originaux_a_supprimer'])} fichier(s) non chiffré(s) subsistent dans « clair_a_supprimer ».", "Après avoir imprimé la clé de secours et vérifié que tout marche, supprimez ce dossier vous-même.")
    # sauvegardes
    files = [p for p in backup.backup_dir().iterdir() if backup._NAME.match(p.name)]
    if not files: add("backup", "todo", "Sauvegardes", "Aucune sauvegarde n'a encore été faite.", "Mon compte > Sauvegarde > « Sauvegarder maintenant ».")
    else:
        age = _age_days(max(p.stat().st_mtime for p in files))
        add("backup", "ok" if age <= 2 else "warn", "Sauvegardes", f"{len(files)} sauvegarde(s), la dernière date de {age} jour(s)." + ("" if age <= 2 else " La sauvegarde automatique ne tourne peut-être pas (ordinateur éteint ?)."), "" if age <= 2 else "Gardez l'ordinateur allumé la nuit ou faites une sauvegarde manuelle.")
    ex = backup.extra_status()
    if not backup.extra_dir():
        if hosted_railway: add("extra_copy", "warn", "Copie hors de Railway", "Aucune copie téléchargée en dehors de l'environnement Railway.", "Mon compte > Télécharger une sauvegarde complète, puis conservez le fichier dans un emplacement sûr séparé.")
        else: add("extra_copy", "warn", "Copie hors de l'ordinateur", "Aucune : un vol ou une panne emporterait aussi les sauvegardes.", "Mon compte > Copie de secours hors de cet ordinateur (clé USB, disque externe).")
    elif ex and not ex.get("ok"): add("extra_copy", "warn", "Copie hors de l'ordinateur", "La dernière copie a échoué : " + str(ex.get("message", "")), "Vérifiez que la clé USB ou le dossier est branché et accessible.")
    else: add("extra_copy", "ok", "Copie hors de l'ordinateur", "Active" + (f", dernière copie le {ex['ts'][:10]}." if ex else ", en attente de la prochaine sauvegarde."))
    # rappels e-mail
    n = notify.load(c)
    add("notify", "ok" if n["enabled"] else "warn", "Résumé du jour par e-mail", "Activé." if n["enabled"] else "Pas activé (facultatif mais utile : retards, stock bas, échéances).", "" if n["enabled"] else "Mon compte > Rappels par e-mail.")
    # journal : chaîne et sceaux
    chain = audit.verify(c)
    if not chain.get("chain_ok", chain["ok"]): add("audit", "todo", "Intégrité du journal", f"ALTÉRATION détectée à l'entrée n° {chain['broken_at']}.", "Restaurez une sauvegarde saine et cherchez la cause avant de continuer.")
    else:
        sc = chain["seals"]
        if not sc["ok"]: add("audit", "todo", "Sceaux du journal", "Un sceau signé ne correspond plus au journal : l'historique a été réécrit.", "Ne continuez pas ; restaurez une sauvegarde saine.")
        elif not sc["public_key"]: add("audit", "warn", "Sceaux du journal", "Aucune clé de signature : le journal est chaîné mais pas signé hors du serveur.", "Journal > Sceaux (clé privée sur une clé USB).")
        elif sc["entries_since_last"] > 500: add("audit", "warn", "Sceaux du journal", f"{sc['entries_since_last']} entrées depuis le dernier sceau.", "Faites un sceau (Journal > Sceaux), par exemple après chaque clôture mensuelle.")
        else: add("audit", "ok", "Journal", f"Intact, {sc['count']} sceau(x) signé(s) hors du serveur.")
    # réseau
    local = (host or "").split(":")[0] in ("", "localhost", "127.0.0.1", "[::1]", "::1")
    if not local and scheme != "https": add("network", "warn", "Connexion non chiffrée", "L'application est ouverte par le réseau en HTTP simple : le Wi-Fi n'est pas chiffré, et l'installation sur téléphone et le mode hors connexion sont impossibles.", "Réseau de confiance seulement, ou HTTPS (voir DEPLOIEMENT.md).")
    else: add("network", "ok", "Connexion", "Locale." if local else "HTTPS.")
    order = {"todo": 0, "warn": 1, "ok": 2}; items.sort(key=lambda i: order[i["level"]])
    counts = {k: sum(1 for i in items if i["level"] == k) for k in order}
    return {"items": items, "counts": counts, "ready": counts["todo"] == 0, "total": len(items)}
