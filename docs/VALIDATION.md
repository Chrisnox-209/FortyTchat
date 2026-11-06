# Validation locale de FortyTchat

Validation initiale le **5 novembre 2026**, étendue à la version **1.4.0 le 6 novembre 2026** dans l’environnement de développement. Python 3.14.4 et Qt/PySide6 6.12.0 pour la validation 1.4.0. Les validations antérieures utilisaient aussi Qt 6.10.2. Le créateur signale un essai réussi d’une version précédente en cluster. Les résultats ci-dessous concernent les tests locaux, sans mesure indépendante des nouveautés 1.4.0 à l’école.

## Résultats

- Suite automatisée actualisée : **114 tests réussis** en **39,16 s** le 6 novembre 2026, couvrant les fonctionnalités 1.4.0 et les régressions des versions précédentes.
- Charge du relais sur loopback : **200 clients réels asyncio**, roster complet, **199 réceptions uniques** pour un message général, aucune réception en double, en **1,13 s** pour cette exécution. Le test ne mesure pas la charge de 200 utilisateurs envoyant continuellement en parallèle.
- Découverte UDP multicast réelle entre deux clients locaux, puis livraison TCP et annonce de départ : réussies.
- ACK direct supprimé après réception : une nouvelle tentative, un seul message affiché.
- TCP : général, message privé, texte Unicode, trames segmentées, trames invalides, source inconnue et fermeture incomplète vérifiés.
- Relais à 40 clients : diffusion, confidentialité du routage privé, refus d’expéditeur falsifié, limites Wizz et anti-spam vérifiés.
- Redémarrage du relais : reconnexion automatique ; un nouveau client ne reçoit aucun ancien message. La limite Wizz survit à la reconnexion d’une même session tant que le relais reste actif.
- Identités : session temporaire, collision de pseudo résolue sans changer l’ID, changement de pseudo propagé, avatar validé, choix conservé et numéro d’avatar transmis par UDP et par le handshake TCP.
- Réception : masquage, DND, Wizz désactivé, invitations liées à un expéditeur et expiration vérifiés.
- Configuration et XDG : premier lancement, sauvegarde atomique à permissions restreintes, état réel du fichier, désactivation conservée, entrée supprimée non recréée et configuration endommagée vérifiés.
- Installation dans des chemins XDG temporaires avec espaces et caractère `$` : copie, lanceur `--version`, entrée de menu validée par `desktop-file-validate`, réinstallation sans réactiver un choix désactivé.
- Interface Qt hors écran : envoi clavier, texte brut, emojis/recherche, thème clair, fermeture réduite, **clic sur une personne en ligne → fenêtre privée indépendante**, réponse TCP depuis cette fenêtre, fermeture/réouverture dans la même session et badge **1** puis effacement à la consultation. Le sélecteur propose 24 avatars, un mode automatique et l’import d’une image personnelle.
- Non-persistance : aucune conversation dans les fichiers de configuration ; aucun stockage de conversation dans les services réseau ; pièces jointes enregistrées uniquement après consentement ; nouveau processus sans historique ; cache en mémoire abandonné à la fermeture complète.

Les tests d’images personnelles vérifient aussi : les trois formats annoncés (PNG, JPEG, WebP), l’aperçu et l’annulation, le zoom et le recadrage, le chargement hors du thread graphique, la fermeture pendant le chargement, la suppression des métadonnées, la sauvegarde de la seule miniature personnelle, le rendu chez un destinataire, l’absence de sauvegarde des images reçues, la découverte UDP suivie du transfert TCP, le relais et la reconnexion, le refus de l’usurpation d’une autre session, les tailles et empreintes invalides, le rejet des dimensions excessives avant décodage, le retrait de l’image et la compatibilité des messages avec un ancien relais sans cette capacité. Les fichiers d’entrée sont des images synthétiques de test.

Les tests ont été exécutés avec une autorisation d’ouverture de sockets locales, le bac à sable par défaut interdisant TCP/UDP. Les installations de test utilisent uniquement des dossiers temporaires, sans modifier l’autostart du profil réel.

## Vérification visuelle

Les fichiers `preview.png` et `private-preview.png` montrent les widgets réellement rendus par Qt, avec la palette anthracite/gris/bleu de la référence fournie. Les personnages prédéfinis sont des illustrations vectorielles déterministes dessinées localement. L’import d’images personnelles est désormais disponible, mais aucune photo personnelle réelle n’a été utilisée pour les tests ou les captures. L’aperçu privé utilise un nom de démonstration et n’affirme pas la présence d’un utilisateur réel. `fortytchat-reference-v3.png` et `fortytchat-private-v3.png` présentent également des profils et textes explicitement marqués comme démonstration, pour illustrer le style en conversation ; ils ne peuplent jamais l’application au lancement.

`avatars-photo-import.png` montre le bouton d’import et `avatar-crop-preview.png` montre le recadrage sur une illustration synthétique.

## Correctif d’affichage 1.0.1 et mise à jour

Le défaut des captures utilisateur a été reproduit à 150 % : champs de 25 pixels pour une hauteur attendue de 40, et paragraphes d’environ 22 pixels pour 48 nécessaires. Les pages de paramètres sont désormais défilables et conservent le calcul de hauteur du texte selon sa largeur. Les champs et boutons gardent leur hauteur naturelle.

Trois tests en processus Qt séparés vérifient les facteurs **1, 1,5 et 2**, dans les deux thèmes et avec des fenêtres de **570 × 590** et **460 × 420** pixels logiques : pas de paragraphe comprimé, champs lisibles, accès au dernier contrôle par défilement, aucun défilement horizontal et aucune zone vide superflue. Les captures `settings-general-1.0.1.png` et `settings-about-1.0.1.png` montrent le rendu réel à 150 %.

L’icône a été remplacée par un SVG noir **FT** blanc, avec PNG dérivé. Le compteur de messages non lus est conservé et dessiné avec une taille de texte indépendante du DPI. Son rendu a été inspecté sur l’icône et avec le badge « 1 ».

La réinstallation est testée dans une installation XDG temporaire : code et icône actualisés, pseudo, thème, photo personnelle et autostart désactivé conservés. Le script de mise à jour a été retiré à la demande de l’utilisateur ; les scripts disponibles sont `install.sh` et `uninstall.sh`.

## Version 1.1.0 : amis, emojis et désinstallation

- Migration des anciens profils sans perdre le pseudo, le thème ou l’autostart désactivé ; création et conservation de l’ID de contact, liste de 256 amis bornée.
- Étoile d’ajout/retrait, amis visibles hors ligne, reconnaissance après changement de pseudo et nouvelle session, rattachement de la même fenêtre privée et envoi au SID courant. Les messages restent absents des préférences.
- Catalogue Unicode/CLDR embarqué de 3 781 emojis, neuf catégories, recherche française sans accents et anglaise, grille virtualisée ; remplacement d’un emoji composé manquant comme séquence entière.
- Désinstallation d’une copie temporaire avec processus Python actif : arrêt de cette seule copie, application, lanceur, entrée de menu et autostart supprimés ; autres programmes laissés actifs. Profil conservé par défaut, puis suppression explicite par `--purge`.

Pour le signalement persistant de texte tronqué, l’instance réelle observée avait été lancée avant le correctif, alors que les fichiers sur disque avaient été remplacés. Un rendu frais a aussi été vérifié avec **Qt 6.12.0 de l’interpréteur installé**, à 150 %, sans utiliser le profil personnel. Les fichiers `friends-1.1.0.png`, `emojis-1.1.0.png` et `settings-installed-qt-1.1.0.png` montrent ces widgets avec des données de démonstration. La désinstallation ferme la copie installée avant sa suppression ; une installation suivante charge réellement le nouveau code.

## Version 1.2.0 : pauses, fichiers et barre supérieure

- TCP et relais réels : les invitations et leurs réponses restent reçues en Ne pas déranger, les Wizz restent refusés. Trois demandes passent ; la quatrième bloque les pauses pendant cinq minutes. La protection reste liée au profil après un changement de pseudo ou de session. Les doublons réseau ne comptent qu’une fois et 20 tentatives bloquées laissent les messages disponibles.
- Popups : cartes centrées, boutons Oui/Non, fermeture et expiration ; retour bleu pour une acceptation, rouge pour un refus. Le changement de DND ne ferme pas les invitations en cours.
- Pièces jointes privées et générales : consentement, SHA-256, longueur et ordre des blocs, fichier vide, refus, quotas, annulation et suppression des temporaires. Une livraison partielle dans le général conserve l’offre pour les destinataires joints. Les fichiers existants, spéciaux, trop grands ou modifiés sont refusés. Les écritures courtes et les accès concurrents sont vérifiés.
- Interface complète : envoi et réception via le bouton de pièce jointe et le choix de destination, conservation de l’état après fermeture/réouverture d’une fenêtre, refus d’une offre générale et retour de pause en DND. Le Wizz ouvre la conversation au bas des messages et anime ses deux axes pendant 2,8 secondes.
- Barre et paramètres : icône rouge à réception d’un message non lu puis noire après lecture, ouverture par doubleclic, retrait des accès réseau. L’installateur crée `network.json` à la première installation et reprend une configuration existante lors de la suivante.
- README : installation SSH ou HTTPS dans `~/.FortyTchat`, mise à jour par `git pull --ff-only` puis `install.sh`, profil et choix d’autostart conservés. Les messages de la session restent éphémères.

Les aperçus `friends-1.2.0.png`, `files-1.2.0.png` et `pause-*-1.2.0.png` sont des rendus Qt 6.12.0 avec des données de démonstration. Les paramètres sont également vérifiés aux facteurs 100 %, 150 % et 200 % dans les deux thèmes. L’onglet À propos présente la version, l’auteur et le lien du dépôt, ainsi que les fonctions et données conservées.

## Version 1.3.0 : réponses, réactions et saisie

- TCP et relais réels avec trois clients actuels et un client annonçant les capacités précédentes : réponses liées au message, texte reçu par les anciens clients, réactions et saisie uniquement pour les clients compatibles, confidentialité du routage privé.
- Réactions ajoutées, remplacées et retirées ; un état plus ancien ne rétablit pas une réaction supprimée. Une réaction arrivée avant son message est appliquée à sa réception, avec expiration et file bornée.
- Trois points animés pendant la saisie, arrêt après réception du message, protection du budget des messages face au spam de saisie, expiration des états et absence de brouillon dans les trames.
- Interface réelle : bouton Répondre, aperçu dans le composeur, envoi avec référence, citation cliquable et mise en évidence, texte HTML conservé littéralement et infobulle échappée, réaction retirée par sa pastille, conservation après fermeture/réouverture d’une fenêtre. Un message source reçu en retard actualise sa citation.
- Aucun texte, citation ni réaction dans les préférences sauvegardées. Les nouveaux textes de pause conservent les callbacks et les couleurs d’acceptation/refus.
- Captures avec Qt 6.12.0 : `friends-1.3.0.png`, `social-1.3.0.png`, `about-1.3.0.png` et `pause-*-1.3.0.png`, avec profils et messages de démonstration.

## Version 1.4.0 : notifications animées et réponses personnalisées

- Les onze GIF fournis sont décodables et animés avec QMovie, à taille réduite sans cache de toutes les images. Un changement de sélection change l’animation et cacher le widget arrête la lecture.
- Modèles locaux privés : création, modification, suppression, rechargement, limite de douze modèles, fichier invalide traité et conservation à la désinstallation ordinaire. Le mode de préparation hors connexion n’émet aucun envoi.
- TCP et relais réels : cartes privées, réponses Oui et personnalisées, référence liée au bon expéditeur, réponse unique, refus des références expirées, anti-spam commun aux pauses, DND et compatibilité des anciens clients. Une pause classique peut recevoir une réponse animée personnalisée.
- Interface : création avec le bouton ✦, choix de GIF, aperçu, modèle réutilisable, carte reçue, annulation de l’éditeur puis réponse Oui, réponse personnalisée, fermeture et libération de la carte. Les textes reçus ne sont pas enregistrés dans les modèles ni dans les préférences.
- Cartes et éditeur défilables avec les textes maximaux et des mots longs ; aperçu dans les deux thèmes. Les noms de fichiers et les libellés elliptiques sont recalculés à la largeur réelle et après changement de police.

Un défaut intermittent a été reproduit avec Python 3.14 et Qt 6.10 dans une séquence de création et destruction de fenêtres. Qt indique que [la branche 6.10 ne prend pas en charge Python 3.14](https://www.qt.io/blog/qt-for-python-release-6.10-is-here). La même séquence passe avec Qt 6.12 ; les dépendances et les contrôles de lancement imposent donc PySide6 6.12 minimum pour Python 3.14. L’environnement utilisateur déjà installé n’a pas été modifié pour ces essais.

Les captures `notification-editor-1.4.0.png`, `notification-1.4.0.png`, `notification-reply-*-1.4.0.png` et les paramètres 1.4.0 sont rendus avec Qt 6.12.0 et des données de démonstration. Attribution et licence des GIF incluses dans `assets/animated/`.

## Ce qui n’est pas validé par ces essais

- Mesure indépendante des nouveautés 1.2.0 sur deux ordinateurs physiques à l’école ; le retour positif du créateur porte sur une version précédente.
- Broadcast entre zones, filtrage multicast, VLAN, pare-feu ou politiques d’isolation du cluster.
- Disponibilité d’une machine relais autorisée, de son DNS et de son certificat TLS de production.
- Rendu de l’icône, de son compteur et des clics dans la session GNOME réelle de l’école ; l’ordre près de Discord reste contrôlé par l’environnement.
- Autostart après une vraie fermeture/réouverture de session Ubuntu, règles institutionnelles et mise au premier plan Wayland.
- Disponibilité de toutes les bibliothèques natives Qt sur une nouvelle installation Ubuntu. Le chemin de l’installateur testé réutilise le Python déjà équipé ; la création de venv et le téléchargement de wheels dépendent de la machine et de son accès réseau.
- Respect du silence par le serveur de notifications propre au poste (le programme émet explicitement la demande `suppress-sound`).
- Protection contre un acteur réseau hostile, identité campus, chiffrement de bout en bout ou garantie Wizz par personne physique sans authentification.

La procédure à réaliser sur place est fournie dans `SCHOOL_CHECKLIST.md`.
