"""
WaggleDance — MicroModel V3 LoRA Training Script
==================================================
Fine-tunes a small language model using LoRA adapters on collected training data.

Supports:
  - Unsloth (preferred, 2x faster)
  - PEFT/Transformers fallback
  - CPU-only mode (slower but works)

Usage:
    python tools/train_micromodel_v3.py                          # Train with defaults
    python tools/train_micromodel_v3.py --base-model smollm2:135m
    python tools/train_micromodel_v3.py --input data/training_v3.jsonl
    python tools/train_micromodel_v3.py --epochs 3 --lr 2e-4
    python tools/train_micromodel_v3.py --check                  # Check dependencies only
"""

# Import unsloth early ONLY if --no-unsloth is not passed
import sys as _sys
if "--no-unsloth" not in _sys.argv:
    try:
        import unsloth  # Must import before transformers/peft/trl for optimizations
    except ImportError:
        pass

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("train_v3")

DEFAULT_BASE_MODEL = "microsoft/Phi-3.5-mini-instruct"
FALLBACK_MODELS = [
    "unsloth/Phi-3.5-mini-instruct",
    "HuggingFaceTB/SmolLM2-135M-Instruct",
    "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
]


def check_dependencies() -> dict:
    """Check which training backends are available."""
    status = {
        "torch": False,
        "torch_cuda": False,
        "transformers": False,
        "peft": False,
        "unsloth": False,
        "datasets": False,
        "trl": False,
    }

    try:
        import torch
        status["torch"] = True
        status["torch_cuda"] = torch.cuda.is_available()
        log.info(f"  ✓ torch {torch.__version__} "
                 f"(CUDA: {'yes' if status['torch_cuda'] else 'no'})")
    except ImportError:
        log.warning("  ✗ torch not installed")

    for pkg_name in ["transformers", "peft", "datasets", "trl"]:
        try:
            mod = __import__(pkg_name)
            status[pkg_name] = True
            ver = getattr(mod, "__version__", "?")
            log.info(f"  ✓ {pkg_name} {ver}")
        except ImportError:
            log.warning(f"  ✗ {pkg_name} not installed")

    try:
        import unsloth
        status["unsloth"] = True
        log.info(f"  ✓ unsloth (fast LoRA)")
    except ImportError:
        log.info(f"  - unsloth not installed (optional, 2x speedup)")

    return status


def load_training_data(input_path: Path, max_samples: int = 5000) -> list:
    """Load training JSONL."""
    if not input_path.exists():
        log.error(f"Training data not found: {input_path}")
        log.info("Run: python tools/collect_training_data.py first")
        return []

    data = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if "messages" in entry:
                    data.append(entry)
            except json.JSONDecodeError:
                continue

    if len(data) > max_samples:
        # Keep highest confidence
        data.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        data = data[:max_samples]

    log.info(f"Loaded {len(data)} training samples from {input_path}")
    return data


def train_with_unsloth(data: list, base_model: str, output_dir: Path,
                        epochs: int, lr: float, batch_size: int):
    """Train using Unsloth (2x faster LoRA)."""
    import torch
    from unsloth import FastLanguageModel
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from datasets import Dataset

    log.info(f"\n=== Unsloth LoRA Training ===")
    log.info(f"Base model: {base_model}")
    log.info(f"Samples: {len(data)}, Epochs: {epochs}, LR: {lr}")

    max_seq = 256  # Keep short to fit in 8GB VRAM

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=max_seq,
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
    )

    # Format data for SFT
    def format_chat(example):
        messages = example.get("messages", [])
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        return {"text": text}

    dataset = Dataset.from_list(data)
    dataset = dataset.map(format_chat)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clamp batch size for 8GB VRAM
    effective_bs = min(batch_size, 2)
    grad_accum = max(4, 16 // max(effective_bs, 1))
    log.info(f"Effective batch: {effective_bs} x {grad_accum} grad_accum = {effective_bs * grad_accum}")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=max_seq,
        args=TrainingArguments(
            per_device_train_batch_size=effective_bs,
            gradient_accumulation_steps=grad_accum,
            warmup_steps=5,
            num_train_epochs=epochs,
            learning_rate=lr,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10,
            output_dir=str(output_dir / "checkpoints"),
            save_strategy="epoch",
        ),
    )

    import torch
    if torch.cuda.is_available():
        log.info(f"VRAM allocated: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    trainer.train()

    # Report final loss
    if trainer.state.log_history:
        final_loss = [h["loss"] for h in trainer.state.log_history if "loss" in h]
        if final_loss:
            log.info(f"\nFinal training loss: {final_loss[-1]:.4f}")

    if torch.cuda.is_available():
        log.info(f"Peak VRAM: {torch.cuda.max_memory_allocated() / 1024**3:.2f} GB")

    # Save LoRA adapters
    model.save_pretrained(str(output_dir / "lora_adapter"))
    tokenizer.save_pretrained(str(output_dir / "lora_adapter"))
    log.info(f"\nLoRA adapter saved to {output_dir / 'lora_adapter'}")


def train_with_peft(data: list, base_model: str, output_dir: Path,
                     epochs: int, lr: float, batch_size: int):
    """Train using PEFT/Transformers (standard LoRA)."""
    from transformers import (
        AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    )
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer
    from datasets import Dataset
    import torch

    log.info(f"\n=== PEFT LoRA Training ===")
    log.info(f"Base model: {base_model}")
    log.info(f"Samples: {len(data)}, Epochs: {epochs}, LR: {lr}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Device: {device}")

    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Use 4-bit quantization + CPU offloading to fit in 8GB VRAM
    quant_kwargs = {}
    if device == "cuda":
        try:
            from transformers import BitsAndBytesConfig
            quant_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
            log.info("Using 4-bit quantization (NF4 + double quant)")
        except ImportError:
            quant_kwargs["torch_dtype"] = torch.float16
            log.info("bitsandbytes not available, using fp16")

    # CPU offloading: keep most on GPU, overflow to CPU
    max_memory = {0: "6GiB", "cpu": "40GiB"} if device == "cuda" else None

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None,
        max_memory=max_memory,
        **quant_kwargs,
    )

    # Auto-detect target modules from model architecture
    # Phi-3.5 uses fused qkv_proj; most others use separate q/k/v_proj
    model_modules = {n.split(".")[-1] for n, _ in model.named_modules()}
    if "qkv_proj" in model_modules:
        target = ["qkv_proj", "o_proj", "gate_up_proj", "down_proj"]
    else:
        target = ["q_proj", "v_proj"]
    log.info(f"LoRA target modules: {target}")

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=target,
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Enable gradient checkpointing to reduce VRAM usage
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    model.gradient_checkpointing_enable()
    log.info("Gradient checkpointing enabled")

    # Format data
    def format_chat(example):
        messages = example.get("messages", [])
        if hasattr(tokenizer, "apply_chat_template"):
            text = tokenizer.apply_chat_template(messages, tokenize=False)
        else:
            parts = []
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                parts.append(f"<|{role}|>\n{content}")
            text = "\n".join(parts)
        return {"text": text}

    dataset = Dataset.from_list(data)
    dataset = dataset.map(format_chat)

    output_dir.mkdir(parents=True, exist_ok=True)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=256,
        args=TrainingArguments(
            per_device_train_batch_size=min(batch_size, 2),
            gradient_accumulation_steps=max(4, 16 // max(batch_size, 1)),
            warmup_steps=5,
            num_train_epochs=epochs,
            learning_rate=lr,
            fp16=(device == "cuda"),
            bf16=False,
            optim="paged_adamw_8bit" if device == "cuda" else "adamw_torch",
            gradient_checkpointing=True,
            logging_steps=10,
            output_dir=str(output_dir / "checkpoints"),
            save_strategy="epoch",
        ),
    )

    if torch.cuda.is_available():
        log.info(f"VRAM before training: {torch.cuda.memory_allocated() / 1024**3:.2f} GB")

    trainer.train()

    # Report metrics
    if trainer.state.log_history:
        final_loss = [h["loss"] for h in trainer.state.log_history if "loss" in h]
        if final_loss:
            log.info(f"\nFinal training loss: {final_loss[-1]:.4f}")
    if torch.cuda.is_available():
        log.info(f"Peak VRAM: {torch.cuda.max_memory_allocated() / 1024**3:.2f} GB")

    # Save LoRA adapters
    model.save_pretrained(str(output_dir / "lora_adapter"))
    tokenizer.save_pretrained(str(output_dir / "lora_adapter"))
    log.info(f"\nLoRA adapter saved to {output_dir / 'lora_adapter'}")

    # Save training report
    report = {
        "base_model": base_model,
        "samples": len(data),
        "epochs": epochs,
        "learning_rate": lr,
        "final_loss": final_loss[-1] if final_loss else None,
        "peak_vram_gb": (torch.cuda.max_memory_allocated() / 1024**3
                         if torch.cuda.is_available() else 0),
        "backend": "peft",
    }
    import time as _time
    report["timestamp"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
    report_path = output_dir / "training_report_v3.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log.info(f"Report saved to {report_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Train WaggleDance MicroModel V3 with LoRA"
    )
    parser.add_argument("--check", action="store_true",
                        help="Check dependencies only")
    parser.add_argument("--input", default="data/training_v3.jsonl",
                        help="Input training JSONL")
    parser.add_argument("--output-dir", default="data/lora_adapters",
                        help="Output directory for LoRA adapters")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL,
                        help=f"Base model (default: {DEFAULT_BASE_MODEL})")
    parser.add_argument("--epochs", type=int, default=3,
                        help="Training epochs (default: 3)")
    parser.add_argument("--lr", type=float, default=2e-4,
                        help="Learning rate (default: 2e-4)")
    parser.add_argument("--batch-size", type=int, default=2,
                        help="Batch size (default: 2)")
    parser.add_argument("--max-samples", type=int, default=5000,
                        help="Max training samples (default: 5000)")
    parser.add_argument("--no-unsloth", action="store_true",
                        help="Force PEFT backend (skip Unsloth)")
    parser.add_argument("--load-test", action="store_true",
                        help="Load model + tokenizer only (verify VRAM fit)")
    args = parser.parse_args()

    log.info("=== WaggleDance MicroModel V3 LoRA Trainer ===\n")

    log.info("Checking dependencies...")
    deps = check_dependencies()

    if args.check:
        if deps["peft"] or deps["unsloth"]:
            log.info("\n✓ Ready for LoRA training")
        else:
            log.info("\n✗ Missing dependencies. Install:")
            log.info("  pip install peft>=0.11.0 trl datasets accelerate")
            log.info("  pip install unsloth  # optional, 2x faster")
            sys.exit(1)
        return

    if not deps["torch"]:
        log.error("\nPyTorch is required. Install: pip install torch")
        sys.exit(1)

    if not deps["peft"] and not deps["unsloth"]:
        log.error("\nNeither PEFT nor Unsloth installed.")
        log.info("Install: pip install peft>=0.11.0 trl datasets accelerate")
        sys.exit(1)

    # Load-test mode: verify model fits in VRAM
    if args.load_test:
        log.info(f"\n=== Load Test: {args.base_model} ===")
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
            tokenizer = AutoTokenizer.from_pretrained(args.base_model)
            model = AutoModelForCausalLM.from_pretrained(
                args.base_model,
                quantization_config=BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_use_double_quant=True,
                ),
                device_map="auto",
                max_memory={0: "6GiB", "cpu": "40GiB"},
            )
            vram = torch.cuda.memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
            log.info(f"✓ Model loaded — VRAM: {vram:.2f} GB")
            log.info(f"  Params: {sum(p.numel() for p in model.parameters()):,}")
            log.info(f"  Vocab: {len(tokenizer):,}")
            del model, tokenizer
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("✓ Load test PASSED")
        except Exception as e:
            log.error(f"✗ Load test FAILED: {e}")
            sys.exit(1)
        return

    # Load data
    data = load_training_data(Path(args.input), args.max_samples)
    if not data:
        sys.exit(1)

    if len(data) < 50:
        log.warning(f"\nOnly {len(data)} samples — recommend 500+ for good results")

    output_dir = Path(args.output_dir)

    # Train with best available backend
    try:
        if deps["unsloth"] and not args.no_unsloth:
            train_with_unsloth(data, args.base_model, output_dir,
                               args.epochs, args.lr, args.batch_size)
        elif deps["peft"]:
            train_with_peft(data, args.base_model, output_dir,
                            args.epochs, args.lr, args.batch_size)
    except Exception as e:
        log.error(f"\nTraining failed: {e}")
        log.info("Try a smaller model: --base-model TinyLlama/TinyLlama-1.1B-Chat-v1.0")
        sys.exit(1)

    log.info("\n=== Training Complete ===")
    log.info(f"Adapter: {output_dir / 'lora_adapter'}")
    log.info("To use: load adapter with PEFT merge_and_unload() or serve via Ollama")


if __name__ == "__main__":
    main()
