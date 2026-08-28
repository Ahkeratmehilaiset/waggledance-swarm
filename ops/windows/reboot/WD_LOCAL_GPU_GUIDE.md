# Local GPU guide

This workstation has one verified local CUDA/PyTorch environment reserved for
optional agent workloads. Cloud-hosted Codex and Claude model inference does
not become faster from this GPU. Only local Python code that is explicitly
launched with the environment below can benefit.

Canonical interpreter:

```text
C:\PDAM_LOGBOOK\AM_TRAINING_RUN_20260525_084143\AM_TRAINING\.venv-am-gpu\Scripts\python.exe
```

Verified 2026-08-19:

- PyTorch `2.7.1+cu118`
- CUDA runtime `11.8`
- `torch.cuda.is_available() == True`
- physical GPU 1: NVIDIA GeForce GTX 980, about 4 GB VRAM

PowerShell example:

```powershell
$env:CUDA_VISIBLE_DEVICES = '1'
& 'C:\PDAM_LOGBOOK\AM_TRAINING_RUN_20260525_084143\AM_TRAINING\.venv-am-gpu\Scripts\python.exe' .\local_script.py
```

CMD example:

```cmd
set CUDA_VISIBLE_DEVICES=1
C:\PDAM_LOGBOOK\AM_TRAINING_RUN_20260525_084143\AM_TRAINING\.venv-am-gpu\Scripts\python.exe local_script.py
```

The wrapper is also available:

```cmd
C:\PDAM_LOGBOOK\AM_TRAINING_RUN_20260525_084143\AM_TRAINING\Aja-AM-GPU.cmd python local_script.py
```

Inside the child process, physical GPU 1 is remapped to logical `cuda:0`.
Keep memory use below the approximately 4 GB limit.

Do not install another CUDA toolkit, alter global Python, modify a project's
own environment, or use physical GPU 0 without explicit operator permission.
Agents in other projects may read this guide and invoke the canonical
interpreter for suitable local tensor, embedding, image, or batch workloads;
there is no automatic acceleration merely from mentioning CUDA in a handoff.
