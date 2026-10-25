# Crash au démarrage après la mise à jour SQLAlchemy 2.1

## Cause

La mise à jour de SQLAlchemy 2.0.52 vers 2.1.6 change le pilote utilisé par une
URL `postgresql://...` : il s'agit désormais de `psycopg` (version 3), et non
plus de `psycopg2`. L'image n'installait que `psycopg2-binary`, ce qui provoquait
`ModuleNotFoundError: No module named 'psycopg'` dès `create_engine`, avant toute
connexion au serveur PostgreSQL.

Ce crash empêche Uvicorn de démarrer et explique l'erreur de connexion à
l'application affichée par le proxy. Ce message ne démontre ni une perte de
données ni une incompatibilité de version du serveur PostgreSQL.

## Correctif

Les dépendances de production incluent maintenant `psycopg[binary]==3.3.6`.
La variante binaire embarque ses bibliothèques : aucun paquet système libpq ou
compilateur supplémentaire n'est nécessaire dans l'image Python slim.
`psycopg2-binary` est conservé pour les déploiements utilisant explicitement
`postgresql+psycopg2://...`.

Les mises à jour de sécurité sont conservées. Aucun changement de `DATABASE_URL`,
de secret Vault, de manifeste Kubernetes ou de données existantes n'est requis
pour ce correctif. Il faut déployer la nouvelle image applicative ; redémarrer
une ancienne image contenant les mêmes dépendances manquantes ne suffit pas.

## Vérifications

- Tests d'import des pilotes pour SQLite, PostgreSQL implicite, psycopg 3
  explicite et psycopg2 explicite, sans ouvrir de connexion.
- Vérification de l'implémentation binaire de psycopg 3.
- Test de démarrage réel de l'application contre un PostgreSQL jetable dans la
  CI : création du schéma, santé HTTP, création/acceptation/lecture/suppression
  d'une session de test. Ce service n'a aucun accès à la base de production.
- Le test d'intégration local est optionnel via `TEST_POSTGRES_URL` et refuse
  les hôtes non locaux et les bases autres que `sessions_driver_test`.

Références : [migration SQLAlchemy 2.1](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html#default-postgresql-driver-changed-to-psycopg-psycopg-3)
et [installation de Psycopg](https://www.psycopg.org/psycopg3/docs/basic/install.html).
