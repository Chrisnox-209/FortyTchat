# Validation locale de FortyTchat 1.5.0

Tests réalisés le **6 novembre 2026**, sous Python **3.14.4**, Qt/PySide6 **6.12.0** et PyNaCl **1.6.2**. Le créateur signale un essai réussi d’une version précédente en cluster ; les nouveautés 1.5.0 sont vérifiées localement, sans mesure indépendante sur deux postes physiques à l’école ni audit cryptographique externe.

## Résultats automatisés

- **133 tests réussis en 22,64 s** dans la suite actualisée : réseau TCP/UDP réel sur la machine locale, interface Qt hors écran, installation et désinstallation dans des dossiers temporaires.
- Diffusion E2EE sur loopback : **200 clients asyncio**, roster complet et **199 réceptions uniques**, en **5,35 s** lors de cette exécution. Cette mesure inclut l’établissement des présences signées ; elle ne représente pas 200 utilisateurs envoyant continuellement en parallèle.
- Profils de test isolés ; application installée, conversations actives, autostart et environnement Python personnels laissés en place.

Les sockets locales demandent un environnement autorisant TCP/UDP. Qt 6.10 n’est pas pris en charge avec Python 3.14 : [documentation officielle de cette version](https://www.qt.io/blog/qt-for-python-release-6.10-is-here). L’installateur et le lancement contrôlent cette combinaison et les dépendances imposent Qt 6.12 pour Python 3.14.

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
