# Field research — what real systems do (2024–2026)

Collected 16 Sep 2026 to ground the innovation features in `docs/superpowers/plans/2026-09-16-overhaul.md`.

## Facts

1. **India, Supreme Court (2025–26):** ANPR cameras to be integrated with the Insurance Information Bureau (IIB) and VAHAN so e-challans for uninsured vehicles are issued automatically; police get handheld devices linked to IIB + VAHAN for real-time checks. https://www.jurishour.in/supreme-court/anpr-based-e-challans-uninsured-vehicles/ , https://www.thelawadvice.com/news/sc-orders-automatic-e-challans-for-uninsured-vehicles-directs-4-year-insurance-for-new-cars-6-years-for-two-wheelers
2. **Scale:** ~56% of vehicles on Indian roads uninsured (~16.5 crore of 30.5 crore). Same source.
3. **IIB as the data hub** between insurers and enforcement; pilots in Delhi and Telangana. https://iib.gov.in/ , https://motoranalytics.iib.gov.in/cms/motor
4. **e-Challan mechanics:** ANPR reads plate → VAHAN insurance-validity date → automatic "no insurance" challan (₹2,000 / ₹4,000 repeat, MV Act §196). https://www.acko.com/traffic-rules/how-anpr-cctv-issue-traffic-challans-online/
5. **UK Motor Insurance Database (MID) / askMID:** central record of insured vehicles; police + DVLA query it via ANPR; citizens self-check their own vehicle free. https://www.mib.org.uk/
6. **UK Continuous Insurance Enforcement:** keeper of an uninsured vehicle is liable unless SORN filed; advisory letter → £100 fixed penalty → clamp/impound. A grace-period + escalation ladder.
7. **Operation Tutelage / Scalis (2025):** ANPR hits compared with MIB's policy database; persistent mismatch sets a marker that flags the vehicle to patrols; seized vehicles. https://www.mib.org.uk/media-centre/news/2025/april/16-uninsured-vehicles-seized-by-police-scotland-to-mark-launch-of-operation-scalis/
8. **Cloned plates via FASTag + ANPR (India, 2025 trials):** camera-observed make/model/colour compared with the tag's registered vehicle; mismatch → alert. Same logic as our REG-vs-CAM rule. https://www.cartoq.com/fastag-going-automatic-number-plate-recognition-anpr-coming-trials-begin-in-india/
9. **Stolen vehicles:** ~1.5–2 lakh/year in India, moved across state lines quickly → real-time cross-source check, not batch reconciliation.
10. **PUC ↔ insurance:** insurers may not renew without a valid PUC; Delhi "No PUC, No Fuel" (Apr 2026) gates fuel sales on VAHAN's PUC status. https://mmcm.in/blog/what-is-puc-certificate-in-india/
11. **Scrapping (RVSF):** only certified facilities issue scrapping certificates, which deregister the vehicle in VAHAN — our SHREDDING/SCRAPPED case.
12. **Wrong e-challans are common enough to need their own appeals machinery (Delhi, 2025):** Delhi Traffic Police launched an **Online Challan Dispute System** — a citizen can contest a challan online with evidence, and it is cancelled if the record does not hold up. The dispute is not an exception path, it is a designed part of the workflow. https://fatehlegacy.in/blogs/contest-wrong-traffic-challan-delhi-2025
13. **Plate misreads are the dominant cause:** guides to contesting challans put **~90% of wrongful challans down to ANPR OCR errors** — the confusable pairs O/0, 8/B, 1/I, 5/S, 2/Z — which means the fine is raised against a *different, real* vehicle whose owner has done nothing. https://mparivahanguide.com/wrong-challan-complaint/
14. **Cloned plates weaponise this (Hyderabad, 2025):** police busted a racket running **duplicate number plates so e-challans landed on the original owner** — "one number, two scooters". The production detection signal is *impossible travel*: the same plate seen by two cameras too far apart for the time between them. It is the subject of UK patent **GB2448780A** ("method of detecting cloned vehicles" from ANPR sighting pairs) and is how the Met's national ANPR service treats implausible sighting sequences. https://the420.in/hyderabad-clone-number-plate-racket-busted-anpr-cctv/ , https://patents.google.com/patent/GB2448780A/en , https://www.met.police.uk/advice/advice-and-information/rs/road-safety/automatic-number-plate-recognition-anpr/
15. **Theft status lags behind reality:** NCRB's **Vahan Samanvay** is the national stolen-vehicle index, and removing a recovered vehicle from it is a manual, slow, police-station-level process — so a stolen-then-recovered vehicle can sit wrong in one database while another is correct. An enforcement decision therefore has to read police records *at the moment of the decision*, not from a nightly copy. https://sudhirrao.com/how-to-remove-a-vehicle-from-the-ncrb/

## Features derived (in the plan)

| Plan task | Mirrors |
|---|---|
| 2.1 Plate photo → OCR → lookup | ANPR camera → VAHAN/IIB lookup |
| 2.2 Evidence bundle in the Ministry report | proof bundle before seizure (MIB / police) |
| 2.3 Watchlist + alerts + risk score | Operation Tutelage marker, IIB flags |
| 2.4 Citizen self-check (data-minimised) | askMID public check |
| 2.5 Grace-period / escalation ladder | UK CIE: advisory → penalty → impound |
| 2.6 Query audit log | IIB / MIB regulated-access logging |
| 0.5 Unknown plate onboarding | handheld device registration + insurance check |
| C Challan Guard — verify before you fine, with a citizen dispute loop | Delhi's Online Challan Dispute System (fact 12) + ANPR misread repair (13) + impossible-travel clone detection, GB2448780A / Met ANPR (14) + live theft check against a lagging index (15) |

## Viva lines

"Challan Guard sits between the camera and the fine. About 90% of wrongful e-challans come from a plate misread, so before any fine is raised we resolve the read against the registration database through a fixed confusable table, check the sighting pair for impossible travel the way the cloned-vehicle patent GB2448780A does, check police records for a theft reported before the sighting, and only then ask whether the vehicle was insured *on the day it was seen*." — "The citizen dispute is not a complaints inbox: it re-runs the identical verification live against the insurer, the RTO and the police, and cancels the challan if the answer has changed — which is only possible because we federate instead of warehousing, so 'the record now' is always what we read. And if a source is unreachable we hold the fine rather than issue one on partial evidence."

"Our mediator does what the Supreme Court has now mandated: cross-reference registration, insurance, theft and camera databases live, not from a stale copy, before issuing a challan." — "The UK's Motor Insurers' Bureau runs the production analogue, Operation Tutelage: live ANPR sightings against its policy database, persistent mismatches flagged to patrols — our conflict detection plus refusal when a source is down." — "Our REG-versus-camera mismatch rule mirrors India's FASTag-vs-ANPR cloned-plate trials on national highways."
