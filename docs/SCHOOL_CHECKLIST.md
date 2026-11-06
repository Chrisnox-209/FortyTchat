# Retour à l’École 42 : essais sur place

Une version précédente de FortyTchat a été testée en cluster par son créateur. Cette fiche sert à vérifier les améliorations 1.8.1 sur place et à consigner les résultats. Les essais automatisés locaux sont décrits dans `VALIDATION.md`.

## Préparer deux postes

1. Copier le projet sur deux PC Ubuntu distincts et lancer `sh ./install.sh`, ou `python3 main.py` si PySide6 et PyNaCl sont disponibles.
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
- Répondre à un message, cliquer sur la citation et vérifier le retour au message. Ajouter, remplacer et retirer une réaction dans le général et en privé.
- Écrire dans une conversation : trois points apparaissent chez le destinataire puis disparaissent après envoi, inactivité ou fermeture. Vérifier qu’aucun brouillon n’est transmis.
- Emoji inséré et affiché ; absence éventuelle de police couleur signalée par libellé dans le sélecteur.
- Fermer la fenêtre laisse le programme actif ; clic sur l’icône ou lancement via le menu la réouvre.
- Cacher FortyTchat, recevoir un message privé : icône **rouge** avec compteur **1** ; doublecliquer et consulter la conversation : disparition du compteur correspondant et retour au noir quand tout est lu. Vérifier le rendu réel dans le thème GNOME installé.
- Recevoir un Wizz : vibration de 2,8 secondes, défilement au dernier message et absence de son ; en tester un autre destinataire dans les 42 minutes : envoi refusé. Sous Wayland, la mise au premier plan peut être refusée ; le contenu doit tout de même signaler l’événement.
- Invitation : accepter, refuser et fermer la popup lors de sessions distinctes, puis vérifier que la quatrième demande successive est bloquée pendant 5 minutes. Vérifier le retour à l’expéditeur et l’expiration après 2 minutes.
- Masquer une session, activer DND, désactiver Wizz. Les messages restent reçus silencieusement en DND ; les pauses et leurs réponses restent affichées. Vérifier bleu pour « yessss !! » et rouge pour « Non je bosse !! ».
- Créer une notification avec ✦, essayer les onze animations, Oui, Non et une réponse personnalisée. Préparer un modèle hors connexion dans les paramètres, puis vérifier sa réutilisation.
- En DND, une notification personnalisée est refusée, une pause classique reste affichée et peut recevoir une réponse personnalisée.
- Envoyer un fichier en privé puis dans le général : refuser, accepter, choisir un emplacement et annuler pendant le transfert. Vérifier le contenu reçu, l’absence d’écrasement et de téléchargement automatique.
- Vérifier l’absence de son de notification dans l’environnement Ubuntu concerné, y compris si le serveur de notifications ignore des options.

## Chiffrement et identité

- Mettre à jour les deux clients et le relais éventuel en 1.7.0 minimum. Comparer les empreintes en personne via le cadenas, puis vérifier le statut **Identité vérifiée** sur chacun des postes.
- Avec le client 1.8.1, vérifier que les empreintes et le champ de comparaison sont visibles, sans masquage. Le dialogue doit expliquer leur caractère public et leur rôle contre l’usurpation d’identité ; aucun changement du chiffrement n’est attendu.
- Quitter puis relancer l’un des clients : conserver son pseudo, ses amis, sa clé d’identité et la vérification, avec un nouveau SID et une nouvelle clé de session. Aucun historique réseau ne doit arriver.
- Vérifier les messages privés, le général, les fichiers et les cartes via les deux routes autorisées. Le général reste lisible par tous ses participants.
- Pour un essai de remplacement de clé, employer uniquement des profils de développement jetables : la session modifiée doit rester bloquée jusqu’à comparaison de sa nouvelle empreinte. Ne pas supprimer les clés du profil personnel.
- Une capture réseau autorisée peut voir les présences, avatars, IP et enveloppes chiffrées ; elle ne doit pas révéler le texte des messages, les noms ou le contenu des pièces jointes. Un essai local ne remplace pas un audit indépendant du protocole.

## Envoyé, livré et vu

- Envoyer un message privé sans ouvrir la conversation chez le destinataire : deux coches grises après réception, pas de « Vu ».
- Ouvrir la conversation chez le destinataire : deux coches bleues. Vérifier aussi une fenêtre minimisée, inactive et un nouveau message reçu pendant une lecture plus haut dans le tchat.
- Désactiver **Partager les confirmations de lecture**, recevoir un message visible : rester à « Livré » chez l’auteur. Réactiver puis consulter : « Vu » peut être confirmé ; les anciennes confirmations déjà envoyées ne sont pas retirées.
- Dans le général, vérifier les compteurs par destinataire et les coches bleues seulement après lecture par tous les destinataires de l’envoi. Un nouveau participant ne reçoit aucun ancien message ni ancien statut.
- Essayer un client chiffré 1.5.0 : messages et livraison disponibles, lecture indisponible. Aucun repli en clair.

## Réponses et doubleclic sur les postes de l’école

- Créer une carte avec un seul bouton Oui, une avec seulement Non, une avec un bouton Autre préparé (texte + emoji), et une avec Fermer uniquement. Vérifier le libellé exact, le retour bleu/rouge et l’absence de réponse à une simple fermeture.
- Activer/désactiver la réponse libre, enregistrer un modèle puis le recharger. Vérifier les choix chez le destinataire et un aperçu fidèle.
- Sur le poste où un clic ouvre le menu : un premier clic garde le menu ; deux clics rapides doivent ouvrir le tchat si les événements du menu sont transmis à Qt. Tester aussi Ouvrir FortyTchat, une action de statut, le clic droit et le clic milieu.
- Fermer rapidement le menu avec Échap : si cela ouvre le tchat involontairement, désactiver la compatibilité doubleclic GNOME dans Général. Noter le nom/version de l’extension et les événements observés ; les tests locaux des signaux Qt ne garantissent pas son comportement.
- Vérifier la mise au premier plan sur X11/Wayland, sans modifier les extensions de l’école.

## Amis depuis la barre et notifications Ubuntu

- Ajouter un ami, puis fermer la fenêtre principale. Clic droit sur l’icône FT : vérifier les groupes en ligne et hors ligne, leur effectif et le pseudo courant.
- Ouvrir le sous-menu d’un ami connecté, puis **Envoyer une popup…**. Préparer texte, emoji et réponses ; vérifier la réception sans ouverture préalable du tchat. Essayer aussi le clic droit sur l’ami dans la liste du tchat et **Discuter…** dans le menu de barre.
- Quitter sur le poste ami : la popup devient indisponible. Relancer après plus de cinq secondes d’absence, et plus d’une minute depuis la dernière alerte : vérifier une seule bannière Ubuntu avec le pseudo actuel, sans son. Renommer l’ami ne doit pas produire une nouvelle alerte.
- Désactiver **Notifier la connexion de mes amis**, puis tester DND séparément : pas de bannière ni de rattrapage en réactivant. Au démarrage de l’application, les cinq premières secondes de découverte restent silencieuses.
- Relever la version de GNOME/AppIndicator, l’apparence des sous-menus, le placement de la bannière et le mode Ne pas déranger d’Ubuntu. Aucun placement n’est imposé au bureau.

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
| IPv4/interface/masque | À compléter | À vérifier en 1.8.1 |
| Découverte même zone | À compléter | À vérifier en 1.8.1 |
| Découverte entre zones | À compléter | À vérifier en 1.8.1 |
| TCP général et privé | À compléter | À vérifier en 1.8.1 |
| Empreintes comparées, identité conservée | À compléter | À vérifier en 1.8.1 |
| Contenu chiffré TCP et relais | À compléter | À vérifier en 1.8.1 |
| TLS/relais autorisé | À compléter | Aucun déploiement présumé |
| Amis de barre et alertes de connexion | À compléter | À vérifier sur la session école en 1.8.1 |
| Badge et barre Ubuntu | À compléter | À vérifier sur la session école en 1.8.1 |
| Wizz/Wayland et notifications silencieuses | À compléter | À vérifier sur la session école en 1.8.1 |
| Autostart après connexion | À compléter | À vérifier sur la session école en 1.8.1 |
