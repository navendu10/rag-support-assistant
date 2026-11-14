# Rewrite change and retrieval: stage `v7`
- Date: 2026-11-14; the `v7` index searched with `v6`'s stored rewrites and with `v7`'s; first search only, no LLM calls
- Rewrites changed: **28/50** (search query, standalone question or product codes)

| Rewrites | Recall@5 | Hit@1 | MRR |
|---|---|---|---|
| v6's | 42/45 (93%) | 33/45 (73%) | 0.83 |
| v7's | 43/45 (96%) | 34/45 (76%) | 0.84 |

| id | v6 search query | v7 search query | v7 codes |
|---|---|---|---|
| g002 | Laptops return window opened | Laptops return window opened | NB-14 |
| g003 | Power and charging return window opened | Power and charging return window opened | PB-20K |
| g005 | XR-2200 Wi-Fi Router factory reset | XR-2200 Wi-Fi Router factory reset | XR-2200 |
| g007 | WashPro 7 kg Washing Machine E-74 error code | WashPro 7 kg Washing Machine error code E-74 | WM-700 |
| g008 | WashingPro 7 kg Washing Machine error E-47 | WashPro 7 kg Washing Machine error E-47 | WM-700 |
| g009 | VisionMax 55" 4K TV lines on screen | VisionMax 55" 4K TV VM-55Q screen lines | VM-55Q |
| g013 | returns | returns policy | - |
| g016 | Audio return window opened | Audio return window opened | - |
| g018 | delivery goodwill credit | delivery goodwill credit amount | - |
| g019 | Payment fraud detection | payment fraud checks | - |
| g020 | returns | returns window closed | - |
| g021 | delivery UK goodwill credit | delivery UK | - |
| g024 | NoteBook 14 Laptop Linux support | NoteBook 14 Linux support | NB-14 |
| g025 | stores location Manchester | store location Manchester | - |
| g031 | discounts | discount senior citizen | - |
| g033 | delivery | delivery standard | - |
| g034 | Power and charging plane | Power and charging take on a plane | PB-20K |
| g035 | SmartWatch Fit 2 swim water resistance | SmartWatch Fit 2 swim | SW-FIT2 |
| g037 | Warranty | products warranty | - |
| g040 | AeroBuds Pro AB-PRO pairing | AeroBuds Pro AB-PRO pairing | AB-PRO |
| g041 | XR-2000 Wi-Fi Router reset button duration | XR-2000 Wi-Fi Router reset button duration | XR-2000 |
| g042 | WashPro 7 kg Washing Machine error E-74 | WM-700 Washing Machine error E-74 | WM-700 |
| g045 | Laptops return window opened | Laptops return window opened | NB-14 |
| g046 | TV return window opened | TVs return window | VM-55Q |
| g047 | Laptops return window opened | Laptops return window opened | - |
| g048 | delivery countries mismatch | delivery billing country mismatch | - |
| g049 | TVs warranty claim process | TVs press complaint handling | - |
| g050 | Policy question: returns outside window | returns outside window | - |
