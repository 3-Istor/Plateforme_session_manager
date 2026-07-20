# 3istor Sessions

Plateforme de planification de sessions de travail pour une petite équipe. Un membre sélectionne les participants et la durée, l'application propose les créneaux où tout le monde est disponible, puis le manager accepte ou refuse la demande.

## Démarrage local

Prérequis : Node 18+ et Python 3.11+.

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..
```

Dans deux terminaux :

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --reload --port 8000
```

```bash
cd frontend
npm run dev
```

Ouvrez http://localhost:5173. En `AUTH_MODE=demo`, utilisez le sélecteur d'identité dans la barre latérale. Le manager défini par `MANAGER_EMAIL` voit les actions d'acceptation et de refus.

## Connexion Google Calendar

Cette intégration fonctionne avec des comptes Gmail personnels : aucun Google Workspace, Google Group ou compte de service n'est nécessaire.

1. Activez **Google Calendar API** dans un projet Google Cloud.
2. Configurez Google Auth Platform en audience **External**, statut **Testing**, puis ajoutez chaque adresse de `TEAM_MEMBERS` comme utilisateur test.
3. Dans **Data Access**, ajoutez les scopes `calendar.freebusy` et `calendar.app.created`. Pour écrire dans un agenda partagé existant, ajoutez aussi `calendar.events`.
4. Créez un client OAuth **Web application**.
5. Ajoutez `http://localhost:5173` aux origines JavaScript autorisées.
6. Ajoutez `http://localhost:8000/api/google/calendar/callback` aux URI de redirection autorisés.
7. Copiez le Client ID et le Client Secret dans `.env`, puis utilisez `AUTH_MODE=google`.

```env
APP_ENV=development
APP_SECRET=replace-with-a-long-random-value
AUTH_MODE=google
FRONTEND_URL=http://localhost:5173
ALLOWED_HOSTS=localhost,127.0.0.1
SESSION_TTL_MINUTES=480

MANAGER_EMAIL=manager@gmail.com
TEAM_MEMBERS=manager@gmail.com,membre@gmail.com

GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
GOOGLE_REDIRECT_URI=http://localhost:8000/api/google/calendar/callback
GOOGLE_TARGET_CALENDAR_ID=
```

Après sa connexion Google, chaque membre clique sur **Connecter mon agenda**. Un membre accorde uniquement le scope `calendar.freebusy`. Le manager accorde ce scope et `calendar.app.created`, qui crée un agenda secondaire **3istor Sessions** et ne donne aucun accès aux détails de son agenda personnel. Les refresh tokens sont chiffrés en base avec une clé dérivée de `APP_SECRET`.

Pour utiliser un agenda partagé existant, renseignez son identifiant dans `GOOGLE_TARGET_CALENDAR_ID`. Le manager utilise alors le scope `calendar.events` et doit disposer du droit de modifier les événements de cet agenda. Les autres membres conservent uniquement `calendar.freebusy`. Après ce changement, le manager doit reconnecter son agenda une fois.

Le calcul des disponibilités appelle exclusivement l'endpoint Google FreeBusy : aucun titre, description, participant ou détail d'un événement personnel n'est demandé. Quand le manager accepte, l'API crée un événement unique, ajoute les participants comme invités et utilise `sendUpdates=all`. L'identifiant stable empêche les doublons en cas de nouvelle tentative.

## Sécurité et production

- Le rôle manager est calculé exclusivement côté serveur à partir de `MANAGER_EMAIL`; le navigateur ne peut pas se promouvoir manager.
- Le jeton d'identité Google est échangé une seule fois côté serveur. Il n'est jamais conservé dans `localStorage` ou `sessionStorage`.
- La session utilise un jeton aléatoire dont seul le hash est stocké en base. En production, le cookie est `HttpOnly`, `Secure`, `SameSite=Lax` et préfixé `__Host-`.
- Les mutations provenant d'une origine différente de `FRONTEND_URL` sont refusées en production.
- Les réponses API privées ne sont pas mises en cache et incluent des en-têtes de sécurité. La documentation interactive de l'API est désactivée en production.
- Le mode démo, HTTP, les secrets faibles et les domaines génériques sont refusés automatiquement si `APP_ENV=production`.
- Les entrées sont validées côté API, les conflits de créneaux sont revérifiés au moment de l'envoi et de l'acceptation.
- Pour plusieurs instances, remplacez SQLite par PostgreSQL via `DATABASE_URL`.
- Les notifications in-app sont toujours créées. L'e-mail au manager est envoyé si les variables SMTP sont configurées.

### Variables minimales de production

Générez `APP_SECRET` une seule fois et conservez-le dans le gestionnaire de secrets du cloud :

```bash
openssl rand -hex 32
```

Exemple à adapter à votre domaine :

```env
APP_ENV=production
APP_SECRET=valeur-aleatoire-de-64-caracteres
AUTH_MODE=google
SESSION_TTL_MINUTES=480

FRONTEND_URL=https://sessions.example.com
ALLOWED_HOSTS=sessions.example.com
DATABASE_URL=sqlite:////var/lib/3istor/worksession.db

GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
GOOGLE_REDIRECT_URI=https://sessions.example.com/api/google/calendar/callback
GOOGLE_TARGET_CALENDAR_ID=identifiant@group.calendar.google.com

MANAGER_EMAIL=manager@gmail.com
TEAM_MEMBERS=manager@gmail.com,membre@gmail.com
```

Le frontend et `/api` doivent idéalement être exposés sous le même domaine via un reverse proxy. Celui-ci doit :

- rediriger HTTP vers HTTPS ;
- transmettre correctement le protocole HTTPS à Uvicorn ;
- limiter le débit global et la taille des requêtes ;
- ajouter une politique CSP adaptée à Google Identity Services ;
- ne jamais exposer `.env`, SQLite, les sauvegardes ou les logs ;
- chiffrer les sauvegardes et restreindre leur accès.

En production, n'utilisez jamais `--reload`. Limitez `--forwarded-allow-ips` uniquement aux adresses du proxy de confiance. Le compte système exécutant l'application doit être le seul à pouvoir lire les secrets (`chmod 600 .env` si un fichier `.env` est utilisé).

## Tests

```bash
pytest backend/tests
pip-audit -r backend/requirements.txt
cd frontend && npm run build
npm audit
```
