# Méthodologie de cadrage : Projet NoSQL

Ce guide récapitule les étapes clés pour structurer votre projet NoSQL avant toute implémentation technique, conformément aux principes de conception orientée requêtes (*query-driven modeling*).

---

## Étape 1 : Cartographier les motifs d'accès (*Access Patterns*)

Contrairement au modèle relationnel (SQL), la modélisation NoSQL découle directement des besoins de lecture et d'écriture.

1. **Recenser les écritures ($W$) :**
   - Identifier les opérations de création et de mise à jour critiques.
   - Spécifier la fréquence et les contraintes transactionnelles (atomicité stricte vs tolérance au délai).
2. **Spécifier les lectures ($R$) :**
   - Lister les requêtes applicatives exactes avec leurs filtres et tris.
   - *Exemple :* « Obtenir les courses du client $X$ pour la date $Y$, triées par heure décroissante. »
3. **Évaluer la volumétrie :**
   - Déterminer la cardinalité des données, la taille moyenne des payloads et les risques de nœuds saturés (*hot partitions*).

---

## Étape 2 : Définir les frontières d'agrégats (*Aggregate Boundaries*)

L'agrégat définit le périmètre d'une transaction unitaire et locale.

1. **Identifier les racines d'agrégats (*Aggregate Roots*) :**
   - Regrouper les entités et objets-valeurs qui partagent le même cycle de vie et doivent garantir des invariants métier stricts.
2. **Arbitrer entre inclusion (*embedding*) et référence :**
   - **Inclusion :** Si les données sont lues et modifiées conjointement (ex. les lignes d'un panier d'achat dans la commande).
   - **Référence par identifiant :** Si l'entité suit un cycle de vie autonome ou est gérée par un autre domaine (ex. l'offre d'assurance vis-à-vis du contrat de location).
3. **Formaliser la structure :**
   - Établir un schéma JSON/BSON représentatif de chaque agrégat principal.

---

## Étape 3 : Concevoir le modèle d'écriture et de partitionnement

Associer l'agrégat au modèle NoSQL adéquat (Document, Clé-Valeur ou Colonnes Larges).

1. **Pour les modèles orientés colonnes larges (ex. Cassandra, ScyllaDB) :**
   - **Clé de partition (*Partition Key*) :** Isole les données sur un nœud cible et prévient le *scatter-gather*.
   - **Colonnes de clustering (*Clustering Columns*) :** Assurent l'ordonnancement physique au sein de la partition pour optimiser les lectures par intervalle.
   - *Exemple :* `PRIMARY KEY ((tenant_id, date_operation), horodatage, id_transaction)`.
2. **Pour les modèles document (ex. MongoDB) :**
   - Définir les index secondaires stricts correspondant exactement aux motifs de filtrage fréquents.

---

## Étape 4 : Projections en lecture et gestion des événements (CQRS)

Séparer le modèle d'écriture (source de vérité) des modèles de lecture dédiés aux vues web.

1. **Publier des événements métier :**
   - Émettre des événements immuables et versionnés lors de chaque transition d'état majeure (ex. `CommandeValidee_v1`).
2. **Construire des projections dédiées :**
   - Mettre en place un projecteur idempotent pour alimenter des vues dénormalisées répondant à des requêtes spécifiques.
3. **Documenter la cohérence à terme :**
   - Prévoir la gestion des doublons (déduplication par `eventId`), la stratégie de rattrapage (*replay*) et la tolérance à la latence de projection.

---

## Étape 5 : Arbitrer les compromis CAP & PACELC

Pour chaque opération critique du système, expliciter le choix opérationnel retenu :

- **En cas de partition réseau (CAP) :**
  - Faut-il refuser l'opération pour préserver la cohérence stricte (**CP**), ou accepter une écriture/lecture avec une vue potentiellement désynchronisée (**AP**) ?
- **En régime nominal (PACELC) :**
  - Privilégie-t-on une latence minimale au prix d'une éventuelle lecture obsolète, ou une cohérence forte au prix d'un aller-retour quorum ?