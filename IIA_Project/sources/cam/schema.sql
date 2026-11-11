-- CAM - Road Camera Network (PostgreSQL / SQLite)
CREATE TABLE CAMERAS (
    camera_id VARCHAR(8) PRIMARY KEY,
    location_name VARCHAR(60),
    lat NUMERIC(9,6),
    lon NUMERIC(9,6)
);

CREATE TABLE PLATE_CAPTURES (
    capture_id INTEGER PRIMARY KEY,
    plate_id VARCHAR(12),
    camera_id VARCHAR(8) REFERENCES CAMERAS(camera_id),
    captured_at TEXT,
    observed_make VARCHAR(30),
    observed_model VARCHAR(30),
    observed_colour VARCHAR(20),
    ocr_confidence NUMERIC(4,3)
);

-- CONTEXT-ADDED (R1: zones, camera placement and challans, FK-linked to CAMERAS and PLATE_CAPTURES)
CREATE TABLE ZONES (
    zone_id INTEGER PRIMARY KEY,
    zone_name VARCHAR(40),
    city VARCHAR(40)
);

CREATE TABLE CAMERA_ZONES (
    camera_id VARCHAR(8) PRIMARY KEY REFERENCES CAMERAS(camera_id),
    zone_id INTEGER REFERENCES ZONES(zone_id),
    road VARCHAR(60)
);

-- issued_at is ISO text like captured_at; challan_status PENDING / PAID / COURT
CREATE TABLE CHALLANS (
    challan_id INTEGER PRIMARY KEY,
    capture_id INTEGER REFERENCES PLATE_CAPTURES(capture_id),
    violation VARCHAR(40),
    fine_inr INTEGER,
    challan_status VARCHAR(10),
    issued_at TEXT
);
-- CONTEXT-END
