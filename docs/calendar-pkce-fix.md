# Connexion Calendar : Missing code verifier

Le parcours OAuth recréait un `Flow` lors du callback sans restaurer la preuve
PKCE utilisée au départ. Google pouvait refuser l'échange avec
`invalid_grant: Missing code verifier`.

Chaque nouveau state signé contient désormais un nonce aléatoire. Une preuve
PKCE de 43 caractères est dérivée du state et de `APP_SECRET` par HMAC-SHA256,
avec un préfixe distinct de celui de la signature. La preuve n'est pas incluse
dans l'URL : seul son challenge S256 est transmis au départ. Après validation
de la signature et de l'expiration du state, le callback reconstruit la même
preuve, même dans un nouveau processus. L'auto-génération du Flow est désactivée
pour ne pas remplacer cette preuve explicite ; PKCE reste actif.

Aucun changement de Vault, de base ou de permissions Google n'est nécessaire.
`APP_SECRET` doit rester stable pendant le parcours, comme pour la signature
du state. Après déploiement, fermer les anciennes pages d'autorisation et
relancer « Connecter mon agenda » depuis le site. Les parcours commencés avec
l'ancienne image ne peuvent pas récupérer leur ancienne preuve perdue.

Les tests utilisent la vraie construction du Flow et interceptent l'échange
réseau pour comparer le verifier au challenge initial (manager et membre),
vérifier l'unicité des parcours et le rejet des states expirés ou modifiés.
La connexion réelle Google et les droits sur l'agenda restent à tester après
déploiement. L'avertissement d'application non vérifiée est indépendant.
