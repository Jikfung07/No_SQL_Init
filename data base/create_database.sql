-- Script de création de la base de données NoSQL
CREATE DATABASE IF NOT EXISTS rental_nosql_demo;
USE rental_nosql_demo;

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id VARCHAR(64) PRIMARY KEY,
    brand VARCHAR(100),
    model VARCHAR(100),
    daily_price DECIMAL(10,2),
    status VARCHAR(32)
);

CREATE TABLE IF NOT EXISTS rentals (
    rental_id VARCHAR(64) PRIMARY KEY,
    vehicle_id VARCHAR(64),
    customer_id VARCHAR(64),
    rental_day DATE,
    total_price DECIMAL(10,2),
    rental_status VARCHAR(32)
);

CREATE TABLE IF NOT EXISTS insurance_offers (
    offer_id VARCHAR(64) PRIMARY KEY,
    rental_id VARCHAR(64),
    company_name VARCHAR(100),
    price DECIMAL(10,2),
    status VARCHAR(32)
);
