-- Mock of CAM, the road camera network (PostgreSQL 16 on laptop 4). Mirrors design PDF section 3.4.
-- BIGSERIAL becomes INTEGER PRIMARY KEY; TIMESTAMPTZ values are stored the way PostgreSQL prints them.
CREATE TABLE CAMERAS (
    camera_id      VARCHAR(8) PRIMARY KEY,
    location_name  VARCHAR(60) NOT NULL,
    lat            NUMERIC(9,6),
    lon            NUMERIC(9,6)
);

CREATE TABLE PLATE_CAPTURES (
    capture_id       INTEGER PRIMARY KEY,
    plate_id         VARCHAR(12) NOT NULL,      -- raw OCR: about 5% misread O<->0, I<->1
    camera_id        VARCHAR(8) NOT NULL REFERENCES CAMERAS (camera_id),
    captured_at      TIMESTAMPTZ NOT NULL,      -- '2026-08-30 21:14:05+05:30'
    observed_make    VARCHAR(30),
    observed_model   VARCHAR(30),
    observed_colour  VARCHAR(20),               -- null when unreadable at night
    ocr_confidence   NUMERIC(4,3) NOT NULL      -- 0 to 1; only CAM has it: must stay unmapped
);
CREATE INDEX ix_capture_plate ON PLATE_CAPTURES (plate_id);
