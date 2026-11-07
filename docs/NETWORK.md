# Réseau et chiffrement de FortyTchat 2.1.0

## Groupes durables, rôles et popups (2.1.0)

Le marqueur signé **`rooms2`** remplace `rooms` parmi les huit capacités. Les fonctions de collaboration exigent 2.1.0 ; les clients chiffrés précédents restent utilisables pour les messages et fichiers classiques. Les relais opaques 1.5.0 continuent à transporter les enveloppes `sealed` sans lire le contenu.

Le créateur signe avec son identité Ed25519 un profil de groupe : identifiant aléatoire, créateur et sa clé publique épinglée, révision monotone, nom (64 caractères), membres (32 maximum), administrateurs, miniature JPEG bornée, avatar et dernière réservation de popup. Signature avec séparation de domaine `FortyTchat group profile v1`. Une clé inconnue ou modifiée, une signature incorrecte, une révision ancienne ou un état concurrent incompatible sont refusés. La présence signée du créateur doit correspondre à cette identité.

`group_invite` et `group_state` remettent ce profil individuellement et chiffré. `group_update` demande une modification unique avec la révision attendue. Seul le créateur valide et signe : il peut gérer tous les membres et rôles ; les administrateurs peuvent changer le nom/la photo et gérer les membres ordinaires. Le créateur ne peut pas être retiré ; un admin ne peut pas retirer un autre admin. Un ajout exige une présence compatible et une acceptation explicite du nouveau membre. Les anciennes révisions ne rétablissent jamais silencieusement un membre retiré.

`group_profiles.json` (privé, 600, au plus 1 Mio) conserve au plus 32 profils, l’état local d’adhésion et le dernier identifiant/horodatage de popup affichée. Aucun message, vote, capture ou ancien SID n’y figure. Les écritures atomiques utilisent le verrou du profil et un contrôle de révision pour éviter qu’une seconde session écrase une réservation récente. Un fichier endommagé désactive les fonctions de groupe sans réinitialiser leurs délais.

Au redémarrage, le créateur recharge ses groupes ; les membres attendent une confirmation de sa part via `group_sync`. Un membre retiré reçoit un refus `group_removed`, authentifié par le canal E2EE du créateur, sans recevoir son nouveau roster. Une clé de créateur modifiée reste bloquée jusqu’à vérification. Le créateur connecté et membre actif est nécessaire aux changements et aux réservations ; après confirmation de restauration, les conversations entre membres présents continuent sans lui. Le départ volontaire et le refus d’invitation sont conservés.

Les messages de groupe gardent **`to` égal au SID individuel du destinataire**, avec `group` pour le contexte. L’expéditeur et le destinataire doivent avoir rejoint ; un SID doit être annoncé par un `group_join` authentifié d’un profil du roster courant (quatre sessions maximum par profil). Les invitations en attente, non-membres et membres retirés sont exclus. Le départ/retrait arrête les offres et transferts du groupe chez les clients qui le reçoivent ; chaque bloc est aussi revérifié. Une coupure réseau peut retarder la propagation d’un retrait aux autres appareils ; elle ne peut pas retirer un contenu déjà reçu.

### Une réservation de popup commune pendant 42 minutes

`group_popup_request` demande un créneau au créateur. Sur sa boucle réseau, il vérifie le membre et le délai **partagé par tout le groupe**, puis persiste la nouvelle révision avant de produire `group_popup_grant`. Une seconde session du même profil doit aussi réussir le contrôle atomique de révision. Sans créateur disponible, aucune popup collective ne part.

Le permis signé Ed25519 (domaine distinct `FortyTchat group popup v1`) lie le groupe, le lot aléatoire, le profil de l’expéditeur et l’heure. Il expire après deux minutes. Chaque destinataire vérifie la signature, le contexte, l’expéditeur et son propre dernier affichage, sauvegardé avant la popup ; un identifiant répété ou un nouvel envoi dans les 42 minutes est refusé. La réservation reste consommée même en cas d’échec ou de DND. La limite porte sur les popups collectives, pas les messages ou les réponses.

Chaque `card_invite` utilise **un identifiant distinct par destinataire**, avec le permis commun et son contexte de groupe. Les réponses restent des `card_reply` privées vers l’expéditeur, liées à leur propre invitation : toutes les réponses peuvent donc être consommées indépendamment. Le général refuse toute popup au niveau du protocole et n’a aucun bouton collectif. Les limites privées existantes restent actives pour les invitations individuelles.

### Messages, mentions et sondages éphémères

`message_edit` / `message_delete` ciblent un ID et un contexte de conversation. Seul le SID de l’auteur original peut les appliquer ; séquences anciennes et résurrection après suppression sont ignorées. Une édition concerne seulement du texte. Les propriétaires restent limités à 4 000 messages et 200 par conversation. Les changements reçus avant l’original attendent au plus 10 secondes, dans une file bornée à 256. Aucun changement n’est rejoué au retour d’un destinataire absent.

Les mentions associent au plus 32 identifiants de profils au texte `@pseudo`, en excluant les blocs clôturés par trois accents graves. Les blocs sont rendus comme texte sélectionnable, jamais exécuté ; seules les actions explicites de copie écrivent dans le presse-papiers. Les alertes de mention respectent DND.

`poll_create` contient question (200 caractères), 2 à 8 options (80 chacune), un mode `multiple` et une audience figée de 256 SID maximum. Un vote simple indique `option` (ou -1 pour retirer). Un vote multiple indique `options`, liste sans doublon (vide pour retirer). La séquence propre à chaque votant remplace son choix précédent. L’audience et le contexte restent vérifiés. Barres et pourcentages utilisent les votants comme dénominateur ; les pourcentages multiples peuvent dépasser 100 % au total. Aucun vote n’est persisté.

### Lots de fichiers

Le composeur accepte au plus 64 chemins de fichiers locaux en attente. Une seule miniature est préparée à la fois ; une offre attend une place parmi les huit offres actives, avec quatre transferts simultanés au plus. Les nouvelles offres sont espacées pour respecter les quotas. Les glisser-déposer acceptent uniquement une **copie**, jamais un déplacement ; les dossiers et URL distantes sont refusés. Les sources sont revérifiées par le protocole de fichiers, sans ouverture automatique chez le destinataire.

## Aperçus et captures en mémoire (2.0.0)

Une offre de fichier peut inclure `preview`, un JPEG en base64 de 8 Kio maximum. Sa taille est validée dans le protocole et ses dimensions sont vérifiées avant décodage Qt (320 × 320 maximum). Le client produit une miniature sans métadonnées, avec proportions conservées. Le champ est retiré pour les anciens clients et reste à l’intérieur du chiffrement, comme le nom et le SHA-256 de l’original. Les images locales compatibles de moins de 10 Mo sont préparées hors du thread graphique.

Le collage Ctrl+V demande confirmation avant émission. Un nouveau canevas ne copie que les pixels, puis encode le PNG en mémoire (16 mégapixels et 20 Mio maximum). Le partage réutilise les offres, acceptations, quotas, blocs chiffrés et vérifications SHA-256 existants, avec une source mémoire au lieu d’un chemin. Le destinataire reçoit seulement la miniature avant acceptation et choisit explicitement l’emplacement de l’original ; aucun fichier temporaire source n’est créé chez l’expéditeur pour cette capture.

## Blocage persistant (2.0.0)

Le profil conserve au plus 256 contacts bloqués, par UUID signé, avec nom d’affichage. La préférence s’applique aux sessions existantes et aux nouvelles présences de ce profil, y compris après changement de pseudo. Réceptions et envois vers ce contact sont refusés, les offres et transferts en cours sont arrêtés et les temporaires reçus sont nettoyés. Le déblocage reste possible depuis les paramètres hors ligne. Ce blocage concerne une identité de profil connue ; il ne constitue pas une attestation de personne physique et n’empêche pas la création d’une autre identité. Le fichier config.json est borné à 64 Kio et n’inclut aucun message, groupe, sondage ou original de capture ; les descriptions de groupes sont conservées séparément dans group_profiles.json.

## Amis et alertes de présence (1.8.0)

Le menu d’amis de l’icône et les alertes Ubuntu utilisent le carnet local et les présences signées déjà reçues. Aucune nouvelle trame ni donnée de présence supplémentaire n’est envoyée. Les actions du menu sont liées à l’identifiant persistant du contact et retrouvent sa session courante au moment du clic. La popup utilise ensuite le mécanisme chiffré `card_invite` existant et ses limites habituelles.

Les alertes sont locales, désactivables, silencieuses en DND et regroupées lors d’arrivées simultanées. Les changements de pseudo/statut, nouvelles sessions d’un contact toujours présent, ajouts d’amis connectés, clés modifiées et sessions masquées ne déclenchent pas de bannière. Un délai initial de cinq secondes, cinq secondes minimales d’absence et un intervalle minimal d’une minute par ami limitent les répétitions. Ce suivi reste en mémoire et n’ajoute aucun historique au profil.

## Découverte et routes

UDP **39741**, groupe multicast **239.42.42.42**, TTL 1 et broadcast calculé depuis l’interface IPv4 active. Annonce toutes les 25 ± 2 secondes ; expiration d’un pair après 90 secondes, plus au maximum une période de nettoyage. Une réponse de groupe au plus toutes les cinq secondes, avec temporisation aléatoire. Les annonces UDP restent sous 1 Kio.

TCP direct **39742**, relais **39743**. Les profils `--dev` utilisent un port TCP dynamique. L’interface ne propose aucun réglage réseau : `network.json`, décrit par `network.example.json`, est un fichier de déploiement. L’installateur crée le fichier absent et conserve celui de la copie installée, sauf configuration fournie dans la source. Aucune IP ou infrastructure scolaire n’est présumée ; aucun scan ni contournement des politiques réseau.

Une route relais connectée est préférée pour les pairs qu’elle connaît ; les autres restent joignables en direct. Reconnexion progressive de 2 à 60 secondes, sans renvoi des anciens messages. Le relais de production exige TLS et un certificat validé. `--insecure-local` est réservé à la boucle locale ; le client exige un profil `--dev` pour cette option. Même sur cette route de test sans TLS, les conversations restent chiffrées de bout en bout.

## Identité signée

Le protocole de base garde `v: 1`, mais **la version applicative 1.5.0 exige les identités signées et les enveloppes chiffrées**. Les clients antérieurs ne sont pas compatibles ; aucun repli en clair n’existe. Le relais doit annoncer `e2ee` dans son premier `pong`, sinon la connexion est refusée.

Chaque profil conserve son UUID de contact et une clé d’identité **Ed25519**. Chaque exécution crée une nouvelle paire de clés de chiffrement **Curve25519**, uniquement en mémoire. `hello`/`presence` publie les champs de profil habituels et :

```json
{"crypto":{"scheme":"ft-box-1","identity":"clé publique Ed25519 base64","session":"clé publique Curve25519 base64","signature":"signature Ed25519 base64"}}
```

La signature porte sur le préfixe de domaine `FortyTchat signed presence v1\0` puis le JSON canonique de tous les champs, sauf `type` et `crypto.signature`. L’exclusion de `type` permet au relais de convertir `hello` en `presence`. Pseudo, SID, statut, port, capacités, référence d’avatar, contact et clés sont ainsi liés. Le relais et le client vérifient cette signature. Une session en cours ne peut pas changer sa clé de session ou son identifiant de contact.

Les capacités restent limitées à huit : `files`, `choices`, `cards`, `rooms2`, `social`, `avatar_image`, `receipts`, `c:` suivi du UUID en base64url canonique. Le relais annonce `avatar_image`, `files`, `social`, `cards` et `e2ee`.

### Premier contact et changement de clé

La première clé publique d’un contact est mémorisée automatiquement (**TOFU**). Cette étape ne protège pas contre une substitution active au premier contact. Le cadenas permet de comparer l’empreinte SHA-256 complète de la clé d’identité, par groupes hexadécimaux, avec celle donnée en personne ou par un autre canal de confiance. La correspondance complète marque l’identité comme vérifiée.

Les empreintes sont publiques et affichées sans masquage. Les clés privées ne sont jamais montrées dans ce dialogue. Les échanges restent chiffrés sans comparaison manuelle ; cette comparaison confirme l’identité derrière la clé et ne change pas le protocole de chiffrement.

Un UUID déjà connu présentant une autre clé reste visible avec **Clé modifiée**, mais ne peut ni envoyer ni recevoir de contenu. Sa nouvelle empreinte doit être comparée et saisie explicitement pour remplacer la clé mémorisée ; les autres sessions utilisant la clé remplacée sont alors bloquées. Le rattachement automatique d’une fenêtre d’ami ignore les sessions bloquées. Une modification de pseudo ou de SID avec la même identité n’annule pas la vérification.

`identity.json` contient le UUID et la graine privée Ed25519 ; `trusted_contacts.json` contient les clés publiques et les vérifications. Fichiers privés **600**, écrits atomiquement, sans texte de conversation. Un fichier illisible, corrompu, non privé ou manquant après initialisation est refusé : aucune réinitialisation silencieuse. Le carnet est borné à 4 096 contacts. Un verrou court et une relecture avant remplacement empêchent une copie obsolète du profil d’effacer les clés et vérifications enregistrées par une autre copie, notamment sur un espace personnel partagé. Les clés restent à l’extérieur du dossier applicatif remplacé lors d’une mise à jour. La désinstallation ordinaire les conserve ; `--purge` supprime le profil.

## Enveloppes E2EE

Le chiffrement utilise **`nacl.public.Box` de PyNaCl/libsodium**, Curve25519 + XSalsa20-Poly1305. Les nonces de 24 octets sont générés aléatoirement par la bibliothèque. Aucune primitive cryptographique n’est réimplémentée dans l’application. La composition du protocole FortyTchat n’a pas reçu d’audit indépendant.

Une enveloppe extérieure contient exclusivement :

```json
{"v":1,"type":"sealed","id":"UUID de transport","from":"SID source","to":"SID destinataire","channel":"event","data":"nonce et contenu authentifié en base64"}
```

`channel` vaut `event`, `stream` ou `receipt`. Il indique une catégorie de flux ; aucun texte, emoji, nom de fichier ou résultat de notification ne figure dans cet en-tête. La trame entière est bornée à **32 Kio**, le contenu déchiffré à **16 Kio**. Le base64 doit être canonique.

Le contenu chiffré contient `id`, `from`, `to`, `channel`, un compteur `seq` et la trame applicative complète `frame`. Les quatre champs d’en-tête doivent correspondre exactement à l’enveloppe extérieure. L’expéditeur de la trame doit correspondre à la session authentifiée ; le destinataire doit être le récepteur ou `general`. Les enveloppes altérées, réfléchies, réadressées ou déchiffrées avec une autre clé sont refusées.

Chaque direction et destinataire possède un compteur croissant. Une fenêtre anti-rejeu de **4 096 compteurs** autorise le réordonnancement des connexions concurrentes, mais refuse un compteur déjà reçu ou trop ancien. Son plus haut compteur est conservé tant que l’exécution vit, même après disparition temporaire du pair. Les cartes de sessions sont bornées à **8 192** ; atteindre cette limite impose de relancer l’application. Aucun compteur ou secret de session n’est écrit sur disque.

Les messages du **général** sont chiffrés séparément pour chaque destinataire autorisé en ligne. Leur trame intérieure conserve `to: general` et son ID logique commun ; chaque enveloppe possède son propre ID de transport, nonce et compteur. Un contact dont la clé a changé est exclu de l’envoi et un avis l’indique. Ce mécanisme n’authentifie pas l’appartenance au campus : tout participant autorisé dans ce salon peut en lire et copier le contenu.

Les clés de session changent à chaque démarrage. Il n’y a pas de Double Ratchet, de renouvellement par message, ni de garantie d’effacement physique de la mémoire Python. Une capture réseau seule ne donne pas le contenu ; une compromission des clés de session en mémoire expose les échanges de cette session. Une compromission de l’appareil ou des copies du destinataire reste hors de cette protection.

Références primaires : [Box](https://pynacl.readthedocs.io/en/latest/public/), [signatures Ed25519](https://pynacl.readthedocs.io/en/latest/signing/).

## Acheminement et confirmations

En direct, le client envoie son `hello` signé, puis uniquement des enveloppes `sealed`. Les événements applicatifs en clair sont refusés même après un `hello` valide. Le récepteur déchiffre, valide, applique ses limites et renvoie un ACK dans une enveloppe chiffrée, liée à l’ID de transport. Une tentative TCP de remplacement emploie un nouvel ID, nonce et compteur, en gardant l’ID applicatif pour éviter un double affichage.

Via le relais, chaque enveloppe vise un seul SID. Le relais contrôle la session de la connexion, la destination, les tailles, le débit et les files ; il ne voit pas les événements déchiffrés et ne conserve aucune clé privée. Son ACK non chiffré indique uniquement un refus ou une mise en file : **une mise en file ne confirme jamais la réception**. Seul l’ACK chiffré venant du destinataire attendu valide une livraison ; l’ACK d’un autre participant ne peut pas la confirmer.

Pas de stockage ni de rattrapage. Les nouveaux arrivants reçoivent seulement les présences et avatars publics, aucun message ou fichier précédent. Cache applicatif borné d’IDs et invitations en RAM ; aucune garantie de livraison après arrêt. Le relais limite à 512 sessions et 576 trames sortantes par connexion, et déconnecte les clients lents. Le flux opaque est limité à 256 jetons, recharge 1 024 trames/seconde par connexion.

## Contenus et protection des récepteurs

Tous les événements ci-dessous sont des **trames intérieures chiffrées**. Leurs validateurs continuent à s’appliquer après déchiffrement. Le relais ne peut plus lire leur type ni appliquer leurs règles métier ; le récepteur constitue la limite de confiance.

- `message` : 1 à 2 000 caractères, 8 000 octets UTF-8 maximum, texte brut. `reply_to` référence seulement l’ID d’un message ; la citation est reconstruite depuis la RAM locale, jamais téléchargée.
- Messages/actions : seau de 8 jetons, recharge 0,75/s à l’envoi et par contact à la réception. Aucun texte reçu dans les erreurs techniques ; aucun `pickle`, `eval` ou exécution de contenu reçu.
- `wizz` : délai global local de 2 520 secondes, réservé sur disque avant envoi. Chaque récepteur limite le contact et impose une garde globale de 10 secondes, la préférence et DND. Un client modifié peut contourner son propre délai global vers des destinataires distincts ; aucun compte campus ne garantit une personne physique.
- `break_invite`, `card_invite` : trois demandes successives par destinataire ; la quatrième bloque pendant 300 secondes, sans prolonger le blocage à chaque tentative. Protection à l’envoi et à la réception, liée au contact dont la clé est mémorisée ; elle survit à sa reconnexion. Invitations bornées, liées au bon expéditeur, valables 120 secondes, réponse unique.
- `break_reply`, `card_reply` : référence de demande et réponse validées ; les pauses classiques et réponses restent actives en DND. Les nouvelles cartes personnalisées sont refusées en DND.
- `card_invite`/`card_reply` : titre 1–80 caractères, texte 0–400, identifiant d’un des onze GIF embarqués. Aucune URL ou image distante envoyée. Réponse acceptée/refusée/personnalisée/fermée ; les animations sont lues localement, une par carte visible. Seuls les douze modèles explicitement enregistrés vont dans `notification_templates.json`.
- `reaction` : référence de message, emoji du catalogue ou chaîne vide pour retrait, compteur d’état. Une réaction par profil et message ; cache de réordonnancement borné, 256 réactions en attente pendant dix secondes maximum.
- `typing` : booléen et compteur d’état, aucun brouillon. Rappel au plus toutes les deux secondes ; arrêt après 2,5 secondes d’inactivité/perte de focus, expiration distante après cinq secondes. Les réactions et la saisie utilisent des seaux indépendants des messages ; l’ordre empêche de rétablir un ancien état.

## Fichiers chiffrés

`file_offer` contient nom simple, taille 0–20 Mio et SHA-256, tous à l’intérieur de l’enveloppe chiffrée. Aucun chemin source ne quitte l’appareil. L’offre générale est chiffrée individuellement et chaque destinataire décide séparément. Expiration après dix minutes, huit offres sortantes et 32 entrantes maximum.

Le destinataire doit cliquer sur **Enregistrer…** et choisir un chemin libre. `file_accept` autorise alors les blocs `file_chunk`, chacun de 6 Kio maximum avec référence et offset exacts. Les blocs et contrôles sont chiffrés, y compris quand une connexion TCP directe est réutilisée pour le flux. Quatre transferts simultanés par sens ; lectures, SHA-256 et écritures hors de la boucle réseau et du thread graphique. Les accès concurrents à un même transfert sont sérialisés.

Les données sont écrites dans un temporaire privé `.fortytchat-*.part`, au côté du chemin choisi, sans utiliser le nom distant pour construire la destination. `file_end` déclenche la vérification de longueur et SHA-256 ; seul un fichier complet est publié par lien atomique refusant tout écrasement, même lors d’une course. `file_done` confirme la vérification au client émetteur. Aucun fichier n’est ouvert ou exécuté automatiquement.

Refus, annulation, expiration (60 secondes sans bloc, plus au maximum un nettoyage) et arrêt normal suppriment le temporaire ; un arrêt brutal peut laisser un `.part` jamais publié. Les fichiers volontairement enregistrés restent sur disque. Pas de reprise après redémarrage. Le relais ne voit ni nom, ni SHA-256, ni consentement ni contenu : il limite uniquement le trafic et les files ; le récepteur vérifie consentement, quotas, offsets et intégrité.

## Métadonnées et avatars publics

Pseudos, statut, UUID de contact, SID, capacités, clés publiques, IP, ports, destinataires, tailles, catégories de flux et horaires restent observables. Le chiffrement n’empêche pas de capturer ou bloquer le trafic. Les départs et connexions peuvent être perturbés ; la protection ne garantit pas la disponibilité face à un réseau hostile.

Les miniatures de profil sont **publiques** : import PNG/JPEG/WebP de 10 Mo/50 MP maximum, recadrage puis JPEG 128 × 128 sans métadonnées, au plus 8 Kio. La présence signée lie leur SHA-256 au profil, puis `avatar_get`/`avatar_image` les distribue via TCP ou relais. La cohérence, la taille et les dimensions sont vérifiées avant décodage Qt. L’original, son nom et ses métadonnées ne sont jamais transmis. Seule la miniature personnelle est sauvegardée ; les avatars reçus et le cache de décodage restent bornés en RAM.

## Diagnostic

`python3 main.py --diagnose` et `python3 tools/network_probe.py` inspectent interfaces et écoute locale sans scanner le réseau ni modifier le pare-feu. Une écoute locale ne prouve pas la communication entre deux postes. Le relais n’interconnecte pas des sous-réseaux s’il n’est pas accessible depuis eux ; sa machine, son DNS et son certificat exigent un déploiement autorisé.

## Livraison et lecture (1.6.0)

Le marqueur `receipts` remplace `friends`, qui n’était pas utilisé pour le routage : les UUID `c:` et la fonction d’amis sont conservés. Les huit capacités restent signées. Les clients chiffrés 1.5.0 restent compatibles pour les messages, fichiers, interactions existantes et confirmations de livraison ; aucune trame de lecture ne leur est envoyée. Un relais opaque 1.5.0 peut transporter ces nouvelles enveloppes sans comprendre leur contenu.

`message_delivery` est un événement local par destinataire après l’ACK authentifié existant. Le récepteur attendu, son SID et l’ID du transport restent liés à cet ACK. Une simple mise en file par le relais ne produit pas « Livré ». Les destinataires sont figés au moment de l’envoi, et l’interface garde leur état individuel ; une confirmation de lecture ne peut pas régresser à « Envoyé » ou « Non confirmé » si un résultat réseau plus ancien arrive ensuite.

La trame intérieure privée `read_receipt` contient les champs de base `v`, `type`, `id`, `from`, `to`, plus `messages` : **1 à 32 IDs uniques**, sans texte ni horaire, vers l’auteur original. Le cœur conserve uniquement des références et destinataires en RAM, avec au plus **200 références par conversation** et **4 000 par registre**. Il n’accepte une lecture que pour un message réellement adressé à la session signataire. Les références inconnues, évincées, déjà confirmées ou appartenant à un autre destinataire ne modifient aucun état. Les références du général restent envoyées individuellement à leur auteur, sans diffusion de l’événement de lecture aux autres membres.

L’interface inspecte la zone visible toutes les 350 ms uniquement dans sa fenêtre de conversation active et non minimisée. Les messages hors de la zone visible, fichiers et messages système sont exclus. Le cœur vérifie que chaque référence a effectivement été reçue et que le contact est compatible, autorisé et joignable. Les envois simultanés sont regroupés et marqués en attente pour éviter les doublons. Un échec permet une nouvelle tentative après cinq secondes si le message est encore affiché ; un ACK authentifié clôt la confirmation. Un arrêt efface ces références.

Débit séparé des messages, réactions et saisie : **16 confirmations**, recharge **4/s**, à l’envoi et par contact à la réception ; chaque trame peut confirmer 32 messages. Aucun événement de confirmation ne crée de notification ou de badge. Le choix `read_receipts` est une préférence locale booléenne, activée par défaut, contrôlant uniquement les confirmations envoyées. Il n’est pas annoncé publiquement. Aucun état de lecture n’est ajouté aux fichiers de configuration.

Le suivi du bas du tchat accompagne les changements différés de hauteur des bulles Qt, tout en restant désactivé quand le lecteur remonte volontairement la conversation. L’état « Vu » rapporte un affichage par le client, pas une attestation qu’une personne a compris le texte ; un client modifié peut mentir sur sa propre lecture.

## Réponses proposées par l’auteur (1.7.0)

La capacité `choices` remplace le marqueur `wizz`, qui n’était pas consulté pour acheminer les Wizz ; leur fonctionnement reste identique. Les annonces signées restent limitées à huit capacités. Le marqueur `cards` est conservé : les anciennes cartes chiffrées restent compatibles. Seuls des clients annonçant `choices` reçoivent les nouveaux champs. Le relais opaque 1.5.0 convient sans modification.

`card_invite` peut ajouter `responses` : objet strict `{buttons: [...], free: bool}`. La liste comporte de zéro à trois éléments, chacun avec `label` (1–32 caractères, une ligne, distinct des autres libellés), `answer` (`accepted`, `declined` ou `custom`), et une carte de réponse `title`, `text`, `emoji`, avec les limites habituelles de 80/400 caractères et l’emoji de la liste locale. Aucun chemin, URL, action système ou code exécutable n’est accepté.

Une liste vide et `free: false` définit une information à fermer. Fermer, Échap et l’expiration ne renvoient aucune réponse pour ces cartes. La fermeture des anciennes cartes et pauses sans politique explicite garde son comportement antérieur.

Un bouton renvoie `card_reply` avec `choice` (indice entier 0–2) et la carte exactement définie par l’auteur. Les extrémités contrôlent le choix, le type et le contenu avant de consommer l’invitation. Sans `choice`, seule une réponse `custom` est admise quand `free` l’autorise. Une réponse interdite, un autre expéditeur, un index ou un texte modifié ne consomment pas la demande valide. Une fermeture informative ne peut pas être transformée en acceptation ou refus. Les politiques sont bornées par les invitations (100 par registre, TTL 120 secondes), nettoyées avec elles et effacées à l’arrêt.

Les modèles acceptent ces options uniquement pour les notifications. Les anciennes données restent lisibles. Le fichier conserve les limites de douze modèles et 64 Kio ; la taille est contrôlée avant remplacement atomique à permissions privées, avec synchronisation des données. Une sauvegarde trop grande ne remplace pas le fichier valide. Aucune notification reçue n’est ajoutée automatiquement aux modèles.
