# Exemple de modele NoSQL pour un site de location

Cette proposition de MVP part d'une base de donnees orientee documents, par
exemple MongoDB. Elle sert de support de conception : elle ne constitue pas
encore une API implementee.

## Contenu

- `schemas/` : JSON Schema Draft 2020-12 pour les agregats et evenements.
- `documents/` : documents JSON d'exemple, un par type de ressource.
- `events.json` : exemples d'evenements metier emis lors des actions.
- `api.md` : routes HTTP proposees, commandes, lectures et regles metier.

## Agregat et evenement, simplement

Un agregat est un groupe de donnees modifie ensemble sous la responsabilite
d'une racine. Par exemple, `Reservation` est modifiee par ses commandes de
confirmation ou d'annulation. `ReservationConfirmed` est ensuite un evenement :
il decrit le fait que la reservation vient d'etre confirmee.

Les references entre agregats utilisent des identifiants (`listingId`,
`guestId`, `hostId`) plutot que de copier leurs documents entiers. La recherche
peut, elle, lire une projection denormalisee concue pour afficher rapidement
les annonces.

## Agregats retenus

| Agregat / collection | Racine | Contenu et raison de la frontiere |
| --- | --- | --- |
| `users` | `userId` | Profil autonome ; aucune reservation n'est embarquee dedans. |
| `listings` | `listingId` | Donnees de l'annonce modifiees ensemble ; reservations referencees. |
| `reservations` | `reservationId` | Cycle de vie et prix instantane propres a un sejour. |
| `conversations` | `conversationId` | Participants et apercu du dernier message seulement. |
| `messages` | `messageId` | Documents append-only separes pour eviter une conversation non bornee. |
| `reviews` | `reviewId` | Avis lie a une reservation terminee. |

## Evenements importants

| Evenement | Agregat | Declencheur |
| --- | --- | --- |
| `UserRegistered` | User | Creation d'un compte valide. |
| `ListingPublished` | Listing | Publication d'une annonce complete. |
| `ListingUpdated` | Listing | Modification d'une annonce. |
| `ReservationRequested` | Reservation | Demande creee apres validation des dates. |
| `ReservationConfirmed` | Reservation | Hote confirme une demande en attente. |
| `ReservationRejected` | Reservation | Hote refuse une demande en attente. |
| `ReservationCancelled` | Reservation | Annulation autorisee par les regles du projet. |
| `StayCompleted` | Reservation | Sejour confirme termine. |
| `MessageSent` | Message | Message enregistre pour les participants. |
| `ReviewSubmitted` | Review | Avis valide soumis apres le sejour. |

Un evenement represente un fait passe et ne doit pas etre modifie. Il porte un
`eventId` unique pour dedupliquer les consommateurs, ainsi que
`aggregateVersion` pour ordonner les changements d'un agregat.

## Etats et disponibilite

Cycle de reservation suggere :

```text
PENDING_HOST -> CONFIRMED -> COMPLETED
      |              |
      +-> REJECTED   +-> CANCELLED
      +-> CANCELLED
```

La regle d'absence de chevauchement doit etre garantie atomiquement au moment
de confirmer une reservation, et pas seulement lors d'une recherche prealable.
Par exemple, le serveur peut reserver atomiquement chaque nuit dans une
collection `booked_nights` avec une contrainte unique sur `(listingId, date)`,
ou utiliser une transaction avec verrouillage/version optimiste. En cas de
conflit, une demande doit etre refusee ou rester non confirmee.

## Index MongoDB de depart

- `listings`: index compose sur `status`, `location.city`, `capacity.guests`.
- `reservations`: index compose sur `guestId`, `createdAt` descendant.
- `reservations`: index compose sur `hostId`, `status`, `createdAt` descendant.
- `messages`: index compose sur `conversationId`, `createdAt` croissant.
- `reviews`: index unique sur `reservationId` pour un seul avis par sejour.
- `events`: index unique sur `eventId`; index compose sur
  `aggregateId`, `aggregateVersion`.

Les choix d'index sont a valider avec les requetes reelles du projet.

## Lancer les validations de structure

Les exemples sont des fichiers JSON autonomes et les schemas sont des JSON
Schema. Aucun serveur de base de donnees n'est requis pour les lire. Les
regles impliquant plusieurs documents (permissions, chevauchement, etats)
doivent etre validees dans le service applicatif en plus du schema.
