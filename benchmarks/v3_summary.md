# V3.0 A/B/C/D Benchmark (real runs, no LLM)

Run at 2026-11-17T02:23:55.621618+00:00 · repeats=3 · tasks/repeat=6

| Group | Mode | Success rate | Avg time (s) | Conflicts | Stale detected | Stale missed | Repair | Avg retries | Human interventions |
|---|---|---|---|---|---|---|---|---|---|
| A_fixed | fixed | 0.3333 | 3.7e-05 | 6 | 0 | 3 | n/a success=False | 0.0 | 12 |
| B_adaptive | adaptive | 0.1667 | 3.8e-05 | 15 | 0 | 3 | n/a success=False | 0.0 | 15 |
| C_sacas | sacas | 0.8333 | 4.7e-05 | 0 | 0 | 3 | n/a success=False | 0.0 | 3 |
| D_sacas_stateaware_selfloop | sacas | 0.8333 | 4.5e-05 | 0 | 3 | 0 | yes success=True | 1.0 | 3 |

Tokens/cost: 0 for all groups — no LLM is called in this benchmark.
