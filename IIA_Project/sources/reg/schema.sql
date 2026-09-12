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
