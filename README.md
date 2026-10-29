# Projet NoSQL — Escales

Escales est une mini-application de location de logements qui montre deux
services applicatifs, deux bases NoSQL et la réplication asynchrone d'une
projection par échange d'événements.

## Démarrage rapide

Prérequis : Docker Desktop avec Docker Compose, et des ports locaux libres
`8000`, `8001`, `27017` et `6379`.

Depuis la racine du dépôt :

```powershell
docker compose up --build
```

Ouvrir ensuite :

- Application de démonstration : <http://localhost:8001/>
- Documentation interactive du service Locations : <http://localhost:8000/docs>
- Documentation interactive du service Recherche : <http://localhost:8001/docs>
- État du service Locations : <http://localhost:8000/health>
- État du service Recherche : <http://localhost:8001/health>

Le service Locations crée au premier démarrage une annonce de démonstration.
L'arrêt avec `Ctrl+C` garde les données. Pour repartir de zéro :

```powershell
docker compose down -v
```

Cette dernière commande supprime les volumes de données du projet.

## Architecture

```text
Navigateur
   |                    |
   | lecture/recherche  | création / modification
   v                    v
Service Recherche      Service Locations
   | Redis                  | MongoDB
   | projection             | agrégats source
   | statistiques           | outbox d'événements
   +---- Redis Streams <----+
         domain-events
```

### Services et agrégats

| Service | Base | Agrégats possédés | Responsabilité |
| --- | --- | --- | --- |
| `listings-service` | MongoDB | `Listing`, `Reservation` | Source de vérité des annonces et réservations ; écrit les événements à publier dans l'outbox embarquée de l'agrégat. |
| `search-service` | Redis | `SearchQueryStats` | Consomme les événements, construit une projection de lecture des annonces et comptabilise les recherches. |

Une annonce dans MongoDB est la source de vérité. La copie Redis ne possède que
les champs utiles à la recherche et ne doit pas servir à modifier l'annonce.
Les réservations forment un agrégat distinct référencé par `listingId`.

### Bases NoSQL : choix

- **MongoDB** est une base orientée documents adaptée aux agrégats de location :
  une annonce et son état courant sont lus et modifiés ensemble. Les documents
  peuvent accueillir les attributs évolutifs d'un logement. Des index uniques
  sont créés sur `listingId` et `reservationId`.
- **Redis** fournit des structures en mémoire adaptées à la recherche simple et
  rapide, aux compteurs d'activité et à Redis Streams, utilisé ici comme journal
  d'événements entre les deux services.

Les services ne partagent pas leurs collections d'agrégats : le Service
Recherche ne lit que Redis, et le Service Locations ne lit que MongoDB pour ses
données métier. Il n'y a pas de PostgreSQL dans le nouveau parcours NoSQL.
L'ancien code PHP/PostgreSQL reste dans `app/site` comme code historique du
dépôt, mais n'est pas nécessaire au lancement ni à la démonstration Escales.

## Flux événementiel et cohérence

1. `POST /listings` enregistre l'annonce et un événement `ListingPublished`
   dans le même document MongoDB. Une modification ajoute
   `ListingUpdated`; une suppression logique ajoute `ListingDeleted`.
2. Le petit worker d'outbox du Service Locations publie les événements dans le
   stream Redis `domain-events`.
3. La publication utilise une clé Redis sur `eventId` et un script atomique :
   si le worker tombe après `XADD`, sa reprise ne republie pas le même événement.
4. Le Service Recherche consomme le stream dans le groupe
   `search-projection`, met à jour la projection Redis, puis acquitte le message.
   Une projection de version supérieure ignore les doublons et les événements
   arrivés en retard. Les messages invalides vont dans
   `domain-events:dead-letter` et apparaissent dans les logs.
5. `GET /search` lit la projection et incrémente l'agrégat Redis
   `SearchQueryStats`. La projection est éventuellement cohérente : juste après
   une écriture, quelques instants peuvent être nécessaires avant que la
   recherche reflète le changement.

L'outbox stockée sur le document de l'agrégat évite la perte d'un événement
entre l'écriture MongoDB et sa publication. Le prototype garde les événements
non publiés jusqu'à l'accusé de publication. Dans un déploiement à grande
échelle, une collection d'outbox séparée, des politiques de rétention, des
identités/authentifications et des limites de taille doivent être ajoutées.

## API principale

### Service Locations — `http://localhost:8000`

| Méthode | Route | Description |
| --- | --- | --- |
| `GET` | `/listings` | Liste les annonces publiées depuis MongoDB. |
| `POST` | `/listings` | Crée une annonce et déclenche `ListingPublished`. |
| `GET` | `/listings/{listingId}` | Lit une annonce publiée. |
| `PATCH` | `/listings/{listingId}` | Modifie une annonce et déclenche `ListingUpdated`. |
| `DELETE` | `/listings/{listingId}` | Supprime logiquement une annonce et déclenche `ListingDeleted`. |
| `POST` | `/reservations` | Crée une réservation et déclenche `ReservationRequested`. |
| `GET` | `/reservations?guest_id=usr_demo_guest` | Liste les réservations, filtrables par voyageur ou hôte. |
| `PATCH` | `/reservations/{reservationId}/status` | Accepte `CONFIRMED`, `REJECTED` ou `CANCELLED`. |
| `GET` | `/events` | Affiche les derniers événements publiés. |

Exemple de création d'annonce :

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/listings `
  -ContentType 'application/json' `
  -Body '{"title":"Appartement près du parc","description":"Appartement lumineux et confortable, proche des transports.","city":"Lyon","countryCode":"FR","hostId":"usr_demo_host","nightlyPrice":82,"guests":3}'
```

### Service Recherche — `http://localhost:8001`

| Méthode | Route | Description |
| --- | --- | --- |
| `GET` | `/search?city=Lyon&guests=2&maxPrice=100` | Recherche dans la projection Redis. |
| `GET` | `/projection` | Inspecte les annonces répliquées. |
| `GET` | `/stats` | Lit l'agrégat de statistiques de recherche Redis. |
| `GET` | `/events` | Inspecte les événements disponibles dans le stream. |
| `GET` | `/health` | Vérifie Redis et le groupe de consommateurs. |

Exemple de recherche et de vérification de propagation :

```powershell
Invoke-RestMethod 'http://localhost:8001/search?city=Lyon&guests=2'
Invoke-RestMethod http://localhost:8001/projection
Invoke-RestMethod http://localhost:8001/stats
```

`hostId` et `guestId` sont des identifiants de démonstration fournis par le
client. Ce MVP n'implémente pas l'authentification, les paiements ni la
réservation atomique de nuits ; il ne doit pas être utilisé en production.

## Organisation des fichiers

```text
app/api/
  listings/          Service source MongoDB et publisher d'outbox
  search/            Consommateur Redis, projection, recherche et interface web
  requirements.txt   Dépendances Python des deux services
app/site/             Ancienne application PHP/PostgreSQL conservée
exemple/              Exemples de documents, schémas et événements
docker-compose.yml    Lancement de l'application NoSQL
```

## Arrêt et inspection

```powershell
docker compose ps
docker compose logs -f listings-service search-service
docker compose down
```

Les tests de validation du contrat d'événement peuvent être exécutés avec les
dépendances du projet :

```powershell
python -m pip install -r app/api/requirements.txt
python -m unittest discover -s app/api/tests -v
```

Le dépôt contient aussi [le rapport du projet](rapport_no_sql.pdf) et sa source
éditable dans [rapport_no_sql.md](rapport_no_sql.md). Pour régénérer le PDF
après modification du rapport : `python app/api/scripts/generate_report.py`.
