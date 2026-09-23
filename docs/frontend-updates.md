# Notification de nouvelle interface

`GET /api/version` renvoie l'empreinte du HTML et le chemin du module JavaScript
contenus dans l'image en cours d'exécution. Aucun identifiant utilisateur, secret
ou chemin du serveur n'est exposé. Comme les autres routes API, cette réponse est
servie avec `Cache-Control: no-store`. La page HTML utilise également `no-store` ;
les ressources Vite gardent leurs noms versionnés.

L'onglet compare le module réellement chargé avec celui annoncé par le serveur,
toutes les 15 secondes lorsqu'il est visible et au retour sur l'onglet. Deux
observations concordantes d'une autre version déclenchent une bannière. Une
panne réseau ou un ancien serveur sans endpoint de version ne bloque pas le site.
« Plus tard » masque cette version pour l'onglet courant ; « Appliquer » demande
confirmation de la perte des saisies non envoyées et recharge avec une URL fraîche.
Il n'y a pas de rechargement automatique ni de suppression des cookies.

Ce mécanisme détecte une nouvelle interface JavaScript disponible sur le serveur,
pas une image seulement construite sur GitHub. Il ne déclenche ni build ni
redémarrage Kubernetes. Il doit être déployé et chargé une première fois : les
anciens onglets sans ce code ne peuvent pas afficher la notification. Une mise à
jour backend seule ne nécessite pas de recharger l'interface et ne déclenche pas
la bannière.
