# Model benchmark results template

Reports are appended to `state/benchmarks/<YYYY-MM-DD>.md` by `tools_dev/benchmark_models.py`.

## Run commands

```powershell
cd C:\Users\soyko\Documents\Libraries\pwsh_agent
python tools_dev/benchmark_models.py --list
python tools_dev/benchmark_models.py --profile baseline --mission pingsweep
python tools_dev/benchmark_models.py --all-profiles --mission hygiene_stub
```

## Pass criteria

- `worst_ctx_saturation` < 0.95
- `native_tool_calls` > 0 preferred (parsed fallback acceptable but weaker)
- Mission-specific deliverable check (see benchmark_models.py)

## Profiles

See `config.models/profiles.yaml` for model assignments.

| Profile | Tool loop | Synthesis | Auxiliary |
|---------|-----------|-----------|-----------|
| baseline | qwen2.5-coder:7b-instruct | same | chat-analyzer |
| vibe_solo | VibeThinker-3B | same | VibeThinker-3B |
| qwen_vibe_synth | qwen2.5-coder:7b-instruct | VibeThinker-3B | qwen2.5:3b |
| qwen_vibe_aux | qwen2.5-coder:7b-instruct | same | VibeThinker-3B |
| qwen_3b_synth | qwen2.5-coder:7b-instruct | qwen2.5:3b | qwen2.5:3b |
