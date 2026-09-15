-- Mock of REG, the Regional Transport Office (PostgreSQL 16 on laptop 1). Mirrors design PDF section 3.1.
-- SQLite accepts the PostgreSQL type names; SERIAL becomes INTEGER PRIMARY KEY.
CREATE TABLE OWNERS (
    owner_id      INTEGER PRIMARY KEY,
    full_name     VARCHAR(80) NOT NULL,
    address_line  VARCHAR(160),
    city          VARCHAR(40)
);

CREATE TABLE VEHICLE_REGISTRATION (
    registration_id  INTEGER PRIMARY KEY,
    registration_no  VARCHAR(12) NOT NULL UNIQUE,  -- 'DL01AB1234': no separators, upper case
    owner_id         INT NOT NULL REFERENCES OWNERS (owner_id),
    make             VARCHAR(30),                  -- nullable: dirty rows exist
    model            VARCHAR(30),
    colour           VARCHAR(20),
    fuel_type        VARCHAR(10),                  -- only REG has it: must stay unmapped
    registered_on    DATE NOT NULL,                -- ISO
    reg_status       VARCHAR(10) NOT NULL CHECK (reg_status IN ('ACTIVE', 'SUSPENDED', 'CANCELLED')),
    rto_code         CHAR(4)
);
