# Retour à l’École 42 : essais sur place

Une version précédente de FortyTchat a été testée en cluster par son créateur. Cette fiche sert à vérifier les améliorations 1.2.0 sur place et à consigner les résultats. Les essais automatisés locaux sont décrits dans `VALIDATION.md`.

## Préparer deux postes

1. Copier le projet sur deux PC Ubuntu distincts et lancer `sh ./install.sh`, ou `python3 main.py` si PySide6 est disponible.
2. Vérifier l’apparition discrète de l’icône. Si elle est absente, récupérer la fenêtre réduite dans le dock ou depuis le menu. Ne pas installer d’extension système ni modifier GNOME sans autorisation.
3. Relever IPv4, interface, masque, broadcast et ports avec les commandes ci-dessous. Les réglages réseau restent hors de l’interface de FortyTchat ; aucune IP n’est à renseigner dans l’application.
4. Vérifier que Docker et `virbr0` n’ont pas été choisis à la place de l’Ethernet physique active.

La mesure connue `enp4s0f0`, `10.12.10.7/16`, broadcast calculé `10.12.255.255` ne concerne qu’un poste. Elle ne prouve ni un domaine de broadcast unique ni l’autorisation de TCP inter-postes.

## Découverte et transport

- Attendre 30 secondes et vérifier la liste **En ligne** sur les deux postes. Les deux pseudos devraient apparaître si le lien autorise la découverte.
- Noter séparément : « présence UDP reçue », « connexion TCP réussie », « message affiché ». Une ouverture locale ne prouve pas les deux dernières étapes.
- Envoyer dans #général dans les deux sens ; ouvrir ensuite une conversation privée. Un troisième poste peut vérifier qu’il ne reçoit pas le message privé.
- Quitter complètement sur un poste : le départ doit apparaître rapidement. En cas d’arrêt brutal, attendre environ 90–117 secondes pour l’expiration.
- Relancer : nouvelle session, aucune conversation restaurée et aucun ancien message transféré.

Si aucun pair n’apparaît, vérifier sans sudo :

```sh
hostname -I
ip -br addr
ip -4 route
ss -tuln
python3 main.py --diagnose
```

Ne pas scanner le `/16`, modifier le pare-feu ou réutiliser un port de service système. Réaliser les mêmes essais sur des postes de zones différentes seulement avec les autorisations appropriées.

## Interface et fonctions

- Pseudo modifié immédiatement visible à distance ; collision de deux pseudos résolue par suffixe.
- Emoji inséré et affiché ; absence éventuelle de police couleur signalée par libellé dans le sélecteur.
- Fermer la fenêtre laisse le programme actif ; clic sur l’icône ou lancement via le menu la réouvre.
- Cacher FortyTchat, recevoir un message privé : icône **rouge** avec compteur **1** ; doublecliquer et consulter la conversation : disparition du compteur correspondant et retour au noir quand tout est lu. Vérifier le rendu réel dans le thème GNOME installé.
- Recevoir un Wizz : vibration de 2,8 secondes, défilement au dernier message et absence de son ; en tester un autre destinataire dans les 42 minutes : envoi refusé. Sous Wayland, la mise au premier plan peut être refusée ; le contenu doit tout de même signaler l’événement.
- Invitation : accepter, refuser et fermer la popup lors de sessions distinctes, puis vérifier que la quatrième demande successive est bloquée pendant 5 minutes. Vérifier le retour à l’expéditeur et l’expiration après 2 minutes.
- Masquer une session, activer DND, désactiver Wizz. Les messages restent reçus silencieusement en DND ; les pauses et leurs réponses restent affichées. Vérifier bleu pour oui et rouge pour non.
- Envoyer un fichier en privé puis dans le général : refuser, accepter, choisir un emplacement et annuler pendant le transfert. Vérifier le contenu reçu, l’absence d’écrasement et de téléchargement automatique.
- Vérifier l’absence de son de notification dans l’environnement Ubuntu concerné, y compris si le serveur de notifications ignore des options.

## Démarrage de session

1. Vérifier la case activée et l’existence de l’entrée XDG personnelle, avec le bon Python absolu.
2. Fermer la session Ubuntu puis se reconnecter. FortyTchat doit démarrer réduit et sans historique.
3. Désactiver l’option, recommencer la déconnexion/reconnexion : aucun lancement automatique.
4. Relancer et réinstaller : l’option désactivée doit le rester.
5. Si XDG Autostart est ignoré par une politique de l’établissement, le signaler ; ne pas chercher à contourner cette politique.

## Si le LAN est filtré

Le diagnostic doit rester explicite : aucune connexion réussie n’est simulée. Demander une machine relais autorisée et accessible, un nom DNS stable et un certificat TLS approuvé. Distribuer ce déploiement via `network.json`, puis répéter les essais en `mode: relay` et `mode: auto`.

Un test réussi sur deux machines ne valide pas à lui seul 200 postes ni plusieurs VLAN. Augmenter progressivement le nombre de participants avec l’accord de l’école ; comparer débit, déconnexions, latence et charge du poste relais.

## Relevé à compléter

| Vérification | Date / postes | Résultat réel |
| --- | --- | --- |
| IPv4/interface/masque | À compléter | À vérifier en 1.2.0 |
| Découverte même zone | À compléter | À vérifier en 1.2.0 |
| Découverte entre zones | À compléter | À vérifier en 1.2.0 |
| TCP général et privé | À compléter | À vérifier en 1.2.0 |
| TLS/relais autorisé | À compléter | Aucun déploiement présumé |
| Badge et barre Ubuntu | À compléter | À vérifier sur la session école en 1.2.0 |
| Wizz/Wayland et notifications silencieuses | À compléter | À vérifier sur la session école en 1.2.0 |
| Autostart après connexion | À compléter | À vérifier sur la session école en 1.2.0 |
