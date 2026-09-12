# 8-Minute Evaluation Demo Script

Follow this script during in-class and TA evaluation:

| Timing | Action | What to Say / Show |
| :--- | :--- | :--- |
| **0:00 – 0:30** | Pitch & Architecture | *"Four autonomous government/observational databases — Registration, Insurance, Theft, and Road Camera — were designed in isolation. We build a federated mediator layer that discovers correspondences, plans sub-queries, evaluates decisions with provenance, and refuses to guess when data is missing. The data never leaves the sources; every query sees fresh data."* |
| **0:30 – 1:30** | Point out Heterogeneity | Show the 4 sources and 3 DBMS engines. Show how one vehicle has 4 spellings of the plate and 4 date formats: *"DL01AB1234, DL-01-AB-1234, dl 01 ab 1234. Nothing is shared but the road."* |
| **1:30 – 2:30** | Matcher Tab | Open Matcher Tab. Run live matcher on `THEFT` or `CAM`. Show the similarity heatmap matrix. Accept correspondences into `MAPPING_REGISTRY`. |
| **2:30 – 4:00** | Investigate `DL05CD9876` | In Investigate Tab, search `DL-05-cd-9876`. Show source cards, profile, provenance hover, and decision `UNINSURED — REPORT`. Switch to Plan Trace tab: *"UC2 contacted 4 sources."* Then select UC1: *"UC1 contacted only 2 sources (INS + REG)."* |
| **4:00 – 4:45** | Real-time Freshness | Run query for `DL01AB1234`. Show `CLEAR`. Update `policy_until` in INS DB to past date, re-run query: decision flips to `UNINSURED` in under a second. *"No ETL, no data duplication, 100% fresh data."* |
| **4:45 – 5:30** | Conflict Detection | Query `UP16GH1122`. REG says Creta/Red, CAM observed Seltos/Blue. Point to the Conflict Warning panel and Decision: `SUSPICIOUS — POSSIBLE CLONED PLATE`. |
| **5:30 – 6:15** | Failure Demo (UC5) | Stop INS wrapper (simulate network partition). Re-query `DL01AB1234`. Status card turns red, decision flips to `UNDETERMINED`, confidence `LOW`. *"The system refuses to guess when a source is down."* Restart wrapper, rerun -> returns `CLEAR`. |
| **6:15 – 7:45** | Add 5th Source (UC6) | In Catalog & Registry Tab, show `Register New Source` for PUC on port 8005. Add it, map it, requery -> `puc_expiry` appears in profile. *"Extensibility via metadata, zero engine code modified."* |
| **7:45 – 8:15** | Ministry Report | Click *File Report to Ministry*. Download generated official 1-page PDF audit report with digital verification seal. Conclude demo. |
