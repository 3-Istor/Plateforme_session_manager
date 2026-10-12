# Agendas consultés lors d'une réservation

En mode Google, le site consulte l'agenda **principal de chaque participant
sélectionné**, l'agenda cible 3-ISTOR et systématiquement
`deleguessigl@gmail.com`. `GOOGLE_AVAILABILITY_CALENDAR_IDS` permet d'ajouter
d'autres agendas collectifs ; une valeur vide ne retire pas ces deux agendas
obligatoires. Les agendas secondaires sont sélectionnables individuellement
depuis **Mon profil → Gérer mes agendas**. Ils ne sont jamais ajoutés sans choix
explicite de leur propriétaire.

## Sélection personnelle et permissions Google

L'agenda principal reste obligatoire ; chaque personne peut ajouter ou retirer
jusqu'à 49 agendas supplémentaires de sa propre liste Google. Pour un nouvel
agenda, le créer ou s'y abonner dans Google Calendar, puis actualiser cette liste.
Les agendas collectifs obligatoires restent contrôlés séparément.

Le bouton **Autoriser avec Google** est proposé lorsque les permissions manquent
ou ont expiré. Cette connexion demande uniquement deux permissions supplémentaires :

- `calendar.calendarlist.readonly` : noms et identifiants des agendas accessibles ;
- `calendar.events.freebusy` : disponibilités des agendas auxquels le compte a accès.

Les membres ne reçoivent pas de nouvelle permission de lecture du contenu ou de
modification des événements. Le manager configuré conserve sa permission existante
de création/modification des sessions dans l'agenda cible. Les noms des agendas
personnels sont visibles uniquement par leur propriétaire ; le planning partagé
ne contient que les périodes occupées et le nom du participant.

Les connexions existantes restent valides pour l'agenda principal sans forcer
toute l'équipe à se reconnecter. Une réautorisation ou son annulation ne supprime
pas les choix précédents. Retirer un agenda ici ne modifie pas Google Calendar et
reste possible si cet agenda est devenu inaccessible.

La sélection est stockée dans la nouvelle table `selected_calendars`, créée au
démarrage par le mécanisme existant `Base.metadata.create_all`. Aucun changement
de schéma des tables existantes, de secrets Vault ou de manifests Helm n'est requis.
Les routes `GET/PATCH /api/google/calendar/selection` sont authentifiées et agissent
uniquement pour le compte connecté ; les ajouts sont validés contre sa liste Google.

## Vérification des créneaux

Le compte manager configuré doit pouvoir lire les disponibilités des agendas
collectifs. Un agenda inaccessible ou une réponse Google incomplète bloque la
recherche/réservation au lieu d'être considéré libre. Chaque participant doit
avoir connecté son agenda. Les disponibilités sont vérifiées à la recherche,
à la création puis à l'acceptation, en incluant les agendas supplémentaires choisis.
Un membre peut demander un créneau forcé, mais seul le manager peut accepter
ses conflits connus. Les événements marqués « Libre » dans Google ne
sont pas des périodes occupées dans la réponse FreeBusy.

Après déploiement, tester une période occupée uniquement dans l'agenda des
délégués, puis uniquement dans l'agenda d'un membre sélectionné. Elles ne doivent
pas être proposées en mode normal ; le mode forcé doit indiquer le conflit.
Si l'agenda collectif n'est pas accessible au manager, corriger le partage dans
Google Calendar, sans supprimer le contrôle de disponibilité.

Tester aussi un entretien occupé uniquement dans un agenda secondaire : après
sélection et enregistrement, il doit bloquer les créneaux de ce membre. Décocher
et enregistrer cet agenda doit le retirer des prochains calculs. Si Google refuse
l'accès à un agenda sélectionné, aucune période ne doit être considérée libre par
défaut. L'aperçu ouvert ne s'actualise pas automatiquement : utiliser son bouton
d'actualisation après avoir changé les événements ou cette sélection.

Le parcours de réservation propose des boutons explicites pour revenir aux
créneaux ou modifier équipe/date/durée. Les détails saisis sont conservés ; le
retour aux paramètres invalide l'ancien créneau et impose une nouvelle recherche.
