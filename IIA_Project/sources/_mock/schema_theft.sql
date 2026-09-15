-- Mock of THEFT, the police crime records (SQLite 3 on laptop 3). Mirrors design PDF section 3.3.
CREATE TABLE CRIME_RECORDS (
    incident_id     INTEGER PRIMARY KEY,
    vehicle_number  TEXT NOT NULL,              -- 'dl 01 ab 1234': lower case, spaces
    fir_no          TEXT NOT NULL,
    reported_date   INTEGER NOT NULL,           -- Unix epoch seconds
    incident_type   TEXT NOT NULL CHECK (incident_type IN ('THEFT', 'SHREDDING', 'HIT_AND_RUN')),
    stolen_flag     TEXT NOT NULL CHECK (stolen_flag IN ('Y', 'N')),
    recovered_flag  TEXT NOT NULL CHECK (recovered_flag IN ('Y', 'N')),
    case_status     TEXT NOT NULL CHECK (case_status IN ('OPEN', 'CLOSED')),
    police_station  TEXT                        -- only THEFT has it: must stay unmapped
);
CREATE INDEX ix_crime_vehicle ON CRIME_RECORDS (vehicle_number);
