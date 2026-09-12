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
