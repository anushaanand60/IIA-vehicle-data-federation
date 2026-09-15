-- Mock of PUC, the Pollution-Certificate Authority: the fifth agency for the UC6 add-a-source demo
-- (design PDF section 8.3). It is registered only in mock_mappings_uc6.json, never in the base registry.
CREATE TABLE POLLUTION_CERT (
    cert_no      TEXT PRIMARY KEY,
    regn_number  TEXT NOT NULL,                 -- 'DL 01 AB 1234': upper case, spaces
    valid_upto   DATE NOT NULL                  -- ISO
);
