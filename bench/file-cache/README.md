# File state cache tests

The scripts behind the numbers in [docs/FILE-CACHE.md](../../docs/FILE-CACHE.md). Each `*_run.sh` stops the live
Strata on the lab VM, runs the patched engine on port 8082 with the matching `*_client.py`, then restores the live
server. Paths are the lab's (`/root/Strata-f1`, `/nvme/f1`); adapt them before use.

| Test | What it measures |
|---|---|
| f16 | Accuracy and first-token KL: normal read, normal read again (noise), loaded from cache; small/medium/large file, compaction replay, three files, 60K deep |
| f17 | Same, plus partial re-reads (head 15 %, tail 15 %, both edges 10 %) |
| f18 | Rerun of f17 cases |
| f31 | Loading at any position (the four shifts mod 4) |
| f32 | Several files by id in another context and order (answers vs a normal read), token check, fallback |
| f34 | f32 + f31 on the compact (v2) image format |
| f37 | Clean capture vs capture inside the conversation; whether an edge-check signal predicts drift |

`replay_blocks.json` (a recorded Hermes conversation used by the compaction replay) is not included.
