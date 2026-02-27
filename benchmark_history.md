# StringFinder 성능 벤치마크 기록

| 시간 | 버전/태그 | 데이터셋 | 전체 시간 | Latency | Jitter | 결과 수 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-02-27 10:28:41 | v1.0.4 - Baseline | Set A: Small/Many | 1.068s | 0.055s | 0.7503 | 100 |
| 2026-02-27 10:28:41 | v1.0.4 - Baseline | Set B: Mixed/Large | 0.266s | 0.262s | 0.1873 | 1 |
| 2026-02-27 10:28:41 | v1.0.4 - Baseline | Set C: Binary Mixed | 0.216s | 0.062s | 0.1435 | 50 |
| 2026-02-27 10:28:41 | v1.0.4 - Baseline | Set D: Boolean Early | 0.053s | 0.052s | 0.0348 | 1 |
| 2026-02-27 10:28:41 | v1.0.4 - Baseline | Set E: ASCII Fast | 0.051s | 0.051s | 0.0356 | 1 |
| --------------------------------------------- |
| 2026-02-27 10:38:10 | v1.0.4 - After Batch 1 | Set A: Small/Many | 1.333s | 0.058s | 0.9356 | 100 |
| 2026-02-27 10:38:10 | v1.0.4 - After Batch 1 | Set B: Mixed/Large | 0.221s | 0.216s | 0.1543 | 1 |
| 2026-02-27 10:38:10 | v1.0.4 - After Batch 1 | Set C: Binary Mixed | 0.268s | 0.052s | 0.1879 | 50 |
| 2026-02-27 10:38:10 | v1.0.4 - After Batch 1 | Set D: Boolean Early | 0.052s | 0.051s | 0.0355 | 1 |
| 2026-02-27 10:38:10 | v1.0.4 - After Batch 1 | Set E: ASCII Fast | 0.103s | 0.102s | 0.0712 | 1 |
| --------------------------------------------- |
| 2026-02-27 13:18:00 | v1.0.4 - After Batch 1 Refined | Set A: Small/Many | 2.375s | 0.112s | 1.3283 | 100 |
| 2026-02-27 13:18:00 | v1.0.4 - After Batch 1 Refined | Set B: Mixed/Large | 0.341s | 0.325s | 0.2379 | 1 |
| 2026-02-27 13:18:00 | v1.0.4 - After Batch 1 Refined | Set C: Binary Mixed | 0.423s | 0.056s | 0.2975 | 50 |
| 2026-02-27 13:18:00 | v1.0.4 - After Batch 1 Refined | Set D: Boolean Early | 0.053s | 0.052s | 0.0360 | 1 |
| 2026-02-27 13:18:00 | v1.0.4 - After Batch 1 Refined | Set E: ASCII Fast | 0.153s | 0.152s | 0.0499 | 1 |
| --------------------------------------------- |
| 2026-02-27 13:21:23 | v1.0.4 - After Batch 1 Final | Set A: Small/Many | 2.450s | 0.073s | 1.7030 | 100 |
| 2026-02-27 13:21:23 | v1.0.4 - After Batch 1 Final | Set B: Mixed/Large | 0.327s | 0.316s | 0.2301 | 1 |
| 2026-02-27 13:21:23 | v1.0.4 - After Batch 1 Final | Set C: Binary Mixed | 0.462s | 0.053s | 0.3254 | 50 |
| 2026-02-27 13:21:23 | v1.0.4 - After Batch 1 Final | Set D: Boolean Early | 0.053s | 0.053s | 0.0362 | 1 |
| 2026-02-27 13:21:23 | v1.0.4 - After Batch 1 Final | Set E: ASCII Fast | 0.103s | 0.101s | 0.0711 | 1 |
| --------------------------------------------- |
| 2026-02-27 13:27:43 | v1.0.4 - Batch 1 Final Optim | Set A: Small/Many | 1.541s | 0.060s | 1.0815 | 100 |
| 2026-02-27 13:27:43 | v1.0.4 - Batch 1 Final Optim | Set B: Mixed/Large | 0.313s | 0.305s | 0.2179 | 1 |
| 2026-02-27 13:27:43 | v1.0.4 - Batch 1 Final Optim | Set C: Binary Mixed | 0.105s | 0.053s | 0.0703 | 50 |
| 2026-02-27 13:27:43 | v1.0.4 - Batch 1 Final Optim | Set D: Boolean Early | 0.054s | 0.053s | 0.0367 | 1 |
| 2026-02-27 13:27:43 | v1.0.4 - Batch 1 Final Optim | Set E: ASCII Fast | 0.103s | 0.103s | 0.0700 | 1 |
| --------------------------------------------- |
| 2026-02-27 13:33:14 | v1.0.4 - Batch 2 Final Optim | Set A: Small/Many | 1.385s | 0.059s | 0.9718 | 100 |
| 2026-02-27 13:33:14 | v1.0.4 - Batch 2 Final Optim | Set B: Mixed/Large | 0.254s | 0.247s | 0.1773 | 1 |
| 2026-02-27 13:33:14 | v1.0.4 - Batch 2 Final Optim | Set C: Binary Mixed | 0.105s | 0.053s | 0.0722 | 50 |
| 2026-02-27 13:33:14 | v1.0.4 - Batch 2 Final Optim | Set D: Boolean Early | 0.053s | 0.052s | 0.0356 | 1 |
| 2026-02-27 13:33:14 | v1.0.4 - Batch 2 Final Optim | Set E: ASCII Fast | 0.104s | 0.103s | 0.0716 | 1 |
| --------------------------------------------- |
