-- INS - Insurance Provider (MySQL / SQLite)
CREATE TABLE INSURERS (
    insurer_id INTEGER PRIMARY KEY,
    insurer_name VARCHAR(60)
);

CREATE TABLE POLICY_RECORDS (
    policy_id INTEGER PRIMARY KEY,
    vehicle_reg VARCHAR(14),
    insurer_id INTEGER REFERENCES INSURERS(insurer_id),
    policy_type VARCHAR(20),
    policy_start VARCHAR(10),
    policy_until VARCHAR(10),
    is_active INTEGER,
    premium_inr DECIMAL(10,2)
);
