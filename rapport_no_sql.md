# Escales — Rapport du projet NoSQL

## 1. Présentation du projet

Escales est une petite application de location de logements, inspirée du fonctionnement de plateformes comme Airbnb.

L'objectif est de permettre à un utilisateur de consulter des annonces, de rechercher un logement selon plusieurs critères (ville, prix, capacité, mots-clés), mais aussi de publier ou modifier une annonce et d'effectuer une demande de réservation.

À travers ce projet, nous avons surtout cherché à mettre en pratique les notions vues en cours sur les bases NoSQL, les agrégats DDD et la communication entre plusieurs services.

Pour cela, nous avons développé deux services qui utilisent MongoDB et Redis. Ils communiquent grâce à des événements afin que les informations restent synchronisées.

## 2. Organisation des services

Notre application repose sur deux services principaux, qui ont chacun un rôle différent.

### Service Locations

Le premier service s'occupe de la gestion des logements et des réservations. Il utilise MongoDB pour enregistrer les données.

Nous avons défini deux agrégats :

- **Listing** : représente une annonce de logement. Il contient notamment le titre, la description, la ville, le prix, la capacité d'accueil, les informations de l'hôte et le statut de l'annonce. Chaque annonce possède un identifiant et une version.
- **Reservation** : représente une réservation effectuée par un utilisateur. Elle contient le logement concerné, les informations du voyageur, les dates du séjour, le montant et le statut de la réservation.

Ces deux agrégats sont séparés, car une annonce et une réservation n'ont pas forcément le même cycle de vie.

Nous avons également mis en place un système d'**outbox**. Lorsqu'une annonce est créée ou modifiée, l'événement correspondant est enregistré dans le même document MongoDB que l'annonce. Cela permet de conserver la modification et son événement ensemble, même si la communication avec Redis ne se fait pas immédiatement.

### Service Recherche

Le deuxième service est consacré à la recherche de logements.

Au lieu d'interroger directement MongoDB à chaque recherche, il utilise une copie des annonces stockée dans Redis. Cette copie est mise à jour à partir des événements envoyés par le Service Locations.

Ce service conserve également des statistiques de recherche, notamment le nombre de recherches effectuées par ville, grâce à l'agrégat **SearchQueryStats**.

L'intérêt est de séparer la gestion des données de leur consultation.

## 3. Choix des bases NoSQL

Nous avons choisi deux bases NoSQL qui répondent à des besoins différents.

### MongoDB

MongoDB est notre base principale. Elle stocke les annonces et les réservations sous forme de documents.

Nous l'avons choisie parce qu'elle permet de représenter assez facilement des objets comme un logement ou une réservation, avec plusieurs propriétés regroupées dans un même document.

Elle offre aussi une certaine souplesse si nous souhaitons ajouter des informations par la suite.

Nous avons ajouté des index pour éviter les doublons sur les identifiants et faciliter certaines recherches, notamment par ville et par statut.

### Redis

Redis est utilisé pour la recherche et la communication entre les services.

Nous utilisons plusieurs types de structures :

- Des données JSON pour conserver les annonces copiées depuis MongoDB.
- Des ensembles pour retrouver les annonces d'une ville.
- Des hash pour les versions et les statistiques.
- **Redis Streams** pour transmettre les événements.

Redis permet donc de disposer d'une version des annonces adaptée à la recherche, sans devoir consulter systématiquement MongoDB.

## 4. Communication entre les services

Une partie importante de notre projet concerne les événements et la synchronisation des données.

Par exemple, lorsqu'un utilisateur publie une nouvelle annonce, celle-ci est d'abord enregistrée dans MongoDB.

Le Service Locations prépare ensuite un événement, comme :

- `ListingPublished` : une annonce vient d'être publiée.
- `ListingUpdated` : une annonce a été modifiée.
- `ListingDeleted` : une annonce a été supprimée.

Ces événements sont enregistrés dans l'outbox MongoDB, puis transmis par un worker dans un Redis Stream appelé `domain-events`.

Le Service Recherche récupère ensuite les événements pour mettre à jour sa propre copie des annonces dans Redis.

Pour éviter les problèmes de synchronisation, nous avons prévu une vérification des versions et des identifiants d'événements. Cela permet notamment d'éviter qu'un événement reçu plusieurs fois applique plusieurs fois la même modification, ou qu'une ancienne version remplace une version plus récente.

Les événements incorrects sont conservés séparément pour pouvoir identifier les erreurs.

Il peut donc y avoir un petit délai entre la modification d'une annonce et son apparition dans les résultats de recherche. C'est ce qu'on appelle la **cohérence éventuelle**.

## 5. Lancement et démonstration

Nous avons utilisé Docker Compose pour simplifier le lancement de l'application et de ses différents services.

Depuis le dossier principal du projet, il suffit d'exécuter :

```bash
docker compose up --build
```

Une fois les conteneurs démarrés, l'interface est accessible à l'adresse :

http://localhost:8001/

Depuis cette interface, nous pouvons consulter les annonces, effectuer des recherches, publier un logement et envoyer une demande de réservation.

Nous pouvons également accéder aux documentations des API :

- http://localhost:8000/docs — Service Locations
- http://localhost:8001/docs — Service Recherche

Pour observer le fonctionnement des événements et de la réplication, nous disposons également des endpoints `/events`, `/projection` et `/stats`.

Le scénario principal de démonstration consiste à créer une annonce, vérifier son enregistrement dans MongoDB, puis observer sa transmission et son apparition dans Redis.

## 6. Limites et améliorations possibles

Notre projet reste une application de démonstration réalisée dans le cadre du cours.

Certaines fonctionnalités présentes sur une véritable plateforme de réservation n'ont pas été développées, comme la connexion sécurisée des utilisateurs, le paiement en ligne ou la vérification complète des disponibilités d'un logement.

Nous avons préféré nous concentrer sur les objectifs principaux du TP : utiliser plusieurs bases NoSQL, organiser les données en agrégats et faire communiquer les services avec des événements.

Pour aller plus loin, nous pourrions améliorer la sécurité, ajouter davantage de fonctionnalités liées aux réservations et mettre en place une meilleure surveillance des événements.

Il faudrait également prévoir une gestion plus complète de la conservation des événements et de la reconstruction des données Redis en cas de problème.

## 7. Conclusion

Ce projet nous a permis de mieux comprendre comment deux services peuvent travailler ensemble tout en utilisant des bases de données différentes.

MongoDB sert à enregistrer les annonces et les réservations, tandis que Redis permet de gérer les recherches et de transmettre les événements.

La principale difficulté a été de comprendre comment synchroniser les données sans que les deux services dépendent directement l'un de l'autre.

Au final, Escales nous a permis d'appliquer concrètement plusieurs notions du cours, notamment les agrégats DDD, les bases NoSQL et la communication asynchrone entre services.