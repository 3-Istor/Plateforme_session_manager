# Correctif de supervision applicatif

Le backend expose désormais `GET /metrics` (et `HEAD`) au format Prometheus
texte 0.0.4. Il fournit uniquement `sessions_process_uptime_seconds`, le temps
écoulé depuis l'initialisation du middleware. Ce n'est **pas** un contrôle de
santé de PostgreSQL, Google Calendar ou Discord.

Ce chemin exact accepte les hôtes internes des collecteurs, sans modifier
`ALLOWED_HOSTS` ni les contrôles des autres routes. Cette métrique est publique,
sans authentification, et ne contient aucune donnée métier, URL de secret ou
identité. Aucun accès à la base n'est effectué lors de la collecte.

Après publication de l'image et redémarrage du Deployment applicatif, vérifier
que les appels `/metrics` reçoivent 200 et que le collecteur accepte le format.
Les anciennes lignes 400 restent dans les historiques. Aucune modification de
Vault, des volumes, de Helm ou de PostgreSQL n'est requise pour ce correctif.

## Problème PostgreSQL séparé

Les logs fournis montrent `invalid length of startup packet` environ deux fois
par minute. Le client SQLAlchemy utilise psycopg2 pour PostgreSQL ; aucune
collecte HTTP vers PostgreSQL n'est implémentée par le site. La source exacte
de ces connexions n'est pas visible dans les extraits.

L'équipe infrastructure doit identifier les sondes/collecteurs concernés et
vérifier qu'aucune collecte HTTP ne cible directement le port PostgreSQL.
Les IP `10.0.0.198` et `10.0.1.226` sont celles des collecteurs HTTP observés
sur l'application, **pas une attribution prouvée des connexions PostgreSQL**.
Ne pas effacer la base ou masquer les logs pour traiter cette erreur.

Référence du format : https://prometheus.io/docs/instrumenting/exposition_formats/
