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
