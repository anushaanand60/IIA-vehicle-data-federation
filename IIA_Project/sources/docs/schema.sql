-- CONTEXT-ADDED (R3: sixth autonomous source) DOCS - Documents agency (SQLite by default, port 8006)
-- One plain table, no full-text extension: FTS5 / tsvector / MySQL FULLTEXT differ per engine,
-- so the wrapper builds its own BM25 index from this table and the DDL stays portable ANSI SQL.
-- Gold links from a document to database rows are NOT stored here (they live in data/phase3_gold.json,
-- evaluation only): a real document store would not know another agency's primary keys.
CREATE TABLE DOCUMENTS (
    doc_id VARCHAR(12) PRIMARY KEY,
    doc_type VARCHAR(20),
    title VARCHAR(160),
    body TEXT,
    issuer VARCHAR(60),
    issued_on DATE
);
