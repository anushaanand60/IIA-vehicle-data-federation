-- THEFT - Police Crime Records (SQLite)
CREATE TABLE CRIME_RECORDS (
    incident_id INTEGER PRIMARY KEY,
    vehicle_number TEXT,
    fir_no TEXT,
    reported_date INTEGER,
    incident_type TEXT,
    stolen_flag TEXT,
    recovered_flag TEXT,
    case_status TEXT,
    police_station TEXT
);

-- CONTEXT-ADDED (R1: stations, stolen property and the people on each FIR, FK-linked to CRIME_RECORDS)
-- CRIME_RECORDS.police_station is a logical FK onto STATIONS.station_name (declared in data/context_graph.json)
CREATE TABLE STATIONS (
    station_name VARCHAR(60) PRIMARY KEY,
    district VARCHAR(40),
    zone VARCHAR(20)
);

-- recovered_on is epoch seconds, like CRIME_RECORDS.reported_date
CREATE TABLE STOLEN_PROPERTY (
    incident_id INTEGER PRIMARY KEY REFERENCES CRIME_RECORDS(incident_id),
    chassis_no VARCHAR(17),
    engine_no VARCHAR(14),
    recovered_on INTEGER NULL
);

CREATE TABLE PERSONS (
    person_id INTEGER PRIMARY KEY,
    name VARCHAR(80),
    mobile VARCHAR(10),
    address VARCHAR(160)
);

-- role is COMPLAINANT / ACCUSED / WITNESS
CREATE TABLE INCIDENT_PERSONS (
    incident_id INTEGER REFERENCES CRIME_RECORDS(incident_id),
    person_id INTEGER REFERENCES PERSONS(person_id),
    role VARCHAR(12),
    PRIMARY KEY (incident_id, person_id, role)
);
-- CONTEXT-END
