# Invented-content judge calibration

- Date: 2026-11-12; labels: `data/golden/invented_calibration.jsonl` (hand-labelled against each answer's rebuilt context); keep the judge at 9/10 or better

- Agreement on counted items: 9/10 (model `openai/gpt-oss-20b`)

| id | counted | hand label | judge | agree | judge reason |
|---|---|---|---|---|---|
| g018 | yes | INVENTED | INVENTED | yes | The answer claims that VoltCart’s shipping policy does not provide a standard goodwill credit for delayed deliveries, a detail not present in the provided sources. |
| g020 | yes | INVENTED | SUPPORTED | **no** | The answer correctly states the 7‑day return window and original packaging requirement, which are directly supported by the sources. |
| g022 | yes | SUPPORTED | SUPPORTED | yes | The answer correctly states that no student discount exists, which is consistent with the provided sources. |
| g023 | yes | INVENTED | INVENTED | yes | The answer claims a price‑matching policy that is not stated in the provided sources. |
| g024 | yes | INVENTED | INVENTED | yes | The answer claims the NoteBook 14 supports Linux and lists specific hardware compatibility details that are not stated or implied in the provided sources. |
| g021 | yes | SUPPORTED | SUPPORTED | yes | The claim of a £20 maximum goodwill credit for late delivery in the UK is directly supported by the source. |
| g031 | yes | SUPPORTED | SUPPORTED | yes | All claims are directly supported by the provided sources. |
| g034 | yes | SUPPORTED | SUPPORTED | yes | All claims are directly supported by the sources. |
| g035 | yes | SUPPORTED | SUPPORTED | yes | All claims are directly supported by the sources. |
| g036 | yes | SUPPORTED | SUPPORTED | yes | The answer correctly states the RAM amount as given in the sources. |
| g030 | extra | INVENTED | SUPPORTED | **no** | All claims are directly supported by the user manual. |
| g040 | extra | INVENTED | SUPPORTED | **no** | All steps are directly supported by the user manual. |
