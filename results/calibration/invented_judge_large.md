# Invented-content judge calibration

- Date: 2026-11-12; labels: `data/golden/invented_calibration.jsonl` (hand-labelled against each answer's rebuilt context); keep the judge at 9/10 or better

- Agreement on counted items: 9/10 (model `openai/gpt-oss-120b`)

| id | counted | hand label | judge | agree | judge reason |
|---|---|---|---|---|---|
| g018 | yes | INVENTED | INVENTED | yes | The answer claims the shipping policy does not provide a standard goodwill credit for delayed deliveries, which is not stated in the sources. |
| g020 | yes | INVENTED | SUPPORTED | **no** | None |
| g022 | yes | SUPPORTED | SUPPORTED | yes | None |
| g023 | yes | INVENTED | INVENTED | yes | The answer states a price‑matching policy that is not mentioned in the provided sources. |
| g024 | yes | INVENTED | INVENTED | yes | Claims that installing Linux does not void the warranty and that the hardware is fully supported by Linux, which are not stated in the provided sources. |
| g021 | yes | SUPPORTED | SUPPORTED | yes | All factual claims match the provided sources. |
| g031 | yes | SUPPORTED | SUPPORTED | yes | None |
| g034 | yes | SUPPORTED | SUPPORTED | yes | All claims are directly supported by the provided sources. |
| g035 | yes | SUPPORTED | SUPPORTED | yes | None |
| g036 | yes | SUPPORTED | SUPPORTED | yes | All factual claims are directly stated in the sources. |
| g030 | extra | INVENTED | INVENTED | yes | Claims about the reset button’s location and needing the power LED lit are not stated in the sources. |
| g040 | extra | INVENTED | SUPPORTED | **no** | All steps are directly described in the provided sources. |
