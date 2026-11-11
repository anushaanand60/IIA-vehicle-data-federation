-- REG - Regional Transport Office (PostgreSQL / SQLite)
CREATE TABLE OWNERS (
    owner_id INTEGER PRIMARY KEY,
    full_name VARCHAR(80),
    address_line VARCHAR(160),
    city VARCHAR(40)
);

CREATE TABLE VEHICLE_REGISTRATION (
    registration_id INTEGER PRIMARY KEY,
    registration_no VARCHAR(12),
    owner_id INTEGER REFERENCES OWNERS(owner_id),
    make VARCHAR(30),
    model VARCHAR(30),
    colour VARCHAR(20),
    fuel_type VARCHAR(10),
    registered_on DATE,
    reg_status VARCHAR(10),
    rto_code CHAR(4)
);

-- CONTEXT-ADDED (R1: vehicle identity, ownership chain and contacts spread over FK-linked tables)
CREATE TABLE VEHICLE_IDENTITY (
    registration_id INTEGER PRIMARY KEY REFERENCES VEHICLE_REGISTRATION(registration_id),
    chassis_no VARCHAR(17) UNIQUE,
    engine_no VARCHAR(14),
    mfg_year INTEGER
);

-- transfer_type is SALE / INHERITANCE / AUCTION
CREATE TABLE OWNERSHIP_HISTORY (
    transfer_id INTEGER PRIMARY KEY,
    registration_id INTEGER REFERENCES VEHICLE_REGISTRATION(registration_id),
    prev_owner_id INTEGER REFERENCES OWNERS(owner_id),
    new_owner_id INTEGER REFERENCES OWNERS(owner_id),
    transfer_date DATE,
    transfer_type VARCHAR(12)
);

CREATE TABLE OWNER_CONTACTS (
    owner_id INTEGER PRIMARY KEY REFERENCES OWNERS(owner_id),
    mobile VARCHAR(10),
    pincode CHAR(6)
);
-- CONTEXT-END
