# Réplication — copie des réservations dans Redis

Le service Transport ne va jamais lire la base des réservations (MongoDB).
Il garde **sa propre copie** dans Redis et la met à jour uniquement quand il reçoit des événements.
C'est ce qui lui donne son autonomie : si le service Hébergement tombe, le Transport continue de répondre.

## Lancer

Prérequis : Python 3.9+. Sur Mac et Linux la commande est `python3` (pas `python`).

```bash
python3 -m pip install -r requirements.txt

# Démo sans serveur Redis (Redis simulé en mémoire)
python3 replicate.py --source file --fake

# Démo avec un vrai Redis
docker run -d --name redis-test -p 6379:6379 redis
python3 replicate.py --source file --reset
python3 replicate.py --show

# Branché sur le vrai stream (intégration avec le reste du projet)
python3 replicate.py --source stream

# Tests
python3 test_replication.py
```

Pour inspecter une réservation directement dans Redis :

```bash
docker exec -it redis-test redis-cli HGETALL replica:reservation:res_1001
```

Si `pip` refuse d'installer (erreur « externally-managed-environment »), passer par un environnement virtuel :
`python3 -m venv .venv && source .venv/bin/activate`, puis relancer l'installation.

Variables d'environnement utiles : `REDIS_URL` (défaut `redis://localhost:6379/0`), `EVENT_STREAM` (défaut `domain-events`).

## Événements gérés

| Événement | Effet sur le réplica |
|---|---|
| `ReservationEffectuee` | **Crée** la copie de la réservation |
| `TaxiReserve` | taxiStatus = RESERVED + infos du taxi |
| `TaxiConfirme` | taxiStatus = CONFIRMED |
| `TaxiAnnule` | taxiStatus = CANCELLED |

Format d'un événement (émetteur, récepteur, données modifiées, nouvelles valeurs) :

```json
{"eventId": "evt-003", "type": "TaxiReserve", "emitter": "transport-service", "receiver": "transport-service",
 "reservationId": "res_1001", "version": 2, "occurredAt": "...", "data": {"taxiId": "taxi_42"}}
```

## Structure Redis

- `replica:reservation:<id>` : hash (la copie)
- `replica:reservations` : set des ids répliqués
- `replica:events:processed` : set des eventId déjà vus
- `replica:dead-letter` : liste des événements inexploitables

## Cas gérés

- **Doublon** : même `eventId` reçu deux fois, ignoré.
- **Événement en retard** : version plus ancienne que celle du réplica, ignoré.
- **Orphelin** : mise à jour reçue avant la création, mise en dead-letter.
- **Invalide** : champ manquant ou type inconnu, dead-letter.
- **Reprise après crash** (mode stream) : le message n'est acquitté qu'après l'écriture.

## À aligner avec la Personne 3

Noms des champs, nom du stream, et format du message (champ `payload` JSON ou champs à plat : les deux sont gérés dans `_decode_stream_message`).
