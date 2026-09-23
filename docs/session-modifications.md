# Modification des sessions

Dans « Mes sessions » / « Demandes », l'auteur d'une session à venir peut
soumettre une modification. Un manager peut le faire pour toute session à venir,
puis accepter sa propre proposition via les mêmes boutons de validation.
Les anciennes sessions passées et les propositions elles-mêmes ne sont pas
éditables. Pour remplacer une proposition en attente, le manager doit d'abord
la refuser. Les sessions refusées à venir peuvent faire l'objet d'une nouvelle
proposition de modification.

La proposition est distincte de l'original : titre/projet, type, ordre du jour,
horaires et participants sont conservés en attente. L'interface indique
« Modification » et permet de comparer l'avant et l'après. Un refus ne change
pas l'original. Une acceptation approuve la session et applique les changements.
Une demande initiale ne peut pas être décidée séparément tant qu'une modification
est en attente. Une proposition devenue obsolète ne peut pas écraser l'original.

La nouvelle table `session_revisions` est créée par `create_all` au démarrage,
sans altérer les colonnes existantes. Les propositions ne sont pas des réservations
supplémentaires et sont exclues des périodes occupées en base ; les sessions
initiales restent réservées jusqu'à acceptation. Une proposition acceptée reste
dans l'historique mais ne compte pas comme une seconde session planifiée.

## Google et disponibilités

Un événement existant est mis à jour avec `events.patch`, son identifiant est
conservé et `sendUpdates=all` notifie les invités. Sans événement existant,
l'acceptation crée celui de la session d'origine. Si Google échoue, la proposition
reste en attente et les champs de l'original ne sont pas modifiés en base.
Il n'existe pas de transaction distribuée entre Google et SQL : en cas d'échec
SQL après succès Google, une vérification/réconciliation peut être nécessaire.

Changer uniquement les textes d'une session déjà approuvée ne réserve aucun
nouveau créneau : sa disponibilité n'est pas recalculée. Pour les changements
d'horaires/participants et les sessions non approuvées, les conflits sont vérifiés
à l'envoi et à l'acceptation. L'original est exclu du calcul SQL, mais pas soustrait
aveuglément du FreeBusy Google : cela pourrait masquer un autre événement.
Un déplacement chevauchant l'original, ou l'ajout d'invités au même horaire, peut
donc nécessiter le forçage explicite du manager. Ce forçage affiche les conflits
et nécessite une reconfirmation s'ils changent. Aucune permission Google
supplémentaire n'est demandée.

## Discord

Les créations gardent leur message « Demande de session » et leur statut.
Chaque proposition a son message « Modification de la session #… », avec
l'ancien titre/horaire et les nouvelles valeurs. Ce même message est modifié
en acceptée/refusée lors de la décision. Après acceptation, le message de la
session d'origine est également actualisé avec ses nouvelles valeurs.
La livraison conserve les retries persistants existants. Si une décision
précède le premier envoi, le message peut apparaître directement avec le statut
final. Aucun webhook réel n'est appelé par les tests.

Après déploiement : tester une modification texte acceptée, une refusée, un
déplacement vers un créneau libre, la conservation de l'ID Google, les messages
Discord et le refus d'une modification de session d'autrui par un non-manager.
