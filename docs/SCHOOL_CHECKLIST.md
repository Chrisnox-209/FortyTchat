# Retour à l’École 42 : essais sur place

Ces essais nécessitent les PC et les autorisations réelles du cluster. Les résultats à domicile ne les remplacent pas.

## Préparer deux postes

1. Copier le projet sur deux PC Ubuntu distincts et lancer `sh ./install.sh`, ou `python3 main.py` si PySide6 est disponible.
2. Vérifier l’apparition discrète de l’icône. Si elle est absente, récupérer la fenêtre réduite dans le dock ou depuis le menu. Ne pas installer d’extension système ni modifier GNOME sans autorisation.
3. Ouvrir les paramètres réseau sur chacun et relever IPv4, interface, masque, broadcast et ports. Aucun IP ne doit être renseigné dans l’application.
4. Vérifier que Docker et `virbr0` n’ont pas été choisis à la place de l’Ethernet physique active.

La mesure connue `enp4s0f0`, `10.12.10.7/16`, broadcast calculé `10.12.255.255` ne concerne qu’un poste. Elle ne prouve ni un domaine de broadcast unique ni l’autorisation de TCP inter-postes.

## Découverte et transport

- Attendre 30 secondes, puis utiliser **Tester le réseau** sur les deux postes. Les deux pseudos devraient apparaître si le lien autorise la découverte.
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
- Cacher FortyTchat, recevoir un message privé : badge **1** sur l’icône ; cliquer et consulter la conversation : disparition du badge correspondant. Vérifier le rendu réel dans le thème GNOME installé.
- Recevoir un Wizz : effet visuel sans son ; en tester un autre destinataire dans les 42 minutes : envoi refusé. Sous Wayland, la mise au premier plan peut être refusée ; le contenu doit tout de même signaler l’événement.
- Invitation : accepter, refuser et fermer la popup lors de sessions distinctes, en respectant 60 secondes entre propositions. Vérifier le retour à l’expéditeur et l’expiration après 2 minutes.
- Masquer une session, activer DND, désactiver Wizz : aucun popup/effet indésirable. Les messages restent reçus silencieusement en DND.
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
| IPv4/interface/masque | À compléter | Non testé à l’école |
| Découverte même zone | À compléter | Non testé à l’école |
| Découverte entre zones | À compléter | Non testé à l’école |
| TCP général et privé | À compléter | Non testé à l’école |
| TLS/relais autorisé | À compléter | Aucun déploiement présumé |
| Badge et barre Ubuntu | À compléter | Non testé sur la session école |
| Wizz/Wayland et notifications silencieuses | À compléter | Non testé sur la session école |
| Autostart après connexion | À compléter | Non testé sur la session école |
