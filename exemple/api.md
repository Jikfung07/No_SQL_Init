# API REST proposee

Prefixe commun : `/api/v1`. JSON en entree/sortie. Les routes marquees
« authentifie » exigent une session ou un jeton ; les roles doivent etre
verifies cote serveur. Les identifiants d'exemple correspondent aux fichiers
du dossier `documents/`.

## Comptes

| Methode et route | Acces | Role | Effet / evenement |
| --- | --- | --- | --- |
| `POST /users` | Public | — | Cree un compte ; `UserRegistered`. |
| `POST /sessions` | Public | — | Authentifie et retourne le jeton/session. |
| `GET /users/me` | Authentifie | Utilisateur | Lit son profil, sans hash ni secret. |
| `PATCH /users/me` | Authentifie | Utilisateur | Modifie son profil. |

Exemple `POST /users` :

```json
{
  "email": "lea.martin@example.test",
  "password": "mot-de-passe-saisi-par-le-client",
  "displayName": "Lea Martin",
  "role": "BOTH"
}
```

Le mot de passe n'apparait jamais dans une reponse, un document public ou un
evenement. En base, seul un hash robuste est conserve.

## Annonces

| Methode et route | Acces | Role | Effet / evenement |
| --- | --- | --- | --- |
| `GET /listings?city=Paris&checkIn=2026-10-20&checkOut=2026-10-23&guests=2` | Public | — | Recherche dans une projection de lecture. |
| `GET /listings/{listingId}` | Public | — | Detail d'une annonce publiee. |
| `POST /listings` | Authentifie | Hote | Cree un brouillon. |
| `PATCH /listings/{listingId}` | Authentifie | Proprietaire | Modifie son annonce ; `ListingUpdated`. |
| `POST /listings/{listingId}/publish` | Authentifie | Proprietaire | Publie si les champs requis sont valides ; `ListingPublished`. |
| `POST /listings/{listingId}/pause` | Authentifie | Proprietaire | Suspend temporairement l'annonce. |

Exemple minimal `POST /listings` :

```json
{
  "title": "Studio lumineux pres du canal",
  "description": "Studio calme et equipe, a quelques minutes des transports.",
  "location": {
    "city": "Paris",
    "countryCode": "FR"
  },
  "capacity": {
    "guests": 2,
    "bedrooms": 1,
    "beds": 1,
    "bathrooms": 1
  },
  "amenities": ["wifi", "kitchen"],
  "nightlyPrice": 95,
  "currency": "EUR"
}
```

Le document complet est dans `documents/listing.json`.

## Reservations

| Methode et route | Acces | Role | Effet / evenement |
| --- | --- | --- | --- |
| `POST /reservations` | Authentifie | Voyageur | Cree une demande `PENDING_HOST` ; `ReservationRequested`. |
| `GET /reservations/{reservationId}` | Authentifie | Voyageur ou hote concerne | Lit la reservation. |
| `GET /me/reservations?as=guest` | Authentifie | Voyageur | Liste ses sejours. `as=host` liste les demandes recues. |
| `POST /reservations/{id}/confirm` | Authentifie | Hote concerne | Confirme une demande valide ; `ReservationConfirmed`. |
| `POST /reservations/{id}/reject` | Authentifie | Hote concerne | Refuse une demande en attente ; `ReservationRejected`. |
| `POST /reservations/{id}/cancel` | Authentifie | Partie autorisee | Annule selon la politique ; `ReservationCancelled`. |
| `POST /reservations/{id}/complete` | Authentifie | Systeme ou hote | Termine un sejour confirme apres le depart ; `StayCompleted`. |

Exemple `POST /reservations` :

```json
{
  "listingId": "lst_paris_001",
  "checkIn": "2026-10-20",
  "checkOut": "2026-10-23",
  "guestCount": 2
}
```

Le serveur determine l'identite du voyageur depuis la session, copie l'hote
et le prix depuis l'annonce, puis recalcule le montant. Il ne fait pas
confiance a un total fourni par le client. La reponse initiale en attente est
illustree par `documents/reservation-requested.json` ; `documents/reservation.json`
montre le meme agregat apres confirmation et fin du sejour.

Le serveur doit re-verifier atomiquement l'absence de chevauchement au moment
de confirmer. Deux requetes simultanees ne doivent pas confirmer le meme
logement pour les memes nuits. En cas de conflit, retourner `409 Conflict`.

## Messagerie

| Methode et route | Acces | Role | Effet / evenement |
| --- | --- | --- | --- |
| `GET /conversations` | Authentifie | Participant | Liste ses conversations. |
| `GET /conversations/{id}/messages?before={cursor}` | Authentifie | Participant | Liste paginee des messages. |
| `POST /conversations/{id}/messages` | Authentifie | Participant | Ajoute un message ; `MessageSent`. |

Exemple `POST /conversations/cnv_res_2026_0042/messages` :

```json
{
  "text": "Bonjour, merci pour votre reservation."
}
```

Le `senderId` vient de la session, jamais du JSON client. Le message est
enregistre separement (`documents/message.json`) ; la conversation ne garde
qu'un apercu du dernier message.

## Avis

| Methode et route | Acces | Role | Effet / evenement |
| --- | --- | --- | --- |
| `GET /listings/{listingId}/reviews` | Public | — | Liste les avis publies. |
| `POST /reservations/{id}/review` | Authentifie | Voyageur du sejour | Cree un avis apres `COMPLETED` ; `ReviewSubmitted`. |

Exemple :

```json
{
  "rating": 5,
  "comment": "Logement propre et bien situe."
}
```

L'API derive `authorId` et `listingId` de la reservation, et un index unique
sur `reservationId` empeche le depot de plusieurs avis pour le meme sejour.

## Erreurs et evenements

- `400 Bad Request` : JSON ou champs invalides.
- `401 Unauthorized` : utilisateur non authentifie.
- `403 Forbidden` : utilisateur authentifie mais non autorise sur la ressource.
- `404 Not Found` : identifiant inconnu ou ressource non visible.
- `409 Conflict` : transition d'etat invalide, conflit de version ou dates non disponibles.

Emettre un evenement seulement apres la validation de la commande et
l'enregistrement de la modification. `eventId` doit etre unique ; les
consommateurs dedupliquent sur cet identifiant et traitent un meme agregat
dans l'ordre de `aggregateVersion`. Les vues de recherche peuvent etre
reconstruites a partir des evenements ou rafraichies depuis les agregats.
