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

-- CONTEXT-ADDED (R1: the insurer's own identity records, policy parties and claims, FK-linked to POLICY_RECORDS)
-- POLICY_HOLDERS is the insurer's own person record, deliberately not REG's owner
CREATE TABLE POLICY_HOLDERS (
    holder_id INTEGER PRIMARY KEY,
    holder_name VARCHAR(80),
    mobile VARCHAR(10),
    address VARCHAR(160),
    pincode CHAR(6)
);

CREATE TABLE POLICY_PARTIES (
    policy_id INTEGER PRIMARY KEY REFERENCES POLICY_RECORDS(policy_id),
    holder_id INTEGER REFERENCES POLICY_HOLDERS(holder_id),
    chassis_no VARCHAR(17)
);

-- loss_date and intimation_date are DD/MM/YYYY text, like policy_start
-- loss_type THEFT / OWN_DAMAGE / THIRD_PARTY; claim_status OPEN / SETTLED / REJECTED / INVESTIGATING
CREATE TABLE CLAIMS (
    claim_id INTEGER PRIMARY KEY,
    policy_id INTEGER REFERENCES POLICY_RECORDS(policy_id),
    loss_date VARCHAR(10),
    intimation_date VARCHAR(10),
    loss_type VARCHAR(12),
    amount_claimed DECIMAL(10,2),
    claim_status VARCHAR(16),
    surveyor_name VARCHAR(60)
);
-- CONTEXT-END
