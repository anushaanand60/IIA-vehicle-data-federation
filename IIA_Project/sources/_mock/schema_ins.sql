-- Mock of INS, the insurance provider (MySQL 8 on laptop 2). Mirrors design PDF section 3.2.
-- SQLite has no ENUM, so policy_type is a VARCHAR carrying the same CHECK list.
CREATE TABLE INSURERS (
    insurer_id    INT PRIMARY KEY,
    insurer_name  VARCHAR(60) NOT NULL
);

CREATE TABLE POLICY_RECORDS (
    policy_id     INT PRIMARY KEY,
    vehicle_reg   VARCHAR(14) NOT NULL,         -- 'DL-01-AB-1234': hyphenated
    insurer_id    INT NOT NULL REFERENCES INSURERS (insurer_id),
    policy_type   VARCHAR(13) NOT NULL CHECK (policy_type IN ('THIRD_PARTY', 'COMPREHENSIVE')),
    policy_start  VARCHAR(10) NOT NULL,         -- 'DD/MM/YYYY' held as text
    policy_until  VARCHAR(10) NOT NULL,         -- 'DD/MM/YYYY' held as text
    is_active     TINYINT(1) NOT NULL,          -- 1 / 0
    premium_inr   DECIMAL(10,2)                 -- only INS has it: must stay unmapped
);
CREATE INDEX ix_policy_vehicle ON POLICY_RECORDS (vehicle_reg);
