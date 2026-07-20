# 3istor Sessions

Plateforme de planification de sessions de travail pour une petite équipe. Un membre sélectionne les participants et la durée, l'application propose les créneaux où tout le monde est disponible, puis le manager accepte ou refuse la demande.

## Démarrage local

Prérequis : Node 18+ et Python 3.11+.

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
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
APP_SECRET=dev-secret
AUTH_MODE=google
FRONTEND_URL=http://localhost:5173

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
- En production, utilisez `AUTH_MODE=google`, une valeur `APP_SECRET` forte et HTTPS.
- Le mode démo est refusé si `APP_ENV=production`.
- Les entrées sont validées côté API, les conflits de créneaux sont revérifiés au moment de l'envoi et de l'acceptation.
- Pour plusieurs instances, remplacez SQLite par PostgreSQL via `DATABASE_URL`.
- Les notifications in-app sont toujours créées. L'e-mail au manager est envoyé si les variables SMTP sont configurées.

## Tests

```bash
pytest backend/tests
cd frontend && npm run build
```
