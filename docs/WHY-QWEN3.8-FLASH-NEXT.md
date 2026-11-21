# Why this design fits Qwen3.8-Flash-Next on Strata

## The model

Qwen3.8-Flash-Next (architecture `qwen4exp`), as read from the GGUF:

| Property | Value | Consequence for caching |
|---|---|---|
| Layers | 48: 36 Gated DeltaNet + 12 attention (`full_attention_interval` 4) | most of the state is fixed-size |
| Attention KV | 2 KV heads × 256 dims, 12 layers | ~12 KB per token at int8 |
| Experts | 512, 10 active per token (~6B active of ~176B total) | prefill is slow on one GPU: reuse matters |
| Trained context | 262,144 tokens | long agent sessions are realistic |

### Snapshots are small

A conventional dense model keeps keys and values for every layer and head. For example 80 layers × 8 KV heads × 128
dims × 2 (K and V) × 2 bytes ≈ 330 KB per token: a 120K-token conversation is ~40 GB, too large to keep many on disk.

Here only 12 layers keep per-token KV, with 2 heads, stored at 8 bits: 12 × 2 × 256 × 2 × 1 byte ≈ 12 KB per token,
so ~1.5 GB at 120K tokens. The 36 DeltaNet layers keep a fixed-size recurrent state whatever the length. Measured
snapshot files:

| Conversation | Snapshot | Save | Restore after a full restart |
|---|---:|---:|---:|
| 8K tokens | 349 MiB | 0.7 s | 0.69 s |
| 32K tokens | 703 MiB | 0.94 s | 1.13 s |
| 120K tokens | 1.96 GiB | 2.1 s | 3.02 s |

An 80 GB budget holds about 40 full-length conversations or 150–200 typical ones.

### The constraint the hybrid model brings

A DeltaNet layer's state is a running summary: it cannot be cut back to an earlier token. Pure-attention engines can
keep one tree of shared prefixes and truncate anywhere; here a conversation can only be resumed from a point where its
whole state was saved. That is why this project saves **complete snapshots at chain-hash boundaries** rather than
sharing pieces, and why several snapshots of one conversation overlap on disk.

### Prefill is the bottleneck

With 512 experts and only part of them resident in 32 GB of VRAM, reading a prompt on one MI100 runs at about
500–600 tokens/s: 13 s for Hermes' 10.5K-token system prompt, 45 s for 38K tokens, ~230 s for 125K. Decode is
comparatively fast (45–64 tokens/s). Every avoided re-read is therefore worth tens of seconds.

## The engine

Strata serves this model on gfx908 (MI100) with expert streaming from RAM and NVMe. It already has:

* a prompt cache that reuses the longest common prefix of the previous conversations (~4 kept in RAM);
* llama.cpp-style slot files: `POST /slots/0?action=save|restore` with `--slot-save-path`;
* `/metrics` with the server start time and engine generation, which tells the Context Manager that the engine lost
  its state.

So no engine patch was needed: this project adds the memory that outlives the process, and the policy deciding when to
save and restore.

## What was ruled out

SGLang with HiCache would give a multi-tier radix cache out of the box, and research code for composing cached
DeltaNet segments (HYPIC) targets it. On this hardware it is not an option (checked 21 Nov 2026):

* SGLang's ROCm build targets gfx942/gfx950/gfx1250 only; gfx908 is rejected.
* The model's SGLang support and published checkpoints (FP8 185 GB, NVFP4 135 GB for Blackwell) are NVIDIA-only; a
  2-bit path exists only as unmerged, CUDA-only patches.

The Context Manager talks to the engine through one small adapter (`strata_context_manager/engine.py`), so another
engine with the same slot API (llama.cpp's server has it) or SGLang on NVIDIA hardware could replace Strata later.
