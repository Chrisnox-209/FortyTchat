# Architecture et protocole FortyTchat v1

## Pourquoi UDP + TCP, avec relais facultatif

UDP sert seulement à annoncer les présences sur le lien local. Les interfaces sont obtenues via les ioctls Linux en lecture seule ; l’interface physique active portant la route par défaut est prioritaire. Le masque effectif détermine le broadcast. Les interfaces connues de Docker, de virtualisation, les tunnels et loopback sont exclues ; l’unique interface active de conteneur peut servir de secours indiqué comme non physique.

Chaque processus dispose d’un UUID v4 temporaire (32 caractères hexadécimaux), d’un pseudo d’affichage et d’un écouteur TCP. Le port direct standard est 39742, le port de découverte 39741. Les profils `--dev` utilisent un TCP dynamique annoncé aux autres instances. `network.json` contient les valeurs de déploiement, jamais les conversations.

Les sockets et les temporisations vivent dans une boucle asyncio dédiée. Les signaux Qt mettent à jour l’interface dans son thread. Les sockets sont bornées (24 envois simultanés, 64 réceptions directes, 16 envois utilisateur en attente), fermées à l’arrêt et réessayées avec des délais bornés. Un changement d’interface invalide les routes directes et recrée l’écoute multicast sur la nouvelle interface.

Le général P2P est une diffusion TCP à chaque pair connu. Cela évite de confondre broadcast et fiabilité, mais le coût reste proportionnel au nombre de destinataires. Le relais effectue une seule réception et diffuse aux sessions connectées. Pour 200 utilisateurs, c’est le mode à préférer si une machine autorisée et un DNS sont disponibles.

## Trames

JSON UTF-8 avec un saut de ligne terminal, **16 384 octets maximum, délimiteur inclus**. `readline()` avec limite et timeout recompose les segments TCP ; les trames incomplètes à la déconnexion sont rejetées. Les annonces UDP sont limitées à 1 024 octets. Les textes sont limités à 2 000 caractères et 8 000 octets UTF-8 ; contrôles non imprimables et surrogates invalides sont rejetés.

Exemple de présence (valeurs illustratives) :

```json
{"v":1,"type":"hello","sid":"dbe8e2c83e144a78890e277489f1d07d3","nick":"CosmicOtter0042","status":"online","port":39742,"caps":["files","wizz","pause","probe","social","avatar_image","friends","c:AAAAAAAAAAAAAAAAAAAAAQ"],"avatar":5,"avatar_hash":""}
```

| Type logique | Type sur le fil | Rôle |
| --- | --- | --- |
| DISCOVER / PRESENCE | `hello` (UDP), `presence` (relais) | Présence et mise à jour de pseudo/statut |
| DISCONNECT | `bye` | Fin d’une route de présence |
| GLOBAL_MESSAGE | `message`, `to="general"` | Salon général |
| PRIVATE_MESSAGE | `message`, `to=ID` | Conversation privée |
| WIZZ | `wizz` | Effet visuel privé |
| BREAK_REQUEST | `break_invite` | Invitation, identifiant de la trame comme référence |
| BREAK_ACCEPT / DECLINE | `break_reply` | `answer`: `accepted`, `declined` ou `dismissed`, `request`: ID de l’invitation |
| ACK / ERROR | `ack` | `id`, `ok` booléen, raison technique bornée |
| Sonde / heartbeat | `ping` / `pong` | Diagnostic consenti et maintien du relais |

Un événement contient `v`, `type`, `id`, `from`, `to` et son contenu spécifique. Les messages contiennent `text`. Tous les identifiants sont validés, les pseudos ont 3–28 caractères ASCII alphanumériques, `_` ou `-` et le statut appartient à `online`, `away`, `dnd`. Le champ `avatar` est un entier de `-1` (automatique) à `23` (personnages prédéfinis). Le numéro détermine le personnage de remplacement. Pour une image personnelle, `avatar_hash` est un SHA-256 hexadécimal de 64 caractères ; la chaîne vide indique un personnage. Les annonces de présence ne transportent jamais le contenu de l’image.

Un envoi TCP direct commence par `hello`, puis l’événement. Cela évite qu’une présence UDP perdue empêche un destinataire d’identifier l’expéditeur. Le récepteur emploie l’adresse source de la socket, pas une IP fournie dans le JSON. Une route active reste liée à sa source observée ; ce mécanisme réduit certains abus mais ne fournit pas d’authentification cryptographique. Les métadonnées du relais ont priorité sur celles reçues par UDP.

## Livraison, absence d’historique et déduplication

En direct, l’ACK indique que le récepteur a accepté l’événement dans sa session. En cas d’erreur de transport ou d’ACK perdu, une seule nouvelle tentative reprend le même ID. Le cache borné d’IDs évite un deuxième affichage. Un refus explicite (blocage, DND, limitation) n’est pas réessayé.

Au relais, l’ACK signifie **acceptation dans la file de transmission du relais**, pas lecture par le destinataire ni confirmation d’affichage. Une file trop lente est fermée ; le serveur ne conserve pas un message pour une reconnexion. Une interruption après acceptation peut donc perdre un message. Les erreurs de livraison sont affichées, sans conserver une boîte d’envoi durable.

L’ACK n’est pas une garantie de livraison exactement une fois dans toutes les situations. Le cache d’IDs est borné ; les reconnexions et redémarrages ne rejouent rien. Les nouveaux arrivants reçoivent seulement la liste des sessions actuellement présentes. Aucun nouvel arrivant ne reçoit les événements des files d’autres sessions.

Le client conserve les préférences autorisées et les fichiers que le destinataire choisit explicitement d’enregistrer. Les files serveur, messages, caches d’IDs et invitations restent en RAM. Les files sortantes sont limitées à 576 trames par connexion ; les clients lents sont déconnectés. Limite de 512 sessions au relais.

## Débit et protection

- Messages/actions : seau de 8 jetons, recharge de 0,75 jeton/seconde, côté émetteur, récepteur et relais.
- Wizz : 2 520 secondes entre envois, tous destinataires confondus. Réservation du délai local sur disque avant l’envoi ; limite serveur par session retenue pendant les reconnexions, uniquement en RAM.
- Réception Wizz : limite par expéditeur et garde globale de 10 secondes, préférence de désactivation et DND.
- Invitations : trois demandes successives par destinataire, puis blocage des pauses pendant 300 secondes à la quatrième ; remise à zéro après 300 secondes sans demande. Vérification à l’envoi, à la réception et au relais, avec identifiant de contact si disponible. Référence liée au destinataire, expiration après 120 secondes, réponses uniques. Les pauses restent actives en DND.
- Réponses de découverte : temporisation aléatoire, au plus une réponse de groupe toutes les 5 secondes par client. Heartbeat toutes les 25 ± 2 secondes, expiration par défaut à 90 secondes plus au maximum une période de nettoyage.
- Aucun `pickle`, `eval`, commande réseau ou exécution de code reçu. Aucun texte de conversation dans les erreurs techniques.

Ce protocole n’est pas résistant à un adversaire contrôlant le réseau LAN ou à des clients modifiés créant de multiples sessions. Voir les limites de confiance dans le README.

## Relais et reprise

Le relais de production exige TLS avec validation du certificat côté client. `--insecure-local` ne peut écouter que sur loopback, et le client non chiffré exige un profil `--dev`. Le nom du relais est distribué dans `network.json` ; aucun DNS scolaire n’est présumé. Le DNS ne remplace pas l’autorisation d’hébergement et d’accès.

En `auto`, un relais connecté est préféré pour les pairs qu’il connaît. Les autres pairs directs peuvent être joints par LAN. Le même événement a un seul ID sur les deux routes. Le relais n’interconnecte pas plusieurs sous-réseaux à lui seul s’il n’est pas accessible depuis ceux-ci ; aucun contournement de politique n’est tenté.

## Diagnostic

Aucun bouton ni onglet de diagnostic réseau n’est exposé dans l’interface. Les commandes `python3 main.py --diagnose` et `python3 tools/network_probe.py` inspectent les interfaces et la disponibilité locale des ports. Elles ne valident pas la communication entre deux postes. Le cœur réseau conserve aussi une méthode de diagnostic interne pour la maintenance ; si le relais est actif, son handshake et ses heartbeats attestent la connexion. Un résultat « aucun pair » peut simplement signifier qu’aucun autre client ne tourne.

## Images de profil personnelles

L’interface importe PNG, JPEG ou WebP (10 Mo et 50 mégapixels maximum), charge et décode hors du thread graphique, applique l’orientation, propose un recadrage déplaçable et un zoom. Fermer le sélecteur pendant le chargement abandonne le résultat sans appliquer de photo. Une nouvelle image JPEG 128 × 128 est peinte à partir des pixels, sans copie des métadonnées du fichier, puis compressée à 8 192 octets maximum. Elle seule est conservée dans les préférences personnelles (fichier de configuration limité à 32 Kio). Les avatars reçus ne sont jamais écrits sur disque.

La capacité `avatar_image` active deux trames TCP, toujours sous la limite de 16 Kio :

- `avatar_get` : `v`, `type`, `from` (session demandeuse), `sid` (propriétaire), `hash` attendu.
- `avatar_image` : `v`, `type`, `sid` (propriétaire), `hash`, `data` (JPEG encodé en base64, au plus 10 924 caractères).

Le contenu est vérifié par taille, base64 strict, marqueurs JPEG et SHA-256. Avant le décodage graphique, le lecteur Qt vérifie que les dimensions sont exactement 128 × 128 ; une image corrompue donne le personnage de remplacement. Le cache de décodage est borné et reste en RAM. Aucun SVG, URL distante ou image dans un message de conversation n’est introduit par cette fonction.

En LAN, la présence annonce seulement l’empreinte. Le client récupère ensuite la miniature auprès de la session découverte, sur son port TCP annoncé, avec au plus quatre récupérations simultanées. Une réponse ne peut remplacer que l’image correspondant à la session et à l’empreinte actuellement annoncées ; une ancienne réponse est ignorée. Le retrait de l’image est annoncé immédiatement.

Le relais annonce sa capacité dans le premier `pong`. Une connexion peut charger uniquement sa propre miniature, correspondant à son dernier `hello`. Le relais la garde avec la présence en RAM, la transmet aux clients compatibles et la sert aux nouveaux arrivants. Une déconnexion ou un changement d’empreinte abandonne la copie serveur ; il n’existe aucun stockage durable. Les chargements et demandes sont limités séparément des messages de chat. Un ancien relais sans cette capacité continue de fonctionner pour les messages, mais ne reçoit pas ces trames nouvelles.

Les images de profil sont visibles des participants, selon les mêmes limites de confidentialité que le reste du réseau : LAN en clair ou relais TLS sans chiffrement de bout en bout. L’empreinte assure la cohérence du fichier, pas l’authenticité de la personne.

Le contrôle des dimensions et de l’allocation utilise [QImageReader](https://doc.qt.io/qt-6/qimagereader.html), puis le rendu et le réencodage utilisent [QImage](https://doc.qt.io/qt-6/qimage.html).

## Contacts et amis (1.1.0)

Le profil conserve un identifiant de contact aléatoire de 128 bits, indépendant du pseudo, de l’IP et de l’UUID temporaire de session. La liste locale d’amis associe ce contact au dernier pseudo observé ; elle ne contient ni messages ni photos reçues. Une présence met à jour le nom d’un ami et une reconnexion rattache une fenêtre encore ouverte à la nouvelle session. Les échanges locaux encore en RAM peuvent suivre cette fenêtre, sans aucun historique téléchargé ou restauré depuis le disque.

Pour conserver la compatibilité avec les anciennes annonces v1, l’identifiant est publié dans une extension de capacité `c:` suivie des 22 caractères base64url de ses 16 octets (sans padding). La capacité `friends` annonce la fonction. Une seule extension de contact canonique est acceptée, sous les limites de taille existantes. Les anciens clients ignorent ces capacités supplémentaires et continuent à échanger des messages.

Le relais transmet cette métadonnée avec la présence. Le routage reste fondé exclusivement sur le SID temporaire de la session actuellement en ligne. L’identifiant de profil est public et ne constitue pas une authentification ; un client modifié peut en revendiquer un autre. Les risques d’usurpation du LAN restent donc applicables aux contacts. Conserver le profil conserve cette identité ; `uninstall.sh --purge` la supprime.

## Pièces jointes (1.2.0)

La capacité `files` est annoncée dans la présence et négociée avec le relais dans son premier `pong`. Les anciens clients restent compatibles pour les messages et les avatars ; aucune trame de fichier n’est adressée à un client ou un relais qui n’annonce pas cette capacité. Le marqueur historique `chat`, qui n’était pas interrogé par le routage, laisse sa place à `files` pour conserver la limite de huit capacités.

Une offre `file_offer` contient un nom simple, une taille de 0 à 20 Mio et un SHA-256. Aucun chemin du système de l’expéditeur n’est transmis. L’offre peut viser une conversation privée ou le salon général ; dans ce dernier cas, chaque destinataire décide séparément de la télécharger. Les offres expirent après dix minutes. Le calcul de l’empreinte et les lectures/écritures de contenu se font hors du thread graphique et de la boucle réseau.

Le destinataire doit accepter et choisir un chemin de sauvegarde. Un fichier existant est refusé. L’acceptation crée alors un fichier temporaire personnel `.fortytchat-*.part`, à côté de la destination choisie. Le nom distant n’intervient jamais dans la construction du chemin local. Après `file_accept`, l’expéditeur transmet des `file_chunk` privés de 6 Kio maximum, encodés en base64 strict, avec une référence d’offre et un offset exact. Une connexion TCP directe est réutilisée pour le flux ; le relais garde seulement les trames dans ses files bornées et les métadonnées de consentement en mémoire.

`file_end` déclenche la vérification de la longueur exacte et de l’empreinte du contenu reçu. Seul un fichier complet et conforme est publié au chemin demandé, par lien atomique qui refuse aussi un fichier apparu à cet emplacement pendant le transfert. `file_done` confirme la vérification au client expéditeur ; la simple acceptation par la file du relais ne suffit donc pas à afficher une réussite. Le téléchargement n’ouvre et n’exécute aucun fichier.

`file_decline` et `file_cancel` permettent le refus ou l’annulation. Un échec, une expiration ou un arrêt normal supprime le temporaire. Une coupure sans nouveau bloc expire après 60 secondes, plus au maximum une période de nettoyage réseau. Un arrêt brutal du système peut laisser un `.part` incomplet, jamais publié sous le nom final. Aucun transfert ni offre ne reprend automatiquement après un redémarrage.

Limites locales : huit offres sortantes, 32 offres entrantes et quatre flux actifs dans chaque sens ; progression graphique regroupée au plus toutes les 150 ms (plus l’état final). Le relais limite aussi les offres (256 globalement, huit par session), les blocs, les tailles cumulées et les files. Les fichiers gardent les mêmes limites de confidentialité que le texte : réseau LAN en clair ou relais TLS, sans chiffrement de bout en bout. Les fichiers enregistrés volontairement restent sur disque après la fermeture de l’application.

## Réponses, réactions et saisie (1.3.0)

Un `message` peut ajouter `reply_to`, un identifiant de message valide. Le texte cité n’est jamais joint à la trame : chaque destinataire construit l’aperçu depuis sa conversation en mémoire. Une référence absente affiche un aperçu indisponible ; si le message arrive ensuite, l’aperçu est actualisé. Les citations restent du texte brut, y compris dans leur infobulle. Aucun historique n’est demandé ni enregistré.

La capacité `social` remplace le marqueur historique `avatar` pour conserver les huit capacités de présence ; les avatars continuent à être annoncés dans leur champ habituel. Le relais annonce aussi `social` dans son `pong`. Les nouveaux événements ne sont routés que vers les clients compatibles, en direct ou via un relais actualisé. Un ancien client continue à afficher le texte des réponses et ignore leur référence.

- `reaction` : champs de base `v`, `type`, `id`, `from`, `to`, plus `message` (référence), `emoji` et `seq`. L’emoji doit appartenir au catalogue embarqué ; une chaîne vide retire la réaction. Une seule réaction par profil et par message, avec remplacement et compteur côté interface. La référence est résolue uniquement dans la conversation visée.
- `typing` : mêmes champs de base, plus `active` (booléen strict) et `seq`. Aucun texte de brouillon n’est accepté. Le client émet un début puis, si la frappe continue, au plus un rappel toutes les deux secondes. L’arrêt survient après 2,5 secondes sans frappe ou à la perte de focus. Le récepteur expire l’indicateur au bout de cinq secondes sans nouvelles.

`seq` est un entier croissant de session. Un cache borné ignore les anciens états arrivant après un état plus récent, pour éviter de réafficher une réaction retirée ou une saisie arrêtée. Les réactions et la saisie ont leurs propres seaux de débit et n’épuisent pas le budget des messages. Blocage, destinataire et identité de session sont vérifiés comme pour le reste du protocole.

Les réactions reçues avant leur message sont gardées au plus dix secondes, dans une file de 256 états maximum. L’ensemble des indicateurs de saisie est limité à 512 états et chaque message à 512 auteurs de réaction. Toutes ces données restent en mémoire ; une reconnexion ou un nouveau client n’en reçoit aucun historique.
