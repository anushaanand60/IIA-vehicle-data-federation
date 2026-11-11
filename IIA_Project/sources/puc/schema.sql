-- PUC - Pollution-Certificate Authority (SQLite)
CREATE TABLE POLLUTION_CERT (
    cert_no VARCHAR(20) PRIMARY KEY,
    regn_number VARCHAR(15),
    valid_upto DATE,
    tested_at VARCHAR(60),
    emission_norm VARCHAR(20)
);

-- CONTEXT-ADDED (R1: test centres, analysers and the individual emission tests behind each certificate)
CREATE TABLE TEST_CENTRES (
    centre_id INTEGER PRIMARY KEY,
    centre_name VARCHAR(60),
    city VARCHAR(40),
    lat NUMERIC(9,6),
    lon NUMERIC(9,6)
);

CREATE TABLE ANALYSERS (
    analyser_id INTEGER PRIMARY KEY,
    centre_id INTEGER REFERENCES TEST_CENTRES(centre_id),
    calibrated_on DATE
);

-- test_ts is ISO text; result PASS / FAIL; a test needs the vehicle physically at the centre
CREATE TABLE CERT_TESTS (
    test_id INTEGER PRIMARY KEY,
    cert_no VARCHAR(20) REFERENCES POLLUTION_CERT(cert_no),
    analyser_id INTEGER REFERENCES ANALYSERS(analyser_id),
    test_ts TEXT,
    co_pct NUMERIC(5,2),
    hc_ppm INTEGER,
    result VARCHAR(4)
);
-- CONTEXT-END
