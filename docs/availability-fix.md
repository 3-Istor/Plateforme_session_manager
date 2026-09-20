# Agendas consultés lors d'une réservation

En mode Google, le site consulte l'agenda **principal de chaque participant
sélectionné**, l'agenda cible 3-ISTOR et systématiquement
`deleguessigl@gmail.com`. `GOOGLE_AVAILABILITY_CALENDAR_IDS` permet d'ajouter
d'autres agendas collectifs ; une valeur vide ne retire pas ces deux agendas
obligatoires. Les agendas secondaires personnels ne sont pas découverts
automatiquement avec les permissions actuelles.

Le compte manager configuré doit pouvoir lire les disponibilités des agendas
collectifs. Un agenda inaccessible ou une réponse Google incomplète bloque la
recherche/réservation au lieu d'être considéré libre. Chaque participant doit
avoir connecté son agenda. Les disponibilités sont vérifiées à la recherche,
à la création puis à l'acceptation ; seul le forçage explicite du manager peut
accepter des conflits connus. Les événements marqués « Libre » dans Google ne
sont pas des périodes occupées dans la réponse FreeBusy.

Après déploiement, tester une période occupée uniquement dans l'agenda des
délégués, puis uniquement dans l'agenda d'un membre sélectionné. Elles ne doivent
pas être proposées en mode normal ; le mode forcé doit indiquer le conflit.
Si l'agenda collectif n'est pas accessible au manager, corriger le partage dans
Google Calendar, sans supprimer le contrôle de disponibilité.

Le parcours de réservation propose des boutons explicites pour revenir aux
créneaux ou modifier équipe/date/durée. Les détails saisis sont conservés ; le
retour aux paramètres invalide l'ancien créneau et impose une nouvelle recherche.
