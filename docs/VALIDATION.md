# Validation locale de FortyTchat 1.8.0

Tests réalisés le **6 novembre 2026**, sous Python **3.14.4**, Qt/PySide6 **6.12.0** et PyNaCl **1.6.2**. Le créateur signale un essai réussi d’une version précédente en cluster ; les nouveautés 1.8.0 sont vérifiées localement, sans mesure indépendante sur deux postes physiques à l’école ni audit cryptographique externe.

## Résultats automatisés

- **168 tests réussis en 30,48 s** dans la suite actualisée : réseau TCP/UDP réel sur la machine locale, interface Qt hors écran, installation et désinstallation dans des dossiers temporaires.
- Mesure de diffusion E2EE réalisée en 1.5.0 sur loopback : **200 clients asyncio**, roster complet et **199 réceptions uniques**, en **5,35 s** lors de cette exécution. Cette mesure inclut l’établissement des présences signées ; elle ne représente pas 200 utilisateurs envoyant continuellement en parallèle.
- Profils de test isolés ; application installée, conversations actives, autostart et environnement Python personnels laissés en place.

Les sockets locales demandent un environnement autorisant TCP/UDP. Qt 6.10 n’est pas pris en charge avec Python 3.14 : [documentation officielle de cette version](https://www.qt.io/blog/qt-for-python-release-6.10-is-here). L’installateur et le lancement contrôlent cette combinaison et les dépendances imposent Qt 6.12 pour Python 3.14.

## Amis depuis l’icône et alertes Ubuntu (1.8.0)

- Groupes en ligne/hors ligne, effectifs, pseudo courant, actions liées à l’identifiant du profil et popup désactivée hors ligne, sans route compatible ou pour une session masquée/clé modifiée. Même action disponible au clic droit dans la liste d’amis.
- Menu déjà ouvert : mise à jour de sa structure différée pour ne pas interrompre la navigation, désactivation immédiate d’une popup devenue indisponible et retour à l’état initial correctement pris en compte. Naviguer dans les sous-menus ne déclenche pas le secours doubleclic GNOME.
- Contrôleur Qt et clients TCP réels : ajouter un ami reste silencieux, son départ désactive l’action, sa reconnexion avec la même clé et un nouveau pseudo produit une seule alerte. L’éditeur ouvert depuis l’icône cible le nouveau SID et envoie effectivement la carte à cette session, sans créer de fenêtre de conversation.
- Suivi déterministe avec horloge contrôlée : silence initial, changements de pseudo/statut, plusieurs sessions, ajout/retrait d’amis, coupures brèves, délai d’une minute, contacts masqués, clés modifiées et non-amis. Les préférences et DND consomment les transitions sans alerte tardive lors de leur réactivation. Plusieurs arrivées sont regroupées dans une seule bannière de taille bornée.
- Préférence activée par défaut, booléen validé et sauvegarde/rechargement vérifiés. Commande native vérifiée par capture des arguments de `QProcess` : priorité normale pour les connexions, `suppress-sound=true`, icône FT, texte de présence uniquement et séparation des arguments. Les essais hors écran n’appellent pas le serveur de notifications personnel.

`tray-friends-1.8.0.png`, `settings-1.8.0.png` et `about-1.8.0.png` sont des captures des widgets Qt avec données de démonstration. Le menu est capturé avec ses vrais sous-menus ouverts. Ce rendu ne représente pas une capture de l’extension GNOME de l’école ; l’affichage d’une vraie bannière, sa position et son silence restent à vérifier sur ce bureau.

## Réponses et compatibilité de barre (1.7.0)

- Schéma des choix strict : trois boutons, libellés uniques, type de réponse et cartes validés, aucun emoji distant ni indice ambigu. Sauvegarde/rechargement privé des modèles, ancien format conservé ; dépassement des 64 Kio refusé avant d’altérer le fichier valide.
- TCP et relais réels : politique remise uniquement au destinataire, réponse préparée exacte, réponse libre seulement si autorisée, fermeture informative ne pouvant pas devenir une réponse. Expéditeur incorrect, mauvais index ou texte changé refusés sans consommer la vraie demande. Expiration et nettoyage des politiques.
- Interface et application réelles : édition d’un bouton (libellé, type, titre, texte, emoji), aperçu, modèle réutilisable, clic du destinataire renvoyant exactement le contenu préparé. Annuler l’ajout d’un bouton ne l’ajoute pas. Bouton Fermer, Échap, fermeture et expiration d’une information ne produisent aucun retour.
- Petits écrans et mises à l’échelle 100 %, 150 %, 200 %, dans les deux thèmes : contenu pleine largeur, textes non comprimés, derniers boutons accessibles par défilement, libellés trop longs elliptiques avec texte complet au survol.
- Icône : noir/rouge et activation native, clic milieu, menu ouvert puis fermé rapidement sans action, absence d’ouverture pour une fermeture lente, un contexte explicite ou une action du menu ; compatibilité désactivable. Les essais couvrent les signaux Qt et la logique locale, **pas les événements d’une vraie extension GNOME des postes de l’école**. La mise au premier plan reste soumise au bureau.

Les images `notification-editor-1.7.0.png`, `notification-response-editor-1.7.0.png`, `notification-choices-1.7.0.png`, `notification-info-1.7.0.png` et `about-1.7.0.png` montrent les widgets réellement rendus sous Qt 6.12 avec des données de démonstration. Aucun profil personnel n’a été utilisé.

## Envoyé, livré et vu (1.6.0)

- TCP et relais : confirmation individuelle de livraison après l’ACK authentifié, sans confondre réception et affichage. Lecture privée et générale, correspondant aux destinataires choisis à l’envoi.
- Confirmations chiffrées bornées à 32 IDs uniques, sans texte ni horaire ; trames générales, champs inconnus, doublons ou références mal formées refusés. Un autre participant ne peut pas confirmer la lecture d’un message privé qu’il n’a pas reçu.
- États monotones : une lecture arrivée avant un résultat de livraison ne régresse pas. Compteurs du général partiels, deux coches bleues seulement après confirmation par tous. Références bornées à 200 par conversation et 4 000 par registre, effacées à l’arrêt.
- Envois groupés, absence de doubles envois simultanés, nouvelle tentative après cinq secondes si la confirmation échoue, et budget indépendant laissant les messages disponibles.
- Client chiffré antérieur sans capacité de lecture : messages et livraison restent disponibles, aucune trame inconnue envoyée. DND ne transforme pas une réception en lecture et n’empêche pas une confirmation lorsque la personne consulte volontairement sa conversation.
- Deux contrôleurs Qt et deux clients réseau réels : « Livré » quand aucune conversation n’est ouverte, « Vu » seulement après affichage actif, aucun « Vu » quand la fenêtre est minimisée ou inactive ou que le message est sous la zone visible. Défiler jusqu’au message déclenche la confirmation.
- Préférence de lecture activée par défaut, persistée, désactivation vérifiée sur un message affiché, réactivation puis confirmation ; une confirmation déjà partagée n’est pas retirée. Les états survivent à la fermeture/réouverture de la fenêtre dans le même processus, mais aucun texte ni état de lecture n’entre dans les fichiers du profil.
- Paramètres lisibles à 100 %, 150 % et 200 %. Le suivi du bas de la conversation accompagne les calculs différés de hauteur de Qt sans déplacer un lecteur qui a volontairement remonté la conversation. Modifier une préférence sans changer de thème ne réapplique plus le style global.

Les captures `friends-1.6.0.png`, `social-1.6.0.png`, `settings-1.6.0.png` et `about-1.6.0.png` sont des rendus Qt de démonstration. Les coches sont illustrées avec des états synthétiques explicitement préparés pour ces captures ; le comportement réel est vérifié par les essais ci-dessus. Les images des fonctions inchangées conservent leur numéro antérieur.

## Chiffrement et frontières de confiance

Les tests vérifient :

- Identité privée conservée entre lancements, clés de session renouvelées, permissions 600, association au bon UUID, fichiers corrompus ou non privés refusés, absence de réinitialisation lorsque le carnet disparaît.
- Conservation des clés publiques et du statut vérifié, fusion entre copies concurrentes du profil sans écraser des clés plus récentes ; clé remplacée bloquée, mauvaise empreinte refusée, autorisation uniquement après correspondance complète. L’ancienne clé reste bloquée après remplacement explicite.
- Signature de présence invalidée par une modification de pseudo, SID, port ou statut ; client sans signature et relais sans `e2ee` refusés.
- Chiffrement authentifié du contenu, modification d’un octet refusée, identifiant/en-tête/destinataire modifiés refusés, autre destinataire incapable de déchiffrer, expéditeur intérieur falsifié refusé.
- Anti-rejeu même après dépassement de la fenêtre de 4 096 compteurs ; réordonnancement légitime des connexions concurrentes accepté une seule fois.
- ACK chiffrés : un autre participant ne peut pas confirmer la réception à la place du destinataire attendu. Le relais ne peut pas confirmer seul une livraison avec son ACK de transport.
- Messages privés et généraux et fichiers réels transmis par TCP et relais, avec inspection de toutes les trames remises par le relais : aucun texte de conversation, nom de fichier ou bloc de contenu en clair. Le général remet une copie chiffrée individuelle à chaque destinataire.
- Événement de chat en clair refusé par une vraie connexion TCP, même après un `hello` signé valide. Aucun retour automatique au protocole en clair.
- Messages Unicode maximaux de 2 000 emojis, lettres accentuées et caractères nécessitant un échappement JSON : enveloppes valides et contenu identique après déchiffrement.

La vérification automatique de ces comportements ne constitue pas une preuve de sécurité globale. Le premier contact reste TOFU jusqu’à comparaison indépendante des empreintes. Les métadonnées et avatars restent publics, les membres du général peuvent lire ses messages, et une compromission des clés de session en mémoire expose cette session. Aucun Double Ratchet, audit externe ou authentification campus n’est revendiqué.

## Régressions fonctionnelles

- TCP privé/général, découverte UDP multicast réelle, collision de pseudo, annonce de départ, ACK perdu et tentative de remplacement sans double affichage.
- Relais à 40 clients, confidentialité du routage privé, reconnexion après redémarrage, absence d’historique chez les nouveaux arrivants et refus d’un expéditeur intérieur falsifié.
- Amis reconnus après changement de pseudo et nouvelle session avec la même identité ; fenêtre encore ouverte rattachée au SID courant, sans récupérer d’anciens messages sur le réseau.
- Anti-spam appliqué aux extrémités après déchiffrement : budgets de messages, réactions et saisie séparés, Wizz global côté émetteur et limites de réception, trois pauses puis blocage de cinq minutes conservé après reconnexion du même profil. Le relais, devenu opaque, limite les flux et files plutôt que les actions qu’il ne peut plus lire.
- DND : messages silencieux, Wizz et nouvelles cartes refusés ; pauses classiques et réponses actives. Références d’invitation liées à l’expéditeur, expiration et réponse unique.
- Fichiers privés/généraux : acceptation explicite, SHA-256, longueur et ordre des blocs, fichier vide, refus, annulation, quotas, suppression des temporaires, livraison générale partielle, absence d’écrasement même lors d’une course. Sources spéciales, trop grandes ou modifiées refusées ; écritures courtes et accès concurrents vérifiés.
- Citations, accès au message source, réactions ajoutées/remplacées/retirées, réordonnancement, saisie transitoire sans brouillon, état conservé dans la RAM de la même session et absent des préférences.
- Onze GIF animés, aperçu, réponses personnalisées, création/modification/suppression de douze modèles, gestion des fichiers invalides, fermeture des cartes et éditeurs sans double réponse.
- Photos PNG/JPEG/WebP synthétiques : import asynchrone, annulation, recadrage, métadonnées retirées, miniature seulement sauvegardée, tailles/dimensions/empreintes validées et avatars reçus conservés uniquement en RAM.
- Installation XDG temporaire avec espaces et `$`, lanceur, entrée de menu, mise à jour sans réactivation d’un autostart désactivé. Désinstallation de cette seule copie, profil conservé puis purge explicite. Aucun script de mise à jour ajouté.

## Interface et captures

Les paramètres Général/À propos sont vérifiés à **100 %, 150 % et 200 %**, dans les deux thèmes et de petites fenêtres : paragraphes et champs non comprimés, derniers contrôles accessibles par défilement, aucun défilement horizontal. Le dialogue d’empreinte est également vérifié à ces trois facteurs, en **440 × 420** pixels logiques, dans les deux thèmes.

Cadenas : empreinte incorrecte refusée, correspondance complète transmise au cœur réseau, statut vérifié uniquement après confirmation. Une clé modifiée désactive le composeur tout en laissant le cadenas accessible. Le parcours complet avec le cœur réseau vérifie aussi un ami, conserve la vérification après sa reconnexion et permet de vérifier une clé remplacée depuis sa ligne d’ami sans rattacher son ancienne conversation avant autorisation. Les autres essais UI couvrent les fenêtres privées, réponses et réactions, fichiers, cartes, badge rouge puis noir et fermeture réduite.

`friends-1.5.0.png`, `social-1.5.0.png`, `security-1.5.0.png` et `about-1.5.0.png` sont des rendus des widgets réels sous Qt 6.12, avec profils, clés publiques et messages de démonstration. Les captures des fonctions inchangées provenant des versions précédentes restent identifiées par leur nom. Les captures Ubuntu montrant le défaut historique de texte tronqué sont présentées comme anciennes dans le README.

## Essais restant à faire sur place

- Détection et livraison E2EE entre deux postes physiques, VLAN, multicast/broadcast et politiques d’isolation du campus.
- Relais autorisé avec son DNS et son certificat TLS réels ; charge soutenue avec plusieurs émetteurs.
- Barre GNOME/AppIndicator, doubleclic, placement près de Discord, autostart après reconnexion Ubuntu, mise au premier plan sous Wayland et respect du silence par le serveur de notifications.
- Disponibilité de Python, wheels et bibliothèques natives Qt sur une nouvelle machine.
- Audit indépendant de la composition du protocole et des choix de stockage des clés ; garantie par étudiant physique sans authentification campus.

Procédure : [SCHOOL_CHECKLIST.md](SCHOOL_CHECKLIST.md). Protocole et limites : [NETWORK.md](NETWORK.md).
