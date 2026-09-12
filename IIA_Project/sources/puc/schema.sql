-- PUC - Pollution-Certificate Authority (SQLite)
CREATE TABLE POLLUTION_CERT (
    cert_no VARCHAR(20) PRIMARY KEY,
    regn_number VARCHAR(15),
    valid_upto DATE,
    tested_at VARCHAR(60),
    emission_norm VARCHAR(20)
);
