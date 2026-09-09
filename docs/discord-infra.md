# Notifications Discord : configuration infra

## Vault / Kubernetes

Ajouter une seule clé au secret applicatif existant dans Vault :

```text
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/ID/TOKEN
```

Remplacer par l’URL réelle d’un webhook de **salon textuel classique**. Les salons forum et fils ne sont pas pris en charge. Pas de bot, token bot ni identifiant de salon supplémentaire nécessaire.

Le contrôleur Vault doit copier cette clé dans le Secret Kubernetes référencé par `existingSecret` du chart (par défaut `3istor-sessions-secrets`). Le Deployment importe déjà les clés avec `envFrom.secretRef`. Ne pas mettre l’URL dans les valeurs Helm versionnées ni dans une variable frontend `VITE_*`.

Conserver `FRONTEND_URL` avec l’URL HTTPS publique du site : elle sert de lien dans les messages. Autoriser les connexions HTTPS sortantes vers `discord.com:443`. Déployer la nouvelle image, synchroniser le Secret, puis redémarrer le Deployment pour charger sa nouvelle variable d’environnement. Un simple changement de Secret ne recharge pas les variables d’un pod existant.

La base doit rester persistante : la table `discord_deliveries` conserve les identifiants des messages et les tentatives en attente. Cette table supplémentaire est créée au démarrage sans modifier les tables existantes. Garder **une réplique et un seul processus Uvicorn**, comme le déploiement actuel : le worker n’utilise pas de verrou distribué.

## Fonctionnement

- URL absente ou vide : intégration désactivée, fonctionnement normal du site.
- Nouvelle demande : message « ⏳ En attente » avec titre, date/horaires de Paris, participants et demandeur.
- Acceptation/refus sur le site : modification du même message en « ✅ Acceptée » / « ❌ Refusée », avec la note éventuelle.
- Les mentions automatiques (`@everyone`, rôles, utilisateurs) sont désactivées. Les détails des agendas personnels ne sont jamais envoyés ; le salon voit les informations de la session et la note du manager.
- Vérification toutes les 5 secondes. Les erreurs réseau, réponses 429 et indisponibilités sont réessayées avec délai croissant (jusqu’à 5 minutes, ou davantage si Discord le demande). Les réservations ne dépendent pas de ces appels réseau.
- Les tentatives persistent au redémarrage et envoient le dernier statut connu : si une demande est déjà acceptée avant le premier envoi, le message indique directement « Acceptée ».
- Si le message est supprimé, il sera recréé lors d’une prochaine modification. Changer de webhook crée des messages dans le nouveau salon pour les sessions suivies ; les anciens messages ne sont pas supprimés.
- Un timeout juste après un premier envoi accepté par Discord peut exceptionnellement produire un doublon à la tentative suivante (Discord ne fournit pas de clé d’idempotence pour ce webhook).

## Vérification après configuration

1. Créer une session test sur le site et attendre quelques secondes : un message en attente apparaît.
2. L’accepter sur le site : le même message change de statut (et Google crée l’événement en mode Google).
3. Créer une seconde demande puis la refuser avec une note : vérifier le statut et la note sur le même message.
4. Vérifier que `DISCORD_WEBHOOK_URL` n’apparaît ni dans `/api/config`, ni dans les fichiers frontend, ni dans les logs. Ne pas copier sa valeur dans un ticket.

Les logs signalent uniquement le numéro de session et le type d’erreur. Retirer la variable ou la vider puis redémarrer désactive l’intégration. Pour une fuite du secret, supprimer/regénérer le webhook dans Discord puis remplacer la valeur dans Vault.
